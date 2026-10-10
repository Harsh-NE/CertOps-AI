from typing import Any, Dict

from app import models
from app.renewal.base import ExecResult, PlanSpec, RenewalExecutor


class AwsAcmExecutor(RenewalExecutor):
    """ACM renews eligible certificates itself and has no 'renew now' API, so this can only explain that."""

    name = "aws_acm"

    def plan(self, cert: models.Certificate) -> PlanSpec:
        reason = "ACM renews eligible (DNS-validated, in-use) certificates automatically; it cannot be forced."
        return PlanSpec(params={"arn": cert.source_ref},
                        steps=["Check the ACM renewal eligibility and status of the certificate (read-only)."],
                        warnings=[reason], executable=False, not_executable_reason=reason)

    def execute(self, cert: models.Certificate, params: Dict[str, Any]) -> ExecResult:
        return ExecResult(success=False, action_taken=False, error="ACM renewal cannot be triggered by the platform")
