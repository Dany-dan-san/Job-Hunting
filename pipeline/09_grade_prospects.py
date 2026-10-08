from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse, urlunparse

import pandas as pd
import streamlit as st


# ============================================================
# CONFIGURATION
# ============================================================

APP_TITLE = "Prospect Review"

REVIEW_FIELDS = [
    "ID",
    "Input_URL",
    "Review_Status",
    "Review_Fit",
    "Review_Interest",
    "Review_Seniority",
    "Review_Language",
    "Review_Tech_Match",
    "Review_Priority",
    "Review_Decision",
    "Review_Reasons",
    "Review_Notes",
    "Reviewed_At",
    "Review_Updated_At",
]

FIT_OPTIONS = ["", "Strong", "Moderate", "Weak"]
INTEREST_OPTIONS = ["", "High", "Medium", "Low"]
SENIORITY_OPTIONS = ["", "Accessible", "Stretch", "Too senior", "Unclear"]
LANGUAGE_OPTIONS = ["", "Compatible", "Unclear", "Conflict"]
TECH_OPTIONS = ["", "Strong", "Moderate", "Weak", "Not applicable"]
PRIORITY_OPTIONS = ["", "Immediate", "High", "Normal", "Low"]
DECISION_OPTIONS = ["", "APPLY", "STRETCH", "HOLD", "REJECT"]
DECISION_UI_OPTIONS = ["Not decided", "APPLY", "STRETCH", "HOLD", "REJECT"]

REASON_OPTIONS = [
    "Excellent profile fit",
    "Strong interest",
    "Fresh vacancy",
    "Graduate/junior compatible",
    "Relevant Python/data work",
    "Relevant statistics/ML",
    "Relevant research/insights work",
    "Relevant domain",
    "Czech mandatory",
    "Language uncertainty",
    "Too senior",
    "Experience requirement",
    "Technology mismatch",
    "Role mismatch",
    "Location/work-mode mismatch",
    "Compensation concern",
    "Low interest",
    "Vacancy aging/stale",
    "Closed/unavailable",
    "Duplicate",
    "Other",
]

DECISION_ORDER = {
    "APPLY": 0,
    "STRETCH": 1,
    "HOLD": 2,
    "REJECT": 3,
    "": 4,
}

PRIORITY_ORDER = {
    "Immediate": 0,
    "High": 1,
    "Normal": 2,
    "Low": 3,
    "": 4,
}

