"""
scraper.py — Fetches job listings from Novo Nordisk and Novonesis.

Novo Nordisk  → SAP SuccessFactors (Career Site Builder)
Novonesis     → Workday

Every job dict returned includes a 'company' field so the email can
group results by employer.
"""

import logging
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# --- Constants -----------------------------------------------------------

SF_COMPANY = "novonordisk"
SF_BASE = "https://career2.successfactors.eu"
CAREERS_BASE = "https://careers.novonordisk.com"

# Polite delay between paginated requests (seconds)
REQUEST_DELAY = 2.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9,da;q=0.8",
}


# --- Public entry point --------------------------------------------------

def fetch_all_jobs() -> list[dict]:
    """
    Return job dicts from both Novo Nordisk and Novonesis.

    Each dict contains at minimum:
        id, title, location, date_posted, url, company
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    jobs: list[dict] = []

    all_jobs: list[dict] = []

    # ── Novo Nordisk (SAP SuccessFactors) ────────────────────────────────
    nn_jobs = _fetch_novo_nordisk(session)
    logger.info("Novo Nordisk: %d jobs fetched", len(nn_jobs))
    all_jobs.extend(nn_jobs)

    time.sleep(REQUEST_DELAY)

    # ── Novonesis (Workday) ───────────────────────────────────────────────
    nv_jobs = _fetch_novonesis(session)
    logger.info("Novonesis: %d jobs fetched", len(nv_jobs))
    all_jobs.extend(nv_jobs)

    if not all_jobs:
        logger.warning("Both scrapers returned 0 jobs.")

    return all_jobs


# --- Novo Nordisk dispatcher ---------------------------------------------

def _fetch_novo_nordisk(session: requests.Session) -> list[dict]:
    for fn, label in [
        (_try_csb_api,     "CSB API"),
        (_try_xml_feed,    "XML feed"),
        (_try_odata,       "OData"),
        (_try_html_scrape, "HTML scrape"),
    ]:
        jobs = fn(session)
        if jobs:
            logger.info("Novo Nordisk via %s: %d jobs", label, len(jobs))
            for j in jobs:
                j["company"] = "Novo Nordisk"
            return jobs
    logger.warning("Novo Nordisk: all strategies failed.")
    return []


# --- Strategy 1: Career Site Builder REST API ----------------------------

def _try_csb_api(session: requests.Session, max_pages: int = 40) -> list[dict]:
    """
    POST-based search API used by SuccessFactors Career Site Builder.
    Handles pagination automatically.
    """
    jobs: list[dict] = []
    page = 0
    page_size = 100

    # Some CSB instances expose the search under /careers/api/search or /api/jobs
    candidate_urls = [
        f"{CAREERS_BASE}/api/jobs",
        f"{CAREERS_BASE}/careers/api/search",
        f"{SF_BASE}/services/recruiting/v1/jobSearch",
    ]

    working_url: Optional[str] = None

    # Probe which endpoint responds
    for url in candidate_urls:
        try:
            probe = session.post(
                url,
                json={"company": SF_COMPANY, "locale": "en_US", "pageNumber": 0, "pageSize": 1},
                timeout=20,
            )
            logger.warning("CSB probe %s → HTTP %d", url, probe.status_code)
            if probe.status_code == 200:
                probe.json()  # verify JSON
                working_url = url
                break
        except Exception as exc:
            logger.warning("CSB probe %s → error: %s", url, exc)
        time.sleep(0.5)

    if not working_url:
        return []

    while page < max_pages:
        try:
            resp = session.post(
                working_url,
                json={
                    "company": SF_COMPANY,
                    "locale": "en_US",
                    "country": "ALL",
                    "pageNumber": page,
                    "pageSize": page_size,
                    "deviceType": "desktop",
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            page_jobs = _parse_json_response(data)
            if not page_jobs:
                break

            jobs.extend(page_jobs)

            total = (
                data.get("total")
                or data.get("totalRecords")
                or data.get("count")
                or 0
            )
            if total and len(jobs) >= int(total):
                break
            if len(page_jobs) < page_size:
                break

            page += 1
            time.sleep(REQUEST_DELAY)

        except requests.HTTPError as exc:
            logger.warning("CSB API HTTP error page %d: %s", page, exc)
            break
        except Exception as exc:
            logger.warning("CSB API error page %d: %s", page, exc)
            break

    return jobs


def _parse_json_response(data: dict | list) -> list[dict]:
    """Normalise a JSON response from any SuccessFactors endpoint."""
    if isinstance(data, list):
        raw_list = data
    else:
        raw_list = (
            data.get("jobs")
            or data.get("jobRequisitions")
            or data.get("results")
            or data.get("d", {}).get("results", [])  # OData envelope
            or []
        )

    return [n for n in (_normalise_job(r) for r in raw_list) if n]


def _normalise_job(raw: dict) -> Optional[dict]:
    job_id = (
        raw.get("jobId")
        or raw.get("id")
        or raw.get("requisitionId")
        or raw.get("jobReqId")
        or raw.get("JobReqId")
    )
    if not job_id:
        return None

    title = (
        raw.get("jobTitle")
        or raw.get("title")
        or raw.get("name")
        or raw.get("JobTitle")
        or "Unknown"
    )

    # Location can be a string, list, or nested dict
    loc_raw = (
        raw.get("jobLocation")
        or raw.get("location")
        or raw.get("locations")
        or raw.get("city")
        or ""
    )
    if isinstance(loc_raw, list):
        location = ", ".join(
            (l.get("label") or l.get("name") or str(l)) if isinstance(l, dict) else str(l)
            for l in loc_raw
        )
    elif isinstance(loc_raw, dict):
        location = loc_raw.get("label") or loc_raw.get("name") or str(loc_raw)
    else:
        location = str(loc_raw)

    date_posted = (
        raw.get("postingDate")
        or raw.get("postedDate")
        or raw.get("startDate")
        or raw.get("datePosted")
        or raw.get("PostedDate")
        or ""
    )

    return {
        "id": str(job_id),
        "title": str(title),
        "location": location,
        "date_posted": str(date_posted),
        "url": _job_url(str(job_id)),
    }


def _job_url(job_id: str) -> str:
    return (
        f"{SF_BASE}/sfcareer/jobreqcareerpvt"
        f"?jobId={job_id}&company={SF_COMPANY}&username=&site=external"
    )


# --- Strategy 2: XML job feed -------------------------------------------

def _try_xml_feed(session: requests.Session) -> list[dict]:
    """
    Many SuccessFactors instances publish a public XML job feed at
    /sfcareer/joblist or /sfcareer/joblist.  We iterate through pages.
    """
    jobs: list[dict] = []
    page = 1

    feed_urls = [
        f"{SF_BASE}/sfcareer/joblist?company={SF_COMPANY}&lang=en_US",
        f"{CAREERS_BASE}/sfcareer/joblist?company={SF_COMPANY}&lang=en_US",
    ]

    for base_url in feed_urls:
        jobs = _fetch_xml_pages(session, base_url)
        if jobs:
            return jobs

    return []


def _fetch_xml_pages(session: requests.Session, base_url: str, max_pages: int = 20) -> list[dict]:
    jobs: list[dict] = []
    for page in range(1, max_pages + 1):
        url = f"{base_url}&start={(page - 1) * 100}&end={page * 100}"
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                break

            soup = BeautifulSoup(resp.content, "xml")
            items = soup.find_all("job") or soup.find_all("item") or soup.find_all("Job")
            if not items:
                break

            for item in items:
                job = _parse_xml_job(item)
                if job:
                    jobs.append(job)

            if len(items) < 100:
                break
            time.sleep(REQUEST_DELAY)

        except Exception as exc:
            logger.debug("XML feed error at %s page %d: %s", base_url, page, exc)
            break

    return jobs


def _parse_xml_job(item) -> Optional[dict]:
    def text(tag: str) -> str:
        node = item.find(tag)
        return node.get_text(strip=True) if node else ""

    job_id = text("jobId") or text("id") or text("JobId") or text("requisitionId")
    if not job_id:
        return None

    return {
        "id": job_id,
        "title": text("jobTitle") or text("title") or text("JobTitle") or "Unknown",
        "location": text("jobLocation") or text("location") or text("city") or "",
        "date_posted": text("postingDate") or text("datePosted") or "",
        "url": _job_url(job_id),
    }


# --- Strategy 3: OData v2 public endpoint --------------------------------

def _try_odata(session: requests.Session) -> list[dict]:
    """
    SuccessFactors exposes an OData v2 endpoint for job requisitions.
    This is read-only and sometimes publicly accessible.
    """
    url = (
        f"{SF_BASE}/odata/v2/JobRequisition"
        f"?$top=100&$format=json"
        f"&$select=JobReqId,JobTitle,City,Country,PostedDate"
        f"&$filter=company_externalCode eq '{SF_COMPANY}'"
    )

    jobs: list[dict] = []
    skip = 0

    while True:
        try:
            paged = url + f"&$skip={skip}"
            resp = session.get(paged, timeout=30)
            if resp.status_code != 200:
                break

            data = resp.json()
            results = data.get("d", {}).get("results", [])
            if not results:
                break

            for r in results:
                job_id = r.get("JobReqId")
                if not job_id:
                    continue
                jobs.append({
                    "id": str(job_id),
                    "title": r.get("JobTitle") or "Unknown",
                    "location": f"{r.get('City', '')} {r.get('Country', '')}".strip(),
                    "date_posted": r.get("PostedDate") or "",
                    "url": _job_url(str(job_id)),
                })

            if len(results) < 100:
                break
            skip += 100
            time.sleep(REQUEST_DELAY)

        except Exception as exc:
            logger.debug("OData error (skip=%d): %s", skip, exc)
            break

    return jobs


# --- Next.js __NEXT_DATA__ extractor -----------------------------------

def _extract_jobs_from_nextdata(data: dict) -> list[dict]:
    """
    Walk the Next.js __NEXT_DATA__ tree looking for job arrays.
    The structure varies between CSB versions; we try common paths.
    """
    import json

    def _walk(node, depth=0):
        if depth > 8:
            return []
        if isinstance(node, list):
            # Check if this looks like a job list
            if node and isinstance(node[0], dict):
                results = [_normalise_job(item) for item in node]
                results = [r for r in results if r]
                if len(results) >= 3:   # at least 3 valid jobs → it's a job list
                    return results
            for item in node:
                found = _walk(item, depth + 1)
                if found:
                    return found
        elif isinstance(node, dict):
            # Common keys where jobs live in CSB __NEXT_DATA__
            for key in ("jobs", "jobRequisitions", "results", "jobPostings", "data"):
                if key in node:
                    found = _walk(node[key], depth + 1)
                    if found:
                        return found
            for v in node.values():
                if isinstance(v, (dict, list)):
                    found = _walk(v, depth + 1)
                    if found:
                        return found
        return []

    return _walk(data)


# --- Strategy 4: HTML scrape fallback ------------------------------------

def _try_html_scrape(session: requests.Session) -> list[dict]:
    """
    Last resort: fetch the careers page and look for any embedded JSON
    or <script> tags with job data.  Will not work if the page is a pure
    client-side SPA with no server-side rendering.
    """
    import json
    import re

    urls_to_try = [
        f"{CAREERS_BASE}/",
        f"{CAREERS_BASE}/careers",
        f"https://www.novonordisk.com/careers/job-listings.html",
    ]

    for url in urls_to_try:
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            # Next.js server-side data (__NEXT_DATA__)
            next_script = soup.find("script", id="__NEXT_DATA__")
            if next_script:
                try:
                    nd = json.loads(next_script.string or "")
                    logger.info("Found __NEXT_DATA__ at %s – scanning for jobs", url)
                    found = _extract_jobs_from_nextdata(nd)
                    if found:
                        return found
                except (json.JSONDecodeError, AttributeError):
                    pass

            # Look for JSON-LD job postings
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    payload = json.loads(script.string or "")
                    if isinstance(payload, list):
                        items = payload
                    else:
                        items = [payload]
                    jobs = []
                    for item in items:
                        if item.get("@type") in ("JobPosting", "jobPosting"):
                            job_id = (
                                item.get("identifier", {}).get("value")
                                or item.get("url", "").split("=")[-1]
                            )
                            if not job_id:
                                continue
                            loc = item.get("jobLocation", {})
                            if isinstance(loc, dict):
                                address = loc.get("address", {})
                                location = (
                                    address.get("addressLocality", "")
                                    + " "
                                    + address.get("addressCountry", "")
                                ).strip()
                            else:
                                location = str(loc)
                            jobs.append({
                                "id": str(job_id),
                                "title": item.get("title") or "Unknown",
                                "location": location,
                                "date_posted": item.get("datePosted") or "",
                                "url": item.get("url") or _job_url(str(job_id)),
                            })
                    if jobs:
                        return jobs
                except (json.JSONDecodeError, AttributeError):
                    pass

            # Look for inline window.__INITIAL_STATE__ or similar
            for script in soup.find_all("script"):
                text = script.string or ""
                for pattern in [
                    r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\});",
                    r"window\.jobData\s*=\s*(\[.+?\]);",
                ]:
                    match = re.search(pattern, text, re.DOTALL)
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            parsed = _parse_json_response(data)
                            if parsed:
                                return parsed
                        except json.JSONDecodeError:
                            pass

            time.sleep(REQUEST_DELAY)

        except Exception as exc:
            logger.debug("HTML scrape error for %s: %s", url, exc)

    return []


# =============================================================================
# Novonesis – Workday scraper
# =============================================================================

# Workday tenant + site combinations to try.
# The merger kept some Novozymes infrastructure; we probe common patterns.
_WD_TENANTS_PRIMARY = ["novonesis", "novozymes"]

# Fallback site names if auto-discovery fails
_WD_SITE_GUESSES = [
    "Novonesis", "NovonesisCareers", "Novonesis_Careers",
    "Novonesis_External", "Novonesis_ExternalCareerSite",
    "External", "novonesis",
    "Novozymes", "Novozymes_External", "novozymes",
]

# Workday uses numbered data-centres: wd1 … wd5
_WD_HOSTS = ["wd3", "wd1", "wd5"]


def _wd_discover_site(session: requests.Session, host: str, tenant: str) -> Optional[str]:
    """
    GET the Workday tenant root and extract the career-site name from the redirect URL.
    Workday redirects / → /en-US/{SiteName}/ which reveals the correct site name.
    """
    import re
    url = f"https://{tenant}.{host}.myworkdayjobs.com/"
    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        # The final URL after redirects contains the site name
        final = resp.url
        match = re.search(r"/en-[A-Z]{2}/([^/?#]+)", final)
        if match:
            site = match.group(1)
            logger.info("Workday auto-discovered site: %s.%s → %s", tenant, host, site)
            return site
    except Exception as exc:
        logger.debug("Workday site discovery failed (%s.%s): %s", tenant, host, exc)
    return None


def _fetch_novonesis(session: requests.Session) -> list[dict]:
    """
    Fetch jobs from Novonesis via Workday's public CXS API.
    First auto-discovers the career-site name, then falls back to guesses.
    """
    for host in _WD_HOSTS:
        for tenant in _WD_TENANTS_PRIMARY:
            # Try auto-discovery first
            discovered = _wd_discover_site(session, host, tenant)
            sites_to_try = (
                [discovered] + _WD_SITE_GUESSES if discovered
                else _WD_SITE_GUESSES
            )

            for site in sites_to_try:
                if not site:
                    continue
                jobs = _wd_fetch_all_pages(session, host, tenant, site)
                if jobs:
                    logger.info(
                        "Novonesis via Workday (%s.%s / %s): %d jobs",
                        tenant, host, site, len(jobs),
                    )
                    for j in jobs:
                        j["company"] = "Novonesis"
                return jobs
        time.sleep(REQUEST_DELAY)

    logger.warning("Novonesis: all Workday tenants/sites returned 0 results.")
    return []


def _wd_fetch_all_pages(
    session: requests.Session,
    host: str,
    tenant: str,
    site: str,
    page_size: int = 100,
    max_pages: int = 20,
) -> list[dict]:
    base_url = (
        f"https://{tenant}.{host}.myworkdayjobs.com"
        f"/wday/cxs/{tenant}/{site}/jobs"
    )
    jobs: list[dict] = []
    offset = 0

    for _ in range(max_pages):
        try:
            resp = session.post(
                base_url,
                json={"appliedFacets": {}, "limit": page_size, "offset": offset, "searchText": ""},
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning("Workday %s/%s → HTTP %d", tenant, site, resp.status_code)
                break
            data = resp.json()

            postings = data.get("jobPostings") or []
            if not postings:
                break

            for p in postings:
                job = _wd_normalise(p, tenant, host, site)
                if job:
                    jobs.append(job)

            total = data.get("total", 0)
            offset += len(postings)
            if offset >= total or len(postings) < page_size:
                break

            time.sleep(REQUEST_DELAY)

        except Exception as exc:
            logger.debug("Workday error (%s/%s offset=%d): %s", tenant, site, offset, exc)
            break

    return jobs


def _wd_normalise(raw: dict, tenant: str, host: str, site: str) -> Optional[dict]:
    """Normalise a Workday jobPosting dict."""
    external_path = raw.get("externalPath", "")
    # externalPath looks like /job/Bagsvaerd-Denmark/Operations-Manager_R-12345
    # We use it as a stable ID.
    job_id = external_path.strip("/").replace("/", "_") or raw.get("bulletFields", [""])[0]
    if not job_id:
        return None

    title = raw.get("title") or "Unknown"
    location = raw.get("locationsText") or raw.get("jobLocation") or ""

    # Workday gives relative dates like "Posted 3 Days Ago"
    date_posted = raw.get("postedOn") or ""

    url = (
        f"https://{tenant}.{host}.myworkdayjobs.com"
        f"/en-US/{site}{external_path}"
    )

    return {
        "id": f"wd_{job_id}",
        "title": str(title),
        "location": str(location),
        "date_posted": str(date_posted),
        "url": url,
    }
