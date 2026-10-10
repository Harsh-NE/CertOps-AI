"""
Synthetic certificate fleet + outcome simulator. THIS IS NOT REAL DATA.

No labelled real-world certificate-incident data exists for this project, so the risk model is trained on a
simulation of one. Any metric computed from it measures how well the model recovers *this simulator's* structure
(including the assumptions below), not how well it would predict real incidents.

Label (outcome, 30-day horizon): the certificate causes a service problem, i.e.
  * it expires before it is renewed ("expiry outage"), or
  * an incident occurs (revocation, hostname/chain breakage after config drift, ...).
Already-expired / already-revoked certificates are not part of the dataset (they are handled by explicit rules at
serving time, because they are failures, not forecasts).

Latent (NOT available as features): `team_reliability` r ~ Beta(a, b) per team. It drives
  * how often renewals silently fail, and how late humans renew;
  * a noisy, partial proxy shows up in the observable `prior_failure_rate`, `has_owner`.
Also a per-certificate lognormal "unobserved circumstances" multiplier on incident hazards.

Renewal process
  * auto-renew: attempts start `lead` days before expiry (lead = min(30, validity/3)); a "systemic" failure
    (DNS/firewall/credentials) with prob s = (1 - r) * systemic_scale blocks all attempts; otherwise each daily
    attempt succeeds with prob q = 0.9 * r_eff.
  * manual: renewal happens 14 days before expiry minus an Exponential delay whose mean grows with (1 - r) and
    with a missing owner (prod certificates get 30% more attention); with prob (1 - r) * 0.25 * (1 + no_owner)
    it is forgotten entirely.
  * Snapshot semantics: if the renewal would already have happened before the snapshot day, the observed
    certificate is the *new* one (fresh, far from expiry).
Incident hazard over 30 days: h = (0.01 + 0.12*hostname_mismatch + 0.10*chain_invalid + 0.05*weak_signature
  + 0.02*private/self_signed) * (1 + (1 - r)) * lognormal(0, 0.5) * incident_scale; p = 1 - exp(-h).

All constants below were fixed before any model was fitted; only the resulting base rate was inspected.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.ml.features import FEATURES, build_features

SIM_VERSION = "1.0"
HORIZON_DAYS = 30

ISSUER_STRINGS = {
    "acme_public": ["CN=R11,O=Let's Encrypt,C=US", "CN=E6,O=Let's Encrypt,C=US"],
    "commercial_public": ["CN=DigiCert TLS RSA SHA256 2020 CA1", "CN=Amazon RSA 2048 M02,O=Amazon", "CN=Sectigo RSA DV CA"],
    "private_ca": ["CN=Corp Issuing CA 01,O=Example Corp", "CN=Internal Services CA,O=Example Corp"],
    "self_signed": ["SELF"],
}


@dataclass(frozen=True)
class SimConfig:
    name: str = "A"
    n_teams: int = 300
    reliability_a: float = 4.0
    reliability_b: float = 2.0
    systemic_scale: float = 0.5
    incident_scale: float = 1.0
    auto_renew_scale: float = 1.0  # multiplies the probability that a certificate has auto-renew enabled


CONFIG_A = SimConfig()
# Deliberately different world used only to test robustness under distribution shift.
CONFIG_B = SimConfig(name="B", reliability_a=3.0, reliability_b=3.0, systemic_scale=0.8, incident_scale=1.5,
                     auto_renew_scale=0.7)


def _choice(rng, options, p, n):
    return rng.choice(options, size=n, p=np.asarray(p) / np.sum(p))


def generate(n: int, cfg: SimConfig = CONFIG_A, seed: int = 0) -> pd.DataFrame:
    """Returns one row per certificate: raw features, `team_id`, `label`, and hidden columns (prefixed `_`)."""
    rng = np.random.default_rng(seed)
    team_r = rng.beta(cfg.reliability_a, cfg.reliability_b, size=cfg.n_teams)
    team = rng.integers(0, cfg.n_teams, size=n)
    r = team_r[team]

    source = _choice(rng, ["network_scan", "aws_acm", "kubernetes_secret", "azure_key_vault", "gcp_cert_manager"],
                     [0.35, 0.25, 0.25, 0.10, 0.05], n)
    issuer_probs = {
        "network_scan": [0.35, 0.40, 0.15, 0.10], "aws_acm": [0.0, 0.95, 0.05, 0.0],
        "kubernetes_secret": [0.50, 0.10, 0.40, 0.0], "azure_key_vault": [0.0, 0.50, 0.50, 0.0],
        "gcp_cert_manager": [0.0, 0.70, 0.30, 0.0],
    }
    types = ["acme_public", "commercial_public", "private_ca", "self_signed"]
    itype = np.empty(n, dtype=object)
    for s, p in issuer_probs.items():
        idx = np.where(source == s)[0]
        itype[idx] = _choice(rng, types, p, len(idx))

    validity = np.where(itype == "acme_public", 90,
                        np.where(itype == "commercial_public", rng.choice([365, 397], size=n),
                                 np.where(itype == "private_ca", rng.choice([365, 730, 1095], size=n, p=[.5, .3, .2]),
                                          rng.choice([365, 825], size=n)))).astype(float)

    ec = rng.random(n) < np.where(itype == "acme_public", 0.4, 0.2)
    rsa_size = rng.choice([1024, 2048, 3072, 4096], size=n, p=[0.02, 0.80, 0.10, 0.08])
    key_size = np.where(ec, rng.choice([256, 384], size=n, p=[0.85, 0.15]), rsa_size)
    key_algorithm = np.where(ec, "EC", "RSA")
    chain_depth = np.where(itype == "self_signed", 1, rng.choice([2, 3, 4], size=n, p=[0.2, 0.65, 0.15]))
    san_count = 1 + rng.geometric(0.55, size=n) - 1
    is_wildcard = rng.random(n) < 0.15
    is_prod = rng.random(n) < 0.35
    has_owner = rng.random(n) < (0.55 + 0.40 * r)

    auto_p = np.select([itype == "acme_public", (source == "aws_acm"), itype == "commercial_public"],
                       [0.80, 0.85, 0.50], default=0.30) * cfg.auto_renew_scale
    auto = rng.random(n) < np.clip(auto_p, 0, 0.98)

    private_like = np.isin(itype, ["private_ca", "self_signed"])
    weak_sig = rng.random(n) < np.where(private_like, 0.10, 0.01)
    chain_invalid = rng.random(n) < np.where(private_like, 0.12, 0.02)
    host_mismatch = rng.random(n) < 0.03

    # --- renewal history: how many (non-dry-run) renewals this cert lineage saw, and how many failed
    prior_attempts = rng.poisson(np.maximum(0.5, 365.0 / validity * 1.5))
    r_eff = np.clip(r * np.where(auto, 1.0, 0.9), 0.02, 0.99)
    prior_failures = rng.binomial(prior_attempts, 1 - r_eff)

    # --- snapshot
    age = rng.random(n) * validity
    dte = validity - age

    # --- renewal timing, in "days before expiry" (negative = after expiry). R >= dte means already renewed.
    lead = np.minimum(30.0, validity / 3.0)
    systemic = rng.random(n) < (1 - r) * cfg.systemic_scale
    q = np.clip(0.9 * r_eff, 0.01, 0.99)
    day_of_success = rng.geometric(q)  # 1 = first attempt
    r_auto = np.where(systemic, -np.inf, lead - day_of_success)
    mean_delay = (1.5 + 10.0 * (1 - r)) * (1 + 1.5 * (~has_owner)) * np.where(is_prod, 0.7, 1.0)
    forgotten = rng.random(n) < (1 - r) * 0.25 * (1 + (~has_owner))
    r_manual = np.where(forgotten, -np.inf, 14.0 - rng.exponential(mean_delay))
    renew_at = np.where(auto, r_auto, r_manual)

    already_renewed = renew_at >= dte
    # observed certificate is the new one: issued (renew_at - dte) days ago
    new_age = np.clip(renew_at - dte, 0, validity - 1)
    dte = np.where(already_renewed, validity - new_age, dte)
    prior_attempts = prior_attempts + already_renewed
    expiry_outage = (~already_renewed) & (dte <= HORIZON_DAYS) & (renew_at < 0)

    drift = 0.01 + 0.12 * host_mismatch + 0.10 * chain_invalid + 0.05 * weak_sig + 0.02 * private_like
    hazard = drift * (1 + (1 - r)) * rng.lognormal(0, 0.5, size=n) * cfg.incident_scale
    incident = rng.random(n) < (1 - np.exp(-hazard))
    label = (expiry_outage | incident).astype(int)

    rows = []
    for i in range(n):
        issuer = ISSUER_STRINGS[itype[i]][i % len(ISSUER_STRINGS[itype[i]])]
        rows.append({
            "days_to_expiry": float(dte[i]), "validity_days": float(validity[i]), "key_size": int(key_size[i]),
            "chain_depth": int(chain_depth[i]), "san_count": int(san_count[i]), "is_wildcard": bool(is_wildcard[i]),
            "auto_renew": bool(auto[i]), "has_owner": bool(has_owner[i]), "is_prod": bool(is_prod[i]),
            "weak_signature": bool(weak_sig[i]), "chain_invalid": bool(chain_invalid[i]),
            "hostname_mismatch": bool(host_mismatch[i]), "issuer": issuer,
            "subject": issuer if itype[i] == "self_signed" else "CN=svc.example.test",
            "key_algorithm": str(key_algorithm[i]), "source": str(source[i]),
            "prior_attempts": int(prior_attempts[i]), "prior_failures": int(prior_failures[i]),
        })
    feats = pd.DataFrame([build_features(r_) for r_ in rows])[FEATURES]
    feats["team_id"] = team
    feats["label"] = label
    feats["_team_reliability"] = r
    feats["_expiry_outage"] = expiry_outage.astype(int)
    feats["_incident"] = incident.astype(int)
    return feats
