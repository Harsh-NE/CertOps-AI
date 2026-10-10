"""Secret stores for issued private keys and certificates. Keys never go to the database, logs or API responses."""
from typing import Dict, Optional

from app.config import settings
from app.renewal.base import SecretStoreError


class VaultKvStore:
    """HashiCorp Vault KV v2 (dev mode in the local lab). Path: <mount>/<prefix>/<name>."""

    def __init__(self, addr: Optional[str] = None, token: Optional[str] = None, mount: Optional[str] = None,
                 prefix: Optional[str] = None, client=None):
        self.addr, self.token = addr or settings.VAULT_ADDR, token or settings.VAULT_TOKEN
        self.mount, self.prefix = mount or settings.VAULT_KV_MOUNT, prefix or settings.VAULT_PATH_PREFIX
        self._client = client

    @property
    def configured(self) -> bool:
        return self._client is not None or bool(self.addr and self.token)

    def _vault(self):
        if self._client is None:
            import hvac
            self._client = hvac.Client(url=self.addr, token=self.token, timeout=10)
        return self._client

    def put(self, name: str, data: Dict[str, str]) -> str:
        path = f"{self.prefix}/{name}".strip("/")
        try:
            client = self._vault()
            if not client.is_authenticated():
                raise SecretStoreError("Vault authentication failed")
            client.secrets.kv.v2.create_or_update_secret(path=path, secret=data, mount_point=self.mount)
        except SecretStoreError:
            raise
        except Exception as e:
            raise SecretStoreError(f"Vault write failed: {type(e).__name__}: {e}") from e
        return f"vault:{self.mount}/{path}"

    def check(self) -> None:
        """Cheap readiness probe used by dry runs (no write)."""
        try:
            if not self._vault().is_authenticated():
                raise SecretStoreError("Vault authentication failed")
        except SecretStoreError:
            raise
        except Exception as e:
            raise SecretStoreError(f"Vault unreachable: {type(e).__name__}: {e}") from e


class MemorySecretStore:
    """For tests only."""

    configured = True

    def __init__(self):
        self.items: Dict[str, Dict[str, str]] = {}

    def put(self, name: str, data: Dict[str, str]) -> str:
        self.items[name] = dict(data)
        return f"memory:{name}"

    def check(self) -> None:
        return None
