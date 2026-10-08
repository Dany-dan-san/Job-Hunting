"""Shared vacancy identity, role targeting and evidence-based ranking.

Scores are transparent ordering rules, not probabilities of recruitment.
No network access or user-data writes occur when this module is imported.
"""
from __future__ import annotations

import csv
import json
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

VERSION = "2026-10-03-v2.1"


def project_root() -> Path:
    configured = os.environ.get("JOB_SCANNER_ROOT")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parent.parent


def clean(value) -> str:
    if value is None or str(value).lower() in {"nan", "none"}:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def fold(value) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", clean(value).casefold()) if not unicodedata.combining(c))


def first(row, *fields) -> str:
    return next((clean(row.get(k, "")) for k in fields if clean(row.get(k, ""))), "")


def normalize_url(value) -> str:
    value = clean(value)
    if not value:
        return ""
    p = urlsplit(value if "://" in value else "https://" + value)
    # Preserve vacancy-identifying query arguments. Remove only known tracking fields.
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), re.sub(r"/{2,}", "/", p.path).rstrip("/") or "/", urlencode(sorted(query)), ""))


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or not path.stat().st_size:
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def atomic_csv(path: Path, rows, fields) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader(); w.writerows(rows)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


DEFAULT_PROFILE = {
    "family_weights": {"ANALYTICS_INSIGHTS": 60, "BUSINESS_PROCESS": 52,
        "ANALYTICAL_SUPPORT": 52, "DATA_SCIENCE_MODELLING": 42,
        "DATA_ENGINEERING_DWH": 22, "SOFTWARE_BACKEND_FULLSTACK": 18,
        "ADJACENT_TECHNICAL": 15, "OTHER": 0},
    "known_skills": ["Python", "R", "SQL", "Statistics", "ML", "Excel", "Power BI", "FastAPI"],
    "languages": ["English", "French"],
    "basic_skills": ["SQL"],
    "professional_analytical_years": 0,
    "refresh_days": 7,
    "partial_retry_hours": 24,
    "blocked_retry_hours": 72,
}


def profile() -> dict:
    p = Path(__file__).with_name("job_profile.json")
    result = dict(DEFAULT_PROFILE)
    if p.exists():
        with p.open(encoding="utf-8") as f: result.update(json.load(f))
    return result


def role_family(row) -> str:
    """Occupation evidence wins over tools, domain tags, and personal notes."""
    title = fold(first(row, "Display_Title", "Job_Title_Page", "Job.Title", "Job Title", "Job_Title", "Job_Title_Input"))
    subtype = fold(first(row, "Position.Subtype", "Position Subtype", "Position_Subtype", "Position_Subtype_Input"))
    root = fold(first(row, "Position.Type", "Position Type", "Root Position", "Position_Type_Input"))
    def has(pattern): return bool(re.search(pattern, title))
    if has(r"\b(?:data engineer|analytics engineer|data architect|dwh|etl developer|data warehouse developer)\b") or subtype in {"data engineer", "data architect", "data/database developer"}:
        return "DATA_ENGINEERING_DWH"
    if has(r"\b(?:data scientist|machine learning (?:engineer|scientist)|statistical model[el]|quantitative scientist|ai engineer)\b") or subtype in {"data scientist", "ai/ml engineer", "statistical/quantitative scientist"}:
        return "DATA_SCIENCE_MODELLING"
    if subtype in {"software engineer", "general software developer", "backend developer", "full-stack developer", "frontend developer", "mobile developer", "enterprise applications developer", "embedded/systems developer"} or root == "developer" or has(r"\b(?:software engineer|developer|programator\w*|vyvojar\w*|ruby|rails|full[- ]?stack|backend)\b"):
        return "SOFTWARE_BACKEND_FULLSTACK"
    if has(r"\b(?:research|analytical|analytics|data) assistant\b|\b(?:vyzkumn\w*|analytick\w*) asistent\w*\b") or subtype in {"research/analytical assistant", "data/analytics support", "master data clerk"}:
        return "ANALYTICAL_SUPPORT"
    if has(r"\b(?:business|process|procesni|operations?|operational|supply chain|logistics) (?:analyst|analytik)\b") or subtype in {"business/process analyst", "operations/supply chain analyst", "sales/commercial analyst"}:
        return "BUSINESS_PROCESS"
    if has(r"\b(?:data analyst|datov\w* analyt\w*|research analyst|market researcher|market research|market intelligence|consumer insights?|customer insights?|pricing analyst|marketing analyst|product analyst|data quality|data governance|data steward)\b") or subtype in {"data/bi analyst", "data/analytics specialist", "data/analytics consultant", "data governance/quality specialist", "marketing/customer insights analyst", "market/consumer intelligence analyst", "commercial/pricing analyst", "product analyst", "quantitative/statistical analyst", "economic/policy analyst", "market/user researcher", "customer experience consultant"}:
        return "ANALYTICS_INSIGHTS"
    if has(r"\b(?:quantitative|statistical|risk model|credit risk model)\b"):
        return "DATA_SCIENCE_MODELLING"
    if subtype in {"it/systems analyst", "business/process consultant", "data/analytics internship"}:
        return "BUSINESS_PROCESS"
    if has(r"\b(?:qa|tester|technical solutions analyst|systems analyst|application support)\b"):
        return "ADJACENT_TECHNICAL"
    return "OTHER"


