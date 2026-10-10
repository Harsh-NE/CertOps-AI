"""
Train and save the risk model artifact (SYNTHETIC training data; see simulator.py).

    cd backend && python -m app.ml.train [--n 20000] [--seed 7] [--docs ../docs/results]

The saved artifact is the exact model that was evaluated: fitted on the train split, calibrated, with the operating
threshold chosen on validation, and the held-out TEST metrics stored in its metadata. NOTE: joblib files are pickles;
only load artifacts you produced yourself (never from user uploads).
"""
import argparse
import os
from datetime import UTC, datetime
from pathlib import Path

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")  # silence joblib's physical-core probe on Windows

import joblib  # noqa: E402
import pandas as pd  # noqa: E402
import sklearn  # noqa: E402

from app.ml.evaluate import run_evaluation, write_docs  # noqa: E402
from app.ml.features import BINARY, CATEGORICAL, FEATURES, NUMERIC  # noqa: E402
from app.ml.simulator import HORIZON_DAYS, SIM_VERSION  # noqa: E402

DEFAULT_ARTIFACT = Path(__file__).resolve().parents[1] / "ml_models" / "risk_model.joblib"
DISCLAIMER = ("Trained on SYNTHETIC data from a simulator. Metrics describe recovery of the simulator's structure, "
              "not accuracy on real certificate incidents.")


def reference_values(train: pd.DataFrame) -> dict:
    """'Typical' value per feature, used by the ablation-based explanation."""
    ref = {f: float(train[f].median()) for f in NUMERIC}
    ref.update({f: int(train[f].mode().iloc[0]) for f in BINARY})
    ref.update({f: str(train[f].mode().iloc[0]) for f in CATEGORICAL})
    return ref


def train(n: int = 20000, seed: int = 7, out: Path = DEFAULT_ARTIFACT, docs_dir: Path | None = None,
          bootstrap: int = 1000) -> dict:
    results, models, train_df = run_evaluation(n, seed, bootstrap)
    metadata = {
        "version": f"synthetic-sim{SIM_VERSION}-seed{seed}-n{n}",
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_source": "synthetic",
        "disclaimer": DISCLAIMER,
        "label": f"certificate causes a service problem within {HORIZON_DAYS} days",
        "features": FEATURES,
        "threshold": results["thresholds_chosen_on_validation"]["gradient_boosting"],
        "reference": reference_values(train_df),
        "test_metrics": results["test"],
        "sklearn_version": sklearn.__version__,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": models["gradient_boosting"], "metadata": metadata}, out)
    if docs_dir:
        write_docs(results, docs_dir)
    return metadata


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(DEFAULT_ARTIFACT))
    ap.add_argument("--docs", default=None)
    ap.add_argument("--bootstrap", type=int, default=1000)
    a = ap.parse_args(argv)
    meta = train(a.n, a.seed, Path(a.out), Path(a.docs) if a.docs else None, a.bootstrap)
    gb = meta["test_metrics"]["gradient_boosting"]
    print(f"saved {a.out}\nversion {meta['version']} (SYNTHETIC)\n"
          f"test: roc_auc={gb['roc_auc']:.3f} pr_auc={gb['pr_auc']:.3f} f1={gb['f1']:.3f}")


if __name__ == "__main__":
    main()
