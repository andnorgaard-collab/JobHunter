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
    Return job dicts from Novo Nordisk, Novonesis, and Novo Nordisk Fonden.

    Each dict contains at minimum:
        id, title, location, date_posted, url, company
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    all_jobs: list[dict] = []

    # ── Novo Nordisk (SAP SuccessFactors / sitemap) ───────────────────────
    nn_jobs = _fetch_novo_nordisk(session)
    logger.info("Novo Nordisk: %d jobs fetched", len(nn_jobs))
    all_jobs.extend(nn_jobs)

    time.sleep(REQUEST_DELAY)

    # ── Novonesis (own careers portal) ───────────────────────────────────
    nv_jobs = _fetch_novonesis(session)
    logger.info("Novonesis: %d jobs fetched", len(nv_jobs))
    all_jobs.extend(nv_jobs)

    time.sleep(REQUEST_DELAY)

    # ── Novo Nordisk Fonden (Workable API) ───────────────────────────────
    nnf_jobs = _fetch_nnfonden(session)
    logger.info("Novo Nordisk Fonden: %d jobs fetched", len(nnf_jobs))
    all_jobs.extend(nnf_jobs)

    if not all_jobs:
        logger.warning("All scrapers returned 0 jobs.")

    return all_jobs


# --- Novo Nordisk dispatcher ---------------------------------------------

