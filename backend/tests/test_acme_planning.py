"""ACME executor planning (no network to a CA): the dry run must refuse unsafe or unusable configurations."""
import json

import pytest

from app import models
from app.config import settings
from app.renewal.acme_executor import PRODUCTION_DIRECTORY, AcmeExecutor, ChallTestSrvSolver, WebrootSolver, renewal_domains
from app.renewal.secrets import MemorySecretStore, VaultKvStore


class UnconfiguredStore:
    configured = False


def cert(names=("shop.example.com",), host=None):
    return models.Certificate(common_name=names[0], san_list=json.dumps(list(names)), expected_hostname=host)


@pytest.fixture()
def ok_executor(tmp_path, monkeypatch):
    ex = AcmeExecutor(store=MemorySecretStore(), solver=WebrootSolver(str(tmp_path)), email="ops@example.com",
                      directory_url="https://acme.test/dir")
    monkeypatch.setattr(ex, "_directory_problem", lambda: None)
    return ex


def test_a_complete_configuration_is_executable(ok_executor):
    spec = ok_executor.plan(cert())
    assert spec.executable and spec.params["domains"] == ["shop.example.com"]
    assert any("NEW private key" in s for s in spec.steps)
    assert any("NOT automated" in w for w in spec.warnings)


def test_issuing_without_a_place_to_keep_the_key_is_refused(ok_executor):
    ok_executor.store = UnconfiguredStore()
    spec = ok_executor.plan(cert())
    assert not spec.executable and "secret store" in spec.not_executable_reason


def test_missing_email_blocks_the_plan(ok_executor):
    ok_executor.email = None
    assert "LETSENCRYPT_EMAIL" in ok_executor.plan(cert()).not_executable_reason


def test_production_ca_is_blocked_unless_explicitly_allowed(ok_executor, monkeypatch):
    ok_executor.directory_url = PRODUCTION_DIRECTORY
    assert "production" in ok_executor.plan(cert()).not_executable_reason
    monkeypatch.setattr(settings, "ACME_ALLOW_PRODUCTION", True)
    assert ok_executor.plan(cert()).executable


def test_wildcards_need_dns_01_and_are_not_attempted(ok_executor):
    spec = ok_executor.plan(cert(("*.example.com", "example.com")))
    assert not spec.executable and "wildcard" in spec.not_executable_reason


def test_ip_addresses_and_junk_are_not_acme_names():
    ok, bad = renewal_domains(cert(("a.example.com", "10.0.0.5", "not a host", "b.example.com")))
    assert ok == ["a.example.com", "b.example.com"] and set(bad) == {"10.0.0.5", "not a host"}


def test_unusable_challenge_solver_blocks_the_plan(ok_executor):
    ok_executor.solver = WebrootSolver("/does/not/exist")
    assert "challenge solver" in ok_executor.plan(cert()).not_executable_reason
    ok_executor.solver = ChallTestSrvSolver("")
    assert "ACME_CHALLTESTSRV_URL" in ok_executor.plan(cert()).not_executable_reason


def test_unreachable_directory_blocks_the_plan(tmp_path):
    ex = AcmeExecutor(store=MemorySecretStore(), solver=WebrootSolver(str(tmp_path)), email="a@b.c",
                      directory_url="http://127.0.0.1:9/dir")
    assert "unreachable" in ex.plan(cert()).not_executable_reason


def test_test_cas_get_a_warning(ok_executor):
    ok_executor.directory_url = "https://acme-staging-v02.api.letsencrypt.org/directory"
    assert any("TEST CA" in w for w in ok_executor.plan(cert()).warnings)


def test_execute_refuses_when_the_directory_changed_since_the_plan(ok_executor):
    res = ok_executor.execute(cert(), {"domains": ["shop.example.com"], "directory": "https://other.test/dir"})
    assert not res.success and not res.action_taken


def test_webroot_solver_writes_and_removes_the_token(tmp_path):
    s = WebrootSolver(str(tmp_path))
    s.present("a.example.com", "tok123", "tok123.keyauth")
    f = tmp_path / ".well-known" / "acme-challenge" / "tok123"
    assert f.read_text() == "tok123.keyauth"
    s.cleanup("a.example.com", "tok123")
    assert not f.exists()


def test_vault_store_wraps_client_errors_and_never_returns_data():
    class Kv:
        def create_or_update_secret(self, **kw):
            self.kw = kw

    class Client:
        secrets = type("S", (), {"kv": type("K", (), {"v2": Kv()})})()

        def is_authenticated(self):
            return True

    store = VaultKvStore(client=Client(), mount="secret", prefix="certops")
    ref = store.put("a.example.com", {"private_key": "SECRET"})
    assert ref == "vault:secret/certops/a.example.com" and "SECRET" not in ref
