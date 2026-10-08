from __future__ import annotations

import argparse
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd


# ============================================================
# PURPOSE
# ============================================================
#
# Deterministic evidence extraction layer for job prospects.
#
# INPUT:
#   data/07_prospects/prospects_enriched_raw.csv
#
# OUTPUT:
#   data/07_prospects/prospect_evidence.csv
#
# This script intentionally DOES NOT:
#   - modify prospects_enriched_raw.csv
#   - assign a fit score
#   - decide APPLY / REJECT
#   - overwrite human review fields
#
# It only converts observable vacancy evidence into explicit,
# reviewable categorical fields plus supporting text snippets.
# ============================================================

EVIDENCE_VERSION = "08_v1.1"

TEXT_FIELDS = [
    "Job_Title_Page",
    "Job_Title_Input",
    "Seniority_Detail",
    "Seniority_Input",
    "Languages",
    "Skills",
    "Requirements",
    "Description",
    "Remote_Mode",
    "Employment_Type",
    "Location_Detail",
    "Status_Evidence",
]

OUTPUT_FIELDS = [
    "ID",
    "Input_URL",
    "Source",
    "Evidence_Version",
    "Evidence_Extracted_At",
    "Auto_Czech_Requirement",
    "Auto_Czech_Evidence",
    "Auto_English_Acceptable",
    "Auto_English_Evidence",
    "Auto_Years_Experience_Min",
    "Auto_Years_Experience_Evidence",
    "Auto_Seniority",
    "Auto_Seniority_Evidence",
    "Auto_Python",
    "Auto_Python_Evidence",
    "Auto_SQL",
    "Auto_SQL_Evidence",
    "Auto_ML",
    "Auto_ML_Evidence",
    "Auto_Statistics",
    "Auto_Statistics_Evidence",
    "Auto_R",
    "Auto_R_Evidence",
    "Auto_Remote",
    "Auto_Remote_Evidence",
    "Auto_Current",
    "Auto_Current_Evidence",
    "Auto_Publication_Age_Days",
    "Auto_Publication_Age_Basis",
    "Auto_Freshness",
    "Auto_Freshness_Evidence",
    "Auto_Hard_Conflict",
    "Auto_Hard_Conflict_Types",
    "Auto_Hard_Conflict_Evidence",
    "Auto_Review_Flags",
]


# ============================================================
# BASIC HELPERS
# ============================================================


def clean(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.lower() == "nan":
        return ""
    return re.sub(r"\s+", " ", text).strip()


def fold(value: str) -> str:
    """Lowercase + remove diacritics for robust Czech/English matching."""
    value = clean(value).lower()
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def first_nonempty(*values: str) -> str:
    for value in values:
        value = clean(value)
        if value:
            return value
    return ""


def join_unique(values: Iterable[str], sep: str = " | ") -> str:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        value = clean(value)
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return sep.join(out)


def snippet(text: str, match_start: int, match_end: int, radius: int = 115) -> str:
    text = clean(text)
    if not text:
        return ""
    start = max(0, match_start - radius)
    end = min(len(text), match_end + radius)
    piece = text[start:end].strip(" ,.;:-")
    if start > 0:
        piece = "…" + piece
    if end < len(text):
        piece = piece + "…"
    return piece


def field_evidence(field: str, text: str, max_len: int = 300) -> str:
    text = clean(text)
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return f"{field}: {text}" if text else ""


def regex_evidence(
    row: pd.Series,
    fields: Sequence[str],
    patterns: Sequence[str],
    max_hits: int = 3,
) -> list[str]:
    hits: list[str] = []
    for field in fields:
        text = clean(row.get(field, ""))
        if not text:
            continue
        for pattern in patterns:
            for m in re.finditer(pattern, text, flags=re.I):
                hits.append(f"{field}: {snippet(text, m.start(), m.end())}")
                if len(hits) >= max_hits:
                    return hits
    return hits


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}_", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        df.to_csv(tmp_path, index=False, encoding="utf-8-sig")
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def parse_dt(value: str):
    value = clean(value)
    if not value:
        return pd.NaT
    return pd.to_datetime(value, errors="coerce", utc=True)


def discover_data_mining_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if parent.name.lower() == "data_mining":
            return parent
    return Path.cwd()


