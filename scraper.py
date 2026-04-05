"""
scraper.py — Fetches job listings from Jobindex for monitored companies.

Companies monitored:
  - Novo Nordisk
  - Novonesis
  - Novo Nordisk Fonden
  - Genmab
  - Lundbeck

Jobindex search URL:
  https://www.jobindex.dk/jobsoegning?virksomhed=COMPANY_NAME

Each job dict returned includes at minimum:
  id, title, location, date_posted, url, company
"""

import logging
import re
import time
import urllib.parse
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

JOBINDEX_BASE = "https://www.jobindex.dk"
JOBINDEX_SEARCH = f"{JOBINDEX_BASE}/jobsoegning"

# Polite delay between company requests (seconds)
REQUEST_DELAY = 2.0

# Max pages to fetch per company (20 jobs/page → 200 jobs max)
MAX_PAGES = 10

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
    "Referer": "https://www.jobindex.dk/",
}

# Companies to monitor: (display name, Jobindex search query)
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
    """
    Return job dicts from all monitored companies via Jobindex.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    all_jobs: list[dict] = []

    for company_name, query in COMPANIES:
        jobs = _fetch_company(session, company_name, query)
        logger.info("%s: %d jobs fetched from Jobindex", company_name, len(jobs))
        all_jobs.extend(jobs)
        time.sleep(REQUEST_DELAY)

    if not all_jobs:
        logger.warning("All Jobindex scrapers returned 0 jobs.")

    return all_jobs


# ---------------------------------------------------------------------------
# Per-company fetcher
# ---------------------------------------------------------------------------

def _fetch_company(
    session: requests.Session,
    company_name: str,
    query: str,
) -> list[dict]:
    """Fetch all Jobindex listings for one company, handling pagination."""
    jobs: list[dict] = []
    page = 0
    seen_ids: set[str] = set()

    while page < MAX_PAGES:
        params: dict = {"virksomhed": query}
        if page > 0:
            params["tstart"] = page * 20  # Jobindex uses tstart=0,20,40,...

        url = f"{JOBINDEX_SEARCH}?{urllib.parse.urlencode(params)}"
        try:
            resp = session.get(url, timeout=30)
        except requests.RequestException as exc:
            logger.warning("%s: request error on page %d: %s", company_name, page, exc)
            break

        if resp.status_code != 200:
            logger.warning(
                "%s: HTTP %d on page %d", company_name, resp.status_code, page
            )
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        page_jobs = _parse_jobindex_page(soup, company_name)

        if not page_jobs:
            logger.debug("%s: no jobs on page %d – stopping", company_name, page)
            break

        # Dedup within the run (Jobindex sometimes repeats listings)
        new_on_page = 0
        for job in page_jobs:
            if job["id"] not in seen_ids:
                seen_ids.add(job["id"])
                jobs.append(job)
                new_on_page += 1

        logger.debug("%s: page %d → %d new jobs", company_name, page, new_on_page)

        # Stop if the page had no genuinely new jobs
        if new_on_page == 0:
            break

        # Check if there's a next page
        if not _has_next_page(soup, page):
            break

        page += 1
        time.sleep(0.5)

    return jobs


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def _parse_jobindex_page(soup: BeautifulSoup, company_name: str) -> list[dict]:
    """Extract job dicts from a Jobindex search results page."""
    jobs: list[dict] = []

    # Jobindex uses <article> tags with class "jix_robotjob" or similar,
    # or <li> with class "PaidJob" / "OrganicJob".
    # Try multiple selectors for robustness.
    candidates = (
        soup.find_all("article", class_=re.compile(r"jix_robotjob", re.I))
        or soup.find_all("li", class_=re.compile(r"(PaidJob|OrganicJob)", re.I))
        or soup.find_all("div", class_=re.compile(r"jobsearch-result", re.I))
    )

    if not candidates:
        # Fallback: any element with a link to /jobannonce/
        candidates = [
            a.find_parent(["article", "li", "div"])
            for a in soup.find_all("a", href=re.compile(r"/jobannonce/\d+", re.I))
            if a.find_parent(["article", "li", "div"])
        ]
        # Deduplicate parent elements
        seen_parents: list = []
        unique: list = []
        for c in candidates:
            if c not in seen_parents:
                seen_parents.append(c)
                unique.append(c)
        candidates = unique

    for item in candidates:
        job = _extract_job(item, company_name)
        if job:
            jobs.append(job)

    return jobs


def _extract_job(item: BeautifulSoup, company_name: str) -> dict | None:
    """Extract a single job dict from a result element."""
    # --- Title and URL ---
    link = item.find("a", href=re.compile(r"/jobannonce/\d+", re.I))
    if not link:
        return None

    href = link.get("href", "")
    title = link.get_text(strip=True)

    # Build absolute URL
    if href.startswith("http"):
        job_url = href
    else:
        job_url = JOBINDEX_BASE + href

    # Extract Jobindex job ID from URL like /jobannonce/1234567/...
    id_match = re.search(r"/jobannonce/(\d+)", href)
    if not id_match:
        return None
    job_id = f"ji_{id_match.group(1)}"

    if not title:
        return None

    # --- Location ---
    location = _extract_location(item)

    # --- Date posted ---
    date_posted = _extract_date(item)

    return {
        "id": job_id,
        "title": title,
        "location": location,
        "date_posted": date_posted,
        "url": job_url,
        "company": company_name,
    }


def _extract_location(item: BeautifulSoup) -> str:
    """Best-effort location extraction from a job result element."""
    # Try <span> or <p> elements containing typical Danish location patterns
    for tag in item.find_all(["span", "p", "div"]):
        text = tag.get_text(strip=True)
        # Look for patterns like "København", "Bagsværd", "Søborg", etc.
        if re.search(
            r"(køben|bagsv|søborg|gladsax|hillerød|lynge|måløv|gentofte|"
            r"lyngby|allerød|ballerup|frederiks|helsin|odense|aarhus|"
            r"[A-ZÆØÅ][a-zæøå]{2,},?\s*(Denmark|Danmark))",
            text,
            re.IGNORECASE,
        ) and len(text) < 60:
            return text
    return ""


def _extract_date(item: BeautifulSoup) -> str:
    """Best-effort date extraction from a job result element."""
    # Try <time> element with datetime attribute
    time_tag = item.find("time")
    if time_tag:
        dt = time_tag.get("datetime", "") or time_tag.get_text(strip=True)
        if dt:
            return dt[:10]  # Return YYYY-MM-DD portion

    # Try text patterns like "I dag", "I går", "3 dage siden", or a date string
    for tag in item.find_all(["span", "p", "div", "small"]):
        text = tag.get_text(strip=True)
        if re.match(r"\d{1,2}/\d{1,2}[-/]\d{2,4}", text):
            return text
        if re.match(r"\d{4}-\d{2}-\d{2}", text):
            return text[:10]
        if re.search(r"\bi dag\b", text, re.IGNORECASE):
            return date.today().isoformat()
        if re.search(r"\bi går\b", text, re.IGNORECASE):
            return (date.today() - timedelta(days=1)).isoformat()
        m = re.search(r"(\d+)\s+dag", text, re.IGNORECASE)
        if m:
            return (date.today() - timedelta(days=int(m.group(1)))).isoformat()

    return ""


def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
    """Return True if a 'next page' link exists on the results page."""
    # Look for pagination links containing the next tstart value
    next_tstart = (current_page + 1) * 20
    # Jobindex next-page link typically has tstart=N in href
    next_link = soup.find(
        "a", href=re.compile(rf"tstart={next_tstart}(&|$)", re.I)
    )
    if next_link:
        return True
    # Fallback: look for a "næste" or ">" navigation link
    nav = soup.find("a", string=re.compile(r"næste|next|»|›|>", re.IGNORECASE))
    return bool(nav)
