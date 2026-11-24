import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage

from budget import config
from budget.db import get_secret, get_setting
from budget.services import features

log = logging.getLogger(__name__)

_SMTP_TIMEOUT_SECONDS = 30


def _instance_name():
    path = config.DB_PATH.absolute()
    return path.parent.name or path.name


def _compose_lockout_alert(when, source_ip=None, *, failures=None, lock_seconds=None):
    facts = [
        ("Instance", _instance_name()),
        ("When", f"{when:%Y-%m-%d %H:%M:%S} (this server's clock)"),
        ("Source address", source_ip or "unknown (the server did not record one)"),
    ]
    if failures is not None:
        facts.append(("Wrong passwords", f"{failures} in a row"))
    if lock_seconds is not None:
        facts.append(("Login locked for", f"{lock_seconds} seconds"))
    width = max(len(label) for label, _ in facts)
    table = "\n".join(f"  {label + ':':<{width + 1}} {value}" for label, value in facts)
    return (
        "Repeated wrong passwords have just locked the login of this budget\n"
        "instance.\n"
        "\n"
        f"{table}\n"
        "\n"
        "If that was you mistyping, there is nothing to do.\n"
        "\n"
        "If it was not, someone who can reach this instance is trying\n"
        "passwords. Signing in yourself with the right password clears the\n"
        "count and switches this alert back on.\n"
        "\n"
        "This is the only email you will get about this run of wrong\n"
        "passwords. Silence after it does not mean the guessing stopped: the\n"
        "count is only cleared by a successful sign-in, so the next alert\n"
        "waits for that.\n"
    )


def owner_address():
    return get_setting("notify_email").strip()


def recipients():
    owner = owner_address()
    if not owner:
        return []
    operator = get_setting("notify_operator_email").strip()
    return [owner, operator] if operator else [owner]


def send_lockout_alert(when, source_ip=None, *, failures=None, lock_seconds=None):
    try:
        if not features.enabled("security_email"):
            return
        to = recipients()
        if not to:
            return
        body = _compose_lockout_alert(when, source_ip,
                                      failures=failures, lock_seconds=lock_seconds)
        deliver(to, f"Security alert: wrong passwords locked {_instance_name()}",
                body, thread_name="lockout-alert")
    except Exception:
        log.exception("Could not hand the lockout alert over for delivery")


def deliver(recipients, subject, body, thread_name="alert"):
    threading.Thread(target=_send_now, args=(recipients, subject, body),
                     name=thread_name, daemon=True).start()


def _send_now(recipients, subject, body):
    try:
        address = get_setting("email_address").strip()
        password = get_secret("email_app_password")
        if not address or not password:
            return
        host = get_setting("smtp_host", "smtp.gmail.com").strip() or "smtp.gmail.com"
        try:
            port = int(get_setting("smtp_port", "587") or "587")
        except ValueError:
            port = 587

        message = EmailMessage()
        message["From"] = address
        message["To"] = ", ".join(recipients)
        message["Subject"] = subject
        message.set_content(body)

        with smtplib.SMTP(host, port, timeout=_SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(address, password)
            smtp.send_message(message)
    except Exception:
        log.exception("Could not send %r", subject)
