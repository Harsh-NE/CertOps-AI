"""
Root-cause analysis knowledge base. The agent's RCA is grounded: `diagnose()` gathers *evidence* (validation findings,
renewal history, provider job errors) and maps each piece to documented candidate causes and fixes. The LLM then
explains this evidence; it is not asked to invent causes.
"""
import json
import re
from typing import Any, Dict, List

from app import models

# check name -> (what it means, likely causes, suggested actions)
CHECK_KNOWLEDGE: Dict[str, Dict[str, List[str] | str]] = {
    "expired": {
        "meaning": "The certificate's notAfter date has passed; clients will reject it.",
        "causes": ["Renewal never ran or failed silently", "No owner or alert for this certificate", "Auto-renew was disabled"],
        "actions": ["Create a renewal plan (plan_renewal) and have an operator approve it",
                    "Deploy the renewed certificate to the endpoint", "Enable auto-renew and assign an owner"],
    },
    "expiring": {
        "meaning": "The certificate is close to expiry.",
        "causes": ["Renewal window reached but renewal has not completed"],
        "actions": ["Create a renewal plan now (plan_renewal)", "Check the renewal history for earlier failures"],
    },
    "not_yet_valid": {
        "meaning": "notBefore is in the future relative to the platform clock.",
        "causes": ["Clock skew on the issuing or scanning host", "Certificate issued with a future start date"],
        "actions": ["Verify NTP/clock on the scanner and the CA", "Reissue if the start date is wrong"],
    },
    "hostname_mismatch": {
        "meaning": "The certificate does not cover the name clients use.",
        "causes": ["Certificate issued for a different name", "Wrong SNI/virtual host serving this endpoint",
                   "SAN list missing this hostname"],
        "actions": ["Reissue with the correct SANs", "Fix the virtual-host or load-balancer certificate binding"],
    },
    "weak_key": {
        "meaning": "The public key is smaller than current minimums (RSA < 2048 bits, EC < 256 bits).",
        "causes": ["Legacy certificate never reissued", "Old key-generation defaults"],
        "actions": ["Reissue with RSA >= 2048 (3072 preferred) or an ECDSA P-256 key"],
    },
    "weak_signature": {
        "meaning": "The certificate (or an intermediate) is signed with MD5 or SHA-1.",
        "causes": ["Legacy CA or old certificate"],
        "actions": ["Reissue so the chain uses SHA-256 or stronger"],
    },
    "self_signed": {
        "meaning": "The certificate is self-signed and not in the trust store, so clients cannot verify it.",
        "causes": ["Default or development certificate left in production", "Internal service without a private CA"],
        "actions": ["Issue from a trusted (public or internal) CA", "Or add the CA to the platform trust store if it is intentional"],
    },
    "chain": {
        "meaning": "The certificate chain could not be built to a trusted root.",
        "causes": ["Server did not send the intermediate certificate", "Issuer CA is not in the trust store",
                   "Intermediate expired or was replaced"],
        "actions": ["Configure the server to send the full chain (leaf + intermediates)",
                    "Add the issuing root to the trust store if it is a private CA"],
    },
    "revoked": {
        "meaning": "The CA reports this certificate as revoked.",
        "causes": ["Key compromise or mis-issuance reported to the CA", "Certificate replaced and old one revoked"],
        "actions": ["Replace the certificate immediately and rotate the private key"],
    },
    "revocation_unchecked": {
        "meaning": "Revocation status could not be determined (no OCSP responder, unreachable, or unverifiable response).",
        "causes": ["Certificate has no OCSP URL (some CAs have retired OCSP)", "Responder unreachable from the scanner"],
        "actions": ["Informational; consider CRL checking if revocation matters for this endpoint"],
    },
}

