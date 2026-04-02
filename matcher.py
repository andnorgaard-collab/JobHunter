"""
matcher.py — Two-dimensional job scoring tailored to a specific profile.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Profile summary
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Background : Clinical supplies (Novo Nordisk RTSM), operations PM,
             process improvement, vendor management, cLEAN 2-star,
             hospitality/entrepreneurship.
Goal       : First people-leadership role (team lead / ops manager /
             process improvement manager) in pharma/biotech or
             regulated industry.  Copenhagen area, hybrid.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scoring model
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Each job receives two independent scores 0–10:

  competency_score  – how well the role matches existing skills
  preference_score  – how well it aligns with career goals

Combined weighted score (used for sorting and classification):
  combined = 0.35 * competency_score + 0.65 * preference_score

Classification:
  combined >= 6.5  →  "Strong match"
  combined >= 3.0  →  "Possible match"
  combined <  3.0  →  dropped

Exclusion patterns override everything.
"""

import re
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data class for a scored job
# ---------------------------------------------------------------------------

@dataclass
class ScoredJob:
    job: dict
    competency_score: float
    preference_score: float
    competency_reasons: list[str] = field(default_factory=list)
    preference_reasons: list[str] = field(default_factory=list)

    @property
    def combined(self) -> float:
        return round(0.35 * self.competency_score + 0.65 * self.preference_score, 2)

    def as_dict(self) -> dict:
        return {
            **self.job,
            "_competency_score": round(self.competency_score, 1),
            "_preference_score": round(self.preference_score, 1),
            "_combined": self.combined,
            "_competency_reasons": self.competency_reasons,
            "_preference_reasons": self.preference_reasons,
        }


# ---------------------------------------------------------------------------
# Exclusion patterns — any match → score 0, job dropped
# ---------------------------------------------------------------------------

EXCLUSION_PATTERNS: list[str] = [
    # Pure science / lab
    r"\bscientist\b",
    r"\bresearch(?:er)?\b",
    r"\bpostdoc\b",
    r"\bphd\s+stud",
    r"\bchemist\b",
    r"\bbiologist\b",
    r"\bbioinformatics\b",
    r"\bpharmacologist\b",
    r"\bmedical\s+writer\b",
    r"\bclinical\s+trial\s+(?:associate|specialist|coordinator)\b",
    r"\blab(?:oratory)?\s+(?:technician|analyst|specialist)\b",
    # IT / software (not digital ops)
    r"\bsoftware\s+(?:engineer|developer|architect)\b",
    r"\bfull[\s-]?stack\b",
    r"\bback[\s-]?end\b",
    r"\bfront[\s-]?end\b",
    r"\bdevops\b",
    r"\bdata\s+(?:scientist|engineer)\b",
    r"\bmachine\s+learning\b",
    r"\bartificial\s+intelligence\b",
    r"\bcybersecurity\b",
    # Finance / accounting
    r"\baccountan",
    r"\bfinancial\s+(?:analyst|controller|advisor)\b",
    r"\bcontroller\b",
    r"\bpayroll\b",
    r"\baudit(?:or)?\b",
    r"\btax\s+(?:manager|specialist|analyst)\b",
]


# ---------------------------------------------------------------------------
# Preference scoring — career goal alignment
# ---------------------------------------------------------------------------
#
# Goal priority order:
#   1. People leadership + process/ops content           → 9-10
#   2. Pure people leadership in ops/supply context      → 8-9
#   3. Process improvement manager (no direct reports)   → 6-7
#   4. Generic manager/lead in relevant domain           → 4-6
#   5. Specialist / individual contributor               → 1-3
# ---------------------------------------------------------------------------

