"""
scraper.py — Fetches job listings for 5 monitored companies.

Strategy per company:
  Novo Nordisk        → SAP SuccessFactors CSB sitemap (careers.novonordisk.com)
  Novo Nordisk Fonden → Workable widget API
  Genmab              → Workday CXS API (genmab.wd3.myworkdayjobs.com)
  Lundbeck            → SAP SuccessFactors CSB sitemap (jobs.lundbeck)
  Novonesis           → SmartRecruiters → Workday → sitemap → website
"""

import logging
import re
import time
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9,da;q=0.8",
}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch_all_jobs() -> list[dict]:
    session = requests.Session()
    session.headers.update(HEADERS)

    all_jobs: list[dict] = []

    for fn, company in [
        (_fetch_novo_nordisk,  "Novo Nordisk"),
        (_fetch_nnfonden,      "Novo Nordisk Fonden"),
        (_fetch_genmab,        "Genmab"),
        (_fetch_lundbeck,      "Lundbeck"),
        (_fetch_novonesis,     "Novonesis"),
    ]:
        jobs = fn(session)
        for j in jobs:
            j["company"] = company
        logger.info("%s: %d jobs fetched", company, len(jobs))
        all_jobs.extend(jobs)
        time.sleep(REQUEST_DELAY)

    return all_jobs


# ---------------------------------------------------------------------------
# Novo Nordisk — SAP SuccessFactors CSB sitemap
# ---------------------------------------------------------------------------

_NN_BASE = "https://careers.novonordisk.com"

def _fetch_novo_nordisk(session: requests.Session) -> list[dict]:
    for sitemap_url in [
        f"{_NN_BASE}/sitemap.xml",
        f"{_NN_BASE}/sitemap-jobs.xml",
    ]:
        try:
            resp = session.get(sitemap_url, timeout=30,
                               headers={"Accept": "text/xml,application/xml,*/*"})
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.content, "xml")
            # Drill into child sitemaps that mention jobs
            for sm in soup.find_all("sitemap"):
                loc = sm.find("loc")
                if loc and "job" in loc.get_text().lower():
                    child = session.get(loc.get_text(strip=True), timeout=30)
                    if child.status_code == 200:
                        jobs = _parse_csb_sitemap(BeautifulSoup(child.content, "xml"))
                        if jobs:
                            return jobs
            jobs = _parse_csb_sitemap(soup)
            if jobs:
                return jobs
        except Exception as exc:
            logger.debug("NN sitemap error: %s", exc)
    logger.warning("Novo Nordisk: sitemap strategy failed")
    return []


# ---------------------------------------------------------------------------
# Lundbeck — SAP SuccessFactors CSB sitemap (same format as NN)
# ---------------------------------------------------------------------------

_LB_BASE = "https://jobs.lundbeck"

def _fetch_lundbeck(session: requests.Session) -> list[dict]:
    for sitemap_url in [
        f"{_LB_BASE}/sitemap.xml",
        f"{_LB_BASE}/sitemap-jobs.xml",
    ]:
        try:
            resp = session.get(sitemap_url, timeout=30,
                               headers={"Accept": "text/xml,application/xml,*/*"})
            if resp.status_code != 200:
                logger.debug("Lundbeck sitemap %s → HTTP %d", sitemap_url, resp.status_code)
                continue
            soup = BeautifulSoup(resp.content, "xml")
            for sm in soup.find_all("sitemap"):
                loc = sm.find("loc")
                if loc and "job" in loc.get_text().lower():
                    child = session.get(loc.get_text(strip=True), timeout=30)
                    if child.status_code == 200:
                        jobs = _parse_csb_sitemap(BeautifulSoup(child.content, "xml"))
                        if jobs:
                            return jobs
            jobs = _parse_csb_sitemap(soup)
            if jobs:
                return jobs
        except Exception as exc:
            logger.debug("Lundbeck sitemap error: %s", exc)
    logger.warning("Lundbeck: sitemap strategy failed")
    return []


# ---------------------------------------------------------------------------
# Shared: SAP SuccessFactors CSB sitemap parser
#
# URL format: /job/{City}-{Title-words}-{StateAbbrev}/{NumericId}/
# ---------------------------------------------------------------------------

def _parse_csb_sitemap(soup) -> list[dict]:
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

        title = location = job_id = ""

        slash_parts = slug.split("/")
        if len(slash_parts) == 2 and slash_parts[1].isdigit():
            job_id = slash_parts[1]
            words = slash_parts[0].split("-")
            if len(words) >= 3:
                location = words[0].title()
                title = " ".join(words[1:-1]).title()
            elif len(words) == 2:
                location = words[0].title()
                title = words[1].title()
            else:
                title = slash_parts[0].replace("-", " ").title()
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

        if not title or not re.search(r"[a-zA-Z]", title):
            continue

        title = re.sub(r"\bSr_\b", "Senior", title)
        title = title.replace("_", " ").strip()

        lastmod = url_tag.find("lastmod")
        date_posted = lastmod.get_text(strip=True) if lastmod else ""

        # Derive a prefix from the URL base
        prefix = "lb" if "lundbeck" in url.lower() else "nn"
        jobs.append({
            "id": f"{prefix}_{job_id}",
            "title": title,
            "location": location,
            "date_posted": date_posted,
            "url": url,
        })

    logger.info("CSB sitemap: parsed %d jobs", len(jobs))
    return jobs


