from __future__ import annotations

import argparse
import os
import re
import tempfile
import unicodedata
from pathlib import Path

import pandas as pd


# ============================================================
# CONFIGURATION
# ============================================================

GROUP_CONFIG = {
    "DATA_SCIENCE_MODELLING": {
        "order": 10,
        "cv": "CV-DATA",
        "cover_letter": "CL-MODELLING",
    },
    "ANALYTICS_INSIGHTS": {
        "order": 20,
        "cv": "CV-DATA",
        "cover_letter": "CL-INSIGHTS",
    },
    "DATA_ENGINEERING_DWH": {
        "order": 30,
        "cv": "CV-ENGINEERING",
        "cover_letter": "CL-DATA-ENGINEERING",
    },
    "SOLUTION_SYSTEMS_CONSULTING": {
        "order": 40,
        "cv": "CV-ANALYST",
        "cover_letter": "CL-SOLUTIONS",
    },
    "SOFTWARE_BACKEND_FULLSTACK": {
        "order": 50,
        "cv": "CV-SOFTWARE",
        "cover_letter": "CL-BACKEND",
    },
    "MARKETING_TECH_AUTOMATION": {
        "order": 60,
        "cv": "CV-ANALYST",
        "cover_letter": "CL-MARTECH",
    },
    "UNCLASSIFIED": {
        "order": 99,
        "cv": "CV-GENERAL",
        "cover_letter": "CL-GENERAL",
    },
}

DECISION_ORDER = {"APPLY": 0, "STRETCH": 1}
PRIORITY_ORDER = {"Immediate": 0, "High": 1, "Normal": 2, "Low": 3, "": 4}
FRESHNESS_ORDER = {"NEW": 0, "FRESH": 1, "RECENT": 2, "AGING": 3, "OLD": 4, "UNKNOWN": 5, "": 6}


# ============================================================
# PATHS / IO
# ============================================================


def discover_data_mining_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if parent.name.lower() == "data_mining":
            return parent
    return Path.cwd()


ROOT = discover_data_mining_root()
DEFAULT_INPUT = ROOT / "data" / "07_prospects" / "application_queue.csv"
DEFAULT_OUTPUT = ROOT / "data" / "07_prospects" / "application_queue_grouped.csv"


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


# ============================================================
# TEXT NORMALIZATION
# ============================================================


