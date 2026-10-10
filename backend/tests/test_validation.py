import dataclasses
import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from app import netguard
from app.config import settings
from app.devtools import pki
from app.devtools.ocsp_responder import OcspResponder
from app.providers.normalize import normalize_x509
from app.validation.engine import validate
from app.validation.hostname import hostname_matches
from app.validation.ocsp import check_revocation


@pytest.fixture(scope="module")
def root():
    return pki.make_root()


@pytest.fixture(scope="module")
def inter(root):
    return root.issue_intermediate("Test Intermediate")


def run(root, leaf, chain=(), host="good.lab.test", online=False, **kw):
    _, cert = leaf
    n = normalize_x509(cert, provider="network_scan", source_ref="t", chain=[cert, *chain], expected_hostname=host)
    return validate(n, [root.cert], check_revocation_online=online, **kw)


def checks(report):
    return {(f.check, f.severity) for f in report.findings}


def test_healthy_chain_passes(root, inter):
    r = run(root, inter.issue_leaf("good.lab.test"), [inter.cert])
    assert r.overall == "pass" and r.chain_valid is True and r.chain_depth == 3


def test_missing_intermediate_is_reported_as_chain_failure(root, inter):
    r = run(root, inter.issue_leaf("good.lab.test"), [])
    assert r.chain_valid is False and ("chain", "error") in checks(r)
    assert "no intermediate" in r.findings[0].message


def test_untrusted_ca(root):
    other = pki.make_root("Some Other CA")
    r = run(root, other.issue_leaf("good.lab.test"), [])
    assert r.chain_valid is False


def test_self_signed_is_never_valid(root):
    r = run(root, pki.make_self_signed("good.lab.test"))
    assert r.chain_valid is False and ("self_signed", "error") in checks(r)


def test_self_signed_root_in_trust_store_is_not_flagged(root):
    n = normalize_x509(root.cert, provider="network_scan", source_ref="t", expected_hostname=None)
    r = validate(n, [root.cert], check_revocation_online=False)
    assert not r.has("self_signed")


def test_hostname_mismatch(root, inter):
    r = run(root, inter.issue_leaf("good.lab.test"), [inter.cert], host="other.lab.test")
    assert checks(r) == {("hostname_mismatch", "error")} and r.chain_valid is True


def test_expired_reports_only_expiry_not_a_chain_failure(root, inter):
    r = run(root, inter.issue_leaf("good.lab.test", days_from=-400, days_to=-10), [inter.cert])
    assert checks(r) == {("expired", "error")} and r.chain_valid is True


def test_not_yet_valid(root, inter):
    r = run(root, inter.issue_leaf("good.lab.test", days_from=5, days_to=95), [inter.cert])
    assert ("not_yet_valid", "error") in checks(r)


@pytest.mark.parametrize("days,severity", [(3, "error"), (20, "warning")])
def test_expiring_soon_severity(root, inter, days, severity):
    r = run(root, inter.issue_leaf("good.lab.test", days_to=days), [inter.cert])
    assert ("expiring", severity) in checks(r)


def test_weak_rsa_key(root, inter):
    r = run(root, inter.issue_leaf("good.lab.test", key_kind="rsa1024"), [inter.cert])
    assert ("weak_key", "error") in checks(r)


def test_ec_p256_is_fine(root, inter):
    assert run(root, inter.issue_leaf("good.lab.test", key_kind="ec256"), [inter.cert]).overall == "pass"


def test_weak_signature_hash_is_flagged(root, inter):
    """cryptography cannot sign with SHA-1, so flip the normalized field to exercise the check."""
    _, cert = inter.issue_leaf("good.lab.test")
    n = normalize_x509(cert, provider="network_scan", source_ref="t", chain=[cert, inter.cert],
                       expected_hostname="good.lab.test")
    r = validate(dataclasses.replace(n, signature_hash="sha1"), [root.cert], check_revocation_online=False)
    assert ("weak_signature", "error") in checks(r)


def test_wildcard_matches_one_label_only(root, inter):
    leaf = inter.issue_leaf("*.lab.test")
    assert run(root, leaf, [inter.cert], host="good.lab.test").overall == "pass"
    assert ("hostname_mismatch", "error") in checks(run(root, leaf, [inter.cert], host="a.b.lab.test"))
    assert ("hostname_mismatch", "error") in checks(run(root, leaf, [inter.cert], host="lab.test"))


