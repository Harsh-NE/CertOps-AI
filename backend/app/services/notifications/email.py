import smtplib
from email.mime.text import MIMEText

from app.config import settings


def send_email_alert(to_address: str, subject: str, body: str) -> dict:
    if not (settings.SMTP_HOST and settings.ALERT_EMAIL_FROM):
        return {"sent": False, "reason": "SMTP not configured"}
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = settings.ALERT_EMAIL_FROM
        msg["To"] = to_address

        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.ALERT_EMAIL_FROM, [to_address], msg.as_string())
        return {"sent": True}
    except Exception as e:
        return {"sent": False, "reason": str(e)}
