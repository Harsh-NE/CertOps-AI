from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app import models
from app.providers.aws_acm import AwsAcmProvider
from app.providers.azure_keyvault import AzureKeyVaultProvider
from app.providers.base import CertificateProvider
from app.providers.gcp_certmanager import GcpCertManagerProvider
from app.providers.kubernetes import KubernetesProvider
from app.providers.network import NetworkProvider, TargetSpec

PROVIDER_NAMES = ["network_scan", "aws_acm", "azure_key_vault", "gcp_cert_manager", "kubernetes_secret"]


def target_specs(db: Session, only_ids: Optional[List[str]] = None) -> List[TargetSpec]:
    q = db.query(models.ScanTarget).filter(models.ScanTarget.enabled.is_(True))
    if only_ids is not None:
        q = q.filter(models.ScanTarget.id.in_(only_ids))
    return [TargetSpec(host=t.host, port=t.port, server_name=t.server_name, environment=t.environment, target_id=t.id)
            for t in q.all()]


def build_providers(db: Session) -> Dict[str, CertificateProvider]:
    return {
        "network_scan": NetworkProvider(target_specs(db)),
        "aws_acm": AwsAcmProvider(),
        "azure_key_vault": AzureKeyVaultProvider(),
        "gcp_cert_manager": GcpCertManagerProvider(),
        "kubernetes_secret": KubernetesProvider(),
    }
