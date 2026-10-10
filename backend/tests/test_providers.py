import base64
import datetime as dt
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError
from cryptography.hazmat.primitives import serialization
from moto import mock_aws

from app import netguard
from app.config import settings
from app.devtools import pki
from app.providers.aws_acm import AwsAcmProvider, map_aws_error
from app.providers.azure_keyvault import AzureKeyVaultProvider, map_azure_error
from app.providers.base import ProviderAuthError, TransientProviderError
from app.providers.gcp_certmanager import GcpCertManagerProvider, map_gcp_error
from app.providers.kubernetes import KubernetesProvider, map_k8s_error
from app.providers.network import NetworkProvider, TargetSpec, parse_target
from tests.conftest import serve_tls

PEM = serialization.Encoding.PEM
DER = serialization.Encoding.DER


@pytest.fixture(scope="module")
def pki_chain():
    root = pki.make_root()
    inter = root.issue_intermediate("Test Intermediate")
    return root, inter


# ---- AWS ACM (moto) ---------------------------------------------------------------------------------------

@pytest.fixture()
def aws_env(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
                 "AWS_DEFAULT_REGION": "us-east-1", "AWS_SHARED_CREDENTIALS_FILE": "NUL"}.items():
        monkeypatch.setenv(k, v)


def test_acm_discovers_issued_and_pending_certificates(aws_env, pki_chain):
    root, inter = pki_chain
    key, leaf = inter.issue_leaf("api.example.test", sans=["api.example.test", "*.example.test"])
    with mock_aws():
        acm = boto3.client("acm", region_name="us-east-1")
        arn = acm.import_certificate(
            Certificate=pki.pem(leaf), PrivateKey=pki.key_pem(key), CertificateChain=pki.pem(inter.cert, root.cert),
            Tags=[{"Key": "Environment", "Value": "Prod"}, {"Key": "team", "Value": "payments"}])["CertificateArn"]
        acm.request_certificate(DomainName="pending.example.test")
        result = AwsAcmProvider(client_factory=lambda: boto3.client("acm", region_name="us-east-1"), enabled=True).discover()
    assert not result.errors
    by_ref = {c.source_ref: c for c in result.certificates}
    assert len(by_ref) == 2
    c = by_ref[arn]
    assert c.provider == "aws_acm" and c.common_name == "api.example.test"
    assert c.environment == "prod" and c.is_wildcard
    assert c.key_algorithm == "RSA" and c.key_size == 2048 and len(c.chain_der) >= 3
    assert c.extras["tags"]["team"] == "payments"
    pending = next(v for k, v in by_ref.items() if k != arn)
    assert pending.common_name == "pending.example.test" and pending.chain_der == []


def test_acm_is_opt_in(monkeypatch):
    def boom():
        raise AssertionError("boto3 client must not be created while disabled")

    provider = AwsAcmProvider(client_factory=boom, enabled=False)
    assert provider.is_configured() is False


def test_aws_error_mapping():
    def err(code):
        return ClientError({"Error": {"Code": code, "Message": "x"}}, "ListCertificates")

    assert isinstance(map_aws_error(err("AccessDeniedException")), ProviderAuthError)
    assert isinstance(map_aws_error(err("ThrottlingException")), TransientProviderError)
    assert map_aws_error(err("ValidationException")).kind == "permanent"


def test_acm_access_denied_is_an_auth_error(aws_env):
    class Denied:
        def get_paginator(self, name):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "nope"}}, "ListCertificates")

    with pytest.raises(ProviderAuthError):
        AwsAcmProvider(client_factory=Denied, enabled=True).discover()


# ---- Kubernetes (fakes with the real client's attribute shapes) -------------------------------------------

def _secret(name, ns, cert_pem, labels=None, annotations=None, type_="kubernetes.io/tls"):
    return SimpleNamespace(
        type=type_, data={"tls.crt": base64.b64encode(cert_pem).decode(), "tls.key": "eA=="},
        metadata=SimpleNamespace(name=name, namespace=ns, labels=labels or {}, annotations=annotations or {}))


class FakeCore:
    def __init__(self, secrets):
        self.secrets, self.selectors = secrets, []

    def list_secret_for_all_namespaces(self, field_selector=None):
        self.selectors.append(field_selector)
        return SimpleNamespace(items=self.secrets)


