"""Outbound mail providers. `mock` records delivery without sending (safe default for a demo);
`smtp` sends for real. Both honour the `mail_down` chaos flag and the mail circuit breaker."""

import smtplib
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.config import get_settings
from app.integrations.breaker import BREAKERS
from app.services import runtime_settings


class DeliveryError(Exception):
    pass


def send(db: Session, to: list[str], subject: str, body: str) -> str:
    """Returns a provider message id or raises DeliveryError."""
    br = BREAKERS["mail"]
    if br.state == "open":
        raise DeliveryError("mail circuit open after repeated failures")
    if runtime_settings.chaos(db)["mail_down"]:
        br.failure()
        raise DeliveryError("SMTP 421: service not available (chaos flag)")
    if not to:
        raise DeliveryError("no recipients")
    s = get_settings()
    if s.mail_provider == "smtp" and s.smtp_host:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = s.smtp_from, ", ".join(to), subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
                smtp.starttls()
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException) as e:
            br.failure()
            raise DeliveryError(str(e)) from e
        br.success()
        return "smtp"
    br.success()
    return "mock-delivered"