@pytest.mark.parametrize("host,sans,ips,ok", [
    ("example.com", ["example.com"], [], True),
    ("EXAMPLE.com.", ["example.com"], [], True),
    ("a.example.com", ["*.example.com"], [], True),
    ("a.b.example.com", ["*.example.com"], [], False),
    ("example.com", ["*.example.com"], [], False),
    ("evil.com", ["*.com"], [], False),
    ("10.0.0.5", ["example.com"], ["10.0.0.5"], True),
    ("10.0.0.6", ["example.com"], ["10.0.0.5"], False),
    ("example.com", [], [], False),
])
def test_hostname_matcher(host, sans, ips, ok):
    assert hostname_matches(host, sans, ips)[0] is ok


# ---- revocation (OCSP) ------------------------------------------------------------------------------------

def _ocsp_case(root, inter, **responder_kwargs):
    resp = OcspResponder(inter, **responder_kwargs)
    key_cert = inter.issue_leaf("good.lab.test", ocsp_url=resp.url)
    return resp, key_cert


def test_ocsp_good(root, inter):
    resp, leaf = _ocsp_case(root, inter)
    try:
        r = run(root, leaf, [inter.cert], online=True)
        assert r.revoked is False and r.revocation_method == "OCSP" and r.overall == "pass"
        assert resp.requests == 1
    finally:
        resp.close()


def test_ocsp_revoked(root, inter):
    resp = OcspResponder(inter)
    key, cert = inter.issue_leaf("good.lab.test", ocsp_url=resp.url)
    resp.revoked.add(cert.serial_number)
    try:
        r = run(root, (key, cert), [inter.cert], online=True)
        assert r.revoked is True and ("revoked", "error") in checks(r)
    finally:
        resp.close()


def test_ocsp_response_signed_by_wrong_key_is_not_trusted(root, inter):
    resp, leaf = _ocsp_case(root, inter, tamper=True)
    try:
        r = run(root, leaf, [inter.cert], online=True)
        assert r.revoked is None  # never claim "not revoked" from an unverifiable response
        assert any("signature" in f.message for f in r.findings if f.check == "revocation_unchecked")
    finally:
        resp.close()


def test_ocsp_unreachable_is_unknown_not_good(root, inter):
    key, cert = inter.issue_leaf("good.lab.test", ocsp_url="http://127.0.0.1:9/ocsp")
    r = run(root, (key, cert), [inter.cert], online=True, ocsp_timeout=1)
    assert r.revoked is None and r.has("revocation_unchecked")


def test_ocsp_no_responder_url(root, inter):
    r = run(root, inter.issue_leaf("good.lab.test"), [inter.cert], online=True)
    assert r.revoked is None and "no OCSP responder URL" in r.findings[0].message


def test_ocsp_stale_response_rejected(root, inter):
    _, cert = inter.issue_leaf("good.lab.test", ocsp_url="http://x/ocsp")
    resp = OcspResponder(inter)
    try:
        far_future = dt.datetime.now(dt.UTC) + dt.timedelta(days=30)
        out = check_revocation(cert, inter.cert, now=far_future,
                               fetch=lambda url, body, t: resp.respond(body))
        assert out.status == "unavailable" and "stale" in out.reason
    finally:
        resp.close()


def test_leaf_der_roundtrip_is_stable(root, inter):
    _, cert = inter.issue_leaf("good.lab.test")
    der = cert.public_bytes(serialization.Encoding.DER)
    assert x509.load_der_x509_certificate(der) == cert


# ---- SSRF guard -------------------------------------------------------------------------------------------

def test_netguard_blocks_private_targets_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_TARGETS", False)
    for ip in ("127.0.0.1", "10.1.2.3", "192.168.0.1", "169.254.169.254", "::1"):
        with pytest.raises(netguard.BlockedTargetError):
            netguard._check_ip(ip)
    netguard._check_ip("93.184.216.34")  # public: allowed


def test_netguard_allows_private_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_TARGETS", True)
    netguard._check_ip("127.0.0.1")


def test_netguard_rejects_odd_url_schemes():
    for url in ("file:///etc/passwd", "gopher://x/", "ftp://x/"):
        with pytest.raises(netguard.BlockedTargetError):
            netguard.check_url(url)