class FakeCustom:
    def __init__(self, items=None, status=None):
        self.items, self.status = items or [], status

    def list_cluster_custom_object(self, group, version, plural):
        if self.status:
            raise SimpleNamespace_error(self.status)
        return {"items": self.items}


def SimpleNamespace_error(status):
    class ApiException(Exception):
        pass

    e = ApiException("boom")
    e.status = status
    return e


def test_kubernetes_tls_secrets_and_cert_manager_join(pki_chain):
    root, inter = pki_chain
    _, leaf = inter.issue_leaf("shop.example.test")
    _, leaf2 = inter.issue_leaf("pending.example.test")
    core = FakeCore([
        _secret("shop-tls", "prod", pki.pem(leaf, inter.cert), labels={"environment": "prod"},
                annotations={"cert-owner/team": "storefront"}),
        _secret("broken", "dev", b"not a certificate"),
    ])
    custom = FakeCustom([
        {"metadata": {"name": "shop", "namespace": "prod"},
         "spec": {"secretName": "shop-tls", "dnsNames": ["shop.example.test"], "issuerRef": {"name": "letsencrypt"}},
         "status": {"conditions": [{"type": "Ready", "status": "False", "reason": "Failed",
                                    "message": "challenge failed"}], "renewalTime": "2030-01-01T00:00:00Z"}},
        {"metadata": {"name": "pending", "namespace": "prod"},
         "spec": {"secretName": "pending-tls", "dnsNames": ["pending.example.test"]},
         "status": {"notAfter": "2030-06-01T00:00:00Z"}},
    ])
    result = KubernetesProvider(core_v1=core, custom_api=custom).discover()
    assert core.selectors == ["type=kubernetes.io/tls"]
    by_ref = {c.source_ref: c for c in result.certificates}
    shop = by_ref["prod/shop-tls"]
    assert shop.environment == "prod" and len(shop.chain_der) == 2
    assert shop.extras["k8s_annotations"]["cert-owner/team"] == "storefront"
    assert shop.extras["cert_manager"]["ready"] is False
    assert shop.extras["cert_manager"]["message"] == "challenge failed"
    pending = by_ref["prod/pending-tls"]
    assert pending.extras["secret_missing"] and pending.not_after.year == 2030
    assert any("dev/broken" in e for e in result.errors)  # one bad secret does not abort the run


def test_kubernetes_without_cert_manager_crds_is_fine(pki_chain):
    _, inter = pki_chain
    _, leaf = inter.issue_leaf("a.example.test")
    result = KubernetesProvider(core_v1=FakeCore([_secret("a", "ns", pki.pem(leaf))]),
                                custom_api=FakeCustom(status=404)).discover()
    assert len(result.certificates) == 1 and not result.errors


def test_kubernetes_error_mapping():
    assert isinstance(map_k8s_error(SimpleNamespace_error(403)), ProviderAuthError)
    assert isinstance(map_k8s_error(SimpleNamespace_error(503)), TransientProviderError)


# ---- Azure Key Vault + GCP (SIMULATED: fakes shaped like the SDK responses) -------------------------------

def test_azure_key_vault_with_fake_client(pki_chain):
    _, inter = pki_chain
    _, leaf = inter.issue_leaf("kv.example.test")

    class FakeClient:
        def list_properties_of_certificates(self):
            return [SimpleNamespace(name="kv-cert", tags={"env": "Staging"}, enabled=True),
                    SimpleNamespace(name="bad", tags={}, enabled=True)]

        def get_certificate(self, name):
            if name == "bad":
                raise RuntimeError("cannot fetch")
            return SimpleNamespace(cer=leaf.public_bytes(DER), policy=SimpleNamespace(issuer_name="Self"))

    result = AzureKeyVaultProvider(client=FakeClient(), vault_url="https://kv.vault.azure.net/").discover()
    c = result.certificates[0]
    assert c.provider == "azure_key_vault" and c.environment == "staging"
    assert c.source_ref == "https://kv.vault.azure.net/certificates/kv-cert"
    assert c.extras["vault_issuer_name"] == "Self" and len(result.errors) == 1