# (pattern, points, description)  — applied to job title only
_PREF_TITLE: list[tuple[str, float, str]] = [
    # Tier 1: People leadership titles (the primary goal)
    (r"\bteam\s+lead\b",           7.0, "team lead title"),
    (r"\bteamleder\b",             7.0, "teamleder title (DK)"),
    (r"\bpeople\s+lead\b",         7.0, "people lead title"),
    (r"\bpeople\s+manager\b",      7.0, "people manager title"),
    (r"\bgroup\s+(?:lead|manager)\b", 6.5, "group lead/manager"),
    # Tier 1b: Ops / process leadership titles
    (r"\boperations\s+manager\b",  7.5, "operations manager"),
    (r"\boperations\s+lead\b",     7.5, "operations lead"),
    (r"\boperations\s+director\b", 7.0, "operations director"),
    (r"\bprocess\s+(?:improvement\s+)?manager\b", 6.5, "process manager"),
    (r"\bprocess\s+(?:improvement\s+)?lead\b",    6.5, "process lead"),
    # Tier 2: Supply/clinical management (directly relevant to background)
    (r"\bsupply\s+(?:chain\s+)?manager\b",     6.5, "supply manager"),
    (r"\bclinical\s+(?:supply|supplies)\s+manager\b", 7.5, "clinical supplies manager"),
    (r"\bclinical\s+operations\s+manager\b",   7.5, "clinical operations manager"),
    (r"\bproject\s+manager\b",                 4.5, "project manager"),
    (r"\bprogram(?:me)?\s+manager\b",          4.5, "programme manager"),
    # Tier 3: Relevant domain managers (qualified "manager" combos)
    (r"\bmanufacturing\s+manager\b",   5.5, "manufacturing manager"),
    (r"\bproduction\s+manager\b",      5.5, "production manager"),
    (r"\bsite\s+manager\b",            5.0, "site manager"),
    (r"\bplanning\s+manager\b",        5.0, "planning manager"),
    (r"\blogistics\s+manager\b",       4.5, "logistics manager"),
    (r"\bwarehouse\s+manager\b",       4.0, "warehouse manager"),
    (r"\bquality\s+manager\b",         4.5, "quality manager"),
    # Tier 4: Process/CI content (good content, possibly no direct reports)
    (r"\bcontinuous\s+improvement\b",  5.0, "continuous improvement"),
    (r"\bprocess\s+improvement\b",     5.0, "process improvement"),
    (r"\bprocess\s+optimis[ae]tion\b", 5.0, "process optimisation"),
    (r"\blean\b",                      4.5, "LEAN"),
    (r"\bvalue\s+stream\b",            4.0, "value stream"),
    (r"\bkaizen\b",                    4.0, "kaizen"),
    (r"\bsix\s+sigma\b",               4.0, "six sigma"),
    # Tier 5: Generic lead/head signals (low weight without context)
    (r"\bhead\s+of\b",                 4.0, "head of"),
    (r"\bdirector\b",                  3.5, "director"),
]

# Bonus: leadership title combined with relevant domain in same title
_PREF_LEADERSHIP_BONUS: list[tuple[str, str, float]] = [
    # (leadership pattern, domain pattern, bonus)
    (r"\b(?:team\s+lead|teamleder|people\s+lead|people\s+manager|manager|lead)\b",
     r"\b(?:operations?|supply|clinical|process|manufacturing|production)\b",
     2.0),
]

# Penalty: individual-contributor signals with no leadership angle
_PREF_IC_PENALTY: list[tuple[str, float]] = [
    (r"\bspecialist\b",   -1.5),
    (r"\bexpert\b",       -1.0),
    (r"\banalyst\b",      -1.5),
    (r"\bconsultant\b",   -0.5),
    (r"\bassociate\b",    -1.0),
    (r"\bcoordinator\b",  -1.0),
]

PREF_MAX = 10.0


# ---------------------------------------------------------------------------
# Competency scoring — background/skills match
# ---------------------------------------------------------------------------
#
# Background: clinical supplies, operations PM, process improvement,
#             vendor management, LEAN, pharma/biotech
# ---------------------------------------------------------------------------

