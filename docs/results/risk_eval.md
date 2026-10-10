> **SYNTHETIC DATA.** SYNTHETIC (simulator v1.0); not real-world data. These numbers describe how well each model recovers the simulator's
> structure, not how well it predicts real certificate incidents.

Label: certificate causes a service problem within 30 days. n=20000, seed=7, split by team {'train': 12014, 'validation': 3973, 'test': 4013}, positive rate (test) 0.058.
Operating thresholds were chosen on the validation set; all metrics below are on the held-out test set.

### Test set (world A)
| model | precision | recall | f1 | roc_auc | pr_auc | brier |
|---|---|---|---|---|---|---|
| baseline_days_to_expiry | 0.580 | 0.438 | 0.499 | 0.694 | 0.440 | n/a |
| logistic_regression | 0.189 | 0.313 | 0.235 | 0.749 | 0.162 | 0.052 |
| gradient_boosting | 0.596 | 0.438 | 0.505 | 0.863 | 0.568 | 0.035 |

Gradient boosting minus baseline, bootstrap 95% CI: ROC-AUC +0.127 .. +0.214, PR-AUC +0.091 .. +0.165.

### Same models on a differently-configured simulated fleet (world B: distribution shift; positive rate 0.100)
| model | precision | recall | f1 | roc_auc | pr_auc | brier |
|---|---|---|---|---|---|---|
| baseline_days_to_expiry | 0.601 | 0.437 | 0.506 | 0.695 | 0.447 | n/a |
| logistic_regression | 0.270 | 0.339 | 0.300 | 0.724 | 0.243 | 0.086 |
| gradient_boosting | 0.689 | 0.413 | 0.516 | 0.837 | 0.578 | 0.062 |

### Permutation importance of the gradient-boosting model (ROC-AUC drop on test)
- days_to_expiry: 0.2191
- hostname_mismatch: 0.0520
- chain_invalid: 0.0286
- issuer_type: 0.0195
- weak_signature: 0.0109
- source: 0.0088
- prior_attempts: 0.0059
- auto_renew: 0.0049