def _fetch_novo_nordisk(session: requests.Session) -> list[dict]:
    for fn, label in [
        (_try_nn_sitemap,  "sitemap"),
        (_try_csb_api,     "CSB API"),
        (_try_sf_restapi,  "SF REST API"),
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


# --- Strategy 0: XML sitemap (works even for pure CSR sites) -------------

def _try_nn_sitemap(session: requests.Session) -> list[dict]:
    """
    Parse the careers sitemap to extract job URLs.
    SF CSB publishes sitemaps for SEO even when the site is CSR-only.
    Job URLs encode title and location in the slug, e.g.:
      /job/Bagsvaerd-Denmark/Operations-Manager_R-123456
    """
    import re

    candidate_sitemaps = [
        f"{CAREERS_BASE}/sitemap.xml",
        f"{CAREERS_BASE}/sitemap-jobs.xml",
        f"{CAREERS_BASE}/sitemap_jobs.xml",
        f"{CAREERS_BASE}/job-sitemap.xml",
    ]

    for sitemap_url in candidate_sitemaps:
        try:
            resp = session.get(sitemap_url, timeout=30,
                               headers={"Accept": "text/xml,application/xml,*/*"})
            if resp.status_code != 200:
                logger.debug("Sitemap %s → HTTP %d", sitemap_url, resp.status_code)
                continue

            logger.info("Sitemap found at %s", sitemap_url)
            soup = BeautifulSoup(resp.content, "xml")

            # Sitemap index → drill into child sitemaps that mention "job"
            for sm in soup.find_all("sitemap"):
                loc_tag = sm.find("loc")
                if loc_tag and "job" in loc_tag.get_text().lower():
                    child = session.get(loc_tag.get_text(strip=True), timeout=30)
                    if child.status_code == 200:
                        child_soup = BeautifulSoup(child.content, "xml")
                        jobs = _parse_sitemap_jobs(child_soup)
                        if jobs:
                            return jobs

            jobs = _parse_sitemap_jobs(soup)
            if jobs:
                return jobs

        except Exception as exc:
            logger.debug("Sitemap error %s: %s", sitemap_url, exc)

    return []


def _parse_sitemap_jobs(soup) -> list[dict]:
    """
    Extract job dicts from a parsed sitemap.

    Novo Nordisk CSB uses the URL format:
        /job/{City}-{Title words}-{StateAbbrev}/{NumericJobId}/
    e.g. /job/Bagsvaerd-Denmark-Operations-Manager-Bags/1234567/

    The first dash-segment contains: City (first word) + Title (middle) +
    StateAbbrev (last word, 3-4 chars truncated from state/city).
    The second segment is the pure numeric job ID.
    """
    import re
    from urllib.parse import unquote

    jobs: list[dict] = []
    for url_tag in soup.find_all("url"):
        loc_tag = url_tag.find("loc")
        if not loc_tag:
            continue
        url = loc_tag.get_text(strip=True)
        if "/job/" not in url.lower():
            continue

        m = re.search(r"/job/([^?#]+)", url, re.IGNORECASE)
        if not m:
            continue
        slug = unquote(m.group(1)).rstrip("/")

        title = ""
        location = ""
        job_id = ""

        # Format A: /job/{city-title-stateabbrev}/{numericId}/   (current NN format)
        slash_parts = slug.split("/")
        if len(slash_parts) == 2 and slash_parts[1].isdigit():
            job_id = slash_parts[1]
            words = slash_parts[0].split("-")
            if len(words) >= 3:
                # First word = city, last word = 3-4 char state/region abbrev, middle = title
                location = words[0].title()
                title = " ".join(words[1:-1]).title()
            elif len(words) == 2:
                location = words[0].title()
                title = words[1].title()
            else:
                title = slash_parts[0].replace("-", " ").title()
        # Format B: /job/{location}/{title}_{jobId}/  (older SF CSB format)
        elif "_" in slug:
            parts = slug.rsplit("_", 1)
            job_id = parts[-1]
            slug_body = parts[0]
            if "/" in slug_body:
                loc_part, title_part = slug_body.split("/", 1)
                location = loc_part.replace("-", " ").title()
                title = title_part.replace("-", " ").title()
            else:
                title = slug_body.replace("-", " ").title()
        else:
            job_id = re.sub(r"[^A-Za-z0-9-]", "", slug)
            title = slug.replace("-", " ").title()

        # Skip entries that have no alphabetic title (just numbers)
        if not title or not re.search(r"[a-zA-Z]", title):
            continue

        # Normalise URL-slug artefacts: "Sr_" → "Senior", trailing/leading underscores
        title = re.sub(r"\bSr_\b", "Senior", title)
        title = title.replace("_", " ").strip()

        lastmod = url_tag.find("lastmod")
        date_posted = lastmod.get_text(strip=True) if lastmod else ""

        jobs.append({
            "id": f"nn_{job_id}",
            "title": title,
            "location": location,
            "date_posted": date_posted,
            "url": url,
        })

    logger.info("Sitemap: parsed %d job URLs", len(jobs))
    if jobs:
        for j in jobs[:8]:
            logger.info("  sample → title=%r  location=%r  url=...%s",
                        j["title"], j["location"], j["url"][-60:])
    return jobs


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
    csb_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/json",
        "Referer": f"{CAREERS_BASE}/",
    }

    # Probe which endpoint responds (try POST, then GET)
    for url in candidate_urls:
        for method in ("post", "get"):
            try:
                kwargs = {"timeout": 20, "headers": csb_headers}
                if method == "post":
                    kwargs["json"] = {"company": SF_COMPANY, "locale": "en_US", "pageNumber": 0, "pageSize": 1}
                else:
                    kwargs["params"] = {"company": SF_COMPANY, "locale": "en_US", "pageNumber": 0, "pageSize": 1}
                probe = getattr(session, method)(url, **kwargs)
                logger.warning("CSB probe %s [%s] → HTTP %d", url, method.upper(), probe.status_code)
                if probe.status_code == 200:
                    probe.json()  # verify JSON
                    working_url = url
                    break
            except Exception as exc:
                logger.warning("CSB probe %s [%s] → error: %s", url, method.upper(), exc)
            time.sleep(0.3)
        if working_url:
            break
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


# --- Strategy 3b: SAP SF public REST API --------------------------------

