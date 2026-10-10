import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from app import models
from app.database import SessionLocal
from app.ml import scoring
from app.ml.evaluate import MODEL_NAMES, run_evaluation, split_by_team, to_markdown
from app.ml.features import BINARY, CATEGORICAL, FEATURES, NUMERIC, build_features, failure_rate, issuer_type, to_frame
from app.ml.modeling import best_f1_threshold, metrics
from app.ml.simulator import CONFIG_A, CONFIG_B, generate
from app.ml.train import train
from app.timeutil import utcnow

# ---- features ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("issuer,expected", [
    ("CN=R11,O=Let's Encrypt,C=US", "acme_public"),
    ("CN=DigiCert TLS RSA SHA256 2020 CA1", "commercial_public"),
    ("CN=Amazon RSA 2048 M02,O=Amazon", "commercial_public"),
    ("CN=Corp Issuing CA 01,O=Example Corp", "private_ca"),
    (None, "private_ca"),
])
def test_issuer_type(issuer, expected):
    assert issuer_type(issuer) == expected


def test_self_signed_detected_from_subject_equals_issuer():
    assert issuer_type("CN=x", "CN=x") == "self_signed"


def test_failure_rate_has_a_neutral_prior_without_history():
    assert failure_rate(0, 0) == 0.25
    assert failure_rate(10, 0) < 0.25 < failure_rate(10, 10)


def test_feature_frame_columns_are_fixed_and_complete():
    assert FEATURES == NUMERIC + BINARY + CATEGORICAL
    frame = to_frame([{"days_to_expiry": 10}, {"days_to_expiry": 500, "issuer": "CN=R11,O=Let's Encrypt"}])
    assert list(frame.columns) == FEATURES and len(frame) == 2


def test_build_features_clips_extremes_and_defaults_unknowns():
    f = build_features({"days_to_expiry": -999, "key_size": None, "chain_depth": 99, "source": "mystery", "key_algorithm": "DSA"})
    assert f["days_to_expiry"] == -30 and f["chain_depth"] == 6 and f["key_size"] == 2048
    assert f["source"] == "network_scan" and f["key_algorithm"] == "other"


# ---- simulator hygiene ------------------------------------------------------------------------------------

def test_simulator_is_deterministic_and_has_a_sane_base_rate():
    a, b = generate(3000, CONFIG_A, seed=3), generate(3000, CONFIG_A, seed=3)
    assert a.equals(b)
    assert 0.02 < a.label.mean() < 0.20
    assert generate(3000, CONFIG_B, seed=3).label.mean() > a.label.mean()  # world B is harsher by construction


def test_latent_and_outcome_columns_are_never_features():
    df = generate(500, CONFIG_A, seed=1)
    assert not any(c.startswith("_") or c in ("label", "team_id") for c in FEATURES)
    assert {"_team_reliability", "_expiry_outage", "_incident", "label", "team_id"} <= set(df.columns)


def test_split_has_no_team_overlap_between_train_validation_and_test():
    df = generate(4000, CONFIG_A, seed=5)
    train_df, val, test = split_by_team(df, 5)
    t, v, s = set(train_df.team_id), set(val.team_id), set(test.team_id)
    assert not (t & v) and not (t & s) and not (v & s)
    assert len(train_df) + len(val) + len(test) == len(df)


# ---- metrics ----------------------------------------------------------------------------------------------

def test_best_f1_threshold_and_metrics_on_a_known_example():
    y = np.array([0, 0, 0, 1, 1, 1])
    score = np.array([0.1, 0.2, 0.6, 0.4, 0.7, 0.9])
    thr = best_f1_threshold(y, score)
    m = metrics(y, score, thr, probabilistic=True)
    assert thr == 0.4 and m["recall"] == 1.0 and m["precision"] == 0.75
    assert m["roc_auc"] == pytest.approx(roc_auc_score(y, score)) and "brier" in m


def test_evaluation_protocol_end_to_end_on_a_small_fleet():
    results, models_, train_df = run_evaluation(n=3000, seed=2, bootstrap=30)
    assert set(results["test"]) == set(MODEL_NAMES) == set(results["shifted_world_B"])
    assert set(results["thresholds_chosen_on_validation"]) == set(MODEL_NAMES)
    assert results["data"].startswith("SYNTHETIC")
    assert "SYNTHETIC DATA" in to_markdown(results)
    assert results["test"]["gradient_boosting"]["roc_auc"] > 0.6


# ---- artifact + serving -----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def small_artifact(tmp_path_factory):
    import joblib
    out = tmp_path_factory.mktemp("model") / "risk_model.joblib"
    train(n=4000, seed=11, out=out, bootstrap=20)
    return joblib.load(out)


@pytest.fixture()
def serving(monkeypatch, small_artifact):
    monkeypatch.setattr(scoring, "load_artifact", lambda: small_artifact)
    return small_artifact


def test_artifact_metadata_is_honest(small_artifact):
    meta = small_artifact["metadata"]
    assert meta["data_source"] == "synthetic" and "SYNTHETIC" in meta["disclaimer"]
    assert meta["features"] == FEATURES and 0 < meta["threshold"] < 1
    assert "gradient_boosting" in meta["test_metrics"]


def test_soon_to_expire_is_riskier_than_far_from_expiry(serving):
    soon = scoring.predict_raw({"days_to_expiry": 3, "validity_days": 90})
    far = scoring.predict_raw({"days_to_expiry": 300, "validity_days": 365})
    assert soon["probability"] > far["probability"] and soon["model_used"] == "gradient_boosting"
    assert soon["data_source"] == "synthetic"