def latest_unique_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Keep one latest record per Input_URL while preserving deterministic ordering."""
    if df.empty or "Input_URL" not in df.columns:
        return df
    out = df.copy()
    out["_input_order"] = range(len(out))
    out["_url_key"] = out["Input_URL"].map(clean)
    if "Extraction_Date" in out.columns:
        out["_extract_dt"] = pd.to_datetime(out["Extraction_Date"], errors="coerce", utc=True)
        out = out.sort_values(["_url_key", "_extract_dt", "_input_order"], kind="stable")
    else:
        out = out.sort_values(["_url_key", "_input_order"], kind="stable")
    out = out.drop_duplicates("_url_key", keep="last")
    out = out.sort_values("_input_order", kind="stable")
    return out.drop(columns=[c for c in ["_input_order", "_url_key", "_extract_dt"] if c in out.columns])


# ============================================================
# LANGUAGE EVIDENCE
# ============================================================


def extract_czech_requirement(row: pd.Series) -> tuple[str, str]:
    searchable = ["Languages", "Requirements", "Description"]

    not_required_patterns = [
        r"czech(?:\s+or\s+slovak)?[^.!?]{0,80}(?:not\s+required|not\s+mandatory|isn['’]?t\s+required)",
        r"(?:not\s+required|not\s+mandatory)[^.!?]{0,80}czech",
        r"czech(?:\s+or\s+slovak)?[^.!?]{0,80}(?:nice\s+bonus|bonus|nice\s+to\s+have|plus)",
        r"(?:cestina|cesky\s+jazyk|znalost\s+cestiny)[^.!?]{0,80}(?:neni\s+podminkou|neni\s+nutna|vyhodou)",
    ]
    # Match on folded text to handle accents consistently. Folding preserves one
    # base character per accented character, so match positions remain suitable
    # for snippets from the original string.
    for field in searchable:
        raw = clean(row.get(field, ""))
        f = fold(raw)
        if not raw:
            continue
        for p in not_required_patterns:
            m = re.search(p, f, flags=re.I)
            if m:
                evidence = f"{field}: {snippet(raw, m.start(), m.end())}"
                if re.search(r"bonus|nice\s+to\s+have|plus|vyhodou", m.group(0), flags=re.I):
                    return "PREFERRED", evidence
                return "NOT_REQUIRED", evidence

    required_fold_patterns = [
        r"fluent\s+czech",
        r"native\s+czech",
        r"czech[^.!?]{0,50}(?:required|mandatory)",
        r"(?:required|mandatory)[^.!?]{0,50}czech",
        r"plynul[a-z]*\s+znalost\s+cestiny",
        r"znalost\s+cestiny",
        r"cestina[^.!?]{0,50}(?:podminkou|nutna|pozadovana)",
        r"cesky\s+jazyk[^.!?]{0,50}(?:podminkou|nutny|pozadovany)",
        r"pozadovane\s+jazyky[^.!?]{0,80}(?:cestina|cesky)",
    ]
    for field in ["Requirements", "Description"]:
        raw = clean(row.get(field, ""))
        f = fold(raw)
        for p in required_fold_patterns:
            m = re.search(p, f, flags=re.I)
            if m:
                return "REQUIRED", f"{field}: {snippet(raw, m.start(), m.end())}"

    languages = clean(row.get("Languages", ""))
    lf = fold(languages)
    if re.search(r"\b(czech|cesky|cestina)\b", lf):
        return "REQUIRED", field_evidence("Languages", languages)

    # Only treat a free-text Czech mention as language evidence when the nearby
    # wording itself is linguistic. This avoids false signals such as "Czech market",
    # "Czech office", or "Czech company".
    linguistic_patterns = [
        r"(?:czech|cestina|cesky)[^.!?]{0,55}(?:language|speaking|speaker|fluent|communicative|knowledge|uroven|jazyk|mluvit)",
        r"(?:language|speaking|speaker|fluent|communicative|knowledge|uroven|jazyk|mluvit)[^.!?]{0,55}(?:czech|cestina|cesky)",
    ]
    for field in ["Requirements", "Description"]:
        raw = clean(row.get(field, ""))
        f = fold(raw)
        for p in linguistic_patterns:
            m = re.search(p, f, flags=re.I)
            if m:
                return "MENTIONED", f"{field}: {snippet(raw, m.start(), m.end())}"

    return "NOT_MENTIONED", ""


def extract_english_acceptable(row: pd.Series) -> tuple[str, str]:
    languages = clean(row.get("Languages", ""))
    lf = fold(languages)
    if re.search(r"\b(english|anglicky|anglictina)\b", lf):
        return "YES", field_evidence("Languages", languages)

    explicit_fields = ["Requirements", "Description"]
    explicit_patterns = [
        r"fluent\s+english",
        r"english[^.!?]{0,60}(?:required|mandatory|proficien|working\s+knowledge|communicative|advanced|intermediate)",
        r"(?:required|mandatory|proficien)[^.!?]{0,60}english",
        r"anglictin[a-z]*[^.!?]{0,60}(?:plynul|pokrocil|komunikativ|urovn)",
        r"znalost\s+anglictiny",
        r"pozadovane\s+jazyky[^.!?]{0,100}(?:anglictina|anglicky)",
    ]
    for field in explicit_fields:
        raw = clean(row.get(field, ""))
        f = fold(raw)
        for p in explicit_patterns:
            m = re.search(p, f, flags=re.I)
            if m:
                return "YES", f"{field}: {snippet(raw, m.start(), m.end())}"

    # An English-language vacancy is positive evidence, but not proof that English
    # alone is sufficient for the role.
    page_lang = clean(row.get("Page_Language", "")).lower()
    description = clean(row.get("Description", ""))
    if page_lang.startswith("en") and len(description) >= 180:
        return "LIKELY", field_evidence("Page_Language", page_lang)

    return "UNKNOWN", ""


def extract_years_experience(row: pd.Series) -> tuple[str, str]:
    matches: list[tuple[int, str]] = []

    # Structured JobStack seniority field is a clean source when present.
    structured = clean(row.get("Seniority_Detail", ""))
    for m in re.finditer(r"\b(\d{1,2})\s*(?:rok|roky|roků|roku|let|year|years|yr|yrs)\b", structured, flags=re.I):
        n = int(m.group(1))
        if 0 < n <= 15:
            matches.append((n, field_evidence("Seniority_Detail", structured)))

    # Ranges represent an admissible band, so the lower end is the minimum.
    range_patterns = [
        r"(?P<lo>\d{1,2})\s*[-–—]\s*(?P<hi>\d{1,2})\s*(?:years?|yrs?)\s+(?:of\s+)?experience",
        r"(?P<lo>\d{1,2})\s*[-–—]\s*(?P<hi>\d{1,2})\s*(?:rok|roky|roků|roku|let)\s+(?:praxe|zkušeností|zkušenosti)",
    ]

    minimum_patterns = [
        r"(?P<n>\d{1,2})\s*\+\s*(?:years?|yrs?)(?:\s+(?:of|in)\b[^.!?]{0,45})?",
        r"(?P<n>\d{1,2})\s*(?:years?|yrs?)\s+(?:of\s+)?(?:relevant\s+|professional\s+|commercial\s+|hands[- ]on\s+)?experience",
        r"(?:at\s+least|minimum|min\.?|minimum\s+of)\s*(?P<n>\d{1,2})\s*(?:years?|yrs?)[^.!?]{0,45}(?:experience|in\s+(?:analytics|data|software|engineering|marketing|research|development|product|finance|banking))",
        r"(?P<n>\d{1,2})\s*\+?\s*(?:roky|roků|roku|let)\s+(?:praxe|zkušeností|zkušenosti)",
        r"(?:alespoň|minimálně|min\.?)\s*(?P<n>\d{1,2})\s*(?:roky|roků|roku|let)[^.!?]{0,45}(?:praxe|zkušeností|zkušenosti)",
    ]

    for field in ["Requirements", "Description"]:
        raw = clean(row.get(field, ""))
        if not raw:
            continue

        occupied: list[tuple[int, int]] = []
        for pattern in range_patterns:
            for m in re.finditer(pattern, raw, flags=re.I):
                lo, hi = int(m.group("lo")), int(m.group("hi"))
                if 0 < lo <= hi <= 15:
                    matches.append((lo, f"{field}: {snippet(raw, m.start(), m.end())}"))
                    occupied.append((m.start(), m.end()))

        for pattern in minimum_patterns:
            for m in re.finditer(pattern, raw, flags=re.I):
                # Do not double-count the upper half of an already parsed range.
                if any(a <= m.start() < b for a, b in occupied):
                    continue
                n = int(m.group("n"))
                if 0 < n <= 15:
                    matches.append((n, f"{field}: {snippet(raw, m.start(), m.end())}"))

    if not matches:
        return "", ""

    # Multiple independent requirements can coexist (e.g. 2+ years analytics,
    # including 1 year product analytics). The largest explicit minimum is the
    # binding threshold, while a range contributes its lower endpoint.
    max_n = max(n for n, _ in matches)
    evidence = join_unique(ev for n, ev in matches if n == max_n)
    return str(max_n), evidence


def extract_seniority(row: pd.Series) -> tuple[str, str]:
    sources = [
        ("Seniority_Detail", clean(row.get("Seniority_Detail", ""))),
        ("Seniority_Input", clean(row.get("Seniority_Input", ""))),
        ("Job_Title_Page", clean(row.get("Job_Title_Page", ""))),
        ("Job_Title_Input", clean(row.get("Job_Title_Input", ""))),
    ]

    levels: set[str] = set()
    evidence: list[str] = []
    for field, raw in sources:
        if not raw:
            continue
        f = fold(raw)
        local: set[str] = set()
        if re.search(r"\b(junior|entry[- ]?level|graduate|trainee|absolvent)\b", f):
            local.add("JUNIOR")
        if re.search(r"\b(medior|mid[- ]?level|midlevel|intermediate)\b", f):
            local.add("MID")
        if re.search(r"\b(senior|lead|principal|staff)\b", f):
            local.add("SENIOR")
        if local:
            levels.update(local)
            evidence.append(field_evidence(field, raw))

    if not levels:
        return "UNKNOWN", ""
    if levels == {"JUNIOR"}:
        value = "JUNIOR"
    elif levels == {"MID"}:
        value = "MID"
    elif levels == {"SENIOR"}:
        value = "SENIOR"
    elif levels == {"JUNIOR", "MID"}:
        value = "JUNIOR_MID"
    elif levels == {"MID", "SENIOR"}:
        value = "MID_SENIOR"
    elif levels == {"JUNIOR", "SENIOR"}:
        value = "JUNIOR_SENIOR"
    else:
        value = "MIXED"
    return value, join_unique(evidence)


# ============================================================
# TECHNOLOGY / DOMAIN SIGNALS
# ============================================================


def alias_regex(alias: str) -> str:
    # Aliases supplied as regex are prefixed with 're:'.
    if alias.startswith("re:"):
        return alias[3:]
    return rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])"


def extract_skill_signal(row: pd.Series, aliases: Sequence[str]) -> tuple[str, str]:
    compiled = [alias_regex(a) for a in aliases]

    preferred_words = [
        r"nice\s+to\s+have",
        r"advantage",
        r"preferred",
        r"bonus",
        r"plus",
        r"ideally",
        r"výhodou",
        r"není\s+podmínk",
        r"not\s+(?:a\s+)?requirement",
        r"not\s+required",
    ]
    required_words = [
        r"must",
        r"required",
        r"requirement",
        r"proficien",
        r"strong",
        r"solid",
        r"experience\s+with",
        r"knowledge\s+of",
        r"znalost",
        r"zvlád",
        r"potřeb",
        r"požad",
    ]

    # Human-readable text can explicitly downgrade a structured tag (e.g.
    # "Python is not required, but is an advantage"). Capture that before the
    # structured Skills field so explicit prose wins over generic tagging.
    mentions: list[tuple[str, str]] = []
    for field in ["Requirements", "Description"]:
        raw = clean(row.get(field, ""))
        if not raw:
            continue
        for pattern in compiled:
            for m in re.finditer(pattern, raw, flags=re.I):
                start = max(0, m.start() - 110)
                end = min(len(raw), m.end() + 110)
                window = raw[start:end]
                level = "MENTIONED"
                if any(re.search(p, window, flags=re.I) for p in preferred_words):
                    level = "PREFERRED"
                elif any(re.search(p, window, flags=re.I) for p in required_words):
                    level = "REQUIRED"
                mentions.append((level, f"{field}: {snippet(raw, m.start(), m.end())}"))

    explicit_preferred = [ev for level, ev in mentions if level == "PREFERRED"]
    explicit_required = [ev for level, ev in mentions if level == "REQUIRED"]
    if explicit_preferred and not explicit_required:
        return "PREFERRED", join_unique(explicit_preferred)

    skills = clean(row.get("Skills", ""))
    if skills:
        # StartupJobs: "Python [required]; SQL [required]"
        for pattern in compiled:
            if re.search(rf"{pattern}\s*\[required\]", skills, flags=re.I):
                return "REQUIRED", field_evidence("Skills", skills)

        # JobStack convention from prospect_details:
        #   required/basic skills | advantage/preferred skills
        left, sep, right = skills.partition("|")
        if any(re.search(p, left, flags=re.I) for p in compiled):
            return "REQUIRED", field_evidence("Skills", skills)
        if sep and any(re.search(p, right, flags=re.I) for p in compiled):
            return "PREFERRED", field_evidence("Skills", skills)
        if any(re.search(p, skills, flags=re.I) for p in compiled):
            return "MENTIONED", field_evidence("Skills", skills)

    if explicit_required:
        return "REQUIRED", join_unique(explicit_required)
    if explicit_preferred:
        return "PREFERRED", join_unique(explicit_preferred)
    if mentions:
        return "MENTIONED", join_unique(ev for _, ev in mentions)
    return "NOT_MENTIONED", ""


def extract_r_signal(row: pd.Series) -> tuple[str, str]:
    """Detect the R programming language without matching ordinary letter 'r'."""
    skills = clean(row.get("Skills", ""))
    if skills:
        # Match R as an actual delimited skill token.
        required = re.search(r"(?:^|[;|,]\s*)R\s*\[required\](?=$|[;|,])", skills)
        if required:
            return "REQUIRED", field_evidence("Skills", skills)
        left, sep, right = skills.partition("|")
        token = r"(?:^|[;,]\s*)R(?=$|[;,])"
        if re.search(token, left):
            return "REQUIRED", field_evidence("Skills", skills)
        if sep and re.search(token, right):
            return "PREFERRED", field_evidence("Skills", skills)

    textual_patterns = [
        r"\bPython\s*(?:and/or|or|and|/)\s*R\b",
        r"\bR\s*(?:and/or|or|and|/)\s*Python\b",
        r"\b(?:using|with|in|programming\s+in|language\s+)R\b",
        r"\bR\s+programming\b",
        r"\bRStudio\b",
        r"\bdata\.table\b",
        r"analytical\s+tools?[^.!?]{0,40}\bR\b",
    ]
    preferred_words = r"nice\s+to\s+have|advantage|preferred|bonus|plus|ideally|výhodou|not\s+required|není\s+podmínk"
    required_words = r"must|required|proficien|strong|solid|experience|knowledge|znalost|zvlád|potřeb|požad"
    found: list[tuple[str, str]] = []
    for field in ["Requirements", "Description"]:
        raw = clean(row.get(field, ""))
        for pattern in textual_patterns:
            for m in re.finditer(pattern, raw):
                window = raw[max(0, m.start()-110): min(len(raw), m.end()+110)]
                if re.search(preferred_words, window, flags=re.I):
                    level = "PREFERRED"
                elif re.search(required_words, window, flags=re.I):
                    level = "REQUIRED"
                else:
                    level = "MENTIONED"
                found.append((level, f"{field}: {snippet(raw, m.start(), m.end())}"))
    if not found:
        return "NOT_MENTIONED", ""
    precedence = {"REQUIRED": 3, "PREFERRED": 2, "MENTIONED": 1}
    best = max(found, key=lambda x: precedence[x[0]])[0]
    return best, join_unique(ev for level, ev in found if level == best)


def extract_remote(row: pd.Series) -> tuple[str, str]:
    raw = clean(row.get("Remote_Mode", ""))
    f = fold(raw)
    if raw:
        has_remote = bool(re.search(r"\bremote\b", f))
        has_hybrid = bool(re.search(r"\bhybrid\b", f))
        has_onsite = bool(re.search(r"\b(on[- ]?site|onsite)\b", f))
        if has_hybrid:
            return "HYBRID", field_evidence("Remote_Mode", raw)
        if has_remote and has_onsite:
            return "FLEXIBLE", field_evidence("Remote_Mode", raw)
        if has_remote:
            return "REMOTE", field_evidence("Remote_Mode", raw)
        if has_onsite:
            return "ONSITE", field_evidence("Remote_Mode", raw)

    # Conservative fallback from full text.
    for field in ["Requirements", "Description"]:
        text = clean(row.get(field, ""))
        ft = fold(text)
        if not text:
            continue
        if re.search(r"\b(fully\s+remote|full\s+remote|100%\s+remote)\b", ft):
            return "REMOTE", field_evidence(field, text)
        if re.search(r"\b(hybrid|home\s+office|work\s+from\s+home|occasional\s+work\s+from\s+home)\b", ft):
            return "HYBRID", field_evidence(field, text)
        if re.search(r"\b(on[- ]?site|onsite|office[- ]based)\b", ft):
            return "ONSITE", field_evidence(field, text)

    return "UNKNOWN", ""


def extract_current(row: pd.Series) -> tuple[str, str]:
    raw = clean(row.get("Current_Status", "")).upper()
    allowed = {"OPEN", "LIKELY_OPEN", "CLOSED", "LIKELY_CLOSED", "UNKNOWN", "REQUEST_FAILED", "HTTP_ERROR"}
    if raw in allowed:
        evidence = join_unique([
            field_evidence("Current_Status", raw),
            field_evidence("Status_Evidence", row.get("Status_Evidence", "")),
        ])
        return raw, evidence

    http = clean(row.get("HTTP_Status", ""))
    if http in {"404", "410"}:
        return "CLOSED", field_evidence("HTTP_Status", http)
    return "UNKNOWN", ""


def publication_evidence(row: pd.Series, as_of: pd.Timestamp) -> tuple[str, str, str, str]:
    exact = parse_dt(row.get("Published_At", ""))
    upper = parse_dt(row.get("Published_Upper_Bound", ""))

    if not pd.isna(exact):
        dt = exact
        basis = "EXACT"
        evidence = field_evidence("Published_At", row.get("Published_At", ""))
    elif not pd.isna(upper):
        dt = upper
        basis = "MINIMUM_AGE"
        evidence = field_evidence("Published_Upper_Bound", row.get("Published_Upper_Bound", ""))
    else:
        return "", "UNKNOWN", "UNKNOWN", ""

    days = max(0, int((as_of - dt).total_seconds() // 86400))
    if days <= 3:
        bucket = "NEW"
    elif days <= 7:
        bucket = "FRESH"
    elif days <= 14:
        bucket = "RECENT"
    elif days <= 30:
        bucket = "AGING"
    else:
        bucket = "OLD"

    if basis == "MINIMUM_AGE":
        evidence = f"{evidence} | true posting age may be older"
    return str(days), basis, bucket, evidence


# ============================================================
# ROW EXTRACTION
# ============================================================


def extract_row(row: pd.Series, as_of: pd.Timestamp, extracted_at: str) -> dict[str, str]:
    czech, czech_ev = extract_czech_requirement(row)
    english, english_ev = extract_english_acceptable(row)
    years, years_ev = extract_years_experience(row)
    seniority, seniority_ev = extract_seniority(row)

    python_sig, python_ev = extract_skill_signal(row, ["Python"])
    sql_sig, sql_ev = extract_skill_signal(row, [
        "SQL", "PostgreSQL", "MySQL", "SQL Server", "Oracle SQL", "T-SQL", "PL/SQL"
    ])
    ml_sig, ml_ev = extract_skill_signal(row, [
        "Machine Learning", "re:\bML\b", "scikit-learn", "sklearn", "XGBoost",
        "TensorFlow", "PyTorch", "predictive model", "predictive modeling", "predictive modelling"
    ])
    stats_sig, stats_ev = extract_skill_signal(row, [
        "statistics", "statistical", "statistician", "probability", "inference",
        "regression", "time series", "hypothesis testing", "econometrics"
    ])
    r_sig, r_ev = extract_r_signal(row)

    remote, remote_ev = extract_remote(row)
    current, current_ev = extract_current(row)
    age, age_basis, freshness, freshness_ev = publication_evidence(row, as_of)

    conflicts: list[str] = []
    conflict_evidence: list[str] = []
    if czech == "REQUIRED":
        conflicts.append("CZECH_REQUIRED")
        if czech_ev:
            conflict_evidence.append(czech_ev)
    if current == "CLOSED":
        conflicts.append("CLOSED")
        if current_ev:
            conflict_evidence.append(current_ev)

    flags: list[str] = []
    extraction_status = clean(row.get("Extraction_Status", "")).upper()
    if extraction_status == "PARTIAL":
        flags.append("PARTIAL_EXTRACTION")
    elif extraction_status in {"FAILED", "BLOCKED"}:
        flags.append("EXTRACTION_UNAVAILABLE")
    if current == "LIKELY_CLOSED":
        flags.append("LIKELY_CLOSED")
    if current in {"REQUEST_FAILED", "HTTP_ERROR"}:
        flags.append("CURRENT_STATUS_UNCERTAIN")
    if years:
        try:
            if int(years) >= 3:
                flags.append("EXPERIENCE_3_PLUS")
        except ValueError:
            pass
    if seniority in {"SENIOR", "MID_SENIOR", "MIXED", "JUNIOR_SENIOR"}:
        flags.append("SENIOR_LEVEL_PRESENT")
    if czech in {"PREFERRED", "MENTIONED"}:
        flags.append("CZECH_SIGNAL_REVIEW")
    if english == "UNKNOWN":
        flags.append("ENGLISH_UNCLEAR")

    result = {
        "ID": clean(row.get("ID", "")),
        "Input_URL": clean(row.get("Input_URL", "")),
        "Source": clean(row.get("Source", "")),
        "Evidence_Version": EVIDENCE_VERSION,
        "Evidence_Extracted_At": extracted_at,
        "Auto_Czech_Requirement": czech,
        "Auto_Czech_Evidence": czech_ev,
        "Auto_English_Acceptable": english,
        "Auto_English_Evidence": english_ev,
        "Auto_Years_Experience_Min": years,
        "Auto_Years_Experience_Evidence": years_ev,
        "Auto_Seniority": seniority,
        "Auto_Seniority_Evidence": seniority_ev,
        "Auto_Python": python_sig,
        "Auto_Python_Evidence": python_ev,
        "Auto_SQL": sql_sig,
        "Auto_SQL_Evidence": sql_ev,
        "Auto_ML": ml_sig,
        "Auto_ML_Evidence": ml_ev,
        "Auto_Statistics": stats_sig,
        "Auto_Statistics_Evidence": stats_ev,
        "Auto_R": r_sig,
        "Auto_R_Evidence": r_ev,
        "Auto_Remote": remote,
        "Auto_Remote_Evidence": remote_ev,
        "Auto_Current": current,
        "Auto_Current_Evidence": current_ev,
        "Auto_Publication_Age_Days": age,
        "Auto_Publication_Age_Basis": age_basis,
        "Auto_Freshness": freshness,
        "Auto_Freshness_Evidence": freshness_ev,
        "Auto_Hard_Conflict": "YES" if conflicts else "NO",
        "Auto_Hard_Conflict_Types": " | ".join(conflicts),
        "Auto_Hard_Conflict_Evidence": join_unique(conflict_evidence),
        "Auto_Review_Flags": " | ".join(flags),
    }
    return {field: clean(result.get(field, "")) for field in OUTPUT_FIELDS}


# ============================================================
# VALIDATION / CLI
# ============================================================


def validate_output(source: pd.DataFrame, evidence: pd.DataFrame) -> None:
    if len(source) != len(evidence):
        raise RuntimeError(f"Row-count mismatch: source={len(source)} evidence={len(evidence)}")

    if evidence["Input_URL"].duplicated().any():
        dupes = evidence.loc[evidence["Input_URL"].duplicated(keep=False), "Input_URL"].tolist()
        raise RuntimeError(f"Duplicate Input_URL values in evidence output: {dupes[:5]}")

    if evidence["ID"].duplicated().any():
        dupes = evidence.loc[evidence["ID"].duplicated(keep=False), "ID"].tolist()
        raise RuntimeError(f"Duplicate ID values in evidence output: {dupes[:5]}")

    allowed = {
        "Auto_Czech_Requirement": {"REQUIRED", "PREFERRED", "NOT_REQUIRED", "MENTIONED", "NOT_MENTIONED"},
        "Auto_English_Acceptable": {"YES", "LIKELY", "UNKNOWN"},
        "Auto_Seniority": {"JUNIOR", "MID", "SENIOR", "JUNIOR_MID", "MID_SENIOR", "JUNIOR_SENIOR", "MIXED", "UNKNOWN"},
        "Auto_Python": {"REQUIRED", "PREFERRED", "MENTIONED", "NOT_MENTIONED"},
        "Auto_SQL": {"REQUIRED", "PREFERRED", "MENTIONED", "NOT_MENTIONED"},
        "Auto_ML": {"REQUIRED", "PREFERRED", "MENTIONED", "NOT_MENTIONED"},
        "Auto_Statistics": {"REQUIRED", "PREFERRED", "MENTIONED", "NOT_MENTIONED"},
        "Auto_R": {"REQUIRED", "PREFERRED", "MENTIONED", "NOT_MENTIONED"},
        "Auto_Remote": {"REMOTE", "HYBRID", "ONSITE", "FLEXIBLE", "UNKNOWN"},
        "Auto_Current": {"OPEN", "LIKELY_OPEN", "CLOSED", "LIKELY_CLOSED", "UNKNOWN", "REQUEST_FAILED", "HTTP_ERROR"},
        "Auto_Publication_Age_Basis": {"EXACT", "MINIMUM_AGE", "UNKNOWN"},
        "Auto_Freshness": {"NEW", "FRESH", "RECENT", "AGING", "OLD", "UNKNOWN"},
        "Auto_Hard_Conflict": {"YES", "NO"},
    }
    for column, values in allowed.items():
        observed = set(evidence[column].dropna().astype(str))
        bad = observed - values
        if bad:
            raise RuntimeError(f"Unexpected values in {column}: {sorted(bad)}")


def print_summary(df: pd.DataFrame, output_path: Path) -> None:
    print(f"Wrote {len(df)} evidence rows -> {output_path}")
    print()
    for col in [
        "Auto_Czech_Requirement",
        "Auto_English_Acceptable",
        "Auto_Seniority",
        "Auto_Remote",
        "Auto_Current",
        "Auto_Freshness",
        "Auto_Hard_Conflict",
    ]:
        print(col)
        counts = df[col].value_counts(dropna=False)
        for value, n in counts.items():
            label = value if clean(value) else "<blank>"
            print(f"  {label}: {n}")
        print()


def parse_as_of(value: str | None) -> pd.Timestamp:
    if not value:
        return pd.Timestamp.now(tz="UTC")
    dt = pd.to_datetime(value, errors="raise", utc=True)
    return pd.Timestamp(dt)


def main() -> None:
    root = discover_data_mining_root()
    default_dir = root / "data" / "07_prospects"

    parser = argparse.ArgumentParser(
        description="Extract deterministic, auditable machine evidence from prospects_enriched_raw.csv."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=default_dir / "prospects_enriched_raw.csv",
        help="Input enriched prospect CSV.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_dir / "prospect_evidence.csv",
        help="Output sidecar evidence CSV.",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="Optional UTC/ISO timestamp for reproducible freshness calculations (default: now).",
    )
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_path = args.output.resolve()
    as_of = parse_as_of(args.as_of)
    extracted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    raw = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    if raw.empty:
        atomic_write_csv(pd.DataFrame(columns=OUTPUT_FIELDS), output_path)
        print(f"Input is empty. Wrote empty evidence schema -> {output_path}")
        return

    required = {"ID", "Input_URL"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Input is missing required columns: {sorted(missing)}")

    source = latest_unique_rows(raw)
    records = [extract_row(row, as_of, extracted_at) for _, row in source.iterrows()]
    evidence = pd.DataFrame(records, columns=OUTPUT_FIELDS)
    validate_output(source, evidence)
    atomic_write_csv(evidence, output_path)
    print_summary(evidence, output_path)


if __name__ == "__main__":
    main()
