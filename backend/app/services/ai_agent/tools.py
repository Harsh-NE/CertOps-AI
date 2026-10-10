"""
Tools the AI agent can call. Each tool is a thin, read-mostly wrapper around
the platform's own services/DB so the agent's answers are always grounded in
real data - never hallucinated. Mutating tools (create_renewal_ticket_tool)
are intentionally narrow and log a RenewalTask row so agent-triggered actions
are auditable exactly like scheduler- or human-triggered ones.
"""
import json

from langchain.tools import tool
from sqlalchemy.orm import Session

from app import models
from app.timeutil import days_until as days_until_expiry
from app.timeutil import utcnow


def build_tools(db: Session):
    """Returns a list of LangChain tools bound to this request's DB session."""

    @tool
    def list_expiring_certificates(days: int = 30) -> str:
        """List certificates expiring within the given number of days. Returns JSON."""
        certs = db.query(models.Certificate).filter(models.Certificate.not_after.is_not(None)).all()
        out = []
        for c in certs:
            d = days_until_expiry(c.not_after)
            if d <= days:
                out.append({
                    "common_name": c.common_name, "days_to_expiry": d,
                    "owner": c.owner.name if c.owner else None,
                    "risk_tier": c.risk_tier.value if c.risk_tier else None,
                    "source": c.source.value if c.source else None,
                })
        return json.dumps(out, default=str)

    @tool
    def get_certificate_details(common_name: str) -> str:
        """Get full details for a certificate by its common name (exact or partial match)."""
        certs = db.query(models.Certificate).filter(
            models.Certificate.common_name.ilike(f"%{common_name}%")
        ).all()
        if not certs:
            return json.dumps({"error": f"No certificate found matching '{common_name}'"})
        out = []
        for c in certs:
            out.append({
                "common_name": c.common_name, "issuer": c.issuer, "status": c.status.value if c.status else None,
                "not_after": str(c.not_after), "chain_valid": c.chain_valid, "revoked": c.revoked,
                "signature_algorithm": c.signature_algorithm, "key_size": c.key_size,
                "risk_score": c.risk_score, "risk_tier": c.risk_tier.value if c.risk_tier else None,
                "predicted_outage_impact": c.predicted_outage_impact,
                "owner": c.owner.name if c.owner else None,
                "auto_renew_enabled": c.auto_renew_enabled, "renewal_provider": c.renewal_provider,
                "source": c.source.value if c.source else None, "source_ref": c.source_ref,
            })
        return json.dumps(out, default=str)

    @tool
    def explain_validation_failure(common_name: str) -> str:
        """
        Explain WHY a certificate is failing validation (chain invalid, revoked,
        expired, or weak crypto) - a root-cause-analysis tool.
        """
        cert = db.query(models.Certificate).filter(
            models.Certificate.common_name.ilike(f"%{common_name}%")
        ).first()
        if not cert:
            return json.dumps({"error": f"No certificate found matching '{common_name}'"})

        reasons = []
        if cert.chain_valid is False:
            reasons.append("Certificate chain is incomplete or does not lead to a trusted root.")
        if cert.revoked:
            reasons.append(f"Certificate has been revoked (checked via {cert.revocation_check_method or 'OCSP'}).")
        if cert.not_after and cert.not_after < utcnow():
            reasons.append(f"Certificate expired on {cert.not_after}.")
        if cert.signature_algorithm and any(w in cert.signature_algorithm.lower() for w in ("sha1", "md5")):
            reasons.append(f"Weak signature algorithm in use: {cert.signature_algorithm}.")
        if cert.key_size and cert.key_size < 2048:
            reasons.append(f"Key size too small: {cert.key_size} bits.")
        if not reasons:
            reasons.append("No validation failures detected - certificate appears healthy.")
        return json.dumps({"common_name": cert.common_name, "reasons": reasons})

    @tool
    def query_certificates_by_issuer(issuer_substring: str) -> str:
        """Find certificates issued by a CA whose name contains the given substring, e.g. 'DigiCert'."""
        certs = db.query(models.Certificate).filter(
            models.Certificate.issuer.ilike(f"%{issuer_substring}%")
        ).all()
        out = [{
            "common_name": c.common_name, "issuer": c.issuer,
            "not_after": str(c.not_after),
            "days_to_expiry": days_until_expiry(c.not_after) if c.not_after else None,
        } for c in certs]
        return json.dumps(out, default=str)

    @tool
    def recommend_remediation(common_name: str) -> str:
        """Generate a step-by-step remediation/renewal checklist for a certificate."""
        cert = db.query(models.Certificate).filter(
            models.Certificate.common_name.ilike(f"%{common_name}%")
        ).first()
        if not cert:
            return json.dumps({"error": f"No certificate found matching '{common_name}'"})

        steps = []
        if cert.not_after and days_until_expiry(cert.not_after) <= 30:
            steps.append("Initiate renewal now - within the 30-day expiry window.")
        if not cert.owner:
            steps.append("Resolve ownership via git CODEOWNERS / k8s annotations before proceeding, "
                          "so the right team is notified and accountable.")
        if cert.signature_algorithm and any(w in cert.signature_algorithm.lower() for w in ("sha1", "md5")):
            steps.append("Reissue with SHA-256 or stronger - current signature algorithm is deprecated.")
        if cert.key_size and cert.key_size < 2048:
            steps.append("Reissue with at least a 2048-bit RSA key (or move to ECDSA P-256).")
        if not cert.auto_renew_enabled:
            steps.append("Enable auto-renewal via Let's Encrypt/Vault/ACM to prevent recurrence.")
        if not steps:
            steps.append("No action required - certificate is healthy and within policy.")
        return json.dumps({"common_name": cert.common_name, "remediation_steps": steps})

    @tool
    def create_renewal_ticket_tool(common_name: str, notes: str = "") -> str:
        """Create a renewal task (and Jira ticket if configured) for a certificate, triggered by the AI agent."""
        from app.services.notifications.jira import create_renewal_ticket
        cert = db.query(models.Certificate).filter(
            models.Certificate.common_name.ilike(f"%{common_name}%")
        ).first()
        if not cert:
            return json.dumps({"error": f"No certificate found matching '{common_name}'"})

        d = days_until_expiry(cert.not_after) if cert.not_after else -1
        jira_result = create_renewal_ticket(cert.common_name, d, cert.owner.team if cert.owner else None)

        task = models.RenewalTask(
            certificate_id=cert.id,
            status=models.RenewalTaskStatus.JIRA_CREATED if jira_result.get("created") else models.RenewalTaskStatus.PENDING,
            jira_ticket_key=jira_result.get("key"),
            notes=notes or "Created by AI agent",
            triggered_by="agent",
        )
        db.add(task)
        db.commit()
        return json.dumps({"task_id": task.id, "jira": jira_result})

    return [
        list_expiring_certificates,
        get_certificate_details,
        explain_validation_failure,
        query_certificates_by_issuer,
        recommend_remediation,
        create_renewal_ticket_tool,
    ]
