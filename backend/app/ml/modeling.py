from typing import Dict

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.ml.features import BINARY, CATEGORICAL, NUMERIC


def _prep() -> ColumnTransformer:
    return ColumnTransformer([
        ("num", StandardScaler(), NUMERIC),
        ("bin", "passthrough", BINARY),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL),
    ])


def make_logistic(seed: int = 0) -> Pipeline:
    return Pipeline([("prep", _prep()), ("clf", LogisticRegression(max_iter=2000, random_state=seed))])


def make_gradient_boosting(seed: int = 0) -> CalibratedClassifierCV:
    """Histogram gradient boosting with isotonic calibration, so predicted probabilities are usable as risk scores."""
    base = Pipeline([("prep", _prep()),
                     ("clf", HistGradientBoostingClassifier(max_depth=4, learning_rate=0.08, max_iter=250,
                                                            random_state=seed))])
    return CalibratedClassifierCV(base, method="isotonic", cv=3)


def best_f1_threshold(y_true, score) -> float:
    """Operating point maximizing F1. Must be chosen on a validation set, never on the test set."""
    p, r, thr = precision_recall_curve(y_true, score)
    f1 = 2 * p[:-1] * r[:-1] / np.maximum(p[:-1] + r[:-1], 1e-12)
    return float(thr[int(np.argmax(f1))])


def metrics(y_true, score, threshold: float, probabilistic: bool) -> Dict[str, float]:
    pred = (np.asarray(score) >= threshold).astype(int)
    out = {
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, score)),
        "pr_auc": float(average_precision_score(y_true, score)),
        "flagged_share": float(pred.mean()),
    }
    if probabilistic:
        out["brier"] = float(brier_score_loss(y_true, score))
    return out