# Applied to full "title + location" haystack
_COMP_SIGNALS: list[tuple[str, float, str]] = [
    # Core background — clinical
    (r"\bclinical\s+suppli(?:es|y)\b",      4.0, "clinical supplies (core)"),
    (r"\bRTSM\b",                            4.5, "RTSM (exact current role)"),
    (r"\bIRT\b",                             4.0, "IRT (clinical tech)"),
    (r"\bclinical\s+trial\b",               3.0, "clinical trial context"),
    (r"\bclinical\s+operations?\b",         3.0, "clinical operations"),
    # Operations / supply chain
    (r"\boperations?\b",                    2.5, "operations"),
    (r"\bsupply\s+chain\b",                 3.0, "supply chain"),
    (r"\bsupply\b",                         1.5, "supply"),
    (r"\bmanufacturing\b",                  2.0, "manufacturing"),
    (r"\bproduction\b",                     1.5, "production"),
    (r"\blogistics\b",                      1.5, "logistics"),
    # Process improvement / LEAN
    (r"\bprocess\s+improvement\b",          3.5, "process improvement"),
    (r"\bprocess\s+optimis[ae]tion\b",      3.5, "process optimisation"),
    (r"\bcontinuous\s+improvement\b",       3.5, "continuous improvement"),
    (r"\blean\b",                           3.0, "LEAN"),
    (r"\bvalue\s+stream\b",                 2.5, "value stream"),
    (r"\bkaizen\b",                         2.5, "kaizen"),
    (r"\bsix\s+sigma\b",                    2.0, "six sigma"),
    # Project / programme management
    (r"\bproject\s+management\b",           2.5, "project management"),
    (r"\bprogram(?:me)?\s+management\b",    2.5, "programme management"),
    (r"\bPMO\b",                            2.0, "PMO"),
    # Vendor / supplier
    (r"\bvendor\b",                         2.0, "vendor"),
    (r"\bsupplier\s+management\b",          2.5, "supplier management"),
    (r"\bprocurement\b",                    1.5, "procurement"),
    # Digital / tools implementation
    (r"\bdigital\s+(?:transformation|implementation|tool)\b", 2.0, "digital impl."),
    (r"\bsystem\s+implementation\b",        1.5, "system implementation"),
    # Industry context
    (r"\bpharma(?:ceutical)?\b",            2.0, "pharma"),
    (r"\bbiotech\b",                        2.0, "biotech"),
    (r"\blife\s+science\b",                 1.5, "life science"),
    (r"\bGMP\b",                            1.5, "GMP"),
    (r"\bregulat(?:ed|ory)\b",              1.0, "regulated industry"),
    # People leadership (aspiring — a match here means the role fits the goal)
    (r"\bteam\s+lead\b",                    2.0, "team lead (leadership goal)"),
    (r"\bpeople\s+(?:lead|manager)\b",      2.0, "people lead (leadership goal)"),
]

COMP_MAX = 10.0


# ---------------------------------------------------------------------------
# Location scoring — added to BOTH scores
# ---------------------------------------------------------------------------

_LOCATION_PRIORITY: list[str] = [
    r"\bdenmark\b", r"\bdanmark\b",
    r"\bcopenhagen\b", r"\bk[oø]benhavn\b",
    r"\bbagsv[aæ]rd\b", r"\bm[åa]l[øo]v\b", r"\bkalundborg\b",
    r"\bh[oø]rsholm\b", r"\bgen(?:tofte|tof)\b",
]
_LOCATION_SECONDARY: list[str] = [
    r"\bhybrid\b", r"\bremote\b",
    r"\beurope\b", r"\beu\b",
    r"\bsweden\b", r"\bnorway\b", r"\bgermany\b", r"\bnetherlands\b",
]

LOCATION_BONUS_PRIORITY  = 1.5   # added to both scores
LOCATION_BONUS_SECONDARY = 0.5


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

