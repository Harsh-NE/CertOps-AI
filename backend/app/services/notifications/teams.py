import requests

from app.config import settings


def send_teams_alert(message: str) -> dict:
    if not settings.TEAMS_WEBHOOK_URL:
        return {"sent": False, "reason": "TEAMS_WEBHOOK_URL not configured"}
    try:
        resp = requests.post(settings.TEAMS_WEBHOOK_URL, json={"text": message}, timeout=5)
        return {"sent": resp.status_code < 300, "status_code": resp.status_code}
    except Exception as e:
        return {"sent": False, "reason": str(e)}