def _try_sf_restapi(session: requests.Session) -> list[dict]:
    """
    SAP SuccessFactors exposes a public careers REST API at /restapi/v1/jobReqs.
    This is separate from OData and sometimes publicly accessible.
    """
    candidate_urls = [
        f"{SF_BASE}/restapi/v1/jobReqs",
        f"{CAREERS_BASE}/restapi/v1/jobReqs",
    ]

    for base in candidate_urls:
        jobs: list[dict] = []
        page = 1
        while True:
            params = {
                "company": SF_COMPANY,
                "language": "en_US",
                "format": "json",
                "pageSize": 100,
                "pageNumber": page,
                "status": "approved",
            }
            try:
                resp = session.get(base, params=params, timeout=30)
                logger.warning("SF REST %s → HTTP %d", base, resp.status_code)
                if resp.status_code != 200:
                    break
                data = resp.json()
                page_jobs = _parse_json_response(data)
                if not page_jobs:
                    break
                jobs.extend(page_jobs)
                total = data.get("total") or data.get("totalRecords") or 0
                if total and len(jobs) >= int(total):
                    break
                if len(page_jobs) < 100:
                    break
                page += 1
                time.sleep(REQUEST_DELAY)
            except Exception as exc:
                logger.debug("SF REST error %s page %d: %s", base, page, exc)
                break
        if jobs:
            return jobs

    return []


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
        f"{CAREERS_BASE}/jobs",
        f"{CAREERS_BASE}/search-results",
        f"{CAREERS_BASE}/en/search-results",
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
# Novonesis scraper
# =============================================================================
# Novonesis hosts jobs on their own website (novonesis.com), NOT on a
# Workday external career portal.  We try:
#   1. SmartRecruiters public API  (common ATS for life-science companies)
#   2. Greenhouse public API
#   3. Lever public API
#   4. Novonesis website with retry on 429


_NV_BASE = "https://www.novonesis.com"
_NV_SLUG = "novonesis"   # used as company slug for ATS API guesses


def _nv_sitemap(session: requests.Session) -> list[dict]:
    """Try the Novonesis XML sitemap — often not rate-limited like page requests."""
    import re
    from urllib.parse import unquote

    sitemap_urls = [
        f"{_NV_BASE}/sitemap.xml",
        f"{_NV_BASE}/en/sitemap.xml",
    ]
    for sitemap_url in sitemap_urls:
        try:
            resp = session.get(sitemap_url, timeout=20,
                               headers={"Accept": "text/xml,application/xml,*/*"})
            logger.debug("Novonesis sitemap %s → HTTP %d", sitemap_url, resp.status_code)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.content, "xml")
            # Look for a jobs sub-sitemap
            for sm in soup.find_all("sitemap"):
                loc_tag = sm.find("loc")
                if loc_tag and "job" in loc_tag.get_text().lower():
                    child = session.get(loc_tag.get_text(strip=True), timeout=20)
                    if child.status_code == 200:
                        child_soup = BeautifulSoup(child.content, "xml")
                        jobs = _nv_parse_sitemap_jobs(child_soup)
                        if jobs:
                            return jobs
            # Direct job URLs in the sitemap
            jobs = _nv_parse_sitemap_jobs(soup)
            if jobs:
                return jobs
        except Exception as exc:
            logger.debug("Novonesis sitemap error %s: %s", sitemap_url, exc)
    return []


def _nv_parse_sitemap_jobs(soup) -> list[dict]:
    """Extract Novonesis job URLs from a sitemap soup object."""
    import re
    from urllib.parse import unquote
    jobs = []
    for url_tag in soup.find_all("url"):
        loc_tag = url_tag.find("loc")
        if not loc_tag:
            continue
        url = loc_tag.get_text(strip=True)
        # Novonesis job pages contain /jobs/ or /job/ in path
        if not re.search(r"/jobs?/", url, re.IGNORECASE):
            continue
        slug = unquote(url.rstrip("/").split("/")[-1])
        title = slug.replace("-", " ").title()
        lastmod = url_tag.find("lastmod")
        date_posted = lastmod.get_text(strip=True) if lastmod else ""
        if date_posted and "T" in date_posted:
            date_posted = date_posted.split("T")[0]
        jobs.append({
            "id": f"nv_site_{slug[:60]}",
            "title": title,
            "location": "Denmark",  # Novonesis HQ is Denmark
            "date_posted": date_posted,
            "url": url,
        })
    logger.debug("Novonesis sitemap: found %d job URLs", len(jobs))
    return jobs