def test_azure_error_mapping():
    class ClientAuthenticationError(Exception):
        pass

    class ServiceRequestError(Exception):
        pass

    assert isinstance(map_azure_error(ClientAuthenticationError("x")), ProviderAuthError)
    assert isinstance(map_azure_error(ServiceRequestError("x")), TransientProviderError)


def test_gcp_certificate_manager_with_fake_client(pki_chain):
    _, inter = pki_chain
    _, leaf = inter.issue_leaf("gcp.example.test")
    exp = dt.datetime(2031, 1, 1, tzinfo=dt.UTC)

    class FakeClient:
        def list_certificates(self, parent):
            assert parent == "projects/p1/locations/-"
            return [
                SimpleNamespace(name="projects/p1/locations/global/certificates/own", labels={"env": "prod"},
                                managed=None, self_managed=SimpleNamespace(pem_certificate=pki.pem(leaf).decode()),
                                san_dnsnames=[], expire_time=None),
                SimpleNamespace(name="projects/p1/locations/global/certificates/google", labels={},
                                managed=SimpleNamespace(state="ACTIVE"), self_managed=None,
                                san_dnsnames=["*.managed.example.test"], expire_time=exp),
            ]

    result = GcpCertManagerProvider(client=FakeClient(), project="p1").discover()
    own, managed = result.certificates
    assert own.common_name == "gcp.example.test" and own.environment == "prod" and own.key_size == 2048
    assert managed.common_name == "*.managed.example.test" and managed.is_wildcard and managed.not_after == exp
    assert managed.chain_der == []  # Google-managed certs expose no PEM: metadata only


def test_gcp_error_mapping():
    class PermissionDenied(Exception):
        pass

    class ServiceUnavailable(Exception):
        pass

    assert isinstance(map_gcp_error(PermissionDenied("x")), ProviderAuthError)
    assert isinstance(map_gcp_error(ServiceUnavailable("x")), TransientProviderError)


# ---- Network provider against real local TLS servers ------------------------------------------------------

def test_network_provider_reads_presented_chain_and_sni(pki_chain):
    root, inter = pki_chain
    key, leaf = inter.issue_leaf("good.lab.test")
    port = serve_tls(key, leaf, inter.cert)
    target = TargetSpec(host="127.0.0.1", port=port, server_name="good.lab.test", environment="Prod")
    result = NetworkProvider([target]).discover()
    c = result.certificates[0]
    assert c.common_name == "good.lab.test" and len(c.chain_der) == 2
    assert c.expected_hostname == "good.lab.test" and c.source_ref == f"127.0.0.1:{port}#good.lab.test"


def test_network_provider_observes_weak_legacy_servers(pki_chain):
    _, inter = pki_chain
    key, leaf = inter.issue_leaf("legacy.lab.test", key_kind="rsa1024")
    port = serve_tls(key, leaf, inter.cert, weak=True)
    c = NetworkProvider([TargetSpec("127.0.0.1", port, "legacy.lab.test")]).discover().certificates[0]
    assert c.key_size == 1024


def test_network_provider_partial_failure_keeps_good_targets(pki_chain):
    _, inter = pki_chain
    key, leaf = inter.issue_leaf("good.lab.test")
    port = serve_tls(key, leaf, inter.cert)
    provider = NetworkProvider([TargetSpec("127.0.0.1", port, "good.lab.test"), TargetSpec("127.0.0.1", 1)],
                               timeout=2)
    result = provider.discover()
    assert len(result.certificates) == 1 and len(result.errors) == 1
    assert "127.0.0.1:1" in provider.target_errors


def test_network_provider_all_unreachable_is_transient():
    with pytest.raises(TransientProviderError):
        NetworkProvider([TargetSpec("127.0.0.1", 1)], timeout=2).discover()


def test_network_provider_blocks_private_targets_when_guard_is_on(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_TARGETS", False)
    provider = NetworkProvider([TargetSpec("127.0.0.1", 443)])
    result = provider.discover()
    assert not result.certificates and "not a public address" in result.errors[0]
    assert netguard.BlockedTargetError.__name__ in result.errors[0]


def test_parse_target():
    t = parse_target("10.0.0.5:8443#app.internal")
    assert (t.host, t.port, t.server_name, t.name) == ("10.0.0.5", 8443, "app.internal", "app.internal")
    assert parse_target("example.com").port == 443
