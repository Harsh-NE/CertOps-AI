"""
Certificate Discovery Service - Network Scanning.

Connects to a host:port over TLS, retrieves the leaf certificate (and, where
possible, the full chain), and extracts the fields the platform tracks.
Pure-Python via the standard `ssl` module + `cryptography` for parsing, so it
has no external service dependency and works the moment you install
requirements.txt.
"""
import hashlib
import json
import socket
import ssl
from datetime import datetime
from typing import Any, Dict, List

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519, rsa
from cryptography.x509.oid import ExtensionOID, NameOID

from app.timeutil import days_until, to_naive_utc


class CertScanError(Exception):
    pass


def _parse_target(target: str) -> tuple[str, int]:
    if ":" in target:
        host, port = target.rsplit(":", 1)
        return host, int(port)
    return target, 443


def fetch_leaf_certificate_der(host: str, port: int = 443, timeout: float = 5.0) -> bytes:
    """Open a TLS connection and return the peer certificate in DER form."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # we validate the chain ourselves in chain_validator
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as ssock:
            der = ssock.getpeercert(binary_form=True)
            if der is None:
                raise CertScanError(f"No certificate returned by {host}:{port}")
            return der


def parse_certificate(der_bytes: bytes) -> Dict[str, Any]:
    cert = x509.load_der_x509_certificate(der_bytes, default_backend())

    try:
        san_ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        sans: List[str] = san_ext.value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        sans = []

    def _name(name_oid, name_obj):
        try:
            return name_obj.get_attributes_for_oid(name_oid)[0].value
        except IndexError:
            return None

    pub = cert.public_key()
    key_size = getattr(pub, "key_size", None)
    if isinstance(pub, rsa.RSAPublicKey):
        key_algorithm = "RSA"
    elif isinstance(pub, ec.EllipticCurvePublicKey):
        key_algorithm = "EC"
    elif isinstance(pub, ed25519.Ed25519PublicKey | ed448.Ed448PublicKey):
        key_algorithm = "Ed25519" if isinstance(pub, ed25519.Ed25519PublicKey) else "Ed448"
    else:
        key_algorithm = type(pub).__name__

    return {
        "common_name": _name(NameOID.COMMON_NAME, cert.subject) or (sans[0] if sans else "unknown"),
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "serial_number": str(cert.serial_number),
        "signature_algorithm": cert.signature_algorithm_oid._name,
        "key_algorithm": key_algorithm,
        "key_size": key_size,
        "not_before": to_naive_utc(cert.not_valid_before_utc),
        "not_after": to_naive_utc(cert.not_valid_after_utc),
        "san_list": json.dumps(sans),
        "is_wildcard": any(n.startswith("*.") for n in sans),
        "fingerprint_sha256": hashlib.sha256(der_bytes).hexdigest(),
    }


def scan_target(target: str) -> Dict[str, Any]:
    """
    Scan a single "host:port" (or "host", defaults to 443) target and return
    a dict ready to persist as a Certificate row.
    Raises CertScanError on connection / TLS failure.
    """
    host, port = _parse_target(target)
    try:
        der = fetch_leaf_certificate_der(host, port)
    except (TimeoutError, socket.gaierror, ConnectionRefusedError, OSError) as e:
        raise CertScanError(f"Could not connect to {host}:{port} - {e}") from e
    except ssl.SSLError as e:
        raise CertScanError(f"TLS handshake failed for {host}:{port} - {e}") from e

    data = parse_certificate(der)
    data["source_ref"] = f"{host}:{port}"
    return data


def days_until_expiry(not_after: datetime) -> int:
    return days_until(not_after)