FAMILY_TRACKS = {
    "ANALYTICS_INSIGHTS": "Analytics, Research & Insights",
    "BUSINESS_PROCESS": "Business, Operations & Process Analysis",
    "ANALYTICAL_SUPPORT": "Research & Analytical Support",
    "DATA_SCIENCE_MODELLING": "Data Science & Modelling",
    "DATA_ENGINEERING_DWH": "Data Engineering & Automation",
    "SOFTWARE_BACKEND_FULLSTACK": "Software Development",
    "ADJACENT_TECHNICAL": "Adjacent Technical", "OTHER": "Other",
}


def senior_signal(value, *, classified=False) -> bool:
    """Recognize leadership titles and explicit source seniority labels.

    'Executive' alone is ambiguous in a title (e.g. a junior BI executive),
    so it is authoritative only in a classified seniority field.
    """
    value = fold(value)
    if re.search(r"\b(?:senior|principal|staff|director|chief|head|lead|leader)\b", value):
        return True
    return classified and bool(re.search(r"\b(?:executive|manager|leadership)\b", value))


def provisional_assessment(row) -> dict[str, str]:
    family = role_family(row); score = profile()["family_weights"][family]
    title = fold(first(row, "Job.Title", "Job Title", "Job_Title"))
    seniority = fold(first(row, "Seniority")) + " " + title
    cautions = ["Advertisement requirements not yet verified"]
    reasons = [FAMILY_TRACKS[family]]
    junior = bool(re.search(r"\bjunior|\bentry|\bgraduate|\btrainee|\babsolvent|\bresearch assistant", seniority))
    root = fold(first(row, "Position.Type", "Position Type", "Root Position", "Position_Type_Input"))
    senior = (senior_signal(title) or senior_signal(first(row, "Seniority"), classified=True)
              or root == "manager")
    if junior: score += 20; reasons.append("Entry-level signal")
    if senior and not junior: score -= 40; cautions.append("Senior/lead requirement needs verification")
    elif senior: score -= 10; cautions.append("Mixed seniority needs verification")
    if re.search(r"\bmedior|\bmid[- ]?level", seniority): score -= 10
    tech = fold(first(row, "Technology", "Technology Tags"))
    # Small tie-breakers only. R must be a delimited technology token, never R&D.
    for token in re.split(r"[,;|]", tech):
        if token.strip() in {"python", "sql", "r", "excel"}: score += 1; reasons.append(token.strip())
    location = fold(first(row, "Location"))
    if location and not re.search(r"\bprague|\bpraha", location): score -= 5; cautions.append("Prague eligibility unresolved")
    priority = "High" if score >= 70 else "Good" if score >= 50 else "Exploratory" if score >= 20 else "Deprioritized"
    if senior and not junior: priority = "Senior/Leadership Stretch" if family != "OTHER" else "Deprioritized"
    return {"Role_Family": family, "Primary.Track": FAMILY_TRACKS[family], "Provisional.Shortlisting.Score": str(max(0, score)),
            "Fit.Score": str(max(0, score)), "Review.Priority": priority,
            "Fit.Reasons": "; ".join(reasons), "Caution.Flags": "; ".join(cautions), "Scoring.Version": VERSION}


