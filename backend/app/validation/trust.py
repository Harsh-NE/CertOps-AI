"""Trust anchors: the Mozilla CA bundle shipped with certifi, plus an optional extra PEM bundle (e.g. the lab CA)."""
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import certifi
from cryptography import x509


@lru_cache(maxsize=8)
def load_roots(extra_pem_path: Optional[str] = None) -> List[x509.Certificate]:
    roots = list(x509.load_pem_x509_certificates(Path(certifi.where()).read_bytes()))
    if extra_pem_path:
        roots.extend(x509.load_pem_x509_certificates(Path(extra_pem_path).read_bytes()))
    return roots
