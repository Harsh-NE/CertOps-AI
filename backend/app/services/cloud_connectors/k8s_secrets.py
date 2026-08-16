"""
Kubernetes Secrets (type: kubernetes.io/tls) discovery connector.
Also surfaces the Secret's annotations so ownership_discovery.py can map an
owning team without a separate lookup.
"""
import base64
import json
from typing import List, Dict, Any
from app.config import settings


def _load_k8s_client():
    from kubernetes import client, config
    try:
        if settings.KUBE_CONFIG_PATH:
            config.load_kube_config(config_file=settings.KUBE_CONFIG_PATH)
        else:
            config.load_incluster_config()
    except Exception:
        config.load_kube_config()  # fall back to default ~/.kube/config for local dev
    return client.CoreV1Api()


def discover_certificates(namespace: str = None) -> List[Dict[str, Any]]:
    try:
        v1 = _load_k8s_client()
    except Exception as e:
        return [{"error": f"Kubernetes client init failed: {e}"}]

    results = []
    try:
        secrets = (
            v1.list_namespaced_secret(namespace) if namespace else v1.list_secret_for_all_namespaces()
        )
        for secret in secrets.items:
            if secret.type != "kubernetes.io/tls":
                continue
            data = secret.data or {}
            cert_b64 = data.get("tls.crt")
            if not cert_b64:
                continue
            der_or_pem = base64.b64decode(cert_b64)
            try:
                from cryptography import x509
                from cryptography.hazmat.backends import default_backend
                try:
                    parsed = x509.load_pem_x509_certificate(der_or_pem, default_backend())
                except ValueError:
                    parsed = x509.load_der_x509_certificate(der_or_pem, default_backend())
                cn = parsed.subject.rfc4514_string()
                not_before, not_after = parsed.not_valid_before_utc, parsed.not_valid_after_utc
                sig_algo = parsed.signature_algorithm_oid._name
                issuer = parsed.issuer.rfc4514_string()
            except Exception:
                cn, not_before, not_after, sig_algo, issuer = secret.metadata.name, None, None, None, "unknown"

            results.append({
                "common_name": cn,
                "san_list": json.dumps([]),
                "issuer": issuer,
                "subject": cn,
                "not_before": not_before,
                "not_after": not_after,
                "signature_algorithm": sig_algo,
                "source": "kubernetes_secret",
                "source_ref": f"{secret.metadata.namespace}/{secret.metadata.name}",
                "k8s_annotations": dict(secret.metadata.annotations or {}),
                "k8s_namespace": secret.metadata.namespace,
            })
    except Exception as e:
        results.append({"error": f"Kubernetes secret discovery error: {e}"})
    return results
