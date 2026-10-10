"""
Network TLS provider: connects to host:port, reads the *presented chain* (leaf first) and normalizes the leaf.
Uses pyOpenSSL because the standard library only exposes the leaf on Python < 3.13. Verification is
deliberately disabled for the handshake (we want to observe broken certificates); trust is evaluated
afterwards by the validation engine.
"""
import select
import socket
from dataclasses import dataclass
from typing import Callable, List, Optional

from cryptography import x509
from OpenSSL import SSL

from app import netguard
from app.config import settings
from app.providers.base import (
    CertificateProvider,
    DiscoveryResult,
    ProviderNotConfigured,
    TransientProviderError,
)
from app.providers.normalize import is_ip, normalize_x509


@dataclass
class TargetSpec:
    host: str
    port: int = 443
    server_name: Optional[str] = None  # SNI + hostname to validate (defaults to host)
    environment: Optional[str] = None
    target_id: Optional[str] = None  # ScanTarget row id, when persisted

    @property
    def name(self) -> str:
        return self.server_name or self.host

    @property
    def ref(self) -> str:
        base = f"{self.host}:{self.port}"
        return f"{base}#{self.server_name}" if self.server_name and self.server_name != self.host else base


def parse_target(text: str) -> TargetSpec:
    """'host', 'host:port' or 'host:port#server_name'."""
    server_name = None
    if "#" in text:
        text, server_name = text.split("#", 1)
    host, port = text, 443
    if text.count(":") == 1:
        host, p = text.rsplit(":", 1)
        port = int(p)
    return TargetSpec(host=host.strip(), port=port, server_name=server_name or None)


def _wait(sock: socket.socket, write: bool, timeout: float) -> None:
    r, w, _ = select.select([] if write else [sock], [sock] if write else [], [], timeout)
    if not (r or w):
        raise TimeoutError("TLS handshake timed out")


def fetch_chain(host: str, port: int, server_name: str, timeout: float) -> List[x509.Certificate]:
    """Return the chain the server presents, leaf first. Raises OSError/TimeoutError/SSL.Error on failure."""
    ip = netguard.resolve_public(host, port)  # vetted address; connect to it directly (no 2nd DNS lookup)
    sock = socket.create_connection((ip, port), timeout=timeout)
    try:
        ctx = SSL.Context(SSL.TLS_CLIENT_METHOD)
        ctx.set_verify(SSL.VERIFY_NONE, lambda *_: True)
        try:
            ctx.set_cipher_list(b"ALL:@SECLEVEL=0")  # observe legacy servers instead of failing the handshake
        except SSL.Error:
            pass
        conn = SSL.Connection(ctx, sock)
        if not is_ip(server_name):
            conn.set_tlsext_host_name(server_name.encode())
        conn.set_connect_state()
        while True:
            try:
                conn.do_handshake()
                break
            except SSL.WantReadError:
                _wait(sock, False, timeout)
            except SSL.WantWriteError:
                _wait(sock, True, timeout)
        chain = conn.get_peer_cert_chain(as_cryptography=True) or []
        try:
            conn.shutdown()
        except SSL.Error:
            pass
        return list(chain)
    finally:
        sock.close()


ChainFetcher = Callable[[str, int, str, float], List[x509.Certificate]]


class NetworkProvider(CertificateProvider):
    name = "network_scan"

    def __init__(self, targets: List[TargetSpec], fetcher: ChainFetcher = fetch_chain,
                 timeout: Optional[float] = None):
        self.targets = targets
        self.fetcher = fetcher
        self.timeout = timeout or settings.NETWORK_TIMEOUT_SECONDS
        self.target_errors: dict[str, str] = {}  # TargetSpec.ref -> error (the job runner persists these)

    def is_configured(self) -> bool:
        return bool(self.targets)

    def discover(self) -> DiscoveryResult:
        if not self.targets:
            raise ProviderNotConfigured("no scan targets configured")
        result = DiscoveryResult()
        transient = 0
        for t in self.targets:
            try:
                chain = self.fetcher(t.host, t.port, t.name, self.timeout)
                if not chain:
                    raise ValueError("server presented no certificate")
                result.certificates.append(normalize_x509(
                    chain[0], provider=self.name, source_ref=t.ref, chain=chain,
                    environment=t.environment, expected_hostname=t.name))
            except (TimeoutError, ConnectionError, socket.gaierror) as e:
                transient += 1
                self.target_errors[t.ref] = f"{type(e).__name__}: {e}"
                result.errors.append(f"{t.ref}: {type(e).__name__}: {e}")
            except (netguard.BlockedTargetError, SSL.Error, OSError, ValueError) as e:
                self.target_errors[t.ref] = f"{type(e).__name__}: {e}"
                result.errors.append(f"{t.ref}: {type(e).__name__}: {e}")
        if not result.certificates and transient == len(self.targets):
            raise TransientProviderError("; ".join(result.errors))
        return result
