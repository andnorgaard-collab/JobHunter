"""
matcher.py — Keyword matching and relevance scoring for job listings.

Scoring model
─────────────
Each job receives a numeric score:

  ≥ 10  → "Strong match"   (highly relevant title + good location)
  3–9   → "Possible match" (partial keyword hit or non-priority location)
  ≤ 2   → filtered out

Exclusion keywords override everything: a job that matches an exclusion
pattern is dropped regardless of its positive score.
"""

import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword definitions
# ---------------------------------------------------------------------------

# Tier-1: leadership / process-improvement / ops roles we definitely want
STRONG_KEYWORDS: list[tuple[str, int]] = [
    # People / team leadership
    (r"\bteam\s+lead\b", 8),
    (r"\bteamleder\b", 8),          # Danish
    (r"\bpeople\s+lead\b", 8),
    (r"\bpeople\s+manager\b", 8),
    # Operations leadership
    (r"\boperations\s+manager\b", 9),
    (r"\boperations\s+lead\b", 9),
    (r"\boperations\s+director\b", 8),
    # Process / continuous improvement
    (r"\bprocess\s+improvement\b", 9),
    (r"\bprocess\s+manager\b", 9),
    (r"\bprocess\s+optimization\b", 9),
    (r"\bprocess\s+optimisation\b", 9),
    (r"\bcontinuous\s+improvement\b", 9),
    (r"\blean\b", 7),
    (r"\bsix\s+sigma\b", 6),
    (r"\bkaizen\b", 6),
    (r"\bvalue\s+stream\b", 6),
]

# Tier-2: "manager" qualified by a relevant domain
QUALIFIED_MANAGER_KEYWORDS: list[tuple[str, str, int]] = [
    # (qualifier pattern, manager pattern, score)
    (r"\bsupply\b",      r"\bmanager\b", 7),
    (r"\bclinical\b",    r"\bmanager\b", 7),
    (r"\boperations?\b", r"\bmanager\b", 7),
    (r"\bprocess\b",     r"\bmanager\b", 7),
    (r"\bproduction\b",  r"\bmanager\b", 6),
    (r"\bmanufacturing\b", r"\bmanager\b", 6),
    (r"\bquality\b",     r"\bmanager\b", 5),
    (r"\bproject\b",     r"\bmanager\b", 5),
    (r"\bprogram(?:me)?\b", r"\bmanager\b", 5),
    (r"\bsite\b",        r"\bmanager\b", 5),
    (r"\bplanning\b",    r"\bmanager\b", 5),
    (r"\blogistics\b",   r"\bmanager\b", 5),
    (r"\bwarehouse\b",   r"\bmanager\b", 5),
]

# Tier-3: generic "manager" alone (low weight – needs location boost to surface)
WEAK_KEYWORDS: list[tuple[str, int]] = [
    (r"\bmanager\b", 3),
    (r"\bdirector\b", 3),
    (r"\blead\b", 2),
    (r"\bhead\s+of\b", 4),
    (r"\bsenior\b", 1),        # only adds to score, never triggers alone
]

# ---------------------------------------------------------------------------
# Exclusion patterns – if ANY matches, the job is dropped
# ---------------------------------------------------------------------------
EXCLUSION_PATTERNS: list[str] = [
    # Science / lab
    r"\bscientist\b",
    r"\bresearcher?\b",
    r"\b(?:research\s+(?:and\s+)?development|R&D)\b",
    r"\blab(?:oratory)?\b",
    r"\bpostdoc\b",
    r"\bphd\s+stud",
    r"\bchemist\b",
    r"\bbiologist\b",
    r"\bpharmacologist\b",
    r"\bclinical\s+trial\s+(?:associate|specialist|coordinator)",
    r"\bmedical\s+writer\b",
    r"\bbioinformatics\b",
    # IT / software
    r"\bsoftware\s+engineer\b",
    r"\bsoftware\s+developer\b",
    r"\bfull[\s-]?stack\b",
    r"\bback[\s-]?end\b",
    r"\bfront[\s-]?end\b",
    r"\bdevops\b",
    r"\bdata\s+(?:scientist|engineer|analyst)\b",
    r"\bmachine\s+learning\b",
    r"\bartificial\s+intelligence\b",
    r"\bcybersecurity\b",
    r"\bcloud\s+architect\b",
    # Finance / accounting
    r"\baccountan",
    r"\bfinancial\s+(?:analyst|controller|advisor)\b",
    r"\bcontroller\b",
    r"\btreasur",
    r"\boutright\s+purchas",
    r"\bpayroll\b",
    r"\baudit(?:or)?\b",
    r"\btax\s+(?:manager|specialist|analyst)\b",
]

