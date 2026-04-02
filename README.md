# Novo Nordisk Job Monitor

Automatically scrapes [Novo Nordisk's career site](https://careers.novonordisk.com) every morning, compares new postings against a set of leadership / process-improvement keywords, and emails you a formatted summary of new matches.

No local machine needed — everything runs on **GitHub Actions**.

---

## How it works

```
GitHub Actions (cron: 07:00 CET)
  │
  ├─ scraper.py   → fetch all current jobs from SuccessFactors API
  ├─ matcher.py   → score & classify new jobs (strong / possible)
  ├─ notifier.py  → send HTML email via SendGrid or Gmail SMTP
  └─ seen_jobs.json committed back → state persists between runs
```

---

## Quick-start

### 1. Fork / clone this repository

```bash
git clone https://github.com/<you>/JobHunter.git
cd JobHunter
```

### 2. Add GitHub Actions secrets

Go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Secret name | Required | Description |
|---|---|---|
| `ALERT_FROM_EMAIL` | ✅ | Sender address (e.g. `alerts@yourdomain.com`) |
| `ALERT_TO_EMAIL`   | ✅ | Your personal email address |
| `SENDGRID_API_KEY` | ⭐ recommended | SendGrid API key (free tier: 100 emails/day) |
| `SMTP_USER`        | alternative | Gmail address (if not using SendGrid) |
| `SMTP_PASSWORD`    | alternative | Gmail App Password (not your login password) |
| `SMTP_HOST`        | optional | SMTP server (default: `smtp.gmail.com`) |
| `SMTP_PORT`        | optional | SMTP port (default: `587`) |

> **SendGrid vs Gmail SMTP**
> The tool tries SendGrid first (if `SENDGRID_API_KEY` is set) and falls back
> to SMTP.  You only need to configure one of the two.

---

### 3. Set up SendGrid (recommended)

1. Create a free account at [sendgrid.com](https://sendgrid.com) (100 emails/day free).
2. Go to **Settings → API Keys → Create API Key**.
3. Choose "Restricted Access" and enable **Mail Send**.
4. Copy the key and add it as the `SENDGRID_API_KEY` secret.
5. Verify your sender email under **Settings → Sender Authentication**.

### 3b. Set up Gmail App Password (alternative)

1. Enable 2-Factor Authentication on your Google account.
2. Go to **Google Account → Security → 2-Step Verification → App passwords**.
3. Create an app password for "Mail".
4. Add it as `SMTP_PASSWORD`; add your Gmail address as `SMTP_USER`.

---

### 4. Test the workflow manually

1. Go to **Actions → Daily Novo Nordisk Job Check**.
2. Click **Run workflow → Run workflow**.
3. Watch the logs. On the first run, all fetched jobs are treated as "new" and
   `seen_jobs.json` is updated.  You will receive a one-time digest of all
   current matching jobs.

From the second run onward, only genuinely new postings trigger an email.

---

## Matching criteria

Jobs are scored and grouped into:

| Group | Meaning |
|---|---|
| **Strong match** | Score ≥ 10 — direct keyword hit (team lead, operations manager, LEAN, …) plus Danish/Copenhagen location |
| **Possible match** | Score 3–9 — partial match or EU-remote role |

Keywords that **always exclude** a job regardless of score:
- Scientific / lab roles (scientist, researcher, lab, postdoc, …)
- IT / software roles (software engineer, developer, DevOps, …)
- Finance / accounting roles (accountant, controller, auditor, …)

Edit `matcher.py` to tune thresholds and keyword lists.

---

## Email format

**Subject:** `🔔 [3] new Novo Nordisk jobs match your profile`

Each job card shows:
- Job title (linked)
- Location + date posted
- "View job →" button

---

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Set environment variables
export ALERT_FROM_EMAIL=you@example.com
export ALERT_TO_EMAIL=you@example.com
export SENDGRID_API_KEY=SG.xxxxx     # or SMTP_* equivalents

python main.py
```

Set `LOG_LEVEL=DEBUG` for verbose output:

```bash
LOG_LEVEL=DEBUG python main.py
```

---

## Files

| File | Purpose |
|---|---|
| `scraper.py` | Fetches job listings (multi-strategy: CSB API → XML feed → OData → HTML) |
| `matcher.py` | Scores and classifies jobs by keyword + location |
| `notifier.py` | Composes and sends HTML email via SendGrid or SMTP |
| `main.py` | Orchestrates the full pipeline |
| `seen_jobs.json` | Persists seen job IDs between runs (auto-committed by workflow) |
| `requirements.txt` | Python dependencies |
| `.github/workflows/daily_job_check.yml` | GitHub Actions scheduled workflow |

---

## Troubleshooting

**No jobs fetched**
The career site may have changed its API. Run with `LOG_LEVEL=DEBUG` and
inspect which scraping strategy is being attempted.  Open an issue with the
log output.

**Email not received**
- Check spam / junk folder.
- For SendGrid: verify the sender email under "Sender Authentication".
- For Gmail: make sure you're using an App Password, not your login password.

**Workflow doesn't run on schedule**
GitHub may disable scheduled workflows on forks after 60 days of inactivity.
Trigger it manually once to re-enable.
