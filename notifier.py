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
from email.utils import formatdate, make_msgid
from datetime import date

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def send_alert(
    strong: list[dict],
    possible: list[dict],
    stats: dict | None = None,
) -> bool:
    """
    Compose and send the daily job-alert email.

    Always sends (even when there are no new matches) so the user gets
    a daily digest with scraping stats. Returns True on success.
    Does NOT raise – caller decides whether to treat failure as fatal.
    """
    from_email = _require_env("ALERT_FROM_EMAIL")
    to_email = _require_env("ALERT_TO_EMAIL")
    if not from_email or not to_email:
        return False

    total_matches = len(strong) + len(possible)
    if total_matches:
        subject = f"🔔 [{total_matches}] nye jobs matcher din profil"
    else:
        subject = "📋 Dagligt joboverblik – ingen nye matches i dag"

    html_body = _render_html(strong, possible, stats or {})
    text_body = _render_text(strong, possible, stats or {})

    # Pick backend
    sendgrid_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    if sendgrid_key:
        return _send_sendgrid(sendgrid_key, from_email, to_email, subject, html_body, text_body)
    else:
        return _send_smtp(from_email, to_email, subject, html_body, text_body)


# ---------------------------------------------------------------------------
# Email rendering
# ---------------------------------------------------------------------------

def _render_html(strong: list[dict], possible: list[dict], stats: dict) -> str:
    today = date.today().strftime("%d %b %Y")
    sections = ""

    if strong:
        sections += _html_section("⭐ Stærke matches", strong, "#1a472a")
    if possible:
        sections += _html_section("🔍 Mulige matches", possible, "#2c5f8a")
    if not strong and not possible:
        sections += (
            '<p style="color:#888; font-style:italic; margin:16px 0;">'
            "Ingen nye jobs matchede din profil i dag."
            "</p>"
        )

    summary_rows = _html_stats_table(stats)
    total_matches = len(strong) + len(possible)

    return f"""<!DOCTYPE html>
<html lang="da">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dagligt joboverblik</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
             background:#f5f5f5; margin:0; padding:24px;">
  <div style="max-width:680px; margin:0 auto; background:#fff;
              border-radius:8px; overflow:hidden;
              box-shadow:0 2px 8px rgba(0,0,0,.12);">

    <!-- Header -->
    <div style="background:#003e72; color:#fff; padding:28px 32px;">
      <h1 style="margin:0; font-size:22px;">Dagligt joboverblik</h1>
      <p style="margin:6px 0 0; opacity:.8; font-size:14px;">{today}</p>
    </div>

    <div style="padding:24px 32px;">

      <!-- Daily stats summary -->
      <h2 style="color:#003e72; font-size:15px; margin:0 0 12px;
                 border-bottom:2px solid #003e72; padding-bottom:6px;">
        📊 Dagens overblik
      </h2>
      {summary_rows}
      <p style="margin:14px 0 24px; color:#444; font-size:14px;">
        <strong>{total_matches}</strong> job matcher din profil i dag
        ({len(strong)} stærke, {len(possible)} mulige).
      </p>

      {sections}

      <hr style="border:none; border-top:1px solid #eee; margin:24px 0;">
      <p style="font-size:12px; color:#888; margin:0;">
        Genereret automatisk fra
        <a href="https://www.jobindex.dk" style="color:#003e72;">Jobindex.dk</a>
        (Novo Nordisk · Novonesis · Novo Nordisk Fonden · Genmab · Lundbeck).
      </p>
    </div>
  </div>
</body>
</html>"""


def _html_stats_table(stats: dict) -> str:
    """Render a compact per-company stats table."""
    if not stats:
        return ""

    by_company = stats.get("by_company", {})
    new_jobs = stats.get("new_jobs", 0)
    total = stats.get("total_scraped", 0)

    rows = ""
    company_order = [
        "Novo Nordisk", "Novonesis", "Novo Nordisk Fonden", "Genmab", "Lundbeck"
    ]
    _COLORS = {
        "Novo Nordisk":        "#003e72",
        "Novonesis":           "#1a6b1a",
        "Novo Nordisk Fonden": "#7a4b00",
        "Genmab":              "#8b0045",
        "Lundbeck":            "#4a008b",
    }
    for company in company_order:
        count = by_company.get(company, 0)
        color = _COLORS.get(company, "#444")
        rows += (
            f'<tr>'
            f'<td style="padding:5px 10px 5px 0; color:{color}; font-weight:600; '
            f'font-size:13px;">{company}</td>'
            f'<td style="padding:5px 0; font-size:13px; color:#333;">'
            f'{count} stillinger fundet</td>'
            f'</tr>'
        )

    return f"""
    <table style="border-collapse:collapse; margin-bottom:8px; width:100%;">
      {rows}
      <tr style="border-top:1px solid #eee;">
        <td style="padding:7px 10px 5px 0; font-size:13px; color:#555;">
          <strong>I alt</strong>
        </td>
        <td style="padding:7px 0 5px; font-size:13px; color:#555;">
          {total} stillinger · <strong>{new_jobs} nye</strong> siden sidst
        </td>
      </tr>
    </table>"""


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


def _score_bar(score: float, max_score: float = 10.0, bars: int = 10) -> str:
    """Render a simple HTML progress bar for a score."""
    filled = round((score / max_score) * bars)
    filled = max(0, min(bars, filled))
    pct = int((score / max_score) * 100)
    color = "#2e7d32" if pct >= 70 else "#f57c00" if pct >= 40 else "#c62828"
    return (
        f'<span style="font-family:monospace; letter-spacing:1px; color:{color};">'
        + "█" * filled + "░" * (bars - filled)
        + f'</span> <span style="color:{color}; font-weight:600;">{score:.1f}/10</span>'
    )