# renewal error text -> (likely cause, action)
RENEWAL_ERROR_PATTERNS = [
    (r"connection refused|timed out|timeout|unreachable", "The CA could not reach the HTTP-01 challenge (port 80/5002 blocked or no listener)",
     "Open port 80 to the CA and confirm the challenge solver is running"),
    (r"unauthorized|invalid response|404", "The CA fetched the challenge URL but got the wrong content",
     "Check the webroot path and the web server's /.well-known/acme-challenge/ mapping"),
    (r"rate.?limit", "The CA's rate limit was hit", "Wait for the limit window; use the staging directory while testing"),
    (r"vault", "The secret store (Vault) was unavailable or rejected the write", "Check VAULT_ADDR/VAULT_TOKEN and Vault health"),
    (r"no secret store|not configured|email", "Renewal prerequisites are missing", "Set LETSENCRYPT_EMAIL and the Vault settings"),
]


def _evidence(source: str, fact: str, **extra) -> Dict[str, Any]:
    return {"source": source, "fact": fact, **extra}


def diagnose(db, cert: models.Certificate) -> Dict[str, Any]:
    evidence: List[Dict[str, Any]] = []
    causes: List[Dict[str, Any]] = []
    actions: List[str] = []

    latest = (db.query(models.ValidationResult).filter_by(certificate_id=cert.id)
              .order_by(models.ValidationResult.checked_at.desc()).first())
    findings = json.loads(latest.findings) if latest else []
    if latest is None:
        evidence.append(_evidence("validation", "This certificate has never been validated."))
        actions.append("Run validation (run_validation) first")
    for f in findings:
        k = CHECK_KNOWLEDGE.get(f["check"])
        evidence.append(_evidence("validation", f["message"], check=f["check"], severity=f["severity"]))
        if k and f["severity"] in ("error", "warning"):
            causes.append({"cause": k["causes"][0], "also_possible": k["causes"][1:], "explains": f["check"],
                           "meaning": k["meaning"]})
            actions.extend(a for a in k["actions"] if a not in actions)
    if latest is not None and not any(f["severity"] in ("error", "warning") for f in findings):
        evidence.append(_evidence("validation", "All validation checks passed at the last check."))

    renewals = (db.query(models.RenewalTask).filter(models.RenewalTask.certificate_id == cert.id,
                                                    models.RenewalTask.dry_run.isnot(True))
                .order_by(models.RenewalTask.created_at.desc()).limit(5).all())
    for t in renewals:
        if t.status == models.RenewalTaskStatus.FAILED:
            note = t.notes or ""
            evidence.append(_evidence("renewal_history", f"Renewal via {t.provider} failed: {note[:300]}",
                                      when=t.created_at.isoformat() if t.created_at else None))
            for pattern, cause, action in RENEWAL_ERROR_PATTERNS:
                if re.search(pattern, note, re.I):
                    causes.append({"cause": cause, "explains": "renewal_failure"})
                    if action not in actions:
                        actions.append(action)
                    break
    if not renewals:
        evidence.append(_evidence("renewal_history", "No renewal has ever been attempted for this certificate."))

    runs = (db.query(models.JobRun).filter(models.JobRun.provider == cert.source.value)
            .order_by(models.JobRun.started_at.desc()).limit(3).all())
    for r in runs:
        errs = [e for e in json.loads(r.errors or "[]") if cert.source_ref and cert.source_ref in e]
        if errs:
            evidence.append(_evidence("discovery_job", f"Last discovery run had errors for this certificate: {errs[0][:300]}"))

    if not cert.owner_id:
        evidence.append(_evidence("metadata", "No owner is assigned, so nobody is notified."))
        actions.append("Assign an owner")
    if not cert.auto_renew_enabled:
        evidence.append(_evidence("metadata", "Auto-renew is not enabled."))

    return {"certificate": cert.common_name, "status": cert.status.value if cert.status else None,
            "evidence": evidence, "likely_causes": causes, "suggested_actions": actions,
            "note": "Causes are derived from documented patterns for the observed evidence; they are not proof."}