def _fetch_novonesis(session: requests.Session) -> list[dict]:
    for fn, label in [
        (_nv_smartrecruiters, "SmartRecruiters API"),
        (_nv_greenhouse,      "Greenhouse API"),
        (_nv_lever,           "Lever API"),
        (_nv_sitemap,         "Novonesis sitemap"),
        (_nv_website,         "Novonesis website"),
    ]:
        jobs = fn(session)
        if jobs:
            logger.info("Novonesis via %s: %d jobs", label, len(jobs))
            for j in jobs:
                j["company"] = "Novonesis"
            return jobs

    logger.warning("Novonesis: all strategies returned 0 results.")
    return []


def _nv_smartrecruiters(session: requests.Session) -> list[dict]:
    """SmartRecruiters public job postings API."""
    slugs = [_NV_SLUG, "novonesis-as", "novonesis-group"]
    for slug in slugs:
        try:
            url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
            resp = session.get(url, params={"limit": 100}, timeout=20)
            logger.debug("SmartRecruiters %s → HTTP %d", slug, resp.status_code)
            if resp.status_code != 200:
                continue
            data = resp.json()
            postings = data.get("content") or data.get("postings") or []
            if not postings:
                continue
            jobs = []
            for p in postings:
                job_id = p.get("id") or p.get("refNumber", "")
                title = p.get("name") or p.get("title") or "Unknown"
                loc = p.get("location", {})
                location = f"{loc.get('city','')} {loc.get('country','')}".strip()
                jobs.append({
                    "id": f"sr_{job_id}",
                    "title": str(title),
                    "location": location,
                    "date_posted": p.get("releasedDate") or p.get("createdOn") or "",
                    "url": p.get("ref") or f"https://jobs.smartrecruiters.com/{slug}/{job_id}",
                })
            return jobs
        except Exception as exc:
            logger.debug("SmartRecruiters error (%s): %s", slug, exc)
    return []


def _nv_greenhouse(session: requests.Session) -> list[dict]:
    """Greenhouse public job board API."""
    slugs = [_NV_SLUG, "novonesis", "novozymes"]
    for slug in slugs:
        try:
            url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
            resp = session.get(url, params={"content": "true"}, timeout=20)
            logger.debug("Greenhouse %s → HTTP %d", slug, resp.status_code)
            if resp.status_code != 200:
                continue
            data = resp.json()
            postings = data.get("jobs") or []
            if not postings:
                continue
            jobs = []
            for p in postings:
                job_id = p.get("id", "")
                location = (p.get("location") or {}).get("name") or ""
                jobs.append({
                    "id": f"gh_{job_id}",
                    "title": p.get("title") or "Unknown",
                    "location": location,
                    "date_posted": p.get("updated_at") or "",
                    "url": p.get("absolute_url") or "",
                })
            return jobs
        except Exception as exc:
            logger.debug("Greenhouse error (%s): %s", slug, exc)
    return []


def _nv_lever(session: requests.Session) -> list[dict]:
    """Lever public job postings API."""
    slugs = [_NV_SLUG, "novonesis", "novozymes"]
    for slug in slugs:
        try:
            url = f"https://api.lever.co/v0/postings/{slug}"
            resp = session.get(url, params={"mode": "json", "limit": 250}, timeout=20)
            logger.debug("Lever %s → HTTP %d", slug, resp.status_code)
            if resp.status_code != 200:
                continue
            postings = resp.json()
            if not isinstance(postings, list) or not postings:
                continue
            jobs = []
            for p in postings:
                job_id = p.get("id", "")
                loc_list = p.get("categories", {}).get("location") or p.get("workplaceType") or ""
                jobs.append({
                    "id": f"lv_{job_id}",
                    "title": p.get("text") or "Unknown",
                    "location": str(loc_list),
                    "date_posted": "",
                    "url": p.get("hostedUrl") or p.get("applyUrl") or "",
                })
            return jobs
        except Exception as exc:
            logger.debug("Lever error (%s): %s", slug, exc)
    return []


