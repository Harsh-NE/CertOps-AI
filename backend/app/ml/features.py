"""
Feature definition shared by training (synthetic simulator) and serving (real certificates), so there is no
train/serve skew: both paths produce a "raw" mapping and go through `build_features`.

Raw keys
  days_to_expiry, validity_days, key_size, chain_depth, san_count      numbers
  is_wildcard, auto_renew, has_owner, is_prod                          booleans
  weak_signature, chain_invalid, hostname_mismatch                     booleans (validation findings)
  issuer, key_algorithm, source                                        strings
  prior_attempts, prior_failures                                       renewal history (non-dry-run attempts)
"""
from typing import Any, Dict, List, Mapping

import pandas as pd

NUMERIC: List[str] = ["days_to_expiry", "validity_days", "key_size", "chain_depth", "san_count",
                      "prior_attempts", "prior_failure_rate"]
BINARY: List[str] = ["is_wildcard", "auto_renew", "has_owner", "is_prod",
                     "weak_signature", "chain_invalid", "hostname_mismatch"]
CATEGORICAL: List[str] = ["issuer_type", "key_algorithm", "source"]
FEATURES: List[str] = NUMERIC + BINARY + CATEGORICAL

ISSUER_TYPES = ["acme_public", "commercial_public", "private_ca", "self_signed"]
SOURCES = ["network_scan", "aws_acm", "kubernetes_secret", "azure_key_vault", "gcp_cert_manager"]
KEY_ALGORITHMS = ["RSA", "EC", "other"]

_ACME = ("let's encrypt", "letsencrypt", "isrg", "zerossl", "buypass")
_COMMERCIAL = ("digicert", "sectigo", "globalsign", "godaddy", "amazon", "google trust", "microsoft", "entrust", "comodo")


def issuer_type(issuer: str | None, subject: str | None = None, self_signed: bool = False) -> str:
    text = (issuer or "").lower()
    if self_signed or (issuer and subject and issuer == subject):
        return "self_signed"
    if any(k in text for k in _ACME):
        return "acme_public"
    if any(k in text for k in _COMMERCIAL):
        return "commercial_public"
    return "private_ca"


def failure_rate(attempts: float, failures: float) -> float:
    """Smoothed historical renewal failure rate; 0.25 when there is no history (same in training and serving)."""
    return (failures + 0.5) / (attempts + 2.0)


def build_features(raw: Mapping[str, Any]) -> Dict[str, Any]:
    algo = raw.get("key_algorithm")
    attempts = float(raw.get("prior_attempts") or 0)
    failures = float(raw.get("prior_failures") or 0)
    itype = raw.get("issuer_type") or issuer_type(raw.get("issuer"), raw.get("subject"))
    return {
        "days_to_expiry": float(min(max(raw["days_to_expiry"], -30), 1200)),
        "validity_days": float(raw.get("validity_days") or 365),
        "key_size": float(raw.get("key_size") or 2048),
        "chain_depth": float(min(raw.get("chain_depth") or 2, 6)),
        "san_count": float(min(raw.get("san_count") or 1, 100)),
        "prior_attempts": min(attempts, 50.0),
        "prior_failure_rate": failure_rate(attempts, failures),
        "is_wildcard": int(bool(raw.get("is_wildcard"))),
        "auto_renew": int(bool(raw.get("auto_renew"))),
        "has_owner": int(bool(raw.get("has_owner"))),
        "is_prod": int(bool(raw.get("is_prod"))),
        "weak_signature": int(bool(raw.get("weak_signature"))),
        "chain_invalid": int(bool(raw.get("chain_invalid"))),
        "hostname_mismatch": int(bool(raw.get("hostname_mismatch"))),
        "issuer_type": itype if itype in ISSUER_TYPES else "private_ca",
        "key_algorithm": algo if algo in ("RSA", "EC") else "other",
        "source": raw.get("source") if raw.get("source") in SOURCES else "network_scan",
    }


def to_frame(rows: List[Mapping[str, Any]]) -> pd.DataFrame:
    """Raw mappings -> model input frame with a fixed column order."""
    return pd.DataFrame([build_features(r) for r in rows])[FEATURES]