SCORE_FIELDS = ["Role_Family", "Shortlisting_Fit", "Capability_Fit", "Suggested_Decision",
    "Blocking_Reasons", "Verification_Needed", "Scoring_Reasons", "Scoring_Version", "Shortlisting_Disadvantages"]


def score_evidence(row) -> dict[str, str]:
    family = role_family(row); cfg = profile(); score = cfg["family_weights"][family]
    blocks = []; verify = []; disadvantages = []; reasons = [FAMILY_TRACKS[family]]
    def add(items, text):
        if text not in items: items.append(text)
    status = first(row, "Current_Status", "Auto_Current").upper()
    if status == "CLOSED": add(blocks, "Vacancy closed")
    elif status in {"LIKELY_CLOSED", "REQUEST_FAILED", "HTTP_ERROR", "UNKNOWN", ""}: add(verify, "Vacancy availability")
    fetched = first(row, "Extraction_Date")
    try:
        dt = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - dt).total_seconds() > cfg["refresh_days"] * 86400:
            add(verify, "Vacancy evidence needs refresh")
    except ValueError: add(verify, "Vacancy fetch date")
    extraction = first(row, "Extraction_Status").upper()
    completeness = first(row, "Content_Completeness").upper()
    if extraction != "OK" or completeness in {"TEASER", "PARTIAL", "UNAVAILABLE"} or not first(row, "Evidence_Version"):
        add(verify, "Full advertisement evidence")
    if first(row, "Auto_Czech_Requirement") == "REQUIRED": add(blocks, "Mandatory Czech")
    if "CONFLICTING" in first(row, "Auto_Review_Flags"): add(verify, "Contradictory requirement evidence")
    english_only = first(row, "Auto_English_Only")
    if english_only == "YES": score += 10; reasons.append("English-only workplace confirmed")
    elif english_only == "NO": add(blocks, "English alone insufficient")
    else: add(verify, "English-only compatibility")
    if first(row, "Auto_Driving_Licence") == "REQUIRED": add(blocks, "Mandatory driving licence")
    if first(row, "Auto_Mandatory_Language_Gaps"): add(blocks, "Mandatory language: " + first(row, "Auto_Mandatory_Language_Gaps"))
    if first(row, "Auto_Prague_Eligibility") == "NO": add(blocks, "Cannot work from Prague")
    elif first(row, "Auto_Prague_Eligibility") != "YES": add(verify, "Prague/work-mode eligibility")
    grad = first(row, "Auto_Graduate_Eligible")
    seniority = first(row, "Auto_Seniority")
    if grad == "YES" or seniority == "JUNIOR": score += 20; reasons.append("Graduate/junior opening")
    elif seniority in {"SENIOR", "MID_SENIOR"}: score -= 40; disadvantages.append("Senior-level opening")
    elif seniority in {"MID", "JUNIOR_MID"}: score -= 15; disadvantages.append("Professional seniority disadvantage")
    else: add(verify, "Entry-level accessibility")
    years = first(row, "Auto_Years_Experience_Min")
    if years:
        try:
            n = float(years)
            academic = first(row, "Auto_Academic_Experience_Accepted") == "YES"
            if n > cfg["professional_analytical_years"] and not academic:
                score -= 35 if n >= 3 else 20
                disadvantages.append(f"Requires {years} professional years")
            elif academic: reasons.append("Academic/project experience accepted")
        except ValueError: add(verify, "Experience requirement")
    gap = first(row, "Auto_Mandatory_Skill_Gaps")
    if gap: score -= 20; disadvantages.append("Mandatory stack gap: " + gap)
    if first(row, "Auto_Advanced_Skill_Requirements"): score -= 15; disadvantages.append("Advanced skill level needs validation")
    if first(row, "Auto_Degree_Conflict") == "YES": add(blocks, "Mandatory degree mismatch")
    elif first(row, "Auto_Degree_Conflict") == "UNCLEAR": add(verify, "Mandatory education background")
    if first(row, "Auto_Domain_Experience") == "REQUIRED": score -= 15; disadvantages.append("Required domain experience needs validation")
    if family in {"SOFTWARE_BACKEND_FULLSTACK", "DATA_ENGINEERING_DWH"}: disadvantages.append("Secondary technical career route")
    if family == "OTHER": add(blocks, "Outside target role families")
    matched = sum(first(row, "Auto_" + k) in {"REQUIRED", "PREFERRED", "MENTIONED"} for k in ["Python", "SQL", "R", "ML", "Statistics"])
    capability = min(100, 20 + matched * 12 + (15 if family in {"ANALYTICS_INSIGHTS", "BUSINESS_PROCESS", "ANALYTICAL_SUPPORT"} else 0))
    if gap: capability -= 20
    if first(row, "Auto_Advanced_Skill_Requirements"): capability -= 15
    if not first(row, "Evidence_Version"): capability_text = ""
    else: capability_text = str(max(0, capability))
    score = max(0, min(100, score))
    suggested = "REJECT" if blocks else "HOLD" if verify else "STRETCH" if disadvantages or score < 70 else "APPLY"
    reasons.extend(disadvantages)
    return dict(zip(SCORE_FIELDS, [family, str(score), capability_text, suggested,
        " | ".join(blocks), " | ".join(verify), " | ".join(reasons), VERSION, " | ".join(disadvantages)]))


