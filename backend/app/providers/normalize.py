import hashlib
import ipaddress
from typing import Iterable, List, Optional

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import dsa, ec, ed448, ed25519, rsa
from cryptography.x509.oid import ExtensionOID, NameOID

from app.providers.base import NormalizedCertificate
from app.timeutil import ensure_utc

ENV_KEYS = ("environment", "env", "Environment", "Env", "app.kubernetes.io/environment", "certops/environment")


def environment_from(tags: Optional[dict]) -> Optional[str]:
    for k in ENV_KEYS:
        v = (tags or {}).get(k)
        if v:
            return str(v).lower()
    return None


def load_chain(data: bytes) -> List[x509.Certificate]:
    """Parse PEM (possibly several certs) or a single DER certificate."""
    if b"-----BEGIN CERTIFICATE-----" in data:
        return x509.load_pem_x509_certificates(data)
    return [x509.load_der_x509_certificate(data)]


def key_info(cert: x509.Certificate) -> tuple[str, Optional[int]]:
    pub = cert.public_key()
    if isinstance(pub, rsa.RSAPublicKey):
        return "RSA", pub.key_size
    if isinstance(pub, ec.EllipticCurvePublicKey):
        return "EC", pub.key_size
    if isinstance(pub, ed25519.Ed25519PublicKey):
        return "Ed25519", 256
    if isinstance(pub, ed448.Ed448PublicKey):
        return "Ed448", 456
    if isinstance(pub, dsa.DSAPublicKey):
        return "DSA", pub.key_size
    return type(pub).__name__, getattr(pub, "key_size", None)


def _cn(name: x509.Name) -> Optional[str]:
    attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return attrs[0].value if attrs else None


def normalize_x509(
    leaf: x509.Certificate,
    *,
    provider: str,
    source_ref: str,
    chain: Optional[Iterable[x509.Certificate]] = None,
    environment: Optional[str] = None,
    expected_hostname: Optional[str] = None,
    extras: Optional[dict] = None,
) -> NormalizedCertificate:
    try:
        san = leaf.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        san_dns = san.get_values_for_type(x509.DNSName)
        san_ips = [str(i) for i in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        san_dns, san_ips = [], []
    der = leaf.public_bytes(serialization.Encoding.DER)
    algo, size = key_info(leaf)
    sig_hash = leaf.signature_hash_algorithm.name if leaf.signature_hash_algorithm else None
    chain_der = [c.public_bytes(serialization.Encoding.DER) for c in (chain or [leaf])]
    if not chain_der or chain_der[0] != der:
        chain_der = [der] + [c for c in chain_der if c != der]
    return NormalizedCertificate(
        provider=provider,
        source_ref=source_ref,
        common_name=_cn(leaf.subject) or (san_dns[0] if san_dns else "unknown"),
        subject=leaf.subject.rfc4514_string(),
        issuer=leaf.issuer.rfc4514_string(),
        serial_number=format(leaf.serial_number, "x"),
        fingerprint_sha256=hashlib.sha256(der).hexdigest(),
        not_before=ensure_utc(leaf.not_valid_before_utc),
        not_after=ensure_utc(leaf.not_valid_after_utc),
        signature_algorithm=getattr(leaf.signature_algorithm_oid, "_name", leaf.signature_algorithm_oid.dotted_string),
        signature_hash=sig_hash,
        key_algorithm=algo,
        key_size=size,
        san_dns=san_dns,
        san_ips=san_ips,
        is_wildcard=any(n.startswith("*.") for n in san_dns),
        environment=environment,
        expected_hostname=expected_hostname,
        chain_der=chain_der,
        extras=extras or {},
    )


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