# ---------------------------------------------------------------------------
# Genmab — Workday CXS API
# ---------------------------------------------------------------------------

_GENMAB_WD_BASE = "https://genmab.wd3.myworkdayjobs.com"
_GENMAB_TENANT  = "genmab"
_GENMAB_BOARD   = "Genmab_Careers_Site"

def _fetch_genmab(session: requests.Session) -> list[dict]:
    api_url = f"{_GENMAB_WD_BASE}/wday/cxs/{_GENMAB_TENANT}/{_GENMAB_BOARD}/jobs"
    wd_headers = {
        "Content-Type": "application/json",
        "Origin": _GENMAB_WD_BASE,
        "Referer": f"{_GENMAB_WD_BASE}/{_GENMAB_BOARD}",
    }
    return _fetch_workday(session, api_url, wd_headers, _GENMAB_WD_BASE, "genmab")


def _fetch_workday(
    session: requests.Session,
    api_url: str,
    extra_headers: dict,
    base_url: str,
    id_prefix: str,
    limit: int = 20,
) -> list[dict]:
    """Generic Workday CXS API fetcher with pagination."""
    jobs: list[dict] = []
    offset = 0

    while True:
        payload = {
            "limit": limit,
            "offset": offset,
            "searchText": "",
            "locations": [],
        }
        try:
            resp = session.post(
                api_url,
                json=payload,
                headers=extra_headers,
                timeout=30,
            )
        except requests.RequestException as exc:
            logger.warning("Workday request error: %s", exc)
            break

        if resp.status_code != 200:
            logger.warning("Workday API %s → HTTP %d", api_url, resp.status_code)
            break

        try:
            data = resp.json()
        except Exception:
            logger.warning("Workday API returned non-JSON")
            break

        postings = data.get("jobPostings") or []
        total = data.get("total", 0)

        for p in postings:
            ext_path = p.get("externalPath", "")
            title = p.get("title", "").strip()
            location = p.get("locationsText", "").strip()
            posted_on = p.get("postedOn", "")

            # Normalise date: "Posted 3 Days Ago" → skip; ISO date kept
            date_posted = posted_on if re.match(r"\d{4}-\d{2}-\d{2}", posted_on) else ""

            # Build ID from the numeric portion of the externalPath
            id_match = re.search(r"(\d{5,})", ext_path)
            job_id = f"{id_prefix}_{id_match.group(1)}" if id_match else f"{id_prefix}_{abs(hash(ext_path))%10_000_000}"

            job_url = base_url + ext_path if ext_path.startswith("/") else ext_path

            if not title:
                continue

            jobs.append({
                "id": job_id,
                "title": title,
                "location": location,
                "date_posted": date_posted,
                "url": job_url,
            })

        offset += limit
        if offset >= total or not postings:
            break
        time.sleep(0.5)

    logger.info("Workday API %s: %d jobs", api_url, len(jobs))
    return jobs


# ---------------------------------------------------------------------------
# Novo Nordisk Fonden — Workable widget API
# ---------------------------------------------------------------------------

_NNF_WORKABLE_SLUG = "novonordiskfoundation"
_NNF_WORKABLE_URL  = f"https://apply.workable.com/api/v1/widget/accounts/{_NNF_WORKABLE_SLUG}"

def _fetch_nnfonden(session: requests.Session) -> list[dict]:
    try:
        resp = session.get(_NNF_WORKABLE_URL, timeout=20,
                           params={"details": "true"},
                           headers={"Referer": "https://novonordiskfonden.dk/"})
        if resp.status_code != 200:
            logger.warning("NNF Workable → HTTP %d", resp.status_code)
            return []
        data = resp.json()
    except Exception as exc:
        logger.warning("NNF Workable error: %s", exc)
        return []

    jobs = []
    for p in data.get("jobs", []):
        location = ""
        loc = p.get("location") or {}
        if isinstance(loc, dict):
            city    = loc.get("city", "")
            country = loc.get("country", "")
            location = ", ".join(filter(None, [city, country]))
        elif isinstance(loc, str):
            location = loc

        if not location:
            location = "Copenhagen, Denmark"

        job_id = str(p.get("id") or p.get("shortcode") or "")
        jobs.append({
            "id": f"nnf_{job_id}",
            "title": p.get("title", "").strip(),
            "location": location,
            "date_posted": (p.get("published_on") or "")[:10],
            "url": f"https://apply.workable.com/{_NNF_WORKABLE_SLUG}/j/{job_id}/",
        })

    return jobs


