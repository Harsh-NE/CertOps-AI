from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization

from app.config import Settings
from app.services.cert_scanner import parse_certificate
from app.timeutil import days_until, to_naive_utc, utcnow
from tests.conftest import make_cert


def test_to_naive_utc_converts_aware_datetimes():
    aware = datetime(2030, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=5)))
    assert to_naive_utc(aware) == datetime(2030, 1, 1, 7, 0)
    assert to_naive_utc(None) is None


def test_days_until():
    assert days_until(utcnow() + timedelta(days=10, minutes=1)) == 10
    assert days_until(utcnow() - timedelta(days=5)) < 0
    assert days_until(None) is None


def test_parse_certificate_ec_key(ec_key):
    _, cert = make_cert("ec.test", key=ec_key)
    parsed = parse_certificate(cert.public_bytes(serialization.Encoding.DER))
    assert parsed["key_algorithm"] == "EC" and parsed["key_size"] == 256
    assert parsed["not_after"].tzinfo is None  # normalised to naive UTC


def test_production_requires_secret_key():
    with pytest.raises(RuntimeError):
        Settings(ENV="production", SECRET_KEY=None, _env_file=None).resolved_secret_key()
    with pytest.raises(RuntimeError):
        Settings(ENV="production", SECRET_KEY="change_me", _env_file=None).resolved_secret_key()
    assert Settings(ENV="production", SECRET_KEY="a-real-random-value", _env_file=None).resolved_secret_key()


def test_development_gets_ephemeral_key():
    assert len(Settings(ENV="development", SECRET_KEY=None, _env_file=None).resolved_secret_key()) > 30
