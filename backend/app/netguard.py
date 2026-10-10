"""
SSRF guard. Scan targets and OCSP/AIA URLs are attacker-influenced (users submit hosts; certificates
carry URLs), so by default we refuse to connect to loopback, private, link-local and metadata addresses.
Set ALLOW_PRIVATE_TARGETS=true for lab/dev use.

`resolve_public()` returns the vetted IP so callers connect to *that* address (no second DNS lookup),
which closes the DNS-rebinding window for direct socket connections.
"""
import ipaddress
import socket
from urllib.parse import urlparse

from app.config import settings


class BlockedTargetError(ValueError):
    pass


def _check_ip(ip: str) -> None:
    addr = ipaddress.ip_address(ip)
    if settings.ALLOW_PRIVATE_TARGETS:
        return
    if not addr.is_global or addr.is_multicast:
        raise BlockedTargetError(f"{ip} is not a public address (set ALLOW_PRIVATE_TARGETS=true to allow)")


def resolve_public(host: str, port: int) -> str:
    """Resolve host and return one vetted IP; raises BlockedTargetError if any answer is non-public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ConnectionError(f"cannot resolve {host}: {e}") from e
    ips = [i[4][0] for i in infos]
    for ip in ips:
        _check_ip(ip)
    return ips[0]


def check_url(url: str) -> None:
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise BlockedTargetError(f"unsupported URL: {url}")
    resolve_public(u.hostname, u.port or (443 if u.scheme == "https" else 80))
