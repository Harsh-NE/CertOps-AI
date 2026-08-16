"""
Certificate Discovery Service - Network Scanning.

Connects to a host:port over TLS, retrieves the leaf certificate (and, where
possible, the full chain), and extracts the fields the platform tracks.
Pure-Python via the standard `ssl` module + `cryptography` for parsing, so it
has no external service dependency and works the moment you install
requirements.txt.
"""
import socket
import ssl
import json
from datetime import datetime
from typing import Optional, Dict, Any, List

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import NameOID, ExtensionOID


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

    try:
        key_size = cert.public_key().key_size
    except Exception:
        key_size = None

    return {
        "common_name": _name(NameOID.COMMON_NAME, cert.subject) or (sans[0] if sans else "unknown"),
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "serial_number": str(cert.serial_number),
        "signature_algorithm": cert.signature_algorithm_oid._name,
        "key_size": key_size,
        "not_before": cert.not_valid_before_utc if hasattr(cert, "not_valid_before_utc") else cert.not_valid_before,
        "not_after": cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after,
        "san_list": json.dumps(sans),
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
    except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError) as e:
        raise CertScanError(f"Could not connect to {host}:{port} - {e}")
    except ssl.SSLError as e:
        raise CertScanError(f"TLS handshake failed for {host}:{port} - {e}")

    data = parse_certificate(der)
    data["source_ref"] = f"{host}:{port}"
    return data


def days_until_expiry(not_after: datetime) -> int:
    if not_after.tzinfo is not None:
        now = datetime.now(not_after.tzinfo)
    else:
        now = datetime.utcnow()
    return (not_after - now).days