def effective_decision(row) -> tuple[str, str]:
    """Keep historical review untouched; compute actionable decision separately."""
    human = first(row, "Review_Decision", "Decision")
    if human not in {"APPLY", "STRETCH"}: return human, ""
    override = first(row, "Review_Override_Reason")
    notes = first(row, "Review_Notes", "Notes")
    if first(row, "Review_Language", "Language_Assessment") == "Conflict": return "REJECT", "Human language conflict"
    if first(row, "Review_Seniority", "Seniority_Assessment") == "Too senior": return "REJECT", "Human seniority conflict"
    if override and len(override) >= 20 and notes:
        return human, "Explicit reviewer correction: " + override
    assessed = score_evidence(row)
    blocks = assessed["Blocking_Reasons"]
    if blocks: return "REJECT", blocks
    unresolved = assessed["Verification_Needed"].split(" | ") if assessed["Verification_Needed"] else []
    # A factual human review may resolve uncertainties, but must document its basis.
    if first(row, "Review_Language", "Language_Assessment") == "Compatible" and notes:
        unresolved = [x for x in unresolved if x != "English-only compatibility"]
    if first(row, "Review_Seniority", "Seniority_Assessment") == "Accessible" and notes:
        unresolved = [x for x in unresolved if x != "Entry-level accessibility"]
    if unresolved: return "HOLD", " | ".join(unresolved)
    if (assessed["Shortlisting_Disadvantages"] or int(assessed["Shortlisting_Fit"]) < 70) and human == "APPLY":
        return "STRETCH", assessed["Scoring_Reasons"]
    if first(row, "Review_Fit", "Fit") == "Weak": return "STRETCH", "Weak shortlisting fit"
    return human, ""
