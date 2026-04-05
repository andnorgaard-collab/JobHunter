"""
scraper.py — Fetches job listings from Jobindex RSS feeds.

Jobindex provides server-rendered RSS feeds for company searches:
  https://www.jobindex.dk/jobsoegning/rss?virksomhed=COMPANY_NAME

This bypasses JavaScript rendering issues with the HTML search page.

Companies monitored:
  - Novo Nordisk
  - Novonesis
  - Novo Nordisk Fonden
  - Genmab
  - Lundbeck
"""

import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JOBINDEX_RSS = "https://www.jobindex.dk/jobsoegning/rss"

REQUEST_DELAY = 2.0  # seconds between company requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
}

# (display name, virksomhed= query parameter)
COMPANIES = [
    ("Novo Nordisk",        "Novo Nordisk"),
    ("Novonesis",           "Novonesis"),
    ("Novo Nordisk Fonden", "Novo Nordisk Fonden"),
    ("Genmab",              "Genmab"),
    ("Lundbeck",            "Lundbeck"),
]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_all_jobs() -> list[dict]:
    """Return job dicts from all monitored companies via Jobindex RSS."""
    session = requests.Session()
    session.headers.update(HEADERS)

    all_jobs: list[dict] = []

    for company_name, query in COMPANIES:
        jobs = _fetch_company(session, company_name, query)
        logger.info("%s: %d jobs fetched", company_name, len(jobs))
        all_jobs.extend(jobs)
        time.sleep(REQUEST_DELAY)

    if not all_jobs:
        logger.warning("All RSS feeds returned 0 jobs.")

    return all_jobs


# ---------------------------------------------------------------------------
# Per-company RSS fetcher
# ---------------------------------------------------------------------------

def _fetch_company(
    session: requests.Session,
    company_name: str,
    query: str,
) -> list[dict]:
    """Fetch all Jobindex RSS items for one company."""
    params = {"virksomhed": query}
    url = f"{JOBINDEX_RSS}?{urllib.parse.urlencode(params)}"

    try:
        resp = session.get(url, timeout=30)
    except requests.RequestException as exc:
        logger.warning("%s: request error: %s", company_name, exc)
        return []

    if resp.status_code != 200:
        logger.warning("%s: HTTP %d from RSS feed", company_name, resp.status_code)
        return []

    logger.debug("%s: RSS feed → %d bytes", company_name, len(resp.content))
    return _parse_rss(resp.content, company_name)


# ---------------------------------------------------------------------------
# RSS parsing
# ---------------------------------------------------------------------------

def _parse_rss(content: bytes, company_name: str) -> list[dict]:
    """Parse an RSS feed and return job dicts."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        logger.warning("%s: RSS parse error: %s", company_name, exc)
        return []

    # RSS namespace handling — strip any namespace prefix
    def _tag(el: ET.Element, name: str) -> str | None:
        child = el.find(name)
        if child is None:
            # Try with common RSS namespaces
            for ns in ("", "{http://purl.org/rss/1.0/}", "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"):
                child = el.find(f"{ns}{name}")
                if child is not None:
                    break
        return child.text.strip() if child is not None and child.text else None

    jobs: list[dict] = []
    channel = root.find("channel") or root
    items = channel.findall("item")

    logger.debug("%s: %d <item> elements in RSS", company_name, len(items))

    for item in items:
        title     = _tag(item, "title") or ""
        link      = _tag(item, "link") or ""
        desc      = _tag(item, "description") or ""
        pub_date  = _tag(item, "pubDate") or ""
        guid      = _tag(item, "guid") or link

        if not title or not link:
            continue

        # Build a stable job ID from the Jobindex job ID in the URL
        job_id = _extract_id(link, guid)

        # Parse location from description (Jobindex puts it in the RSS desc)
        location = _extract_location_from_desc(desc)

        # Parse date
        date_posted = _parse_date(pub_date)

        jobs.append({
            "id":          job_id,
            "title":       title,
            "location":    location,
            "date_posted": date_posted,
            "url":         link,
            "company":     company_name,
        })

    return jobs


def _extract_id(link: str, guid: str) -> str:
    """Extract a stable ID from a Jobindex job URL."""
    # Jobindex URLs: https://www.jobindex.dk/jobannonce/1234567/title
    for text in (link, guid):
        m = re.search(r"/jobannonce/(\d+)", text)
        if m:
            return f"ji_{m.group(1)}"
    # Fallback: hash the URL
    return f"ji_{abs(hash(link)) % 10_000_000}"


def _extract_location_from_desc(desc: str) -> str:
    """
    Jobindex RSS descriptions look like:
      '<b>Virksomhed:</b> Novo Nordisk<br><b>Sted:</b> Bagsværd<br>...'
    Extract the 'Sted:' field.
    """
    # Strip HTML tags
    plain = re.sub(r"<[^>]+>", " ", desc)
    plain = re.sub(r"\s+", " ", plain).strip()

    # Try "Sted: <location>" pattern
    m = re.search(r"Sted\s*:\s*([^|;,\n]+)", plain, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # Try "Location: <location>" pattern (English RSS)
    m = re.search(r"Location\s*:\s*([^|;,\n]+)", plain, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    return ""


def _parse_date(pub_date: str) -> str:
    """Parse RFC 2822 date string to YYYY-MM-DD."""
    if not pub_date:
        return ""
    try:
        dt = parsedate_to_datetime(pub_date)
        return dt.date().isoformat()
    except Exception:
        # Return raw string trimmed to 10 chars if it looks like a date
        if re.match(r"\d{4}-\d{2}-\d{2}", pub_date):
            return pub_date[:10]
        return pub_date