def test_explanations_name_features_that_raise_the_risk(serving):
    pred = scoring.predict_raw({"days_to_expiry": 3, "validity_days": 90, "hostname_mismatch": True})
    assert pred["factors"] and all(f["effect"] > 0 for f in pred["factors"])
    assert "days_to_expiry" in {f["feature"] for f in pred["factors"]}


def test_without_an_artifact_the_rule_baseline_is_used_and_labelled(monkeypatch):
    monkeypatch.setattr(scoring, "load_artifact", lambda: None)
    pred = scoring.predict_raw({"days_to_expiry": 5})
    assert pred["model_used"] == "rule_baseline" and pred["data_source"] == "none" and pred["factors"] == []


def tier_rank(t):
    return scoring.TIER_ORDER.index(t)


def _db_cert(status, **kw):
    db = SessionLocal()
    try:
        cert = models.Certificate(common_name="risk.test", status=status, not_after=kw.pop("not_after", utcnow()),
                                  not_before=utcnow(), **kw)
        db.add(cert)
        db.commit()
        db.refresh(cert)
        return db, cert
    except Exception:
        db.close()
        raise


@pytest.mark.parametrize("status,min_tier", [
    (models.CertStatus.REVOKED, "critical"), (models.CertStatus.EXPIRED, "critical"),
    (models.CertStatus.INVALID_CHAIN, "high"), (models.CertStatus.HOSTNAME_MISMATCH, "medium"),
    (models.CertStatus.WEAK_CRYPTO, "medium"),
])
def test_already_failing_certificates_are_floored_not_forecast(serving, status, min_tier):
    from datetime import timedelta
    db, cert = _db_cert(status, not_after=utcnow() + timedelta(days=300), is_wildcard=False)
    try:
        out = scoring.score_certificate(db, cert)
        assert tier_rank(out["tier"]) >= tier_rank(min_tier) and out["floor_reason"]
        assert out["risk_score"] >= scoring.FLOORS[status][1] * 100 - 0.1
    finally:
        db.close()


def test_a_healthy_far_from_expiry_certificate_is_not_floored(serving):
    from datetime import timedelta
    db, cert = _db_cert(models.CertStatus.VALID, not_after=utcnow() + timedelta(days=300), auto_renew_enabled=True)
    try:
        out = scoring.score_certificate(db, cert)
        assert out["floor_reason"] is None and out["tier"] in ("low", "medium")
    finally:
        db.close()


def test_renewal_history_comes_only_from_real_attempts(serving):
    from datetime import timedelta
    db, cert = _db_cert(models.CertStatus.VALID, not_after=utcnow() + timedelta(days=60))
    try:
        S = models.RenewalTaskStatus
        for st, dry in [(S.FAILED, False), (S.AUTO_RENEWED, False), (S.DRY_RUN, True), (S.SKIPPED, False),
                        (S.JIRA_CREATED, False)]:
            db.add(models.RenewalTask(certificate_id=cert.id, status=st, dry_run=dry))
        db.commit()
        raw = scoring.raw_from_certificate(db, cert, {})
        assert (raw["prior_attempts"], raw["prior_failures"]) == (2, 1)  # dry runs, skips and tickets don't count
    finally:
        db.close()


def test_train_and_serve_feature_paths_agree():
    """A DB certificate and the equivalent raw dict must produce identical model input."""
    from datetime import timedelta
    db, cert = _db_cert(models.CertStatus.VALID, not_after=utcnow() + timedelta(days=45, seconds=30),
                        key_size=2048, key_algorithm="RSA", issuer="CN=R11,O=Let's Encrypt", chain_depth=3,
                        environment="Prod", auto_renew_enabled=True, san_list='["a.test", "b.test"]')
    try:
        from_db = build_features(scoring.raw_from_certificate(db, cert, {"hostname_mismatch": True}))
        by_hand = build_features({
            "days_to_expiry": 45, "validity_days": 0, "key_size": 2048, "key_algorithm": "RSA", "chain_depth": 3,
            "san_count": 2, "is_prod": True, "auto_renew": True, "has_owner": False,
            "issuer": "CN=R11,O=Let's Encrypt", "source": "network_scan", "hostname_mismatch": True})
        by_hand["validity_days"] = from_db["validity_days"]  # validity depends on the row's timestamps
        assert from_db == by_hand
    finally:
        db.close()


# ---- API --------------------------------------------------------------------------------------------------

def test_api_model_info_discloses_synthetic_training(client, admin_headers, serving):
    body = client.get("/api/risk/model", headers=admin_headers).json()
    assert body["data_source"] == "synthetic" and "SYNTHETIC" in body["disclaimer"]
    assert "reference" not in body and "test_metrics" in body


def test_api_predict_what_if(client, admin_headers, serving):
    r = client.post("/api/risk/predict", json={"days_to_expiry": 2, "is_prod": True}, headers=admin_headers)
    assert r.status_code == 200
    assert 0 <= r.json()["probability"] <= 1 and r.json()["tier"] in scoring.TIER_ORDER
    assert client.post("/api/risk/predict", json={}, headers=admin_headers).status_code == 422


def test_api_certificate_risk_and_404(client, admin_headers, serving):
    from datetime import timedelta
    db, cert = _db_cert(models.CertStatus.EXPIRED, not_after=utcnow() - timedelta(days=3))
    db.close()
    r = client.get(f"/api/risk/certificates/{cert.id}", headers=admin_headers).json()
    assert r["tier"] == "critical" and r["floor_reason"] == "certificate is expired"
    assert client.get("/api/risk/certificates/nope", headers=admin_headers).status_code == 404
    assert client.get("/api/risk/model").status_code == 401
