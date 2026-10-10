"""
Real ACME issuance against a local Pebble CA with the key and chain stored in a dev-mode Vault.

    docker compose -f ../docker-compose.renewal-lab.yml up -d
    RENEWAL_INTEGRATION=1 pytest tests/integration/test_acme_pebble.py -m integration

Nothing here touches a public CA. Skipped unless RENEWAL_INTEGRATION=1.
"""
import json
import os
import uuid

import hvac
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.agent import rca
from app.config import settings
from app.database import Base
from app.renewal.acme_executor import AcmeExecutor, ChallTestSrvSolver
from app.renewal.secrets import VaultKvStore
from app.renewal.service import Actor, RenewalService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RENEWAL_INTEGRATION") != "1", reason="set RENEWAL_INTEGRATION=1 and start the lab"),
]

PEBBLE = "https://localhost:14000/dir"
CHALLTESTSRV = "http://localhost:8055"
VAULT = ("http://localhost:8200", "dev-root-token")
HUMAN = Actor("user", "alice", role="operator")


@pytest.fixture()
def lab(monkeypatch):
    monkeypatch.setattr(settings, "ACME_VERIFY_TLS", False)  # Pebble's API certificate is self-signed
    store = VaultKvStore(addr=VAULT[0], token=VAULT[1])
    executor = AcmeExecutor(store=store, solver=ChallTestSrvSolver(CHALLTESTSRV), directory_url=PEBBLE,
                            email="ops@example.com")
    return executor, store


@pytest.fixture()
def db_and_cert():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    domain = f"shop-{uuid.uuid4().hex[:8]}.certops-lab.com"
    cert = models.Certificate(common_name=domain, expected_hostname=domain, san_list=json.dumps([domain]),
                              renewal_provider="letsencrypt", fingerprint_sha256="cd" * 32, source_ref=f"{domain}:443")
    db.add(cert)
    db.commit()
    yield db, cert, domain
    db.close()


def read_vault(domain: str) -> dict:
    client = hvac.Client(url=VAULT[0], token=VAULT[1])
    return client.secrets.kv.v2.read_secret_version(path=f"certops/{domain}", mount_point="secret")["data"]["data"]


def test_plan_is_executable_against_the_lab(lab, db_and_cert):
    executor, _ = lab
    _, cert, domain = db_and_cert
    spec = executor.plan(cert)
    assert spec.executable, spec.not_executable_reason
    assert spec.params["domains"] == [domain]
    assert any("TEST CA" in w for w in spec.warnings)


def test_real_issuance_stores_key_and_chain_in_vault(lab, db_and_cert):
    executor, _ = lab
    _, cert, domain = db_and_cert
    result = executor.execute(cert, executor.plan(cert).params)
    assert result.success, result.error
    assert result.secret_ref == f"vault:secret/certops/{domain}" and result.serial_number

    secret = read_vault(domain)
    chain = x509.load_pem_x509_certificates(secret["fullchain"].encode())
    leaf = chain[0]
    assert format(leaf.serial_number, "x") == result.serial_number == secret["serial"]
    assert domain in leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    assert "Pebble" in leaf.issuer.rfc4514_string() and len(chain) >= 2
    key = serialization.load_pem_private_key(secret["private_key"].encode(), password=None)
    assert key.public_key().public_numbers() == leaf.public_key().public_numbers()  # the stored key belongs to the cert


def test_service_flow_never_leaks_the_private_key(lab, db_and_cert):
    executor, _ = lab
    db, cert, domain = db_and_cert
    svc = RenewalService(db, {"letsencrypt": executor})
    plan = svc.plan(cert, Actor("agent", "alice"))
    assert plan.executable
    svc.approve(plan.id, HUMAN)
    task = svc.execute(plan.id, Actor("agent", "alice"))
    assert task.status == models.RenewalTaskStatus.AUTO_RENEWED and task.new_serial
    db.refresh(plan)
    persisted = " ".join([task.notes or "", plan.result or "", plan.steps, plan.warnings or ""])
    assert "PRIVATE KEY" not in persisted and "BEGIN" not in persisted
    assert f"vault:secret/certops/{domain}" in persisted


class SilentSolver:
    """Presents nothing, so the CA's HTTP-01 fetch finds no token: a realistic misconfiguration."""

    def present(self, domain, token, key_authorization):
        pass

    def cleanup(self, domain, token):
        pass

    def check(self):
        return None


def test_a_failed_challenge_is_recorded_and_explained_by_rca(lab, db_and_cert):
    executor, store = lab
    db, cert, domain = db_and_cert
    executor.solver = SilentSolver()
    svc = RenewalService(db, {"letsencrypt": executor})
    plan = svc.plan(cert, HUMAN)
    svc.approve(plan.id, HUMAN)
    task = svc.execute(plan.id, HUMAN)
    assert task.status == models.RenewalTaskStatus.FAILED
    assert db.get(models.RenewalPlan, plan.id).status == models.RenewalPlanStatus.FAILED
    diagnosis = rca.diagnose(db, cert)
    assert any(e["source"] == "renewal_history" and "failed" in e["fact"] for e in diagnosis["evidence"])
    assert any("wrong content" in c["cause"] or "HTTP-01" in c["cause"] for c in diagnosis["likely_causes"]), diagnosis


def test_unreachable_vault_means_the_plan_is_refused(db_and_cert, monkeypatch):
    monkeypatch.setattr(settings, "ACME_VERIFY_TLS", False)
    _, cert, _ = db_and_cert
    executor = AcmeExecutor(store=VaultKvStore(addr="http://127.0.0.1:9", token="x"),
                            solver=ChallTestSrvSolver(CHALLTESTSRV), directory_url=PEBBLE, email="ops@example.com")
    spec = executor.plan(cert)
    assert not spec.executable and "Vault" in spec.not_executable_reason
