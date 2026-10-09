"""
Trains the two risk-prediction models on a synthetic-but-realistic dataset
and persists them to app/ml_models/*.joblib.

Run manually whenever you want to (re)train:
    cd backend
    python -m app.services.train_risk_model

Why synthetic data: a brand-new deployment has no certificate history yet.
This generator encodes the domain knowledge from the platform's own risk
rules (expiry proximity, weak crypto, broken chains, missing ownership,
auto-renew coverage) as *noisy* labelled examples, so the model starts
useful on day one and can be retrained later on real historical data by
swapping `generate_synthetic_dataset()` for a query against `scan_logs` /
`renewal_tasks`.
"""
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.metrics import classification_report, mean_absolute_error
from sklearn.model_selection import train_test_split

from app.services.risk_ml import CLASSIFIER_PATH, FEATURE_ORDER, MODEL_DIR, REGRESSOR_PATH

RNG = np.random.default_rng(42)


def generate_synthetic_dataset(n=4000) -> pd.DataFrame:
    days_to_expiry = RNG.integers(-60, 400, size=n).astype(float)
    weak_signature = RNG.choice([0, 1], size=n, p=[0.9, 0.1]).astype(float)
    weak_key = RNG.choice([0, 1], size=n, p=[0.92, 0.08]).astype(float)
    chain_invalid = RNG.choice([0, 1], size=n, p=[0.93, 0.07]).astype(float)
    revoked = RNG.choice([0, 1], size=n, p=[0.98, 0.02]).astype(float)
    has_owner = RNG.choice([0, 1], size=n, p=[0.25, 0.75]).astype(float)
    auto_renew_enabled = RNG.choice([0, 1], size=n, p=[0.6, 0.4]).astype(float)
    multi_san = RNG.choice([0, 1], size=n, p=[0.5, 0.5]).astype(float)

    df = pd.DataFrame({
        "days_to_expiry": days_to_expiry,
        "weak_signature": weak_signature,
        "weak_key": weak_key,
        "chain_invalid": chain_invalid,
        "revoked": revoked,
        "has_owner": has_owner,
        "auto_renew_enabled": auto_renew_enabled,
        "multi_san": multi_san,
    })

    # --- Ground-truth risk score (with noise) drives the tier label ---
    score = np.zeros(n)
    score += np.where(days_to_expiry < 0, 45, 0)
    score += np.where((days_to_expiry >= 0) & (days_to_expiry <= 7), 35, 0)
    score += np.where((days_to_expiry > 7) & (days_to_expiry <= 30), 20, 0)
    score += np.where((days_to_expiry > 30) & (days_to_expiry <= 60), 8, 0)
    score += weak_signature * 20
    score += weak_key * 15
    score += chain_invalid * 25
    score += revoked * 40
    score += (1 - has_owner) * 10
    score -= auto_renew_enabled * 10
    score += RNG.normal(0, 6, size=n)  # label noise
    score = np.clip(score, 0, 100)

    tier = np.select(
        [score >= 70, score >= 45, score >= 20],
        ["critical", "high", "medium"],
        default="low",
    )

    # --- Ground-truth renewal delay (days), correlated with ownership/automation ---
    base_delay = 10 - has_owner * 6 - auto_renew_enabled * 8
    delay = np.clip(base_delay + RNG.normal(0, 2.5, size=n), 0, None)

    df["risk_tier"] = tier
    df["renewal_delay_days"] = delay
    return df


def train_and_save():
    os.makedirs(MODEL_DIR, exist_ok=True)
    df = generate_synthetic_dataset()

    X = df[FEATURE_ORDER]
    y_clf = df["risk_tier"]
    y_reg = df["renewal_delay_days"]

    X_train, X_test, y_clf_train, y_clf_test, y_reg_train, y_reg_test = train_test_split(
        X, y_clf, y_reg, test_size=0.2, random_state=42, stratify=y_clf
    )

    classifier = GradientBoostingClassifier(random_state=42, n_estimators=150, max_depth=3)
    classifier.fit(X_train, y_clf_train)
    print("=== Risk tier classifier ===")
    print(classification_report(y_clf_test, classifier.predict(X_test)))

    regressor = GradientBoostingRegressor(random_state=42, n_estimators=150, max_depth=3)
    regressor.fit(X_train, y_reg_train)
    mae = mean_absolute_error(y_reg_test, regressor.predict(X_test))
    print(f"=== Renewal delay regressor === MAE: {mae:.2f} days")

    joblib.dump(classifier, CLASSIFIER_PATH)
    joblib.dump(regressor, REGRESSOR_PATH)
    print(f"Saved models to {MODEL_DIR}")


if __name__ == "__main__":
    train_and_save()
