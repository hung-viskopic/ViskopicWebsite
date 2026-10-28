"""Outgoing email. Mailpit locally; a real provider or the university's relay later."""
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from .config import get_settings


@dataclass
class Message:
    to: str
    subject: str
    text: str
    html: str


class Mailer(Protocol):
    def send(self, msg: Message) -> None: ...


class SmtpMailer:
    def send(self, msg: Message) -> None:
        s = get_settings()
        em = EmailMessage()
        em["From"], em["To"], em["Subject"] = s.smtp_from, msg.to, msg.subject
        em.set_content(msg.text)
        em.add_alternative(msg.html, subtype="html")
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
            if s.smtp_tls:
                smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(em)


class MemoryMailer:
    """Tests: keeps sent messages; can be told to fail."""

    def __init__(self) -> None:
        self.sent: list[Message] = []
        self.fail_with: str | None = None

    def send(self, msg: Message) -> None:
        if self.fail_with:
            raise smtplib.SMTPException(self.fail_with)
        self.sent.append(msg)


_mailer: Mailer | None = None


def get_mailer() -> Mailer:
    global _mailer
    if _mailer is None:
        _mailer = SmtpMailer()
    return _mailer


def set_mailer(m: Mailer | None) -> None:
    global _mailer
    _mailer = m
