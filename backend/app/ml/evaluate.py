"""
Evaluation of the risk model against baselines on SYNTHETIC data (see simulator.py; not real-world accuracy).

    cd backend && python -m app.ml.evaluate [--n 20000] [--seed 7] [--bootstrap 1000] [--docs ../docs/results]

Protocol
  * one simulated fleet; split by team (GroupShuffleSplit) so no team appears in more than one split
    -> train 60% / validation 20% / test 20%
  * the operating threshold of every model is chosen on VALIDATION (max F1); metrics are reported on TEST
  * baseline = rule on days_to_expiry only (flag if days_to_expiry <= T); T is the validation-optimal threshold
  * extra reference model: logistic regression on all features
  * bootstrap 95% CI for (model - baseline) ROC-AUC / PR-AUC on the test set
  * robustness: the same trained models on a fleet from a DIFFERENT simulator configuration (world B)
"""
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
import pandas as pd
import sklearn
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import GroupShuffleSplit

from app.ml.features import FEATURES, NUMERIC
from app.ml.modeling import best_f1_threshold, make_gradient_boosting, make_logistic, metrics
from app.ml.simulator import CONFIG_A, CONFIG_B, HORIZON_DAYS, SIM_VERSION, generate

MODEL_NAMES = ["baseline_days_to_expiry", "logistic_regression", "gradient_boosting"]


def split_by_team(df: pd.DataFrame, seed: int) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    outer = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=seed)
    trainval_idx, test_idx = next(outer.split(df, groups=df.team_id))
    trainval, test = df.iloc[trainval_idx], df.iloc[test_idx]
    inner = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=seed + 1)
    train_idx, val_idx = next(inner.split(trainval, groups=trainval.team_id))
    return trainval.iloc[train_idx], trainval.iloc[val_idx], test


def scores(models: Dict[str, Any], X: pd.DataFrame) -> Dict[str, np.ndarray]:
    return {
        "baseline_days_to_expiry": -X["days_to_expiry"].to_numpy(),  # sooner expiry -> higher score
        "logistic_regression": models["logistic_regression"].predict_proba(X)[:, 1],
        "gradient_boosting": models["gradient_boosting"].predict_proba(X)[:, 1],
    }


def _bootstrap_delta(y, s_model, s_base, n_boot: int, seed: int) -> Dict[str, list]:
    rng = np.random.default_rng(seed)
    idx = np.arange(len(y))
    d_auc, d_pr = [], []
    for _ in range(n_boot):
        b = rng.choice(idx, size=len(idx), replace=True)
        if y[b].min() == y[b].max():
            continue
        d_auc.append(roc_auc_score(y[b], s_model[b]) - roc_auc_score(y[b], s_base[b]))
        d_pr.append(average_precision_score(y[b], s_model[b]) - average_precision_score(y[b], s_base[b]))
    q = lambda a: [float(np.percentile(a, 2.5)), float(np.percentile(a, 97.5))]  # noqa: E731
    return {"roc_auc_delta_95ci": q(d_auc), "pr_auc_delta_95ci": q(d_pr)}