def _nv_website(session: requests.Session) -> list[dict]:
    """
    Scrape the Novonesis careers page directly.
    Retries up to 3 times on 429 with backoff.
    Looks for __NEXT_DATA__ or embedded JSON.
    """
    import json
    import re

    urls = [
        f"{_NV_BASE}/en/careers/jobs",
        f"{_NV_BASE}/en/careers",
    ]
    browser_headers = {**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*"}

    for url in urls:
        for attempt in range(2):
            try:
                resp = session.get(url, timeout=25, headers=browser_headers)
                if resp.status_code == 429:
                    wait = 3 * (attempt + 1)
                    logger.debug("Novonesis website 429, retry in %ds", wait)
                    time.sleep(wait)
                    continue
                if resp.status_code != 200:
                    break

                soup = BeautifulSoup(resp.text, "html.parser")

                # Next.js SSR data
                nd_tag = soup.find("script", id="__NEXT_DATA__")
                if nd_tag:
                    try:
                        nd = json.loads(nd_tag.string or "")
                        found = _extract_jobs_from_nextdata(nd)
                        if found:
                            logger.info("Novonesis __NEXT_DATA__ jobs: %d", len(found))
                            return found
                    except (json.JSONDecodeError, AttributeError):
                        pass

                # Inline JSON patterns
                for script in soup.find_all("script"):
                    text = script.string or ""
                    for pat in [
                        r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\});",
                        r'"jobs"\s*:\s*(\[.+?\])',
                        r'"postings"\s*:\s*(\[.+?\])',
                    ]:
                        m = re.search(pat, text, re.DOTALL)
                        if m:
                            try:
                                data = json.loads(m.group(1))
                                parsed = _parse_json_response(data) if isinstance(data, (dict, list)) else []
                                if parsed:
                                    return parsed
                            except json.JSONDecodeError:
                                pass
                break  # got a 200, no jobs found → try next URL
            except Exception as exc:
                logger.debug("Novonesis website error (%s attempt %d): %s", url, attempt, exc)
                break

    return []


# Kept for potential future use (Workday-based ATS)
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


# =============================================================================
# Novo Nordisk Fonden – Workable API
# =============================================================================

_NNF_WORKABLE_SLUG = "novonordiskfoundation"


def _fetch_nnfonden(session: requests.Session) -> list[dict]:
    """
    Fetch jobs from Novo Nordisk Fonden via the public Workable widget API.
    No authentication required.
    """
    url = f"https://apply.workable.com/api/v1/widget/accounts/{_NNF_WORKABLE_SLUG}"
    try:
        resp = session.get(url, timeout=20,
                           headers={**HEADERS, "Accept": "application/json"})
        logger.debug("NNF Workable API → HTTP %d", resp.status_code)
        if resp.status_code != 200:
            logger.warning("NNF Workable API → HTTP %d", resp.status_code)
            return []

        data = resp.json()
        postings = data.get("jobs") or []
        if not postings:
            logger.info("NNF Workable API returned 0 postings")
            return []

        jobs: list[dict] = []
        for p in postings:
            job_id = p.get("id") or p.get("shortcode") or ""
            title = p.get("title") or "Unknown"
            loc = p.get("location") or {}
            if isinstance(loc, dict):
                city = loc.get("city") or loc.get("location") or ""
                country = loc.get("country") or ""
                location = f"{city}, {country}".strip(", ") if country else city
            else:
                location = str(loc)
            # NNF is always Copenhagen-based; default when API omits location
            if not location:
                location = "Copenhagen, Denmark"

            date_posted = p.get("created_at") or p.get("published_on") or ""
            # Trim ISO timestamp to date only
            if date_posted and "T" in date_posted:
                date_posted = date_posted.split("T")[0]

            job_url = (
                p.get("url")
                or p.get("application_url")
                or f"https://apply.workable.com/{_NNF_WORKABLE_SLUG}/j/{job_id}/"
            )

            jobs.append({
                "id": f"nnf_{job_id}",
                "title": str(title),
                "location": location,
                "date_posted": date_posted,
                "url": job_url,
                "company": "Novo Nordisk Fonden",
            })

        return jobs

    except Exception as exc:
        logger.warning("NNF Workable API error: %s", exc)
        return []