def clean(value) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.lower() == "nan":
        return ""
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(value) -> str:
    """Lowercase, remove accents, and normalize punctuation for matching."""
    text = clean(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9+#./ -]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


# ============================================================
# APPLICATION FAMILY CLASSIFICATION
# ============================================================


def classify_application_group(row: pd.Series) -> str:
    """
    Classify by application story, not merely by normalized occupation title.

    Precedence matters. For example:
      - "Marketing Data Analyst" belongs to ANALYTICS_INSIGHTS, not martech.
      - "DWH Developer" belongs to DATA_ENGINEERING_DWH, not software.
      - "Technical Solutions Analyst (Catastrophe Modeling)" belongs to
        SOLUTION_SYSTEMS_CONSULTING, not modelling.
    """
    title = normalize_text(row.get("Job_Title", ""))

    # 1) Data engineering / warehouse titles are highly specific.
    if contains_any(title, (
        r"\bdata engineer(?:s|ing)?\b",
        r"\bdwh\b",
        r"\bdata warehouse\b",
        r"\betl developer\b",
        r"\bdata pipeline engineer\b",
    )):
        return "DATA_ENGINEERING_DWH"

    # 2) Solution / system analysis and technology consulting.
    if contains_any(title, (
        r"\b(?:it|technical|technology) solutions? analyst\b",
        r"\bsolutions? analyst\b",
        r"\bis analytik\b",
        r"\bsystem(?:s)? analyst\b",
        r"\bapplication specialist\b",
        r"\baplikacni specialista\b",
    )):
        return "SOLUTION_SYSTEMS_CONSULTING"

    # Market-intelligence / CX consultants are insight roles, not general consulting.
    if contains_any(title, (
        r"\bmarket intelligence\b",
        r"\bcustomer experience\b",
        r"\bcustomer insights?\b",
        r"\binsights? consultant\b",
    )):
        return "ANALYTICS_INSIGHTS"

    if contains_any(title, (
        r"\bconsultant\b",
        r"\bconsulting\b",
        r"\bprocesy\b",
        r"\bmanagement\b",
    )):
        return "SOLUTION_SYSTEMS_CONSULTING"

    # 3) Quantitative / statistical / DS modelling roles.
    if contains_any(title, (
        r"\bdata scientist\b",
        r"\bquantitative\b",
        r"\bstatistical model(?:er|ing)\b",
        r"\bcredit risk model(?:er|ing)\b",
        r"\brisk model(?:er|ing)\b",
        r"\bmachine learning scientist\b",
    )):
        return "DATA_SCIENCE_MODELLING"

    # A data analyst whose role is explicitly about modelling belongs with modelling.
    if "data analyst" in title and contains_any(title, (r"\bmodel(?:er|ing)\b", r"\brisk\b")):
        return "DATA_SCIENCE_MODELLING"

    # 4) General analytics, research and insight roles.
    if contains_any(title, (
        r"\bdata analyst\b",
        r"\bdatovy analytik\b",
        r"\banalytik junior\b",
        r"\bresearch .*analyst\b",
        r"\bresearch analyst\b",
        r"\binsights?\b",
        r"\bmarket research\b",
        r"\bmarketing data analyst\b",
        r"\bresearch and sales analyst\b",
    )):
        return "ANALYTICS_INSIGHTS"

    # 5) Marketing technology / CRM / web optimization.
    if contains_any(title, (
        r"\bcrm\b",
        r"\bcdp\b",
        r"\bweb optimization\b",
        r"\bseo\b",
        r"\bdigital marketing\b",
        r"\bmarketing automation\b",
        r"\bautomation specialist\b",
    )):
        return "MARKETING_TECH_AUTOMATION"

    # 6) Backend / full-stack / general software engineering.
    if contains_any(title, (
        r"\bfull[ -]?stack\b",
        r"\bsoftware engineer\b",
        r"\bbackend\b",
        r"\bback-end\b",
        r"\bruby\b",
        r"\brails\b",
        r"\bror\b",
        r"\bdeveloper\b",
        r"\bprogramator\b",
        r"\bvyvojar\b",
        r"\bnestjs\b",
    )):
        return "SOFTWARE_BACKEND_FULLSTACK"

    # Conservative fallback scoring from the user's own review notes/reasons.
    # This only runs when the title itself is not informative enough.
    context = normalize_text(
        " ".join([
            clean(row.get("Reasons", "")),
            clean(row.get("Notes", "")),
        ])
    )

    scores = {key: 0 for key in GROUP_CONFIG if key != "UNCLASSIFIED"}

    fallback_signals = {
        "DATA_SCIENCE_MODELLING": (
            r"\bmachine learning\b", r"\bstatistical\b", r"\bmodelling\b",
            r"\bmodeling\b", r"\btime series\b", r"\bquantitative\b",
        ),
        "ANALYTICS_INSIGHTS": (
            r"\bdata analysis\b", r"\binsights?\b", r"\bresearch\b",
            r"\bsurvey\b", r"\bvisualization\b", r"\bdashboard\b",
        ),
        "DATA_ENGINEERING_DWH": (
            r"\bdata pipeline\b", r"\betl\b", r"\bdwh\b", r"\bsnowflake\b",
            r"\bdatabricks\b", r"\bdata warehouse\b",
        ),
        "SOLUTION_SYSTEMS_CONSULTING": (
            r"\brequirements\b", r"\bprocess improvement\b", r"\bdigital transformation\b",
            r"\bstakeholder\b", r"\bsystem\b", r"\bconsulting\b",
        ),
        "SOFTWARE_BACKEND_FULLSTACK": (
            r"\bfastapi\b", r"\bflask\b", r"\bapi\b", r"\bbackend\b",
            r"\bweb app\b", r"\bprogramming\b", r"\bruby\b",
        ),
        "MARKETING_TECH_AUTOMATION": (
            r"\bcrm\b", r"\bseo\b", r"\bhtml\b", r"\bcss\b",
            r"\bweb optimization\b", r"\bmarketing automation\b",
        ),
    }

    for group, patterns in fallback_signals.items():
        scores[group] = sum(1 for pattern in patterns if re.search(pattern, context, flags=re.IGNORECASE))

    best_group = max(scores, key=scores.get)
    return best_group if scores[best_group] > 0 else "UNCLASSIFIED"


# ============================================================
# APPLICATION PACK / COMPANY BATCH
# ============================================================


def company_batch(company: str, job_id: str) -> str:
    company = clean(company)
    if not company:
        return f"UNKNOWN_COMPANY_{clean(job_id) or 'JOB'}"

    normalized = normalize_text(company)

    aliases = (
        (r"\bsiemens\b", "SIEMENS"),
        (r"\baon\b", "AON"),
        (r"\btimepress\b", "TIMEPRESS"),
        (r"\braiffeisenbank\b", "RAIFFEISENBANK"),
        (r"\bcreditinfo\b", "CREDITINFO"),
        (r"\bcsob\b", "CSOB"),
        (r"\bcreditas\b", "CREDITAS"),
        (r"\bquantco\b", "QUANTCO"),
    )
    for pattern, batch in aliases:
        if re.search(pattern, normalized):
            return batch

    # Stable readable fallback from company name.
    batch = re.sub(r"[^A-Z0-9]+", "_", company.upper()).strip("_")
    return batch or f"UNKNOWN_COMPANY_{clean(job_id) or 'JOB'}"


def enrich_groups(df: pd.DataFrame) -> pd.DataFrame:
    required = {"ID", "Job_Title", "Company", "Decision", "Priority", "Freshness"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(f"Input application queue is missing required columns: {', '.join(missing)}")

    out = df.copy()
    out["Application_Group"] = out.apply(classify_application_group, axis=1)
    out["CV_Variant"] = out["Application_Group"].map(lambda g: GROUP_CONFIG[g]["cv"])
    out["Cover_Letter_Variant"] = out["Application_Group"].map(
        lambda g: GROUP_CONFIG[g]["cover_letter"]
    )
    out["Application_Batch"] = out.apply(
        lambda r: company_batch(r.get("Company", ""), r.get("ID", "")), axis=1
    )

    # Sort for application sessions: family -> human decision -> priority -> company batch -> freshness.
    out["_group_order"] = out["Application_Group"].map(lambda g: GROUP_CONFIG[g]["order"])
    out["_decision_order"] = out["Decision"].map(DECISION_ORDER).fillna(99)
    out["_priority_order"] = out["Priority"].map(PRIORITY_ORDER).fillna(99)
    out["_freshness_order"] = out["Freshness"].map(FRESHNESS_ORDER).fillna(99)

    out = out.sort_values(
        ["_group_order", "_decision_order", "_priority_order", "Application_Batch", "_freshness_order", "Job_Title"],
        ascending=[True, True, True, True, True, True],
        kind="stable",
    ).reset_index(drop=True)

    out = out.drop(columns=["_group_order", "_decision_order", "_priority_order", "_freshness_order"])

    # Insert grouping columns directly after Company for easy scanning.
    original_cols = [c for c in df.columns if c not in {
        "Application_Group", "CV_Variant", "Cover_Letter_Variant", "Application_Batch"
    }]
    prefix = ["ID", "Job_Title", "Company"]
    grouping = ["Application_Group", "CV_Variant", "Cover_Letter_Variant", "Application_Batch"]
    remainder = [c for c in original_cols if c not in prefix]
    return out[prefix + grouping + remainder]


# ============================================================
# CLI
# ============================================================


def print_summary(df: pd.DataFrame, output_path: Path) -> None:
    print(f"Grouped application queue written to: {output_path}")
    print(f"Rows: {len(df)}")
    print()
    print("Application groups:")
    counts = df["Application_Group"].value_counts()
    ordered_groups = sorted(counts.index, key=lambda g: GROUP_CONFIG.get(g, {"order": 999})["order"])
    for group in ordered_groups:
        print(f"  {group}: {int(counts[group])}")

    print()
    print("Reusable company batches (>1 role):")
    batch_counts = df["Application_Batch"].value_counts()
    repeated = batch_counts[batch_counts > 1]
    if repeated.empty:
        print("  None")
    else:
        for batch, count in repeated.items():
            print(f"  {batch}: {int(count)}")

    unclassified = df[df["Application_Group"] == "UNCLASSIFIED"]
    if not unclassified.empty:
        print()
        print("WARNING: Unclassified roles require manual rule review:")
        for _, row in unclassified.iterrows():
            print(f"  {row['ID']}: {row['Job_Title']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Group application_queue.csv into reusable application families and company batches."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Input application_queue.csv")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output grouped CSV")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Application queue not found: {input_path}")

    df = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    grouped = enrich_groups(df)
    atomic_write_csv(grouped, output_path)
    print_summary(grouped, output_path)


if __name__ == "__main__":
    main()