def run_evaluation(n: int = 20000, seed: int = 7, bootstrap: int = 1000) -> Tuple[Dict[str, Any], Dict[str, Any], pd.DataFrame]:
    """Returns (results, fitted_models, train_frame)."""
    df = generate(n, CONFIG_A, seed=seed)
    train, val, test = split_by_team(df, seed)
    X = lambda d: d[FEATURES]  # noqa: E731
    models = {"logistic_regression": make_logistic(seed).fit(X(train), train.label),
              "gradient_boosting": make_gradient_boosting(seed).fit(X(train), train.label)}

    s_val, s_test = scores(models, X(val)), scores(models, X(test))
    thresholds = {m: best_f1_threshold(val.label, s_val[m]) for m in MODEL_NAMES}
    probabilistic = {"baseline_days_to_expiry": False, "logistic_regression": True, "gradient_boosting": True}
    test_metrics = {m: metrics(test.label, s_test[m], thresholds[m], probabilistic[m]) for m in MODEL_NAMES}

    y = test.label.to_numpy()
    boot = {m: _bootstrap_delta(y, s_test[m], s_test["baseline_days_to_expiry"], bootstrap, seed)
            for m in ("logistic_regression", "gradient_boosting")}

    shifted = generate(n // 2, CONFIG_B, seed=seed + 100)
    s_shift = scores(models, X(shifted))
    shift_metrics = {m: metrics(shifted.label, s_shift[m], thresholds[m], probabilistic[m]) for m in MODEL_NAMES}

    imp = permutation_importance(models["gradient_boosting"], X(test), test.label, scoring="roc_auc",
                                 n_repeats=5, random_state=seed)
    importance = sorted(({"feature": f, "roc_auc_drop": float(m)} for f, m in zip(FEATURES, imp.importances_mean, strict=True)),
                        key=lambda d: -d["roc_auc_drop"])[:8]

    results = {
        "data": f"SYNTHETIC (simulator v{SIM_VERSION}); not real-world data",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sklearn": sklearn.__version__,
        "label": f"certificate causes a service problem within {HORIZON_DAYS} days",
        "n": n, "seed": seed,
        "split": {"train": len(train), "validation": len(val), "test": len(test)},
        "positive_rate": {"train": float(train.label.mean()), "validation": float(val.label.mean()),
                          "test": float(test.label.mean()), "shifted_world_B": float(shifted.label.mean())},
        "thresholds_chosen_on_validation": thresholds,
        "test": test_metrics,
        "bootstrap_vs_baseline": boot,
        "shifted_world_B": shift_metrics,
        "permutation_importance_top": importance,
    }
    return results, models, train


def to_markdown(r: Dict[str, Any]) -> str:
    cols = ["precision", "recall", "f1", "roc_auc", "pr_auc", "brier"]
    def table(block):
        lines = ["| model | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
        for m in MODEL_NAMES:
            lines.append(f"| {m} | " + " | ".join(f"{block[m][c]:.3f}" if c in block[m] else "n/a" for c in cols) + " |")
        return "\n".join(lines)
    b = r["bootstrap_vs_baseline"]["gradient_boosting"]
    auc_lo, auc_hi = b["roc_auc_delta_95ci"]
    pr_lo, pr_hi = b["pr_auc_delta_95ci"]
    shift_rate = r["positive_rate"]["shifted_world_B"]
    return f"""> **SYNTHETIC DATA.** {r['data']}. These numbers describe how well each model recovers the simulator's
> structure, not how well it predicts real certificate incidents.

Label: {r['label']}. n={r['n']}, seed={r['seed']}, split by team {r['split']}, positive rate (test) {r['positive_rate']['test']:.3f}.
Operating thresholds were chosen on the validation set; all metrics below are on the held-out test set.

### Test set (world A)
{table(r['test'])}

Gradient boosting minus baseline, bootstrap 95% CI: ROC-AUC {auc_lo:+.3f} .. {auc_hi:+.3f}, PR-AUC {pr_lo:+.3f} .. {pr_hi:+.3f}.

### Same models on a differently-configured simulated fleet (world B: distribution shift; positive rate {shift_rate:.3f})
{table(r['shifted_world_B'])}

### Permutation importance of the gradient-boosting model (ROC-AUC drop on test)
""" + "\n".join(f"- {d['feature']}: {d['roc_auc_drop']:.4f}" for d in r["permutation_importance_top"]) + "\n"


def write_docs(results: Dict[str, Any], docs_dir: Path) -> None:
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "risk_eval.json").write_text(json.dumps(results, indent=2))
    (docs_dir / "risk_eval.md").write_text(to_markdown(results))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--docs", default=None, help="directory to write risk_eval.json/.md")
    args = ap.parse_args(argv)
    results, _, _ = run_evaluation(args.n, args.seed, args.bootstrap)
    print(to_markdown(results))
    if args.docs:
        write_docs(results, Path(args.docs))
        print(f"wrote {args.docs}/risk_eval.json and risk_eval.md")


if __name__ == "__main__":
    main()

__all__ = ["run_evaluation", "to_markdown", "write_docs", "NUMERIC"]
