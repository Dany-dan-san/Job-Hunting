from __future__ import annotations

import csv
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


# =============================================================================
# PATHS
# =============================================================================

INPUT_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data"
    r"\04_enriched\register_enriched.csv"
)

OUTPUT_DIR = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data"
    r"\05_review_queues"
)


# =============================================================================
# CONFIGURATION
# =============================================================================

RECENT_MINING_DAYS = 3

HIGH_PRIORITY_SCORE = 9
GOOD_PRIORITY_SCORE = 6
EXPLORATORY_PRIORITY_SCORE = 3

INPUT_COLUMNS = [
    "ID",
    "Job.Title",
    "Salary",
    "Location",
    "Position.Type",
    "Position.Subtype",
    "Seniority",
    "Tags",
    "Technology",
    "URL",
    "Mining_Date",
]

ADDED_COLUMNS = [
    "Review.Priority",
    "Fit.Score",
    "Primary.Track",
    "Location.Fit",
    "Mining.Age.Days",
    "Fit.Reasons",
    "Caution.Flags",
]

OUTPUT_COLUMNS = INPUT_COLUMNS + ADDED_COLUMNS

HEADER_ALIASES = {
    "ID": "ID",
    "Job Title": "Job.Title",
    "Job.Title": "Job.Title",
    "Salary": "Salary",
    "Location": "Location",
    "Position Type": "Position.Type",
    "Position.Type": "Position.Type",
    "Root Position": "Position.Type",
    "Position Subtype": "Position.Subtype",
    "Position.Subtype": "Position.Subtype",
    "Seniority": "Seniority",
    "Tags": "Tags",
    "Domain Tags": "Tags",
    "Technology": "Technology",
    "URL": "URL",
    "Mining_Date": "Mining_Date",
    "Mining Date": "Mining_Date",
}


# =============================================================================
# POSITION SUBTYPE GROUPS
# =============================================================================

DATA_SCIENCE_SUBTYPES = {
    "data scientist",
    "ai/ml analyst",
    "ai/ml engineer",
    "statistical/quantitative scientist",
    "quantitative/statistical analyst",
    "ai/automation specialist",
    "ai/transformation consultant",
}

DATA_ANALYTICS_SUBTYPES = {
    "data/bi analyst",
    "data/analytics specialist",
    "data/analytics consultant",
    "data governance/quality specialist",
    "marketing/customer insights analyst",
    "product analyst",
    "quantitative/statistical analyst",
    "technical/r&d analyst",
}

DATA_ENGINEERING_SUBTYPES = {
    "data engineer",
    "data/database developer",
    "data architect",
    "data/analytics support",
    "automation/low-code developer",
    "ai/automation specialist",
}

BUSINESS_PRODUCT_SUBTYPES = {
    "business/process analyst",
    "it/systems analyst",
    "product analyst",
    "sales/commercial analyst",
    "technical/r&d analyst",
    "business/process consultant",
}

SOFTWARE_SUBTYPES = {
    "general software developer",
    "software engineer",
    "backend developer",
    "full-stack developer",
    "data/database developer",
    "automation/low-code developer",
    "enterprise applications developer",
}

ADJACENT_TECHNICAL_SUBTYPES = {
    "qa/test analyst",
    "qa/test engineer",
    "general software tester",
    "functional/qa tester",
    "automation tester",
    "it/technology consultant",
    "it/systems specialist",
    "application/software support",
    "data/analytics support",
}

FINANCE_SUBTYPES = {
    "finance/investment analyst",
    "risk/credit analyst",
    "finance/banking specialist",
    "finance/risk manager",
    "financial controller",
    "business/commercial controller",
    "general accountant",
    "financial accountant",
    "internal auditor",
    "finance/transactions consultant",
}

GENERIC_MARKETING_SUBTYPES = {
    "marketing/communications specialist",
    "marketing/brand manager",
    "sales/marketing coordinator",
    "marketing/sales consultant",
}

NON_TARGET_ROOTS = {
    "accountant",
    "controller",
    "auditor",
    "recruiter",
    "sales representative",
    "customer service representative",
    "assistant",
    "teacher/trainer",
    "healthcare professional",
    "operator",
    "driver",
    "warehouse worker",
    "production worker",
    "agent",
    "officer",
    "clerk",
    "other",
}


# =============================================================================
# TEXT HELPERS
# =============================================================================