STRONG_THRESHOLD   = 6.5   # combined score
POSSIBLE_THRESHOLD = 3.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_jobs(jobs: list[dict]) -> dict[str, list[dict]]:
    """
    Score and classify jobs.  Returns::

        {"strong": [...], "possible": [...]}

    Each job dict is annotated with:
        _competency_score, _preference_score, _combined,
        _competency_reasons, _preference_reasons
    """
    strong:   list[dict] = []
    possible: list[dict] = []

    for job in jobs:
        scored = _score_job(job)
        if scored is None:
            continue
        d = scored.as_dict()
        if scored.combined >= STRONG_THRESHOLD:
            strong.append(d)
        elif scored.combined >= POSSIBLE_THRESHOLD:
            possible.append(d)

    strong.sort(key=lambda j: j["_combined"], reverse=True)
    possible.sort(key=lambda j: j["_combined"], reverse=True)

    logger.info("Classified: %d strong, %d possible", len(strong), len(possible))
    return {"strong": strong, "possible": possible}


# ---------------------------------------------------------------------------
# Internal scoring
# ---------------------------------------------------------------------------

def _score_job(job: dict) -> ScoredJob | None:
    title    = job.get("title", "").lower()
    location = job.get("location", "").lower()
    haystack = f"{title} {location}"

    # ── Exclusions ──────────────────────────────────────────────────────────
    for pat in EXCLUSION_PATTERNS:
        if re.search(pat, haystack, re.IGNORECASE):
            return None

    # ── Preference score ────────────────────────────────────────────────────
    pref = 0.0
    pref_reasons: list[str] = []

    for pat, pts, label in _PREF_TITLE:
        if re.search(pat, title, re.IGNORECASE):
            pref += pts
            pref_reasons.append(f"+{pts:.1f} {label}")

    for lead_pat, domain_pat, bonus in _PREF_LEADERSHIP_BONUS:
        if re.search(lead_pat, title, re.IGNORECASE) and re.search(domain_pat, title, re.IGNORECASE):
            pref += bonus
            pref_reasons.append(f"+{bonus:.1f} leadership×domain bonus")

    for pat, penalty in _PREF_IC_PENALTY:
        if re.search(pat, title, re.IGNORECASE):
            pref += penalty   # penalty is negative
            pref_reasons.append(f"{penalty:.1f} IC penalty")

    # ── Competency score ────────────────────────────────────────────────────
    comp = 0.0
    comp_reasons: list[str] = []

    for pat, pts, label in _COMP_SIGNALS:
        if re.search(pat, haystack, re.IGNORECASE):
            comp += pts
            comp_reasons.append(f"+{pts:.1f} {label}")

    # ── Location bonus (both scores) ─────────────────────────────────────
    loc_bonus = 0.0
    for pat in _LOCATION_PRIORITY:
        if re.search(pat, location, re.IGNORECASE):
            loc_bonus = LOCATION_BONUS_PRIORITY
            pref_reasons.append(f"+{loc_bonus} DK/CPH location")
            comp_reasons.append(f"+{loc_bonus} DK/CPH location")
            break
    else:
        for pat in _LOCATION_SECONDARY:
            if re.search(pat, location, re.IGNORECASE):
                loc_bonus = LOCATION_BONUS_SECONDARY
                pref_reasons.append(f"+{loc_bonus} EU/hybrid location")
                comp_reasons.append(f"+{loc_bonus} EU/hybrid location")
                break

    pref = min(pref + loc_bonus, PREF_MAX)
    comp = min(comp + loc_bonus, COMP_MAX)

    # Drop if both scores are negligible
    combined = 0.35 * comp + 0.65 * pref
    if combined < POSSIBLE_THRESHOLD:
        return None

    return ScoredJob(
        job=job,
        competency_score=round(comp, 1),
        preference_score=round(pref, 1),
        competency_reasons=comp_reasons,
        preference_reasons=pref_reasons,
    )