AUTO_FIELDS = [
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

TECH_SIGNAL_FIELDS = [
    ("Python", "Auto_Python", "Auto_Python_Evidence"),
    ("SQL", "Auto_SQL", "Auto_SQL_Evidence"),
    ("ML", "Auto_ML", "Auto_ML_Evidence"),
    ("Statistics", "Auto_Statistics", "Auto_Statistics_Evidence"),
    ("R", "Auto_R", "Auto_R_Evidence"),
]


# ============================================================
# PATHS / IO
# ============================================================


def discover_data_mining_root() -> Path:
    """Find nearest Data_Mining ancestor; otherwise use current working directory."""
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if parent.name.lower() == "data_mining":
            return parent
    return Path.cwd()


ROOT = discover_data_mining_root()
PROSPECT_DIR = ROOT / "data" / "07_prospects"
GROSS_CSV = PROSPECT_DIR / "prospects_gross.csv"
ENRICHED_CSV = PROSPECT_DIR / "prospects_enriched_raw.csv"
EVIDENCE_CSV = PROSPECT_DIR / "prospect_evidence.csv"
REVIEWS_CSV = PROSPECT_DIR / "prospect_reviews.csv"
APPLICATION_QUEUE_CSV = PROSPECT_DIR / "application_queue.csv"


def read_csv_safe(path: Path) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    """Write a CSV atomically so interruption does not corrupt the existing file."""
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


def normalize_url(url: str) -> str:
    url = clean(url)
    if not url:
        return ""
    try:
        p = urlparse(url)
        scheme = (p.scheme or "https").lower()
        netloc = p.netloc.lower()
        path = re.sub(r"/{2,}", "/", p.path or "/")
        if path != "/":
            path = path.rstrip("/")
        return urlunparse((scheme, netloc, path, "", p.query, ""))
    except Exception:
        return url


def clean(value) -> str:
    if value is None:
        return ""
    value = str(value)
    if value.lower() == "nan":
        return ""
    return re.sub(r"\s+", " ", value).strip()


def first_nonempty(*values: str) -> str:
    for value in values:
        value = clean(value)
        if value:
            return value
    return ""


def split_multi(value: str, separators: Iterable[str] = ("|", ";")) -> list[str]:
    text = clean(value)
    if not text:
        return []
    pattern = "|".join(re.escape(s) for s in separators)
    return [x.strip() for x in re.split(pattern, text) if x.strip()]


def detect_source(url: str) -> str:
    host = urlparse(clean(url)).netloc.lower()
    if "builtin.com" in host:
        return "builtin"
    if "startupjobs.cz" in host:
        return "startupjobs"
    if "jobstack.it" in host:
        return "jobstack"
    if "jobs.cz" in host:
        return "jobscz"
    if "indeed." in host:
        return "indeed"
    return "unknown"


# ============================================================
# DATA ASSEMBLY
# ============================================================


def latest_enrichment(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "Input_URL" not in df.columns:
        return df

    out = df.copy()
    out["_url_key"] = out["Input_URL"].map(normalize_url)
    if "Extraction_Date" in out.columns:
        out["_extract_dt"] = pd.to_datetime(out["Extraction_Date"], errors="coerce", utc=True)
        out = out.sort_values(["_url_key", "_extract_dt"], kind="stable")
    return out.drop_duplicates("_url_key", keep="last").drop(columns=[c for c in ["_extract_dt"] if c in out.columns])


def latest_evidence(df: pd.DataFrame) -> pd.DataFrame:
    """Return the newest deterministic evidence record per vacancy URL."""
    if df.empty or "Input_URL" not in df.columns:
        return df

    out = df.copy()
    out["_url_key"] = out["Input_URL"].map(normalize_url)
    if "Evidence_Extracted_At" in out.columns:
        out["_evidence_dt"] = pd.to_datetime(out["Evidence_Extracted_At"], errors="coerce", utc=True)
        out = out.sort_values(["_url_key", "_evidence_dt"], kind="stable")
    return out.drop_duplicates("_url_key", keep="last").drop(
        columns=[c for c in ["_evidence_dt"] if c in out.columns]
    )


def load_master() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    gross = read_csv_safe(GROSS_CSV)
    enriched = latest_enrichment(read_csv_safe(ENRICHED_CSV))
    evidence = latest_evidence(read_csv_safe(EVIDENCE_CSV))
    reviews = read_csv_safe(REVIEWS_CSV)

    if gross.empty and enriched.empty:
        return pd.DataFrame(), enriched, reviews

    # Gross remains the authoritative admission list. If it is temporarily absent,
    # enriched output can still drive the review UI.
    if not gross.empty:
        g = gross.copy()
        if "URL" not in g.columns:
            raise ValueError(f"{GROSS_CSV} must contain a URL column.")
        g["_url_key"] = g["URL"].map(normalize_url)
        g = g[g["_url_key"] != ""].drop_duplicates("_url_key", keep="first")

        # Prefix only enrichment columns that collide with gross columns is unnecessary:
        # prospect_details already uses *_Input names for lineage fields.
        if not enriched.empty:
            e = enriched.copy()
            if "_url_key" not in e.columns:
                e["_url_key"] = e["Input_URL"].map(normalize_url)
            master = g.merge(e, on="_url_key", how="left", suffixes=("", "_enriched"))
        else:
            master = g
    else:
        master = enriched.copy()
        master["URL"] = master.get("Input_URL", "")

    # Canonical display columns.
    master["Review_URL"] = master.apply(
        lambda r: first_nonempty(r.get("Final_URL", ""), r.get("Input_URL", ""), r.get("URL", "")), axis=1
    )
    master["Input_URL"] = master.apply(
        lambda r: first_nonempty(r.get("Input_URL", ""), r.get("URL", "")), axis=1
    )
    master["Display_Title"] = master.apply(
        lambda r: first_nonempty(r.get("Job_Title_Page", ""), r.get("Job Title", ""), r.get("Job_Title_Input", "")), axis=1
    )
    master["Display_Company"] = master.apply(
        lambda r: first_nonempty(r.get("Company_Page", ""), r.get("Company", ""), r.get("Company_Input", "")), axis=1
    )
    master["Display_Seniority"] = master.apply(
        lambda r: first_nonempty(r.get("Seniority_Detail", ""), r.get("Seniority", ""), r.get("Seniority_Input", "")), axis=1
    )
    master["Display_Grade"] = master.apply(
        lambda r: first_nonempty(r.get("Grade", ""), r.get("Grade_Input", "")), axis=1
    )
    if "Source" not in master.columns:
        master["Source"] = master["Input_URL"].map(detect_source)
    else:
        master["Source"] = master.apply(
            lambda r: first_nonempty(r.get("Source", ""), detect_source(r.get("Input_URL", ""))), axis=1
        )

    # Deterministic machine-evidence sidecar join. The Auto_* fields are advisory
    # and are never used to populate or overwrite human review fields.
    if not evidence.empty and "Input_URL" in evidence.columns:
        ev = evidence.copy()
        if "_url_key" not in ev.columns:
            ev["_url_key"] = ev["Input_URL"].map(normalize_url)
        keep = ["_url_key"] + [c for c in AUTO_FIELDS if c in ev.columns]
        master = master.merge(ev[keep], on="_url_key", how="left")

    for col in AUTO_FIELDS:
        if col not in master.columns:
            master[col] = ""
        master[col] = master[col].fillna("").astype(str)

    # Review join.
    if not reviews.empty and "Input_URL" in reviews.columns:
        rv = reviews.copy()
        rv["_url_key"] = rv["Input_URL"].map(normalize_url)
        if "Review_Updated_At" in rv.columns:
            rv["_review_dt"] = pd.to_datetime(rv["Review_Updated_At"], errors="coerce", utc=True)
            rv = rv.sort_values(["_url_key", "_review_dt"], kind="stable")
        rv = rv.drop_duplicates("_url_key", keep="last")
        keep = ["_url_key"] + [c for c in REVIEW_FIELDS if c not in {"ID", "Input_URL"} and c in rv.columns]
        master = master.merge(rv[keep], on="_url_key", how="left")

    for col in REVIEW_FIELDS:
        if col not in master.columns:
            master[col] = ""
        master[col] = master[col].fillna("").astype(str)

    master["Review_Status"] = master["Review_Status"].replace("", "UNREVIEWED")
    master["Freshness"] = master.apply(publication_bucket, axis=1)
    master["Enrichment"] = master.apply(enrichment_label, axis=1)

    return master.reset_index(drop=True), enriched, reviews


def parse_datetime(value: str):
    value = clean(value)
    if not value:
        return pd.NaT
    return pd.to_datetime(value, errors="coerce", utc=True)


def best_publication_date(row: pd.Series):
    exact = parse_datetime(row.get("Published_At", ""))
    if not pd.isna(exact):
        return exact
    upper = parse_datetime(row.get("Published_Upper_Bound", ""))
    if not pd.isna(upper):
        return upper
    return parse_datetime(row.get("Mining_Date", ""))


def publication_bucket(row: pd.Series) -> str:
    dt = best_publication_date(row)
    if pd.isna(dt):
        return "UNKNOWN"
    now = pd.Timestamp.now(tz="UTC")
    days = max(0, int((now - dt).total_seconds() // 86400))
    if days <= 3:
        return "NEW"
    if days <= 7:
        return "FRESH"
    if days <= 14:
        return "RECENT"
    if days <= 30:
        return "AGING"
    return "OLD"


def publication_display(row: pd.Series) -> str:
    exact = clean(row.get("Published_At", ""))
    lower = clean(row.get("Published_Lower_Bound", ""))
    upper = clean(row.get("Published_Upper_Bound", ""))
    method = clean(row.get("Publication_Method", ""))
    confidence = clean(row.get("Publication_Confidence", ""))

    if exact:
        return f"Exact: {exact} · {confidence or method}"
    if lower and upper and lower != upper:
        return f"Between {lower} and {upper} · {confidence or method}"
    if upper:
        return f"Seen by {upper} · {confidence or method or 'upper bound only'}"
    return "Publication date unknown"


def enrichment_label(row: pd.Series) -> str:
    status = clean(row.get("Extraction_Status", ""))
    if not status:
        return "NOT ENRICHED"
    if status.lower() == "ok":
        return "ENRICHED"
    if status.lower() == "partial":
        return "PARTIAL"
    if status.lower() == "blocked":
        return "BLOCKED"
    if status.lower() == "failed":
        return "FAILED"
    return status.upper()


# ============================================================
# REVIEW PERSISTENCE / QUEUE
# ============================================================


def upsert_review(review: dict[str, str]) -> None:
    existing = read_csv_safe(REVIEWS_CSV)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    review = {field: clean(review.get(field, "")) for field in REVIEW_FIELDS}
    review["Input_URL"] = normalize_url(review["Input_URL"])
    review["Review_Status"] = "REVIEWED"
    review["Review_Updated_At"] = now
    if not review["Reviewed_At"]:
        review["Reviewed_At"] = now

    if existing.empty:
        out = pd.DataFrame([review], columns=REVIEW_FIELDS)
    else:
        for field in REVIEW_FIELDS:
            if field not in existing.columns:
                existing[field] = ""
        existing = existing[REVIEW_FIELDS].fillna("").astype(str)
        existing["_url_key"] = existing["Input_URL"].map(normalize_url)
        key = review["Input_URL"]
        mask = existing["_url_key"] == key
        if mask.any():
            original_reviewed = clean(existing.loc[mask, "Reviewed_At"].iloc[-1])
            if original_reviewed:
                review["Reviewed_At"] = original_reviewed
            existing = existing.loc[~mask, REVIEW_FIELDS]
        else:
            existing = existing[REVIEW_FIELDS]
        out = pd.concat([existing, pd.DataFrame([review])], ignore_index=True)

    out = out.sort_values("Review_Updated_At", kind="stable")
    atomic_write_csv(out, REVIEWS_CSV)
    rebuild_application_queue()



def rebuild_application_queue() -> None:
    """
    Rebuild application_queue.csv from human APPLY/STRETCH decisions.

    This function intentionally simplifies ONLY the application queue output.
    It does not modify prospect_reviews.csv, prospect_evidence.csv, or the joined
    master data used by the Streamlit review interface. Human review fields remain
    sourced from prospect_reviews.csv; machine warning/context fields remain sourced
    from prospect_evidence.csv; vacancy identity/source fields remain sourced from
    the gross/enriched vacancy records.
    """
    master, _, _ = load_master()
    if master.empty:
        atomic_write_csv(pd.DataFrame(), APPLICATION_QUEUE_CSV)
        return

    queue = master[master["Review_Decision"].isin(["APPLY", "STRETCH"])].copy()

    output_columns = [
        "ID",
        "Job_Title",
        "Company",
        "Decision",
        "Priority",
        "Fit",
        "Interest",
        "Seniority_Assessment",
        "Language_Assessment",
        "Tech_Match",
        "Freshness",
        "Current_Status",
        "Warnings",
        "Czech_Signal",
        "Experience_Min",
        "Work_Mode",
        "Source",
        "URL",
        "Reasons",
        "Notes",
        "Reviewed_At",
    ]

    if queue.empty:
        atomic_write_csv(pd.DataFrame(columns=output_columns), APPLICATION_QUEUE_CSV)
        return

    queue["_decision_order"] = queue["Review_Decision"].map(DECISION_ORDER).fillna(99)
    queue["_priority_order"] = queue["Review_Priority"].map(PRIORITY_ORDER).fillna(99)
    queue["_pub_dt"] = queue.apply(best_publication_date, axis=1)
    queue = queue.sort_values(
        ["_decision_order", "_priority_order", "_pub_dt"],
        ascending=[True, True, False],
        na_position="last",
        kind="stable",
    )

    def col(name: str) -> pd.Series:
        if name in queue.columns:
            return queue[name].fillna("").astype(str)
        return pd.Series([""] * len(queue), index=queue.index, dtype=str)

    def warning_text(row: pd.Series) -> str:
        warnings: list[str] = []

        # Explicit machine conflicts are surfaced compactly, but never override
        # the human APPLY/STRETCH decision.
        if clean(row.get("Auto_Hard_Conflict", "")).upper() == "YES":
            conflict_types = split_multi(row.get("Auto_Hard_Conflict_Types", ""))
            warnings.extend(conflict_types or ["HARD_CONFLICT"])

        # Preserve useful non-hard machine review flags without exploding them
        # into separate queue columns.
        warnings.extend(split_multi(row.get("Auto_Review_Flags", "")))

        # Keep queue-level lifecycle warnings visible even when no machine flag
        # exists for them.
        current = clean(row.get("Current_Status", "")).upper()
        if current in {"CLOSED", "LIKELY_CLOSED", "REQUEST_FAILED", "HTTP_ERROR"}:
            warnings.append(current)

        # Stable de-duplication while preserving first-seen order.
        seen: set[str] = set()
        ordered: list[str] = []
        for warning in warnings:
            warning = clean(warning)
            if warning and warning not in seen:
                seen.add(warning)
                ordered.append(warning)
        return " | ".join(ordered)

    warnings = queue.apply(warning_text, axis=1)

    out = pd.DataFrame({
        # Vacancy identity / factual source information.
        "ID": col("ID"),
        "Job_Title": col("Display_Title"),
        "Company": col("Display_Company"),

        # Human review information from prospect_reviews.csv.
        "Decision": col("Review_Decision"),
        "Priority": col("Review_Priority"),
        "Fit": col("Review_Fit"),
        "Interest": col("Review_Interest"),
        "Seniority_Assessment": col("Review_Seniority"),
        "Language_Assessment": col("Review_Language"),
        "Tech_Match": col("Review_Tech_Match"),

        # Compact factual / machine context. Full machine evidence remains in
        # prospect_evidence.csv and is NOT rewritten or simplified here.
        "Freshness": col("Freshness"),
        "Current_Status": col("Current_Status"),
        "Warnings": warnings,
        "Czech_Signal": col("Auto_Czech_Requirement"),
        "Experience_Min": col("Auto_Years_Experience_Min"),
        "Work_Mode": col("Auto_Remote"),

        # Vacancy source / navigation plus human notes.
        "Source": col("Source"),
        "URL": col("Review_URL"),
        "Reasons": col("Review_Reasons"),
        "Notes": col("Review_Notes"),
        "Reviewed_At": col("Reviewed_At"),
    })

    atomic_write_csv(out[output_columns], APPLICATION_QUEUE_CSV)


# ============================================================
# STREAMLIT HELPERS
# ============================================================


def option_index(options: list[str], value: str) -> int:
    value = clean(value)
    try:
        return options.index(value)
    except ValueError:
        return 0


def review_reason_list(value: str) -> list[str]:
    values = split_multi(value)
    return [x for x in values if x in REASON_OPTIONS]


def text_or_dash(value: str) -> str:
    return clean(value) or "—"


def truncate(text: str, limit: int = 5000) -> str:
    text = clean(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " …"


def render_fact(label: str, value: str) -> None:
    st.markdown(f"**{label}:** {text_or_dash(value)}")


def render_auto_evidence(label: str, value: str) -> None:
    value = clean(value)
    if value:
        st.markdown(f"**{label}:** {value}")


def available_values(master: pd.DataFrame, column: str) -> list[str]:
    if column not in master.columns:
        return []
    return sorted({clean(x) for x in master[column].tolist() if clean(x)})


def initialize_selection(filtered: pd.DataFrame) -> None:
    keys = filtered["_url_key"].tolist()
    if not keys:
        st.session_state.current_url_key = None
        return
    current = st.session_state.get("current_url_key")
    if current not in keys:
        st.session_state.current_url_key = keys[0]


def move_selection(filtered: pd.DataFrame, delta: int) -> None:
    keys = filtered["_url_key"].tolist()
    if not keys:
        return
    current = st.session_state.get("current_url_key")
    try:
        idx = keys.index(current)
    except ValueError:
        idx = 0
    st.session_state.current_url_key = keys[(idx + delta) % len(keys)]


def next_unreviewed(filtered: pd.DataFrame, current_key: str) -> None:
    keys = filtered["_url_key"].tolist()
    if not keys:
        return
    try:
        start = keys.index(current_key)
    except ValueError:
        start = -1
    for offset in range(1, len(keys) + 1):
        idx = (start + offset) % len(keys)
        row = filtered.iloc[idx]
        if clean(row.get("Review_Status", "")) != "REVIEWED":
            st.session_state.current_url_key = row["_url_key"]
            return
    move_selection(filtered, 1)


def apply_filters(master: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("Review queue")

    search = st.sidebar.text_input("Search title / company", value="")

    review_state = st.sidebar.multiselect(
        "Review status",
        ["UNREVIEWED", "REVIEWED"],
        default=["UNREVIEWED", "REVIEWED"],
    )

    sources = sorted(x for x in master["Source"].dropna().astype(str).unique() if x)
    source_filter = st.sidebar.multiselect("Source", sources, default=sources)

    decisions_available = ["Unreviewed", "APPLY", "STRETCH", "HOLD", "REJECT"]
    decision_filter = st.sidebar.multiselect("Decision", decisions_available, default=decisions_available)

    freshness_values = ["NEW", "FRESH", "RECENT", "AGING", "OLD", "UNKNOWN"]
    freshness_filter = st.sidebar.multiselect("Freshness", freshness_values, default=freshness_values)

    grades = sorted(x for x in master["Display_Grade"].dropna().astype(str).unique() if x)
    grade_filter = st.sidebar.multiselect("Original grade", grades, default=grades)

    only_open = st.sidebar.checkbox("Exclude explicitly closed vacancies", value=False)
    only_enriched = st.sidebar.checkbox("Only enriched / partial records", value=False)

    # Optional machine-evidence filters. Nothing here changes human judgments;
    # these controls only change which records are visible in the review queue.
    with st.sidebar.expander("Machine evidence filters", expanded=False):
        hide_hard_conflicts = st.checkbox("Hide explicit hard conflicts", value=False)

        czech_values = available_values(master, "Auto_Czech_Requirement")
        czech_filter = st.multiselect("Czech requirement", czech_values, default=czech_values)

        english_values = available_values(master, "Auto_English_Acceptable")
        english_filter = st.multiselect("English compatibility signal", english_values, default=english_values)

        auto_seniority_values = available_values(master, "Auto_Seniority")
        auto_seniority_filter = st.multiselect("Machine seniority", auto_seniority_values, default=auto_seniority_values)

        remote_values = available_values(master, "Auto_Remote")
        remote_filter = st.multiselect("Machine work mode", remote_values, default=remote_values)

        current_values = available_values(master, "Auto_Current")
        current_filter = st.multiselect("Machine vacancy state", current_values, default=current_values)

    df = master.copy()
    df = df[df["Review_Status"].isin(review_state)]
    if source_filter:
        df = df[df["Source"].isin(source_filter)]
    else:
        df = df.iloc[0:0]

    mapped_decision = df["Review_Decision"].replace("", "Unreviewed")
    df = df[mapped_decision.isin(decision_filter)]
    df = df[df["Freshness"].isin(freshness_filter)]

    if grades:
        if grade_filter:
            df = df[df["Display_Grade"].isin(grade_filter)]
        else:
            df = df.iloc[0:0]

    if only_open and "Current_Status" in df.columns:
        df = df[~df["Current_Status"].str.upper().isin(["CLOSED", "HTTP_ERROR", "REQUEST_FAILED"])]

    if only_enriched:
        df = df[df["Enrichment"] != "NOT ENRICHED"]

    if hide_hard_conflicts and "Auto_Hard_Conflict" in df.columns:
        df = df[df["Auto_Hard_Conflict"].str.upper() != "YES"]

    evidence_filters = [
        ("Auto_Czech_Requirement", czech_values, czech_filter),
        ("Auto_English_Acceptable", english_values, english_filter),
        ("Auto_Seniority", auto_seniority_values, auto_seniority_filter),
        ("Auto_Remote", remote_values, remote_filter),
        ("Auto_Current", current_values, current_filter),
    ]
    for column, available, selected in evidence_filters:
        if available:
            if selected:
                df = df[df[column].isin(selected)]
            else:
                df = df.iloc[0:0]

    if search.strip():
        q = search.strip().lower()
        mask = (
            df["Display_Title"].str.lower().str.contains(q, regex=False)
            | df["Display_Company"].str.lower().str.contains(q, regex=False)
        )
        df = df[mask]

    # Human workflow sorting remains independent of machine-evidence judgments:
    # unreviewed first, then freshest observable publication date.
    df["_review_order"] = (df["Review_Status"] == "REVIEWED").astype(int)
    df["_pub_dt"] = df.apply(best_publication_date, axis=1)
    df = df.sort_values(
        ["_review_order", "_pub_dt", "Display_Title"],
        ascending=[True, False, True],
        na_position="last",
        kind="stable",
    )
    return df.reset_index(drop=True)


def sidebar_metrics(master: pd.DataFrame) -> None:
    total = len(master)
    reviewed = int((master["Review_Status"] == "REVIEWED").sum())
    apply_n = int((master["Review_Decision"] == "APPLY").sum())
    stretch_n = int((master["Review_Decision"] == "STRETCH").sum())
    conflicts = int((master.get("Auto_Hard_Conflict", pd.Series(dtype=str)).astype(str).str.upper() == "YES").sum())
    evidence_n = int(master.get("Evidence_Version", pd.Series(dtype=str)).astype(str).str.strip().ne("").sum())
    st.sidebar.caption(
        f"{reviewed}/{total} reviewed · {apply_n} APPLY · {stretch_n} STRETCH · "
        f"{conflicts} machine conflicts"
    )
    st.sidebar.caption(f"Machine evidence: {evidence_n}/{total} records")
    if total:
        st.sidebar.progress(reviewed / total)


# ============================================================
# APP
# ============================================================


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🔎", layout="wide")
    st.title("Prospect Review")
    st.caption("Human review layer for gross prospects, factual enrichment, and deterministic machine evidence. Machine evidence is advisory and never overwrites your judgments.")

    try:
        master, enriched, reviews = load_master()
    except Exception as exc:
        st.error(f"Could not load prospect data: {exc}")
        st.stop()

    if master.empty:
        st.warning(f"No prospects found. Expected {GROSS_CSV} or {ENRICHED_CSV}.")
        st.stop()

    if not EVIDENCE_CSV.exists():
        st.warning(
            f"Machine evidence file not found: {EVIDENCE_CSV}. "
            "The review app will still work, but Auto_* signals will be blank."
        )

    sidebar_metrics(master)
    filtered = apply_filters(master)
    st.sidebar.caption(f"Showing {len(filtered)} of {len(master)} prospects")

    st.sidebar.divider()
    st.sidebar.caption("Files")
    st.sidebar.code(str(EVIDENCE_CSV), language=None)
    st.sidebar.code(str(REVIEWS_CSV), language=None)
    st.sidebar.code(str(APPLICATION_QUEUE_CSV), language=None)

    if filtered.empty:
        st.info("No prospects match the current filters.")
        st.stop()

    initialize_selection(filtered)
    current_key = st.session_state.get("current_url_key")
    matches = filtered.index[filtered["_url_key"] == current_key].tolist()
    if not matches:
        st.session_state.current_url_key = filtered.iloc[0]["_url_key"]
        matches = [0]
    pos = matches[0]
    row = filtered.iloc[pos]

    # Navigation bar.
    nav1, nav2, nav3, nav4 = st.columns([1, 1, 2, 2])
    with nav1:
        if st.button("← Previous", use_container_width=True):
            move_selection(filtered, -1)
            st.rerun()
    with nav2:
        if st.button("Next →", use_container_width=True):
            move_selection(filtered, 1)
            st.rerun()
    with nav3:
        st.markdown(f"**{pos + 1} / {len(filtered)}** in current view")
    with nav4:
        st.markdown(f"**Review:** {text_or_dash(row.get('Review_Status', ''))}")

    st.divider()

    # Vacancy header.
    st.header(text_or_dash(row.get("Display_Title", "")))
    company = text_or_dash(row.get("Display_Company", ""))
    st.subheader(company)

    b1, b2, b3, b4, b5, b6 = st.columns(6)
    b1.metric("Source", text_or_dash(row.get("Source", "")))
    b2.metric("Original grade", text_or_dash(row.get("Display_Grade", "")))
    b3.metric("Extracted seniority", text_or_dash(row.get("Display_Seniority", "")))
    b4.metric("Machine seniority", text_or_dash(row.get("Auto_Seniority", "")))
    b5.metric("Machine freshness", text_or_dash(first_nonempty(row.get("Auto_Freshness", ""), row.get("Freshness", ""))))
    b6.metric("Enrichment", text_or_dash(row.get("Enrichment", "")))

    current_status = clean(row.get("Current_Status", ""))
    if current_status.upper() in {"CLOSED", "HTTP_ERROR", "REQUEST_FAILED"}:
        st.error(f"Vacancy status: {current_status} — {clean(row.get('Status_Evidence', ''))}")
    elif current_status:
        st.info(f"Vacancy status: {current_status}" + (f" — {clean(row.get('Status_Evidence', ''))}" if clean(row.get('Status_Evidence', '')) else ""))

    link_url = clean(row.get("Review_URL", ""))
    if link_url:
        st.link_button("Open vacancy ↗", link_url)

    # Deterministic machine-evidence sidecar. These values are advisory only.
    st.markdown("### Machine evidence")
    st.caption("Deterministic signals from 08_extract_prospect_evidence.py. Review the supporting text before relying on a signal.")

    hard_conflict = clean(row.get("Auto_Hard_Conflict", ""))
    if hard_conflict.upper() == "YES":
        conflict_types = clean(row.get("Auto_Hard_Conflict_Types", ""))
        message = "Machine hard-conflict flag" + (f": {conflict_types}" if conflict_types else "")
        st.error(message)
        conflict_evidence = clean(row.get("Auto_Hard_Conflict_Evidence", ""))
        if conflict_evidence:
            st.caption(conflict_evidence)

    m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
    m1.metric("Czech", text_or_dash(row.get("Auto_Czech_Requirement", "")))
    m2.metric("English", text_or_dash(row.get("Auto_English_Acceptable", "")))
    m3.metric("Min. years", text_or_dash(row.get("Auto_Years_Experience_Min", "")))
    m4.metric("Seniority", text_or_dash(row.get("Auto_Seniority", "")))
    m5.metric("Work mode", text_or_dash(row.get("Auto_Remote", "")))
    m6.metric("Vacancy state", text_or_dash(row.get("Auto_Current", "")))
    m7.metric("Freshness", text_or_dash(row.get("Auto_Freshness", "")))

    t1, t2, t3, t4, t5 = st.columns(5)
    tech_cols = [t1, t2, t3, t4, t5]
    for container, (label, field, _) in zip(tech_cols, TECH_SIGNAL_FIELDS):
        container.metric(label, text_or_dash(row.get(field, "")))

    review_flags = clean(row.get("Auto_Review_Flags", ""))
    if review_flags:
        st.warning(f"Machine review flags: {review_flags}")

    with st.expander("Machine evidence — supporting text", expanded=False):
        render_auto_evidence("Czech", row.get("Auto_Czech_Evidence", ""))
        render_auto_evidence("English", row.get("Auto_English_Evidence", ""))
        render_auto_evidence("Experience", row.get("Auto_Years_Experience_Evidence", ""))
        render_auto_evidence("Seniority", row.get("Auto_Seniority_Evidence", ""))
        render_auto_evidence("Work mode", row.get("Auto_Remote_Evidence", ""))
        render_auto_evidence("Vacancy state", row.get("Auto_Current_Evidence", ""))
        render_auto_evidence("Freshness", row.get("Auto_Freshness_Evidence", ""))
        for label, _, evidence_field in TECH_SIGNAL_FIELDS:
            render_auto_evidence(label, row.get(evidence_field, ""))
        render_auto_evidence("Hard conflict", row.get("Auto_Hard_Conflict_Evidence", ""))
        render_fact("Evidence version", row.get("Evidence_Version", ""))
        render_fact("Evidence extracted", row.get("Evidence_Extracted_At", ""))

    # Raw vacancy facts and extraction diagnostics.
    left, right = st.columns([1.25, 1])
    with left:
        st.markdown("### Raw vacancy facts")
        render_fact("Publication", publication_display(row))
        render_fact("Mining date", row.get("Mining_Date", ""))
        render_fact("Valid through", row.get("Valid_Through", ""))
        render_fact("Repost text", row.get("Repost_Text", ""))
        render_fact("Location", row.get("Location_Detail", ""))
        render_fact("Remote mode", row.get("Remote_Mode", ""))
        render_fact("Employment type", row.get("Employment_Type", ""))
        render_fact("Salary", row.get("Salary_Detail", ""))
        render_fact("Languages", row.get("Languages", ""))
        render_fact("Skills", row.get("Skills", ""))
        render_fact("Applicant countries", row.get("Applicant_Countries", ""))

    with right:
        st.markdown("### Extraction diagnostics")
        render_fact("Parser", row.get("Parser", ""))
        render_fact("Structured data", row.get("Structured_Data_Found", ""))
        render_fact("Extraction status", row.get("Extraction_Status", ""))
        render_fact("Page language", row.get("Page_Language", ""))
        render_fact("HTTP", row.get("HTTP_Status", ""))
        render_fact("Publication method", row.get("Publication_Method", ""))
        render_fact("Publication confidence", row.get("Publication_Confidence", ""))
        err = clean(row.get("Extraction_Error", ""))
        if err:
            st.warning(err)

    with st.expander("Description", expanded=True):
        description = truncate(first_nonempty(row.get("Description", ""), "No description extracted."), 9000)
        st.write(description)

    requirements = clean(row.get("Requirements", ""))
    if requirements:
        with st.expander("Requirements", expanded=True):
            st.write(truncate(requirements, 7000))

    # Human review form.
    st.divider()
    st.markdown("### Your review")
    st.caption("These values are yours. Auto_* evidence above is advisory only; it does not prefill, score, or overwrite this form.")

    form_key = f"review_form_{row['_url_key']}"
    with st.form(form_key, clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            fit = st.selectbox("Profile fit", FIT_OPTIONS, index=option_index(FIT_OPTIONS, row.get("Review_Fit", "")))
            interest = st.selectbox("Interest", INTEREST_OPTIONS, index=option_index(INTEREST_OPTIONS, row.get("Review_Interest", "")))
        with c2:
            seniority = st.selectbox("Seniority accessibility", SENIORITY_OPTIONS, index=option_index(SENIORITY_OPTIONS, row.get("Review_Seniority", "")))
            language = st.selectbox("Language compatibility", LANGUAGE_OPTIONS, index=option_index(LANGUAGE_OPTIONS, row.get("Review_Language", "")))
        with c3:
            tech_match = st.selectbox("Technical match", TECH_OPTIONS, index=option_index(TECH_OPTIONS, row.get("Review_Tech_Match", "")))
            priority = st.selectbox("Application priority", PRIORITY_OPTIONS, index=option_index(PRIORITY_OPTIONS, row.get("Review_Priority", "")))

        current_decision = clean(row.get("Review_Decision", ""))
        decision_ui = st.radio(
            "Decision",
            DECISION_UI_OPTIONS,
            index=DECISION_UI_OPTIONS.index(current_decision) if current_decision in DECISION_UI_OPTIONS else 0,
            horizontal=True,
        )
        decision = "" if decision_ui == "Not decided" else decision_ui

        reasons = st.multiselect(
            "Reasons / signals",
            REASON_OPTIONS,
            default=review_reason_list(row.get("Review_Reasons", "")),
        )
        notes = st.text_area("Notes", value=clean(row.get("Review_Notes", "")), height=130)

        s1, s2 = st.columns([1, 1])
        save_next = s1.form_submit_button("Save + next unreviewed", type="primary", use_container_width=True)
        save_stay = s2.form_submit_button("Save", use_container_width=True)

    if save_next or save_stay:
        review = {
            "ID": first_nonempty(row.get("ID", ""), row.get("ID_enriched", "")),
            "Input_URL": row.get("Input_URL", ""),
            "Review_Status": "REVIEWED",
            "Review_Fit": fit,
            "Review_Interest": interest,
            "Review_Seniority": seniority,
            "Review_Language": language,
            "Review_Tech_Match": tech_match,
            "Review_Priority": priority,
            "Review_Decision": decision,
            "Review_Reasons": " | ".join(reasons),
            "Review_Notes": notes,
            "Reviewed_At": row.get("Reviewed_At", ""),
            "Review_Updated_At": row.get("Review_Updated_At", ""),
        }
        upsert_review(review)
        st.toast("Review saved")
        if save_next:
            # Reload filtered data so the just-saved review status is respected.
            refreshed, _, _ = load_master()
            refreshed_filtered = apply_filters_without_widgets(refreshed, filtered)
            next_unreviewed(refreshed_filtered if not refreshed_filtered.empty else refreshed, row["_url_key"])
        st.rerun()

    st.divider()
    with st.expander("Raw record / debugging"):
        debug_fields = [
            "ID", "Input_URL", "Final_URL", "Canonical_URL", "Job_Title_Input", "Company_Input",
            "Seniority_Input", "Grade_Input", "Published_At", "Published_Lower_Bound",
            "Published_Upper_Bound", "Publication_Method", "Publication_Confidence",
            "Extraction_Date", "Extraction_Status", "Extraction_Error", "Parser",
            "Evidence_Version", "Evidence_Extracted_At", "Auto_Hard_Conflict",
            "Auto_Hard_Conflict_Types", "Auto_Review_Flags",
        ]
        raw = {k: clean(row.get(k, "")) for k in debug_fields if k in row.index}
        st.json(raw)



def apply_filters_without_widgets(master: pd.DataFrame, previous_filtered: pd.DataFrame) -> pd.DataFrame:
    """
    Best-effort post-save navigation helper.

    Streamlit filter widgets are rebuilt on rerun. For the immediate next-selection step,
    retain the URLs that were in the previous view and refresh their review state.
    """
    if previous_filtered.empty:
        return master
    allowed = set(previous_filtered["_url_key"].tolist())
    return master[master["_url_key"].isin(allowed)].copy()


if __name__ == "__main__":
    main()
