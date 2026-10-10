"""
OCSP revocation check with response verification. We never report a certificate as "not revoked"
unless a signed, fresh OCSP response says GOOD; anything else is `unavailable`/`unknown` (revoked=None).
"""
import http.client
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional
from urllib.parse import urlparse

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509 import ocsp
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtendedKeyUsageOID, ExtensionOID

from app import netguard

MAX_RESPONSE_BYTES = 1_000_000


@dataclass
class OcspOutcome:
    status: str  # good | revoked | unknown | unavailable
    reason: str = ""
    url: Optional[str] = None

    @property
    def revoked(self) -> Optional[bool]:
        return {"good": False, "revoked": True}.get(self.status)


Fetcher = Callable[[str, bytes, float], bytes]


def http_fetch(url: str, body: bytes, timeout: float) -> bytes:
    """
    POST an OCSP request to a plain-http responder, connecting to the *vetted* IP (the address we checked is the
    address we use, so DNS rebinding cannot swap in an internal one). No redirects are followed, the response is
    size-capped, and https responders are refused (they are rare and would need pinned TLS handling).
    """
    u = urlparse(url)
    if u.scheme != "http" or not u.hostname:
        raise netguard.BlockedTargetError(f"only http OCSP responders are supported: {url}")
    port = u.port or 80
    ip = netguard.resolve_public(u.hostname, port)
    conn = http.client.HTTPConnection(ip, port, timeout=timeout)
    try:
        conn.request("POST", u.path or "/", body=body, headers={
            "Host": u.netloc, "Content-Type": "application/ocsp-request", "Content-Length": str(len(body))})
        resp = conn.getresponse()
        if resp.status != 200:  # includes 3xx: redirects are never followed
            raise ValueError(f"OCSP responder returned HTTP {resp.status}")
        data = resp.read(MAX_RESPONSE_BYTES + 1)
        if len(data) > MAX_RESPONSE_BYTES:
            raise ValueError("OCSP response too large")
        return data
    finally:
        conn.close()


def ocsp_urls(cert: x509.Certificate) -> List[str]:
    try:
        aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
    except x509.ExtensionNotFound:
        return []
    return [d.access_location.value for d in aia if d.access_method == AuthorityInformationAccessOID.OCSP]


def _verify_signature(resp: ocsp.OCSPResponse, issuer: x509.Certificate) -> bool:
    signer = issuer
    for c in resp.certificates:  # delegated responder certificate, issued by the CA, EKU=OCSPSigning
        try:
            c.verify_directly_issued_by(issuer)
            eku = c.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
            if ExtendedKeyUsageOID.OCSP_SIGNING in eku:
                signer = c
                break
        except Exception:
            continue
    pub = signer.public_key()
    try:
        if isinstance(pub, rsa.RSAPublicKey):
            pub.verify(resp.signature, resp.tbs_response_bytes, padding.PKCS1v15(), resp.signature_hash_algorithm)
        elif isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(resp.signature, resp.tbs_response_bytes, ec.ECDSA(resp.signature_hash_algorithm))
        else:
            return False
        return True
    except InvalidSignature:
        return False


def check_revocation(leaf: x509.Certificate, issuer: Optional[x509.Certificate], *, now: datetime,
                     fetch: Fetcher = http_fetch, timeout: float = 5.0) -> OcspOutcome:
    urls = ocsp_urls(leaf)
    if not urls:
        return OcspOutcome("unavailable", "certificate has no OCSP responder URL")
    if issuer is None:
        return OcspOutcome("unavailable", "issuer certificate not available", urls[0])
    url = urls[0]
    try:
        req = ocsp.OCSPRequestBuilder().add_certificate(leaf, issuer, hashes.SHA1()).build()
        data = fetch(url, req.public_bytes(serialization.Encoding.DER), timeout)
        resp = ocsp.load_der_ocsp_response(data)
    except Exception as e:  # network error, blocked target, malformed response
        return OcspOutcome("unavailable", f"{type(e).__name__}: {e}", url)
    if resp.response_status != ocsp.OCSPResponseStatus.SUCCESSFUL:
        return OcspOutcome("unavailable", f"responder status {resp.response_status.name}", url)
    if not _verify_signature(resp, issuer):
        return OcspOutcome("unavailable", "OCSP response signature could not be verified", url)
    if resp.this_update_utc > now or (resp.next_update_utc is not None and resp.next_update_utc < now):
        return OcspOutcome("unavailable", "OCSP response is stale or not yet valid", url)
    if resp.certificate_status == ocsp.OCSPCertStatus.REVOKED:
        reason = resp.revocation_reason.name if resp.revocation_reason else "unspecified"
        return OcspOutcome("revoked", f"revoked (reason: {reason})", url)
    if resp.certificate_status == ocsp.OCSPCertStatus.GOOD:
        return OcspOutcome("good", "responder reports certificate as good", url)
    return OcspOutcome("unknown", "responder does not know this certificate", url)
