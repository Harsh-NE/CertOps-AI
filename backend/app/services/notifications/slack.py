from typing import Optional
from app.config import settings


def send_slack_alert(message: str, channel: Optional[str] = None) -> dict:
    if not settings.SLACK_BOT_TOKEN:
        return {"sent": False, "reason": "SLACK_BOT_TOKEN not configured"}
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
        client = WebClient(token=settings.SLACK_BOT_TOKEN)
        resp = client.chat_postMessage(channel=channel or settings.SLACK_DEFAULT_CHANNEL, text=message)
        return {"sent": True, "ts": resp.get("ts")}
    except Exception as e:
        return {"sent": False, "reason": str(e)}