def _html_card(job: dict) -> str:
    title       = _esc(job.get("title", "—"))
    location    = _esc(job.get("location", "—"))
    date_posted = _esc(job.get("date_posted", "—"))
    company     = _esc(job.get("company", ""))
    url         = job.get("url", "#")

    comp_score = job.get("_competency_score", 0.0)
    pref_score = job.get("_preference_score", 0.0)
    combined   = job.get("_combined", 0.0)

    _BADGES = {
        "Novo Nordisk":        ("#e8f0fb", "#003e72"),
        "Novonesis":           ("#e8f4e8", "#1a6b1a"),
        "Novo Nordisk Fonden": ("#fdf3e3", "#7a4b00"),
        "Genmab":              ("#fde8f0", "#8b0045"),
        "Lundbeck":            ("#f0e8fd", "#4a008b"),
    }
    company_badge = ""
    if company in _BADGES:
        bg, fg = _BADGES[company]
        company_badge = (
            f'<span style="background:{bg}; color:{fg}; font-size:11px; '
            f'padding:2px 7px; border-radius:10px; margin-left:8px;">{company}</span>'
        )

    score_rows = ""
    if comp_score or pref_score:
        score_rows = f"""
      <div style="margin-top:10px; font-size:12px; color:#555; line-height:1.8;">
        <div>🎯 Career goal fit &nbsp; {_score_bar(pref_score)}</div>
        <div>🛠 Background fit &nbsp;&nbsp; {_score_bar(comp_score)}</div>
      </div>"""

    return f"""
    <div style="border:1px solid #e8e8e8; border-radius:6px; padding:14px 16px;
                margin-bottom:10px;">
      <div>
        <a href="{url}" style="font-size:15px; font-weight:600; color:#003e72;
                                text-decoration:none;">
          {title}
        </a>{company_badge}
      </div>
      <div style="margin-top:6px; font-size:13px; color:#666;">
        📍 {location} &nbsp;|&nbsp; 📅 {date_posted}
      </div>{score_rows}
      <div style="margin-top:10px;">
        <a href="{url}" style="font-size:12px; background:#003e72; color:#fff;
                                padding:4px 10px; border-radius:4px;
                                text-decoration:none;">
          View job →
        </a>
      </div>
    </div>"""


def _render_text(strong: list[dict], possible: list[dict], stats: dict) -> str:
    today = date.today().strftime("%d %b %Y")
    lines = [
        f"Dagligt joboverblik — {today}",
        "=" * 40,
        "",
        "DAGENS OVERBLIK",
        "-" * 30,
    ]

    by_company = stats.get("by_company", {})
    for company in ["Novo Nordisk", "Novonesis", "Novo Nordisk Fonden", "Genmab", "Lundbeck"]:
        lines.append(f"  {company}: {by_company.get(company, 0)} stillinger")

    total = stats.get("total_scraped", 0)
    new_jobs = stats.get("new_jobs", 0)
    total_matches = len(strong) + len(possible)
    lines += [
        f"  I alt: {total} stillinger · {new_jobs} nye siden sidst",
        f"  Matcher din profil: {total_matches} ({len(strong)} stærke, {len(possible)} mulige)",
        "",
    ]

    if strong:
        lines.append("STÆRKE MATCHES")
        lines.append("-" * 30)
        for job in strong:
            lines += _text_job(job)

    if possible:
        lines.append("MULIGE MATCHES")
        lines.append("-" * 30)
        for job in possible:
            lines += _text_job(job)

    if not strong and not possible:
        lines.append("Ingen nye jobs matchede din profil i dag.")
        lines.append("")

    return "\n".join(lines)


def _text_job(job: dict) -> list[str]:
    pref = job.get("_preference_score", 0.0)
    comp = job.get("_competency_score", 0.0)
    company = job.get("company", "")
    score_line = (
        f"  Scores:      Career goal fit {pref:.1f}/10  |  Background fit {comp:.1f}/10"
        if (pref or comp) else ""
    )
    lines = [
        f"  [{company}] {job.get('title', '—')}",
        f"  Location:    {job.get('location', '—')}",
        f"  Date posted: {job.get('date_posted', '—')}",
    ]
    if score_line:
        lines.append(score_line)
    lines += [f"  Link:        {job.get('url', '—')}", ""]
    return lines


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
    smtp_host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    smtp_port = int(os.environ.get("SMTP_PORT") or "587")
    smtp_user = os.environ.get("SMTP_USER") or from_email
    smtp_password = os.environ.get("SMTP_PASSWORD", "")

    if not smtp_password:
        logger.error(
            "No SMTP_PASSWORD set and SENDGRID_API_KEY is missing. "
            "Cannot send email."
        )
        return False

    # Pre-flight log — shows addresses without exposing the password
    logger.info(
        "SMTP config: host=%s port=%d user=%s | from=%s to=%s",
        smtp_host, smtp_port, smtp_user, from_email, to_email,
    )
    if smtp_user.lower() != from_email.lower():
        logger.warning(
            "SMTP_USER (%s) != ALERT_FROM_EMAIL (%s). "
            "Gmail will send as %s — update ALERT_FROM_EMAIL to match.",
            smtp_user, from_email, smtp_user,
        )
        # Use smtp_user as the actual From so Gmail accepts it
        from_email = smtp_user

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="github-actions.local")
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(smtp_user, smtp_password)
            server.sendmail(from_email, to_email, msg.as_bytes())
        logger.info("Email sent via SMTP to %s", to_email)
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
