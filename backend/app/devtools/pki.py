"""
Tiny test PKI used by the test-suite, the local lab and demos: root CA -> intermediate CA -> leaves,
with control over validity window, key type, SANs and an OCSP responder URL.
NOT for production use (keys are generated in memory with no protection).
"""
import datetime as dt
import ipaddress
from dataclasses import dataclass
from typing import Optional, Sequence

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import AuthorityInformationAccessOID, ExtendedKeyUsageOID, NameOID


def _name(cn: str, org: str = "CertOps Lab") -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, org), x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def new_key(kind: str = "rsa2048"):
    if kind == "rsa2048":
        return rsa.generate_private_key(public_exponent=65537, key_size=2048)
    if kind == "rsa1024":
        return rsa.generate_private_key(public_exponent=65537, key_size=1024)
    if kind == "ec256":
        return ec.generate_private_key(ec.SECP256R1())
    raise ValueError(kind)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass
class CA:
    key: object
    cert: x509.Certificate
    name: str

    def _sign(self, builder: x509.CertificateBuilder) -> x509.Certificate:
        return builder.sign(self.key, hashes.SHA256())

    def issue_intermediate(self, name: str, days: int = 1825, days_from: int = -800) -> "CA":
        key = new_key("rsa2048")
        now = _now()
        b = (x509.CertificateBuilder().subject_name(_name(name)).issuer_name(self.cert.subject)
             .public_key(key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now + dt.timedelta(days=days_from)).not_valid_after(now + dt.timedelta(days=days))
             .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
             .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.cert.public_key()), critical=False))
        return CA(key, self._sign(b), name)

    def issue_leaf(
        self,
        cn: str,
        sans: Optional[Sequence[str]] = None,
        ips: Sequence[str] = (),
        days_from: int = -1,
        days_to: int = 90,
        key_kind: str = "rsa2048",
        ocsp_url: Optional[str] = None,
    ):
        key = new_key(key_kind)
        now = _now()
        names = [x509.DNSName(s) for s in (sans if sans is not None else [cn])]
        names += [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
        b = (x509.CertificateBuilder().subject_name(_name(cn)).issuer_name(self.cert.subject)
             .public_key(key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now + dt.timedelta(days=days_from)).not_valid_after(now + dt.timedelta(days=days_to))
             .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
             .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
             .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self.cert.public_key()), critical=False))
        if names:
            b = b.add_extension(x509.SubjectAlternativeName(names), critical=False)
        if ocsp_url:
            b = b.add_extension(x509.AuthorityInformationAccess([
                x509.AccessDescription(AuthorityInformationAccessOID.OCSP, x509.UniformResourceIdentifier(ocsp_url))]),
                critical=False)
        return key, self._sign(b)


def make_root(name: str = "CertOps Lab Root CA", days: int = 3650, days_from: int = -1000) -> CA:
    key = new_key("rsa2048")
    now = _now()
    subject = _name(name)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now + dt.timedelta(days=days_from)).not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    return CA(key, cert, name)


def make_self_signed(cn: str, days_from: int = -1, days_to: int = 90, key_kind: str = "rsa2048", sans=None):
    key = new_key(key_kind)
    now = _now()
    subject = _name(cn)
    b = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
         .public_key(key.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(now + dt.timedelta(days=days_from)).not_valid_after(now + dt.timedelta(days=days_to))
         .add_extension(x509.SubjectAlternativeName([x509.DNSName(s) for s in (sans or [cn])]), critical=False))
    return key, b.sign(key, hashes.SHA256())


def pem(*certs: x509.Certificate) -> bytes:
    return b"".join(c.public_bytes(serialization.Encoding.PEM) for c in certs)


def key_pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                             serialization.NoEncryption())
