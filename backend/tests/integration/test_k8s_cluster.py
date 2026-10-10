"""
Real-cluster test for the Kubernetes provider (kind, k3s, Docker Desktop Kubernetes, ...).

    KUBE_INTEGRATION=1 pytest tests/integration -m integration

Creates a throwaway namespace with a kubernetes.io/tls Secret, runs the real provider against the cluster,
and deletes the namespace afterwards. Skipped unless KUBE_INTEGRATION=1.
"""
import base64
import os
import uuid

import pytest

from app.devtools import pki
from app.providers.kubernetes import KubernetesProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("KUBE_INTEGRATION") != "1", reason="set KUBE_INTEGRATION=1 to run"),
]


@pytest.fixture()
def cluster():
    from kubernetes import client, config
    config.load_kube_config()
    core = client.CoreV1Api()
    ns = f"certops-it-{uuid.uuid4().hex[:8]}"
    core.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=ns)))
    try:
        yield core, ns
    finally:
        core.delete_namespace(ns)


def test_provider_discovers_a_real_tls_secret(cluster):
    from kubernetes import client
    core, ns = cluster
    inter = pki.make_root().issue_intermediate("IT Intermediate")
    key, leaf = inter.issue_leaf("it.example.test")
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name="it-tls", namespace=ns, labels={"environment": "staging"},
                                     annotations={"cert-owner/team": "platform"}),
        type="kubernetes.io/tls",
        data={"tls.crt": base64.b64encode(pki.pem(leaf, inter.cert)).decode(),
              "tls.key": base64.b64encode(pki.key_pem(key)).decode()})
    core.create_namespaced_secret(ns, secret)
    core.create_namespaced_secret(ns, client.V1Secret(metadata=client.V1ObjectMeta(name="opaque"), string_data={"a": "b"}))

    result = KubernetesProvider(namespace=ns).discover()

    assert not result.errors
    assert [c.source_ref for c in result.certificates] == [f"{ns}/it-tls"]  # Opaque secrets are ignored
    c = result.certificates[0]
    assert c.common_name == "it.example.test" and c.environment == "staging" and len(c.chain_der) == 2
    assert c.extras["k8s_annotations"]["cert-owner/team"] == "platform"
