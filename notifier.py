"""
notifier.py — Email composition and delivery.

Supports two backends (checked in order):
  1. SendGrid  – set SENDGRID_API_KEY secret
  2. Gmail SMTP – set SMTP_USER + SMTP_PASSWORD secrets

Required environment variables (always):
  ALERT_FROM_EMAIL   sender address (e.g. jobs@yourdomain.com)
  ALERT_TO_EMAIL     recipient address

Optional / backend-specific:
  SENDGRID_API_KEY   → enables SendGrid backend
  SMTP_HOST          SMTP server host  (default: smtp.gmail.com)
  SMTP_PORT          SMTP server port  (default: 587)
  SMTP_USER          SMTP login username
  SMTP_PASSWORD      SMTP login password
"""

import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import date

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def send_alert(strong: list[dict], possible: list[dict]) -> bool:
    """
    Compose and send the job-alert email.

    Returns True if the email was sent successfully, False otherwise.
    Does NOT raise – caller decides whether to treat failure as fatal.
    """
    total = len(strong) + len(possible)
    if total == 0:
        logger.info("No new matching jobs – skipping email.")
        return True

    from_email = _require_env("ALERT_FROM_EMAIL")
    to_email = _require_env("ALERT_TO_EMAIL")
    if not from_email or not to_email:
        return False

    subject = f"🔔 [{total}] new Novo Nordisk job{'s' if total != 1 else ''} match your profile"
    html_body = _render_html(strong, possible)
    text_body = _render_text(strong, possible)

    # Pick backend
    sendgrid_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if sendgrid_key:
        return _send_sendgrid(sendgrid_key, from_email, to_email, subject, html_body, text_body)
    else:
        return _send_smtp(from_email, to_email, subject, html_body, text_body)


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------

def _render_html(strong: list[dict], possible: list[dict]) -> str:
    today = date.today().strftime("%d %b %Y")
    sections = ""

    if strong:
        sections += _html_section("⭐ Strong matches", strong, "#1a472a")
    if possible:
        sections += _html_section("🔍 Possible matches", possible, "#2c5f8a")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Novo Nordisk Job Alert</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
             background:#f5f5f5; margin:0; padding:24px;">
  <div style="max-width:680px; margin:0 auto; background:#fff;
              border-radius:8px; overflow:hidden;
              box-shadow:0 2px 8px rgba(0,0,0,.12);">

    <!-- Header -->
    <div style="background:#003e72; color:#fff; padding:28px 32px;">
      <h1 style="margin:0; font-size:22px;">Novo Nordisk Job Alert</h1>
      <p style="margin:6px 0 0; opacity:.8; font-size:14px;">{today}</p>
    </div>

    <div style="padding:24px 32px;">
      <p style="margin:0 0 20px; color:#444;">
        {len(strong) + len(possible)} new job posting(s) match your criteria.
      </p>
      {sections}
      <hr style="border:none; border-top:1px solid #eee; margin:24px 0;">
      <p style="font-size:12px; color:#888; margin:0;">
        This alert was generated automatically.
        Jobs are sourced from
        <a href="https://careers.novonordisk.com" style="color:#003e72;">
          careers.novonordisk.com</a>.
      </p>
    </div>
  </div>
</body>
</html>"""


def _html_section(heading: str, jobs: list[dict], accent_color: str) -> str:
    cards = "".join(_html_card(job) for job in jobs)
    return f"""
    <h2 style="color:{accent_color}; font-size:16px; margin:0 0 12px;
               border-bottom:2px solid {accent_color}; padding-bottom:6px;">
      {heading} ({len(jobs)})
    </h2>
    {cards}
    <br>
"""


def _html_card(job: dict) -> str:
    title = _esc(job.get("title", "—"))
    location = _esc(job.get("location", "—"))
    date_posted = _esc(job.get("date_posted", "—"))
    url = job.get("url", "#")

    return f"""
    <div style="border:1px solid #e8e8e8; border-radius:6px; padding:14px 16px;
                margin-bottom:10px;">
      <a href="{url}" style="font-size:15px; font-weight:600; color:#003e72;
                              text-decoration:none;">
        {title}
      </a>
      <div style="margin-top:6px; font-size:13px; color:#666;">
        📍 {location} &nbsp;|&nbsp; 📅 {date_posted}
      </div>
      <div style="margin-top:8px;">
        <a href="{url}" style="font-size:12px; background:#003e72; color:#fff;
                                padding:4px 10px; border-radius:4px;
                                text-decoration:none;">
          View job →
        </a>
      </div>
    </div>"""


def _render_text(strong: list[dict], possible: list[dict]) -> str:
    lines = [
        "Novo Nordisk Job Alert",
        "=" * 40,
        "",
    ]

    if strong:
        lines.append("STRONG MATCHES")
        lines.append("-" * 30)
        for job in strong:
            lines += _text_job(job)

    if possible:
        lines.append("POSSIBLE MATCHES")
        lines.append("-" * 30)
        for job in possible:
            lines += _text_job(job)

    return "\n".join(lines)


def _text_job(job: dict) -> list[str]:
    return [
        f"  {job.get('title', '—')}",
        f"  Location:    {job.get('location', '—')}",
        f"  Date posted: {job.get('date_posted', '—')}",
        f"  Link:        {job.get('url', '—')}",
        "",
    ]


def _esc(text: str) -> str:
    """Minimal HTML escaping."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# SendGrid backend
# ---------------------------------------------------------------------------

def _send_sendgrid(
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> bool:
    try:
        import sendgrid  # type: ignore
        from sendgrid.helpers.mail import Mail, Email, To, Content, HtmlContent  # type: ignore

        sg = sendgrid.SendGridAPIClient(api_key=api_key)
        message = Mail(
            from_email=Email(from_email),
            to_emails=To(to_email),
            subject=subject,
        )
        message.add_content(Content("text/plain", text_body))
        message.add_content(HtmlContent(html_body))

        response = sg.client.mail.send.post(request_body=message.get())
        if response.status_code in (200, 202):
            logger.info("Email sent via SendGrid (status %d)", response.status_code)
            return True
        else:
            logger.error("SendGrid returned status %d: %s", response.status_code, response.body)
            return False

    except ImportError:
        logger.warning("sendgrid package not installed, falling back to SMTP")
        return _send_smtp(from_email, to_email, subject, html_body, text_body)
    except Exception as exc:
        logger.error("SendGrid error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# SMTP backend (Gmail default)
# ---------------------------------------------------------------------------

def _send_smtp(
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> bool:
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", from_email)
    smtp_password = os.environ.get("SMTP_PASSWORD", "")

    if not smtp_password:
        logger.error(
            "No SMTP_PASSWORD set and SENDGRID_API_KEY is missing. "
            "Cannot send email."
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, to_email, msg.as_bytes())
        logger.info("Email sent via SMTP (%s:%d)", smtp_host, smtp_port)
        return True
    except Exception as exc:
        logger.error("SMTP error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        logger.error("Required environment variable %s is not set.", name)
    return value
