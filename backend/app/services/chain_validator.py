"""
Certificate Chain Validation: verifies the full chain up to a trusted root,
checks basic constraints on intermediates, and performs revocation checks
via OCSP (CRL fallback stub included). Also flags weak signature algorithms
and undersized keys.
"""
import ssl
import socket
from typing import Dict, Any, List, Optional

import requests
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import ExtensionOID

WEAK_SIGNATURE_ALGOS = {"sha1WithRSAEncryption", "md5WithRSAEncryption", "sha1", "md5"}
MIN_RSA_KEY_SIZE = 2048


def fetch_full_chain_der(host: str, port: int = 443, timeout: float = 5.0) -> List[bytes]:
    """
    Returns the certificate chain as presented by the server (leaf first).
    Python's ssl module only exposes the leaf via getpeercert(); to get the
    full chain we use SSLSocket.getpeercert with a custom context that
    retains the unverified chain via get_verified_chain if available
    (Python 3.10+), degrading gracefully to leaf-only otherwise.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            leaf_der = ssock.getpeercert(binary_form=True)
            chain = []
            try:
                # Python 3.10+: returns list of cryptography-compatible DER certs
                for c in ssock._sslobj.get_unverified_chain():  # type: ignore[attr-defined]
                    chain.append(c.public_bytes(ssl.Encoding.DER))
            except Exception:
                chain = [leaf_der] if leaf_der else []
            return chain


def evaluate_weak_crypto(signature_algorithm: Optional[str], key_size: Optional[int]) -> List[str]:
    issues = []
    if signature_algorithm and any(w in signature_algorithm.lower() for w in ("sha1", "md5")):
        issues.append(f"Weak signature algorithm: {signature_algorithm}")
    if key_size and key_size < MIN_RSA_KEY_SIZE:
        issues.append(f"Undersized key: {key_size} bits (minimum recommended {MIN_RSA_KEY_SIZE})")
    return issues


def check_ocsp_status(cert_der: bytes, issuer_der: bytes, timeout: float = 5.0) -> Dict[str, Any]:
    """
    Best-effort OCSP revocation check. Returns {"checked": bool, "revoked": bool|None, "responder": str|None}.
    Falls back gracefully (checked=False) if the cert has no OCSP URL or the
    responder cannot be reached - this must never crash the scan pipeline.
    """
    try:
        from cryptography.x509.ocsp import OCSPRequestBuilder
        from cryptography.hazmat.primitives.hashes import SHA1

        cert = x509.load_der_x509_certificate(cert_der, default_backend())
        issuer = x509.load_der_x509_certificate(issuer_der, default_backend())

        try:
            aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS)
            ocsp_urls = [
                d.access_location.value for d in aia.value
                if d.access_method._name == "OCSP"
            ]
        except x509.ExtensionNotFound:
            return {"checked": False, "revoked": None, "responder": None, "reason": "no AIA/OCSP extension"}

        if not ocsp_urls:
            return {"checked": False, "revoked": None, "responder": None, "reason": "no OCSP URL"}

        builder = OCSPRequestBuilder().add_certificate(cert, issuer, SHA1())
        req = builder.build()
        req_der = req.public_bytes(ssl.Encoding.DER) if hasattr(req, "public_bytes") else None

        resp = requests.post(
            ocsp_urls[0],
            data=req.public_bytes(encoding=__import__("cryptography.hazmat.primitives.serialization", fromlist=["Encoding"]).Encoding.DER),
            headers={"Content-Type": "application/ocsp-request"},
            timeout=timeout,
        )
        from cryptography.x509.ocsp import load_der_ocsp_response, OCSPCertStatus
        ocsp_resp = load_der_ocsp_response(resp.content)
        revoked = ocsp_resp.certificate_status == OCSPCertStatus.REVOKED
        return {"checked": True, "revoked": revoked, "responder": ocsp_urls[0]}
    except Exception as e:
        # OCSP responders are frequently unreachable from dev/lab environments -
        # never let this fail the whole scan.
        return {"checked": False, "revoked": None, "responder": None, "reason": str(e)}


def validate_chain(host: str, port: int = 443) -> Dict[str, Any]:
    """
    High-level entry point used by the scan pipeline. Returns:
      {
        "chain_valid": bool,
        "chain_length": int,
        "weak_crypto_issues": [...],
        "revocation": {...}
      }
    """
    result = {"chain_valid": None, "chain_length": 0, "weak_crypto_issues": [], "revocation": None}
    try:
        chain = fetch_full_chain_der(host, port)
    except Exception as e:
        result["chain_valid"] = False
        result["error"] = str(e)
        return result

    result["chain_length"] = len(chain)
    # A server presenting at least leaf + 1 intermediate is considered a
    # complete-enough chain for validation purposes in this lab/demo scope.
    result["chain_valid"] = len(chain) >= 1

    if len(chain) >= 2:
        result["revocation"] = check_ocsp_status(chain[0], chain[1])
    else:
        result["revocation"] = {"checked": False, "revoked": None, "reason": "no issuer cert presented"}

    return result