# ---------------------------------------------------------------------------
# Location scoring
# ---------------------------------------------------------------------------
PRIORITY_LOCATION_PATTERNS: list[str] = [
    r"\bdenmark\b",
    r"\bdanmark\b",
    r"\bcopenhagen\b",
    r"\bk[oø]benhavn\b",
    r"\bbagsv[aæ]rd\b",   # Novo Nordisk HQ
    r"\bm[åa]l[øo]v\b",
    r"\bkalundborg\b",     # major Novo Nordisk manufacturing site
]

SECONDARY_LOCATION_PATTERNS: list[str] = [
    r"\bremote\b",
    r"\bhybrid\b",
    r"\beurope\b",
    r"\beu\b",
    r"\bgermany\b",
    r"\bsweden\b",
    r"\bnorway\b",
    r"\bnetherlands\b",
    r"\buk\b",
    r"\bunited\s+kingdom\b",
]

LOCATION_SCORE_PRIORITY = 3
LOCATION_SCORE_SECONDARY = 1

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

STRONG_MATCH_THRESHOLD = 10
POSSIBLE_MATCH_THRESHOLD = 3


def classify_jobs(jobs: list[dict]) -> dict[str, list[dict]]:
    """
    Score and classify a list of job dicts.

    Returns:
        {
            "strong":   [...],   # score >= STRONG_MATCH_THRESHOLD
            "possible": [...],   # POSSIBLE_MATCH_THRESHOLD <= score < STRONG_MATCH_THRESHOLD
        }
    """
    strong: list[dict] = []
    possible: list[dict] = []

    for job in jobs:
        score, reasons = _score_job(job)
        if score < POSSIBLE_MATCH_THRESHOLD:
            continue

        annotated = {**job, "_score": score, "_reasons": reasons}
        if score >= STRONG_MATCH_THRESHOLD:
            strong.append(annotated)
        else:
            possible.append(annotated)

    # Sort each group by score descending
    strong.sort(key=lambda j: j["_score"], reverse=True)
    possible.sort(key=lambda j: j["_score"], reverse=True)

    logger.info("Classified: %d strong, %d possible", len(strong), len(possible))
    return {"strong": strong, "possible": possible}


def _score_job(job: dict) -> tuple[int, list[str]]:
    """Return (total_score, list_of_matching_reasons)."""
    haystack = f"{job.get('title', '')} {job.get('location', '')}".lower()
    title_only = job.get("title", "").lower()
    location_only = job.get("location", "").lower()

    # --- Exclusions first ---
    for pattern in EXCLUSION_PATTERNS:
        if re.search(pattern, haystack, re.IGNORECASE):
            return 0, [f"excluded:{pattern}"]

    score = 0
    reasons: list[str] = []

    # --- Strong keyword hits (title only for accuracy) ---
    for pattern, pts in STRONG_KEYWORDS:
        if re.search(pattern, title_only, re.IGNORECASE):
            score += pts
            reasons.append(f"+{pts}:{pattern}")

    # --- Qualified manager combos ---
    for qual_pat, mgr_pat, pts in QUALIFIED_MANAGER_KEYWORDS:
        if re.search(qual_pat, title_only, re.IGNORECASE) and re.search(
            mgr_pat, title_only, re.IGNORECASE
        ):
            score += pts
            reasons.append(f"+{pts}:{qual_pat}+{mgr_pat}")

    # --- Weak keywords (only add if we already have some signal) ---
    if score > 0:
        for pattern, pts in WEAK_KEYWORDS:
            if re.search(pattern, title_only, re.IGNORECASE):
                score += pts
                reasons.append(f"+{pts}:{pattern}")

    # If no signal yet, check full haystack for weak match
    if score == 0:
        for pattern, pts in WEAK_KEYWORDS:
            if re.search(pattern, title_only, re.IGNORECASE):
                score += pts
                reasons.append(f"+{pts}:{pattern}(weak)")

    if score == 0:
        return 0, []

    # --- Location boost ---
    for pattern in PRIORITY_LOCATION_PATTERNS:
        if re.search(pattern, location_only, re.IGNORECASE):
            score += LOCATION_SCORE_PRIORITY
            reasons.append(f"+{LOCATION_SCORE_PRIORITY}:priority_location")
            break
    else:
        for pattern in SECONDARY_LOCATION_PATTERNS:
            if re.search(pattern, location_only, re.IGNORECASE):
                score += LOCATION_SCORE_SECONDARY
                reasons.append(f"+{LOCATION_SCORE_SECONDARY}:secondary_location")
                break

    return score, reasons
