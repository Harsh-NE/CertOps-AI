import requests
from requests.auth import HTTPBasicAuth

from app.config import settings


def create_renewal_ticket(certificate_common_name: str, days_to_expiry: int, owner_team: str = None) -> dict:
    if not (settings.JIRA_BASE_URL and settings.JIRA_EMAIL and settings.JIRA_API_TOKEN):
        return {"created": False, "reason": "Jira not configured"}
    try:
        url = f"{settings.JIRA_BASE_URL}/rest/api/3/issue"
        payload = {
            "fields": {
                "project": {"key": settings.JIRA_PROJECT_KEY},
                "summary": f"Renew certificate: {certificate_common_name} (expires in {days_to_expiry}d)",
                "description": {
                    "type": "doc", "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text",
                        "text": f"Certificate {certificate_common_name} needs renewal. "
                                f"Owning team: {owner_team or 'unassigned'}. "
                                f"Auto-created by the Certificate Lifecycle Management Platform."}]}]
                },
                "issuetype": {"name": "Task"},
            }
        }
        resp = requests.post(
            url, json=payload, timeout=10,
            auth=HTTPBasicAuth(settings.JIRA_EMAIL, settings.JIRA_API_TOKEN),
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code >= 300:
            return {"created": False, "reason": resp.text}
        return {"created": True, "key": resp.json().get("key")}
    except Exception as e:
        return {"created": False, "reason": str(e)}
