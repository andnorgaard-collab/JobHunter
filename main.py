"""
main.py — Entry point for the job-monitoring tool.

Flow:
  1. Load previously seen job IDs from seen_jobs.json
  2. Fetch current job listings via scraper.py
  3. Find jobs that have NOT been seen before
  4. Run keyword matching (matcher.py) on new jobs only
  5. Send email alert if any matches found (notifier.py)
  6. Persist the updated seen-job IDs back to seen_jobs.json

Run normally:
  python main.py

Run with verbose logging:
  LOG_LEVEL=DEBUG python main.py

Send a test email (verifies SMTP/SendGrid setup without scraping):
  python main.py --test-email
"""

import json
import logging
import os
import sys
from pathlib import Path

import scraper
import matcher
import notifier

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("main")

# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

STATE_FILE = Path(__file__).parent / "seen_jobs.json"


def load_seen_ids() -> set[str]:
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("seen_ids", []))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s – starting fresh.", STATE_FILE, exc)
        return set()


def save_seen_ids(seen_ids: set[str]) -> None:
    try:
        STATE_FILE.write_text(
            json.dumps({"seen_ids": sorted(seen_ids)}, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved %d seen job IDs to %s", len(seen_ids), STATE_FILE)
    except OSError as exc:
        logger.error("Could not write %s: %s", STATE_FILE, exc)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def run() -> int:
    """Execute one monitoring cycle. Returns exit code (0 = success)."""
    logger.info("=== Job monitor started ===")

    # 1. Load state
    seen_ids = load_seen_ids()
    logger.info("Loaded %d previously seen job IDs.", len(seen_ids))

    # 2. Fetch all current jobs
    all_jobs = scraper.fetch_all_jobs()
    if not all_jobs:
        logger.warning("No jobs fetched – check scraper logs for errors.")

    logger.info("Fetched %d total jobs.", len(all_jobs))

    # 3. Filter to only new (unseen) jobs
    new_jobs = [j for j in all_jobs if j["id"] not in seen_ids]
    logger.info("%d new jobs (not seen before).", len(new_jobs))

    # 4. Match / classify new jobs
    classified = matcher.classify_jobs(new_jobs) if new_jobs else {"strong": [], "possible": []}
    strong = classified["strong"]
    possible = classified["possible"]
    logger.info(
        "Matching results: %d strong, %d possible (out of %d new).",
        len(strong), len(possible), len(new_jobs),
    )

    # 5. Build stats for the daily summary section
    from collections import Counter
    by_company = Counter(j["company"] for j in all_jobs)
    stats = {
        "total_scraped": len(all_jobs),
        "new_jobs": len(new_jobs),
        "by_company": dict(by_company),
    }

    # 6. Always send the daily email
    ok = notifier.send_alert(strong, possible, stats=stats)
    if not ok:
        logger.error("Email delivery failed.")

    # 7. Persist state (mark ALL fetched jobs as seen, not just matches)
    _update_seen(seen_ids, all_jobs)
    return 0


def _update_seen(seen_ids: set[str], jobs: list[dict]) -> None:
    before = len(seen_ids)
    seen_ids.update(j["id"] for j in jobs)
    after = len(seen_ids)
    if after > before:
        save_seen_ids(seen_ids)
    else:
        logger.debug("No new IDs to persist.")


# ---------------------------------------------------------------------------
# Test-email mode
# ---------------------------------------------------------------------------

def run_test_email() -> int:
    """Send a test email with dummy jobs to verify email delivery works."""
    logger.info("=== TEST EMAIL MODE ===")
    dummy_strong = [
        {
            "id": "test-001",
            "title": "Operations Manager, Clinical Supplies",
            "location": "Bagsværd, Denmark",
            "date_posted": "2026-04-02",
            "url": "https://careers.novonordisk.com",
            "company": "Novo Nordisk",
            "_competency_score": 9.5,
            "_preference_score": 9.5,
            "_combined": 9.5,
        }
    ]
    dummy_possible = [
        {
            "id": "test-002",
            "title": "Process Improvement Lead",
            "location": "Copenhagen, Denmark",
            "date_posted": "2026-04-02",
            "url": "https://www.novonesis.com/en/careers/jobs",
            "company": "Novonesis",
            "_competency_score": 7.0,
            "_preference_score": 6.0,
            "_combined": 6.35,
        }
    ]
    ok = notifier.send_alert(dummy_strong, dummy_possible)
    if ok:
        logger.info("Test email sent successfully — check your inbox.")
        return 0
    else:
        logger.error("Test email FAILED — check your secret/env var settings.")
        return 1


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--test-email" in sys.argv:
        sys.exit(run_test_email())
    sys.exit(run())