def clean_value(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize(value: object) -> str:
    """Lowercase text and remove accents for Czech/English matching."""

    text = clean_value(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip()


def contains(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def combined_text(row: dict[str, str]) -> str:
    return normalize(
        " | ".join(
            [
                row.get("Job.Title", ""),
                row.get("Position.Type", ""),
                row.get("Position.Subtype", ""),
                row.get("Seniority", ""),
                row.get("Tags", ""),
                row.get("Technology", ""),
            ]
        )
    )


def add_once(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


# =============================================================================
# CSV INPUT
# =============================================================================

def read_register() -> list[dict[str, str]]:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input file was not found:\n{INPUT_FILE}")

    with INPUT_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)

        if not reader.fieldnames:
            raise ValueError("The input CSV does not contain a header.")

        mapped_headers = {
            header: HEADER_ALIASES.get(clean_value(header), clean_value(header))
            for header in reader.fieldnames
        }

        rows: list[dict[str, str]] = []

        for source_row in reader:
            row = {column: "" for column in INPUT_COLUMNS}

            for source_name, value in source_row.items():
                target_name = mapped_headers.get(source_name, source_name)

                if target_name in row:
                    row[target_name] = clean_value(value)

            if row["ID"] or row["URL"]:
                rows.append(row)

    missing = [
        column
        for column in INPUT_COLUMNS
        if not any(column in HEADER_ALIASES.values() for _ in [column])
    ]

    if not rows:
        raise ValueError("No usable job rows were found in the input CSV.")

    return rows


# =============================================================================
# FEATURE DETECTION
# =============================================================================

def detect_features(row: dict[str, str]) -> dict[str, bool]:
    text = combined_text(row)
    title = normalize(row["Job.Title"])
    subtype = normalize(row["Position.Subtype"])
    root = normalize(row["Position.Type"])
    seniority = normalize(row["Seniority"])
    location = normalize(row["Location"])

    python_match = contains(
        text,
        r"\bpython\b|\bpandas\b|\bpolars\b|\bscikit[- ]?learn\b|"
        r"\bfastapi\b|\bdjango\b|\bflask\b",
    )

    sql_match = contains(
        text,
        r"\bsql\b|\bpostgres(?:ql)?\b|\bmysql\b|\boracle\b|"
        r"\bmssql\b|\bsnowflake\b|\bbigquery\b|\bdatabricks\b|"
        r"\bdata ?warehouse\b|\bdwh\b|\betl\b|\belt\b",
    )

    r_match = contains(
        text,
        r"(^|[^a-z])r([^a-z]|$)|\brstudio\b|\btidyverse\b|\bshiny\b",
    )

    ml_ai_match = contains(
        text,
        r"\bmachine learning\b|\bdeep learning\b|\bdata scien(?:ce|tist)\b|"
        r"\bai\b|\bartificial intelligence\b|\bml engineer\b|\bllm\b|"
        r"\bnlp\b|\bcomputer vision\b|\bgen(?:erative)? ai\b",
    )

    modelling_match = contains(
        text,
        r"\bmodell?ing\b|\bmodelovani\b|\bstatistic|\bforecast|"
        r"\bpredictive\b|\bregression\b|\bclassification\b|\bclustering\b|"
        r"\bexperimentation\b|\ba/?b test",
    )

    pipeline_match = contains(
        text,
        r"\bdata pipeline|\betl\b|\belt\b|\bdata engineer|\bdata platform|"
        r"\bairflow\b|\bspark\b|\bkafka\b|\bdbt\b|\bdagster\b|"
        r"\borchestrat|\bdata integration",
    )

    insights_match = contains(
        text,
        r"\bcustomer insight|\bmarket insight|\bconsumer insight|"
        r"\bpricing analyst|\bmarketing analyst|\bmarket research|"
        r"\bmarket intelligence|\bproduct analys|\bbehavioral analys|"
        r"\bcustomer analy|\bsegmentation\b|\bresearch analyst",
    )

    french_match = contains(
        text,
        r"\bfrench\b|\bfrancais\b|\bfrancouz|\bfrancophone\b|"
        r"\bfr\b.{0,12}\bspeaker\b",
    )

    junior_match = contains(
        seniority + " " + title,
        r"\bjunior\b|\bentry\b|\bgraduate\b|\btrainee\b|\bintern(ship)?\b|"
        r"\babsolvent|\bstudent|\bjr\.?\b",
    )

    mid_match = contains(
        seniority + " " + title,
        r"\bmedior\b|\bmid[- ]?level\b|\bmiddle\b",
    )

    senior_match = contains(
        seniority + " " + title,
        r"\bsenior\b|\bsr\.?\b|\bstaff\b|\bprincipal\b|\bexpert\b",
    )

    leadership_match = contains(
        title,
        r"\bhead of\b|\bdirector\b|\bchief\b|\bteam lead\b|\btech lead\b|"
        r"\blead engineer\b|\blead developer\b|\bvedouci\b|\breditel|"
        r"\bmanager\b|\bmanazer|\bmanagement\b",
    )

    explicit_czech_match = contains(
        text,
        r"\bfluent czech\b|\bnative czech\b|\bczech.*required\b|"
        r"\bmandatory czech\b|\bcz.*required\b|\bcesky jazyk.*podmink|"
        r"\bplynula cestina\b|\brodily mluvci\b|\bbez cestiny nelze\b",
    )

    driver_match = contains(
        text,
        r"\bdriver'?s licence\b|\bdriving licence\b|\bridicsk[ey]\s+prukaz\b|"
        r"\bridicak\b|\bskupin[ay]\s*b\b",
    )

    finance_role = (
        subtype in FINANCE_SUBTYPES
        or root in {"accountant", "controller", "auditor"}
        or contains(
            title,
            r"\baccountant\b|\bucetni\b|\bcontroller\b|\bkontroler\b|"
            r"\bauditor\b|\btax\b|\bdanov|\bpayroll\b|\bvaluation\b|"
            r"\btreasury\b|\bcredit risk\b|\bfinancial reporting\b",
        )
    )

    bi_reporting_only = (
        contains(
            text,
            r"\bpower bi\b|\btableau\b|\breporting\b|\breport developer\b|"
            r"\bdashboard\b|\bbi developer\b",
        )
        and not any(
            [
                python_match,
                ml_ai_match,
                modelling_match,
                pipeline_match,
                insights_match,
            ]
        )
    )

    generic_marketing = (
        subtype in GENERIC_MARKETING_SUBTYPES
        or contains(
            title,
            r"\bsocial media\b|\bcontent creator\b|\bcopywriter\b|"
            r"\bbrand manager\b|\bdigital marketing\b|\bmarketing communication",
        )
    ) and not insights_match

    non_target_root = root in NON_TARGET_ROOTS

    prague_match = contains(
        location,
        r"\bpraha\b|\bprague\b|\bhlavni mesto praha\b",
    )

    remote_match = contains(
        location + " " + text,
        r"\bremote\b|\bfully remote\b|\bhome office\b|\bwork from home\b",
    )

    czechia_match = contains(
        location,
        r"\bczech republic\b|\bczechia\b|\bceska republika\b|\bcz\b",
    )

    foreign_location = bool(location) and not (
        prague_match or remote_match or czechia_match
    )

    return {
        "python": python_match,
        "sql": sql_match,
        "r": r_match,
        "ml_ai": ml_ai_match,
        "modelling": modelling_match,
        "pipeline": pipeline_match,
        "insights": insights_match,
        "french": french_match,
        "junior": junior_match,
        "mid": mid_match,
        "senior": senior_match,
        "leadership": leadership_match,
        "explicit_czech": explicit_czech_match,
        "driver": driver_match,
        "finance_role": finance_role,
        "bi_reporting_only": bi_reporting_only,
        "generic_marketing": generic_marketing,
        "non_target_root": non_target_root,
        "prague": prague_match,
        "remote": remote_match,
        "czechia": czechia_match,
        "foreign_location": foreign_location,
    }


# =============================================================================
# PRIMARY TRACK
# =============================================================================

def assign_primary_track(
    row: dict[str, str],
    features: dict[str, bool],
) -> str:
    subtype = normalize(row["Position.Subtype"])
    text = combined_text(row)

    if (
        subtype in DATA_SCIENCE_SUBTYPES
        or contains(
            text,
            r"\bdata scientist\b|\bmachine learning\b|\bml engineer\b|"
            r"\bai/ml\b|\bstatistical model|\bquantitative scientist\b",
        )
    ):
        return "Data Science & Machine Learning"

    if (
        features["insights"]
        or contains(
            text,
            r"\bcustomer insights?\b|\bmarket research\b|\bpricing analyst\b|"
            r"\bmarket intelligence\b|\bconsumer insights?\b",
        )
    ):
        return "Customer/Market/Pricing Insights"

    if (
        subtype in DATA_ENGINEERING_SUBTYPES
        or features["pipeline"]
        or contains(
            text,
            r"\bdata engineer\b|\bdata architect\b|\bdata platform\b|"
            r"\bdata pipeline\b",
        )
    ):
        return "Data Engineering & Automation"

    if (
        subtype in DATA_ANALYTICS_SUBTYPES
        or contains(
            text,
            r"\bdata analyst\b|\bdatov[ya] analytik\b|\banalytics specialist\b|"
            r"\bdata quality\b|\bdata governance\b|\bbi analyst\b",
        )
    ):
        return "Core Data & Analytics"

    if (
        subtype in BUSINESS_PRODUCT_SUBTYPES
        or contains(
            text,
            r"\bbusiness analyst\b|\bprocess analyst\b|\bproduct analyst\b|"
            r"\bit analyst\b|\bprocesni analytik\b|\bbusiness consultant\b",
        )
    ):
        return "Business/Product Analysis"

    if (
        subtype in SOFTWARE_SUBTYPES
        or contains(
            text,
            r"\bpython developer\b|\bsoftware developer\b|\bbackend developer\b|"
            r"\bfull[- ]?stack developer\b|\bprogramator\b|\bvyvojar\b",
        )
    ):
        return "Python/Software Development"

    if (
        subtype in ADJACENT_TECHNICAL_SUBTYPES
        or contains(
            text,
            r"\bqa\b|\btester\b|\btest engineer\b|\bit consultant\b|"
            r"\bit support\b|\bapplication support\b|\bsystem specialist\b",
        )
    ):
        return "Adjacent Technical"

    return "Other"


# =============================================================================
# LOCATION AND DATE
# =============================================================================

def assign_location_fit(features: dict[str, bool]) -> str:
    if features["prague"]:
        return "Prague"

    if features["remote"]:
        return "Remote/Hybrid"

    if features["czechia"]:
        return "Czechia - verify Prague/remote"

    if features["foreign_location"]:
        return "Outside Prague - verify"

    return "Unknown - verify"


def parse_mining_date(value: str) -> datetime | None:
    value = clean_value(value)

    if not value:
        return None

    candidates = [
        value,
        value.replace("Z", "+00:00"),
    ]

    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)

            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass

    formats = [
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y",
        "%d/%m/%Y",
    ]

    for date_format in formats:
        try:
            parsed = datetime.strptime(value, date_format)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    return None


def mining_age_days(value: str) -> int | None:
    parsed = parse_mining_date(value)

    if parsed is None:
        return None

    age = datetime.now(timezone.utc) - parsed
    return max(0, age.days)


# =============================================================================
# SCORING
# =============================================================================

BASE_TRACK_SCORES = {
    "Other": 0,
    "Adjacent Technical": 2,
    "Python/Software Development": 4,
    "Business/Product Analysis": 4,
    "Core Data & Analytics": 5,
    "Data Engineering & Automation": 6,
    "Customer/Market/Pricing Insights": 7,
    "Data Science & Machine Learning": 7,
}


def score_job(
    row: dict[str, str],
    features: dict[str, bool],
    primary_track: str,
    location_fit: str,
) -> tuple[int, list[str], list[str], bool]:
    score = BASE_TRACK_SCORES.get(primary_track, 0)
    reasons: list[str] = []
    cautions: list[str] = []

    if primary_track != "Other":
        add_once(reasons, primary_track)

    if features["python"]:
        score += 2
        add_once(reasons, "Python")

    if features["sql"]:
        score += 2
        add_once(reasons, "SQL/data platform")

    if features["r"]:
        score += 1
        add_once(reasons, "R")

    if features["ml_ai"] or features["modelling"]:
        score += 2
        add_once(reasons, "ML/AI or modelling")

    if features["insights"]:
        score += 2
        add_once(reasons, "Insights/research orientation")

    if features["french"]:
        score += 3
        add_once(reasons, "French-language advantage")

    if features["junior"]:
        score += 3
        add_once(reasons, "Junior/entry level")

    if features["mid"]:
        score += 1
        add_once(reasons, "Medior level")

    if features["senior"]:
        score -= 4
        add_once(cautions, "Senior-level position")

    if features["leadership"]:
        score -= 4
        add_once(cautions, "Leadership/management position")

    if features["finance_role"]:
        score -= 3
        add_once(cautions, "Finance/accounting orientation")

    if features["bi_reporting_only"]:
        score -= 2
        add_once(cautions, "Primarily BI/reporting")

    if features["generic_marketing"]:
        score -= 4
        add_once(cautions, "Generic marketing role")

    if features["non_target_root"]:
        score -= 7
        add_once(cautions, "Non-target root occupation")

    if location_fit in {
        "Czechia - verify Prague/remote",
        "Outside Prague - verify",
        "Unknown - verify",
    }:
        score -= 1
        add_once(cautions, "Location requires verification")

    hard_exclusion = False

    if features["explicit_czech"]:
        score -= 10
        hard_exclusion = True
        add_once(cautions, "Explicit Czech-language requirement")

    if features["driver"]:
        score -= 10
        hard_exclusion = True
        add_once(cautions, "Driver's licence requirement")

    return score, reasons, cautions, hard_exclusion


def assign_review_priority(
    score: int,
    features: dict[str, bool],
    hard_exclusion: bool,
) -> str:
    if hard_exclusion:
        return "Exclude"

    senior_or_leadership = features["senior"] or features["leadership"]

    if senior_or_leadership:
        if score >= EXPLORATORY_PRIORITY_SCORE:
            return "Senior/Leadership Stretch"
        return "Deprioritized"

    if score >= HIGH_PRIORITY_SCORE:
        return "High"

    if score >= GOOD_PRIORITY_SCORE:
        return "Good"

    if score >= EXPLORATORY_PRIORITY_SCORE:
        return "Exploratory"

    return "Deprioritized"


# =============================================================================
# ROW CLASSIFICATION
# =============================================================================

def classify_row(row: dict[str, str]) -> dict[str, str]:
    features = detect_features(row)
    primary_track = assign_primary_track(row, features)
    location_fit = assign_location_fit(features)

    score, reasons, cautions, hard_exclusion = score_job(
        row=row,
        features=features,
        primary_track=primary_track,
        location_fit=location_fit,
    )

    priority = assign_review_priority(
        score=score,
        features=features,
        hard_exclusion=hard_exclusion,
    )

    age = mining_age_days(row["Mining_Date"])

    enriched = dict(row)
    enriched["Review.Priority"] = priority
    enriched["Fit.Score"] = str(score)
    enriched["Primary.Track"] = primary_track
    enriched["Location.Fit"] = location_fit
    enriched["Mining.Age.Days"] = "" if age is None else str(age)
    enriched["Fit.Reasons"] = "; ".join(reasons)
    enriched["Caution.Flags"] = "; ".join(cautions)

    return enriched


# =============================================================================
# SORTING AND OUTPUT
# =============================================================================

PRIORITY_ORDER = {
    "High": 1,
    "Good": 2,
    "Exploratory": 3,
    "Senior/Leadership Stretch": 4,
    "Deprioritized": 5,
    "Exclude": 6,
}


def sort_key(row: dict[str, str]) -> tuple:
    priority_rank = PRIORITY_ORDER.get(row["Review.Priority"], 99)

    try:
        score = int(row["Fit.Score"])
    except (TypeError, ValueError):
        score = -999

    mining_date = parse_mining_date(row["Mining_Date"])
    timestamp = mining_date.timestamp() if mining_date else 0

    return (
        priority_rank,
        -score,
        -timestamp,
        normalize(row["Job.Title"]),
    )


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """
    utf-8-sig writes a UTF-8 BOM, making the output easier to open correctly
    in Excel on Windows.
    """

    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=OUTPUT_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(sorted(rows, key=sort_key))


def select(
    rows: list[dict[str, str]],
    condition: Callable[[dict[str, str]], bool],
) -> list[dict[str, str]]:
    return [row for row in rows if condition(row)]


# =============================================================================
# REVIEW QUEUES
# =============================================================================

def build_review_queues(
    rows: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    active_priorities = {"High", "Good", "Exploratory"}

    queues = {
        "00_all_jobs_scored.csv": rows,

        "01_priority_review.csv": select(
            rows,
            lambda row: row["Review.Priority"] in {"High", "Good"},
        ),

        "02_data_science_ml.csv": select(
            rows,
            lambda row:
                row["Primary.Track"] == "Data Science & Machine Learning"
                and row["Review.Priority"] in active_priorities,
        ),

        "03_data_analytics.csv": select(
            rows,
            lambda row:
                row["Primary.Track"] == "Core Data & Analytics"
                and row["Review.Priority"] in active_priorities,
        ),

        "04_data_engineering_automation.csv": select(
            rows,
            lambda row:
                row["Primary.Track"] == "Data Engineering & Automation"
                and row["Review.Priority"] in active_priorities,
        ),

        "05_business_product_analysis.csv": select(
            rows,
            lambda row:
                row["Primary.Track"] == "Business/Product Analysis"
                and row["Review.Priority"] in active_priorities,
        ),

        "06_customer_market_pricing_insights.csv": select(
            rows,
            lambda row:
                row["Primary.Track"]
                == "Customer/Market/Pricing Insights"
                and row["Review.Priority"] in active_priorities,
        ),

        "07_python_sql_technical.csv": select(
            rows,
            lambda row:
                (
                    "Python" in row["Fit.Reasons"]
                    or "SQL/data platform" in row["Fit.Reasons"]
                    or row["Primary.Track"]
                    in {
                        "Python/Software Development",
                        "Adjacent Technical",
                    }
                )
                and row["Review.Priority"] in active_priorities,
        ),

        "08_junior_entry_watchlist.csv": select(
            rows,
            lambda row:
                "Junior/entry level" in row["Fit.Reasons"]
                and row["Review.Priority"] != "Exclude",
        ),

        "09_french_language_advantage.csv": select(
            rows,
            lambda row:
                "French-language advantage" in row["Fit.Reasons"]
                and row["Review.Priority"] != "Exclude",
        ),

        "10_newly_mined.csv": select(
            rows,
            lambda row:
                row["Mining.Age.Days"].isdigit()
                and int(row["Mining.Age.Days"]) <= RECENT_MINING_DAYS
                and row["Review.Priority"] != "Exclude",
        ),

        "11_senior_leadership_stretch.csv": select(
            rows,
            lambda row:
                row["Review.Priority"] == "Senior/Leadership Stretch",
        ),

        "12_manual_review.csv": select(
            rows,
            lambda row:
                row["Review.Priority"] == "Deprioritized"
                and (
                    row["Primary.Track"] == "Other"
                    or not row["Position.Type"]
                    or not row["Position.Subtype"]
                    or not row["Seniority"]
                ),
        ),

        "13_deprioritized_or_excluded.csv": select(
            rows,
            lambda row:
                row["Review.Priority"] in {"Deprioritized", "Exclude"},
        ),
    }

    return queues


# =============================================================================
# SUMMARY
# =============================================================================

def build_summary(
    rows: list[dict[str, str]],
    queues: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    summary: list[dict[str, str]] = []

    for priority in [
        "High",
        "Good",
        "Exploratory",
        "Senior/Leadership Stretch",
        "Deprioritized",
        "Exclude",
    ]:
        count = sum(
            row["Review.Priority"] == priority
            for row in rows
        )

        summary.append(
            {
                "Section": "Review Priority",
                "Category": priority,
                "Count": str(count),
            }
        )

    for track in [
        "Data Science & Machine Learning",
        "Customer/Market/Pricing Insights",
        "Data Engineering & Automation",
        "Core Data & Analytics",
        "Business/Product Analysis",
        "Python/Software Development",
        "Adjacent Technical",
        "Other",
    ]:
        count = sum(row["Primary.Track"] == track for row in rows)

        summary.append(
            {
                "Section": "Primary Track",
                "Category": track,
                "Count": str(count),
            }
        )

    for filename, queue_rows in queues.items():
        summary.append(
            {
                "Section": "Output Queue",
                "Category": filename,
                "Count": str(len(queue_rows)),
            }
        )

    return summary


def write_summary(rows: list[dict[str, str]]) -> None:
    path = OUTPUT_DIR / "filter_summary.csv"

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["Section", "Category", "Count"],
        )
        writer.writeheader()
        writer.writerows(rows)


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    print(f"Reading: {INPUT_FILE}")

    source_rows = read_register()
    classified_rows = [classify_row(row) for row in source_rows]
    queues = build_review_queues(classified_rows)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for filename, queue_rows in queues.items():
        output_path = OUTPUT_DIR / filename
        write_csv(output_path, queue_rows)
        print(f"Created {filename}: {len(queue_rows):,} rows")

    summary = build_summary(classified_rows, queues)
    write_summary(summary)

    priority_count = len(queues["01_priority_review.csv"])

    print()
    print("Review queues created successfully.")
    print(f"Input jobs: {len(classified_rows):,}")
    print(f"High/Good priority jobs: {priority_count:,}")
    print(f"Output directory: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()