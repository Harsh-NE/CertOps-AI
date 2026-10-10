from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.providers.normalize import normalize_x509
from app.timeutil import days_until, ensure_utc, utcnow
from tests.conftest import make_cert


def test_ensure_utc_converts_and_assumes_naive_is_utc():
    aware = datetime(2030, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=5)))
    assert ensure_utc(aware) == datetime(2030, 1, 1, 7, 0, tzinfo=UTC)
    assert ensure_utc(datetime(2030, 1, 1, 7, 0)).tzinfo is not None
    assert ensure_utc(None) is None


def test_days_until():
    assert days_until(utcnow() + timedelta(days=10, minutes=1)) == 10
    assert days_until(utcnow() - timedelta(days=5)) < 0
    assert days_until(None) is None


def test_normalize_ec_certificate(ec_key):
    _, cert = make_cert("ec.test", key=ec_key)
    n = normalize_x509(cert, provider="network_scan", source_ref="x")
    assert n.key_algorithm == "EC" and n.key_size == 256 and n.signature_hash == "sha256"
    assert n.not_after.tzinfo is not None and len(n.fingerprint_sha256) == 64


def test_production_requires_secret_key():
    with pytest.raises(RuntimeError):
        Settings(ENV="production", SECRET_KEY=None, _env_file=None).resolved_secret_key()
    with pytest.raises(RuntimeError):
        Settings(ENV="production", SECRET_KEY="change_me", _env_file=None).resolved_secret_key()
    assert Settings(ENV="production", SECRET_KEY="a-real-random-value", _env_file=None).resolved_secret_key()


def test_development_gets_ephemeral_key():
    assert len(Settings(ENV="development", SECRET_KEY=None, _env_file=None).resolved_secret_key()) > 30


def test_cors_origins_parse_comma_separated_env(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a.example, http://b.example")
    assert Settings(_env_file=None).cors_origins_list == ["http://a.example", "http://b.example"]
