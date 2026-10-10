"""
Kubernetes provider: `kubernetes.io/tls` Secrets (the real certificate material) joined with cert-manager
`Certificate` objects (readiness, renewal time, failure messages). Secrets are read via the real client;
unit tests use fakes with the same attribute shapes, and a kind cluster can be used for integration runs.
"""
import base64
import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import settings
from app.providers.base import (
    CertificateProvider,
    DiscoveryResult,
    NormalizedCertificate,
    ProviderAuthError,
    ProviderError,
    TransientProviderError,
)
from app.providers.normalize import environment_from, load_chain, normalize_x509
from app.timeutil import ensure_utc

CM_GROUP, CM_VERSION, CM_PLURAL = "cert-manager.io", "v1", "certificates"


def _default_clients():
    from kubernetes import client, config
    if settings.KUBE_CONFIG_PATH:
        config.load_kube_config(config_file=settings.KUBE_CONFIG_PATH)
    elif os.environ.get("KUBERNETES_SERVICE_HOST"):
        config.load_incluster_config()
    else:
        config.load_kube_config()
    return client.CoreV1Api(), client.CustomObjectsApi()


def _kubeconfig_available() -> bool:
    return bool(settings.KUBE_CONFIG_PATH or os.environ.get("KUBERNETES_SERVICE_HOST")
                or Path.home().joinpath(".kube", "config").exists())


def map_k8s_error(e: Exception) -> ProviderError:
    status = getattr(e, "status", None)
    if status in (401, 403):
        return ProviderAuthError(f"Kubernetes API {status}: {getattr(e, 'reason', e)}")
    if status in (429, 500, 502, 503, 504):
        return TransientProviderError(f"Kubernetes API {status}: {getattr(e, 'reason', e)}")
    name = type(e).__name__
    if name in ("MaxRetryError", "NewConnectionError", "ProtocolError", "ConnectTimeoutError", "ReadTimeoutError"):
        return TransientProviderError(f"cluster unreachable: {e}")
    return ProviderError(f"{name}: {e}")


def _cm_info(obj: dict) -> Dict[str, Any]:
    status = obj.get("status", {}) or {}
    ready = next((c for c in status.get("conditions", []) if c.get("type") == "Ready"), {})
    return {
        "name": obj["metadata"]["name"], "ready": ready.get("status") == "True",
        "reason": ready.get("reason"), "message": ready.get("message"),
        "not_after": status.get("notAfter"), "renewal_time": status.get("renewalTime"),
        "issuer_ref": (obj.get("spec", {}) or {}).get("issuerRef"),
    }


class KubernetesProvider(CertificateProvider):
    name = "kubernetes_secret"

    def __init__(self, core_v1=None, custom_api=None, namespace: Optional[str] = None):
        self._core, self._custom, self.namespace = core_v1, custom_api, namespace

    def is_configured(self) -> bool:
        return self._core is not None or _kubeconfig_available()

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        try:
            if self._core is None:
                self._core, self._custom = _default_clients()
            secrets = (self._core.list_namespaced_secret(self.namespace, field_selector="type=kubernetes.io/tls")
                       if self.namespace else
                       self._core.list_secret_for_all_namespaces(field_selector="type=kubernetes.io/tls"))
            managed = self._cert_manager_certificates()
        except ProviderError:
            raise
        except Exception as e:
            raise map_k8s_error(e) from e

        seen: set[tuple[str, str]] = set()
        for secret in secrets.items:
            ns, nm = secret.metadata.namespace, secret.metadata.name
            seen.add((ns, nm))
            try:
                raw = (secret.data or {}).get("tls.crt")
                if not raw:
                    raise ValueError("secret has no tls.crt")
                chain = load_chain(base64.b64decode(raw))
                labels = dict(secret.metadata.labels or {})
                annotations = dict(secret.metadata.annotations or {})
                extras: Dict[str, Any] = {"k8s_annotations": annotations, "k8s_namespace": ns, "labels": labels}
                if (ns, nm) in managed:
                    extras["cert_manager"] = managed[(ns, nm)]
                result.certificates.append(normalize_x509(
                    chain[0], provider=self.name, source_ref=f"{ns}/{nm}", chain=chain,
                    environment=environment_from({**labels, **annotations}), extras=extras))
            except Exception as e:
                result.errors.append(f"{ns}/{nm}: {type(e).__name__}: {e}")

        # cert-manager Certificates whose Secret does not exist (yet): report what cert-manager knows.
        for (ns, secret_name), info in managed.items():
            if (ns, secret_name) in seen:
                continue
            na = info.get("not_after")
            from datetime import datetime
            result.certificates.append(NormalizedCertificate(
                provider=self.name, source_ref=f"{ns}/{secret_name}", common_name=info.get("common_name") or secret_name,
                san_dns=info.get("dns_names", []), not_after=ensure_utc(datetime.fromisoformat(na.replace("Z", "+00:00")))
                if na else None,
                extras={"cert_manager": info, "k8s_namespace": ns, "secret_missing": True}))
        return result

    def _cert_manager_certificates(self) -> Dict[tuple[str, str], Dict[str, Any]]:
        if self._custom is None:
            return {}
        try:
            objs = (self._custom.list_namespaced_custom_object(CM_GROUP, CM_VERSION, self.namespace, CM_PLURAL)
                    if self.namespace else
                    self._custom.list_cluster_custom_object(CM_GROUP, CM_VERSION, CM_PLURAL))
        except Exception as e:
            if getattr(e, "status", None) == 404:  # cert-manager CRDs not installed
                return {}
            raise
        out: Dict[tuple[str, str], Dict[str, Any]] = {}
        for o in objs.get("items", []):
            spec = o.get("spec", {}) or {}
            info = _cm_info(o)
            info["dns_names"] = spec.get("dnsNames", [])
            info["common_name"] = spec.get("commonName") or (info["dns_names"][0] if info["dns_names"] else None)
            out[(o["metadata"]["namespace"], spec.get("secretName", o["metadata"]["name"]))] = info
        return out

