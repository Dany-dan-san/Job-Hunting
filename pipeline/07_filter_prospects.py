"""Create prospects_gross.csv from evaluations graded 1 or 2."""

import csv
import os
from pathlib import Path


EVALUATION_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\06_review\evaluation.csv"
)
REGISTER_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\02_consolidated\register_basic.csv"
)
OUTPUT_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\07_prospects\prospects_gross.csv"
)

OUTPUT_FIELDS = [
    "ID",
    "Job Title",
    "Company",
    "Seniority",
    "Grade",
    "URL",
    "Mining_Date",
]


def clean(value):
    return str(value or "").strip()


def selected_grade(value):
    """Return normalized grade 1 or 2; otherwise return None."""

    text = clean(value)

    try:
        number = float(text)
        if number in (1, 2):
            return str(int(number))
    except ValueError:
        pass

    if text.startswith("1 -"):
        return "1"
    if text.startswith("2 -"):
        return "2"

    return None


def load_register():
    register = {}

    with REGISTER_FILE.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)

        required = {
            "ID",
            "Job Title",
            "Company",
            "Seniority",
            "URL",
            "Mining_Date",
        }
        missing = required.difference(reader.fieldnames or [])

        if missing:
            raise ValueError(
                "register_basic.csv is missing: " + ", ".join(sorted(missing))
            )

        for row in reader:
            job_id = clean(row["ID"])
            if not job_id:
                continue

            if job_id in register:
                raise ValueError(f"Duplicate ID in register_basic.csv: {job_id}")

            register[job_id] = row

    return register


def build_prospects():
    if not EVALUATION_FILE.exists():
        raise FileNotFoundError(f"Evaluation file not found: {EVALUATION_FILE}")

    if not REGISTER_FILE.exists():
        raise FileNotFoundError(f"Basic register not found: {REGISTER_FILE}")

    register = load_register()
    prospects = []
    processed_ids = set()
    unmatched_ids = []

    with EVALUATION_FILE.open(
        "r", newline="", encoding="utf-8-sig"
    ) as file:
        reader = csv.DictReader(file)

        required = {"ID", "Job Title", "Seniority", "URL", "Grade"}
        missing = required.difference(reader.fieldnames or [])

        if missing:
            raise ValueError(
                "evaluation.csv is missing: " + ", ".join(sorted(missing))
            )

        for evaluation in reader:
            grade = selected_grade(evaluation["Grade"])
            if grade is None:
                continue

            job_id = clean(evaluation["ID"])
            if not job_id or job_id in processed_ids:
                continue

            processed_ids.add(job_id)
            basic = register.get(job_id, {})

            if not basic:
                unmatched_ids.append(job_id)

            prospects.append(
                {
                    "ID": job_id,
                    "Job Title": (
                        clean(evaluation["Job Title"])
                        or clean(basic.get("Job Title"))
                    ),
                    "Company": clean(basic.get("Company")),
                    "Seniority": (
                        clean(evaluation["Seniority"])
                        or clean(basic.get("Seniority"))
                    ),
                    "Grade": grade,
                    "URL": (
                        clean(evaluation["URL"])
                        or clean(basic.get("URL"))
                    ),
                    "Mining_Date": clean(basic.get("Mining_Date")),
                }
            )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = OUTPUT_FILE.with_suffix(".csv.tmp")

    try:
        with temporary_file.open(
            "w", newline="", encoding="utf-8-sig"
        ) as file:
            writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(prospects)

        os.replace(temporary_file, OUTPUT_FILE)

    finally:
        temporary_file.unlink(missing_ok=True)

    print(f"Created: {OUTPUT_FILE}")
    print(f"Grade 1 or 2 prospects: {len(prospects)}")

    if unmatched_ids:
        print(
            f"Warning: {len(unmatched_ids)} ID(s) were not found "
            "in register_basic.csv:"
        )
        for job_id in unmatched_ids[:10]:
            print(f"  - {job_id}")


if __name__ == "__main__":
    build_prospects()