# ---------------------------------------------------------------------------
# Novonesis — SmartRecruiters → Workday → sitemap → website
# ---------------------------------------------------------------------------

_NV_BASE = "https://www.novonesis.com"

def _fetch_novonesis(session: requests.Session) -> list[dict]:
    for fn, label in [
        (_nv_smartrecruiters, "SmartRecruiters"),
        (_nv_workday,         "Workday"),
        (_nv_sitemap,         "sitemap"),
        (_nv_website,         "website"),
    ]:
        try:
            jobs = fn(session)
        except Exception as exc:
            logger.debug("Novonesis %s error: %s", label, exc)
            jobs = []
        if jobs:
            logger.info("Novonesis via %s: %d jobs", label, len(jobs))
            return jobs
        logger.debug("Novonesis %s: 0 jobs", label)
    logger.warning("Novonesis: all strategies returned 0 jobs")
    return []


def _nv_smartrecruiters(session: requests.Session) -> list[dict]:
    # Try both current (novonesis) and legacy (novozymes) SmartRecruiters slugs
    for slug in ("novonesis", "novozymes"):
        resp = session.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
            params={"limit": 100},
            timeout=20,
        )
        if resp.status_code == 200:
            break
    if resp.status_code != 200:
        return []
    data = resp.json()
    jobs = []
    for p in data.get("content", []):
        loc = p.get("location", {}) or {}
        location = ", ".join(filter(None, [loc.get("city", ""), loc.get("country", "")]))
        jobs.append({
            "id": f"nv_sr_{p.get('id', '')}",
            "title": p.get("name", "").strip(),
            "location": location,
            "date_posted": (p.get("releasedDate") or "")[:10],
            "url": p.get("ref", ""),
        })
    return jobs


def _nv_workday(session: requests.Session) -> list[dict]:
    """Try common Novonesis/Novozymes Workday tenant names."""
    for tenant, server, board in [
        ("novonesis",  "wd3", "Novonesis_External"),
        ("novonesis",  "wd5", "Novonesis_External"),
        ("novozymes",  "wd3", "Novozymes_External"),
    ]:
        base = f"https://{tenant}.{server}.myworkdayjobs.com"
        api_url = f"{base}/wday/cxs/{tenant}/{board}/jobs"
        hdrs = {"Content-Type": "application/json", "Origin": base, "Referer": base}
        try:
            jobs = _fetch_workday(session, api_url, hdrs, base, "nv")
            if jobs:
                return jobs
        except Exception:
            pass
        time.sleep(0.5)
    return []


def _nv_sitemap(session: requests.Session) -> list[dict]:
    for sitemap_url in [f"{_NV_BASE}/sitemap.xml", f"{_NV_BASE}/en/sitemap.xml"]:
        try:
            resp = session.get(sitemap_url, timeout=20,
                               headers={"Accept": "text/xml,application/xml,*/*"})
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.content, "xml")
            jobs = []
            for url_tag in soup.find_all("url"):
                loc = url_tag.find("loc")
                if not loc:
                    continue
                url = loc.get_text(strip=True)
                if "/careers/jobs/" not in url and "/job/" not in url:
                    continue
                slug = url.rstrip("/").split("/")[-1]
                title = slug.replace("-", " ").title()
                if not title or not re.search(r"[a-zA-Z]", title):
                    continue
                jobs.append({
                    "id": f"nv_sm_{abs(hash(url)) % 10_000_000}",
                    "title": title,
                    "location": "Denmark",
                    "date_posted": "",
                    "url": url,
                })
            if jobs:
                return jobs
        except Exception as exc:
            logger.debug("Novonesis sitemap %s: %s", sitemap_url, exc)
    return []


def _nv_website(session: requests.Session) -> list[dict]:
    """Scrape the Novonesis careers listing page directly."""
    jobs_url = f"{_NV_BASE}/en/careers/jobs"
    for attempt in range(2):
        if attempt > 0:
            time.sleep(3 * attempt)
        try:
            resp = session.get(jobs_url, timeout=25)
            if resp.status_code == 429:
                logger.warning("Novonesis website: 429 rate-limited (attempt %d)", attempt + 1)
                continue
            if resp.status_code != 200:
                logger.warning("Novonesis website: HTTP %d", resp.status_code)
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            jobs = []
            for a in soup.find_all("a", href=re.compile(r"/careers/jobs/")):
                href = a["href"]
                title = a.get_text(strip=True)
                if not title or not re.search(r"[a-zA-Z]{3,}", title):
                    continue
                url = href if href.startswith("http") else _NV_BASE + href
                jobs.append({
                    "id": f"nv_web_{abs(hash(url)) % 10_000_000}",
                    "title": title,
                    "location": "Denmark",
                    "date_posted": "",
                    "url": url,
                })
            return jobs
        except requests.RequestException as exc:
            logger.debug("Novonesis website attempt %d: %s", attempt + 1, exc)
    return []
