"""Notification persistence plus optional SendGrid email delivery."""
import json
import logging
import os
import urllib.error
import urllib.request
from database import Notification

logger = logging.getLogger(__name__)


def send_email(account, subject, body):
    api_key = os.environ.get("SENDGRID_API_KEY")
    sender = os.environ.get("FROM_EMAIL")
    if not api_key or not sender or not account.email:
        return "not_configured"
    payload = {
        "personalizations": [{"to": [{"email": account.email}]}],
        "from": {"email": sender},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return "sent" if response.status in (200, 202) else "failed"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("Email delivery failed for account %s: %s", account.id, exc)
        return "failed"


def create_notification(db, account, title, body, *, kind="info", patient_id=None, alert_id=None):
    notification = Notification(account_id=account.id, patient_id=patient_id, alert_id=alert_id,
                                title=title, body=body, kind=kind)
    db.add(notification)
    db.flush()
    notification.email_status = send_email(account, title, body)
    return notification
