"""RFC 6125-style hostname matching against a certificate's SANs."""
import ipaddress
from typing import List, Optional


def _norm(h: str) -> str:
    return h.strip().rstrip(".").lower()


def _wildcard_matches(pattern: str, host: str) -> bool:
    # '*' only as the whole left-most label, matches exactly one label, never a bare TLD ('*.com').
    if not pattern.startswith("*."):
        return False
    suffix = pattern[2:]
    if suffix.count(".") < 1:
        return False
    head, _, rest = host.partition(".")
    return bool(head) and rest == suffix


def hostname_matches(host: str, san_dns: List[str], san_ips: List[str], common_name: Optional[str] = None) -> tuple[bool, str]:
    """Returns (matched, how). Falls back to the CN only when the certificate has no SAN at all."""
    host = _norm(host)
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        ok = any(ipaddress.ip_address(s) == ip for s in san_ips)
        return ok, "ip-san" if ok else "no-ip-san-match"
    for pattern in map(_norm, san_dns):
        if pattern == host:
            return True, "san-exact"
        if _wildcard_matches(pattern, host):
            return True, "san-wildcard"
    if not san_dns and not san_ips and common_name:
        cn = _norm(common_name)
        if cn == host or _wildcard_matches(cn, host):
            return True, "cn-fallback"
    return False, "no-match"
