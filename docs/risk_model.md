# Risk model

> **All model training and evaluation in this project uses SYNTHETIC data.** No labelled real-world certificate
> incident data exists here. Every metric below measures how well a model recovers the structure of the
> simulator in `backend/app/ml/simulator.py`, **not** how well it would predict real incidents.

## What is predicted

`P(certificate causes a service problem within 30 days)`, where a "problem" is either

* the certificate **expires before it is renewed**, or
* an **incident** happens (revocation, hostname/chain breakage after configuration drift, ...).

Certificates that are *already* expired, revoked or have an invalid chain are failures, not forecasts. They are
handled by explicit floors at serving time (`final = max(model, floor)`): expired/revoked -> critical (p >= 0.95),
invalid chain -> at least high (0.50), hostname mismatch / weak crypto -> at least medium (0.20).

## Features (same code path in training and serving)

`backend/app/ml/features.py`: days to expiry, validity period, key size, key algorithm, chain depth, SAN count,
wildcard, issuer type (ACME / commercial / private CA / self-signed), source (network, ACM, k8s, ...), auto-renew
enabled, has owner, production environment, weak signature, invalid chain, hostname mismatch, and renewal history
(number of real past renewal attempts and a smoothed failure rate; dry runs, skips and tickets are not counted).

## The simulator (and why its assumptions matter)

A latent per-team *reliability* (not a feature) drives how often renewals fail and how late humans renew. Auto-renew
has systemic and per-attempt failure modes; manual renewal has an exponential delay and a "forgotten" probability
that depends on reliability, owner presence and environment. Incidents follow a hazard that rises with hostname
mismatch, invalid chain, weak signature and private CAs, with lognormal noise. Full definition: module docstring.

* **Constants were fixed before any model was fitted**; only the resulting base rate (5.8% positive) was inspected.
* The incident hazard is *wired by me* to `hostname_mismatch`, `chain_invalid` and `weak_signature`. A model that
  "learns" those effects is recovering my own assumptions.
* A second simulator configuration (world B: less reliable teams, more incidents, less auto-renew) tests robustness
  to distribution shift. It is still the same simulator family.

## Evaluation protocol

`python -m app.ml.evaluate` - team-grouped train 60% / validation 20% / test 20% (no team in two splits); every
model's operating threshold is chosen on **validation** (max F1) and reported on **test**; baseline =
"flag if days_to_expiry <= T"; bootstrap 95% confidence intervals for the model-minus-baseline gap; permutation
importance. Results: `docs/results/risk_eval.md` (generated, includes the command's exact output).

## What the results do and do not show

* The model **ranks** much better than the days-to-expiry rule (ROC-AUC and PR-AUC gaps have confidence intervals
  that exclude zero).
* At the F1-optimal operating point it is **essentially tied** with the rule (F1 0.505 vs 0.499): the extra ranking
  quality is spent on cases the rule also catches at that threshold.
* Permutation importance is dominated by `days_to_expiry`, then the two validation flags the simulator wires to
  incidents. The hypothesis that renewal history reveals unreliable teams shows **almost no signal** even inside the
  simulator (`prior_attempts` costs 0.006 ROC-AUC when permuted, `auto_renew` 0.005, and `prior_failure_rate` is not in the top 8).
* Under the shifted world B the ranking advantage persists but precision/recall move; probabilities are calibrated
  for world A only.

## Limitations

* Synthetic data; no claim about real-world accuracy. Real use requires retraining on labelled history (renewal
  tasks, scan history, incident tickets) through the same `build_features` path.
* Probability tiers (`low < 5% <= medium < 15% <= high < 40% <= critical`) are a policy choice, not derived.
* Explanations are one-feature-at-a-time ablations (not SHAP) and attribute interactions crudely.
* The artifact is a pickle (joblib): only load files you produced.
* `predicted_outage_impact` is a rule (blast radius from environment and expiry), not a model output.
  The earlier "renewal delay" regressor was removed: it was trained on a target invented from the same rules.
