r"""Open ranked job listings for manual review and record one grade per ID.

Usage examples:
    python review_jobs.py 01_priority_review.csv
    python review_jobs.py 01_priority_review.csv --random
    python review_jobs.py 01_priority_review.csv --random --seed 42
    python review_jobs.py "C:\path\to\another_subset.csv"

Relative filenames are resolved inside:
    C:\Users\demps\Job_Scanner\Data_Mining\r_research\data

Evaluations are saved immediately to:
    C:\Users\demps\Job_Scanner\Data_Mining\review\data\evaluation.csv

The script uses only Python's standard library.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import re
import sys
import unicodedata
import webbrowser
from pathlib import Path
from typing import Iterable, Mapping


DEFAULT_INPUT_DIR = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\05_review_queues"
)
DEFAULT_EVALUATION_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\06_review\evaluation.csv"
)

# The corrected spelling Position.Subtype is used in new evaluation files.
OUTPUT_FIELDS = [
    "ID",
    "Job Title",
    "Position.Type",
    "Position.Subtype",
    "Seniority",
    "URL",
    "Grade",
]

INPUT_ALIASES = {
    "ID": ("ID",),
    "Job Title": ("Job Title", "Job.Title"),
    "Position Type": ("Position Type", "Position.Type", "Root Position"),
    "Position Subtype": (
        "Position Subtype",
        "Position.Subtype",
        "Position.Subtpe",
    ),
    "Seniority": ("Seniority",),
    "URL": ("URL",),
    "Fit Score": ("Fit Score", "Fit.Score"),
}

EVALUATION_ALIASES = {
    "ID": ("ID",),
    "Job Title": ("Job Title", "Job.Title"),
    "Position Type": ("Position.Type", "Position Type"),
    "Position Subtype": (
        "Position.Subtype",
        "Position Subtype",
        "Position.Subtpe",
    ),
    "Seniority": ("Seniority",),
    "URL": ("URL",),
    "Grade": ("Grade",),
}

GRADE_LABELS = {
    "1": "Perfect Fit",
    "2": "Good Fit",
    "3": "Poor Fit",
    "4": "Disqualifying Fit",
}


def normalize_header(value: str | None) -> str:
    """Ignore harmless spaces, dots, underscores, accents, and case."""

    text = unicodedata.normalize("NFKD", (value or "").removeprefix("\ufeff"))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def build_header_map(
    fieldnames: Iterable[str | None],
    aliases: Mapping[str, tuple[str, ...]],
) -> dict[str, str]:
    physical_by_key = {
        normalize_header(field): field
        for field in fieldnames
        if field and normalize_header(field)
    }

    result: dict[str, str] = {}
    for canonical, possible_names in aliases.items():
        for possible_name in possible_names:
            physical = physical_by_key.get(normalize_header(possible_name))
            if physical is not None:
                result[canonical] = physical
                break
    return result


def require_columns(
    path: Path,
    header_map: Mapping[str, str],
    required: Iterable[str],
) -> None:
    missing = [column for column in required if column not in header_map]
    if missing:
        raise ValueError(
            f"{path.name} is missing required column(s): {', '.join(missing)}"
        )


def value(
    row: Mapping[str | None, object],
    header_map: Mapping[str, str],
    canonical: str,
) -> str:
    physical = header_map[canonical]
    raw = row.get(physical, "")
    if raw is None:
        return ""
    if isinstance(raw, list):
        return " ".join(str(part).strip() for part in raw if part).strip()
    return str(raw).strip()


def parse_fit_score(raw_value: str) -> float:
    """Return a sortable number; blank or malformed scores sort last."""

    text = raw_value.strip().replace(" ", "").replace(",", ".")
    if not text:
        return -math.inf
    try:
        return float(text)
    except ValueError:
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        return float(match.group()) if match else -math.inf


def resolve_input_path(argument: str) -> Path:
    candidate = Path(argument)
    if not candidate.suffix:
        candidate = candidate.with_suffix(".csv")
    if candidate.is_absolute():
        return candidate
    return DEFAULT_INPUT_DIR / candidate


def load_jobs(
    input_file: Path,
    *,
    random_order: bool = False,
    random_seed: int | None = None,
) -> list[dict[str, str]]:
    if not input_file.exists():
        raise FileNotFoundError(f"Input CSV does not exist: {input_file}")
    if not input_file.is_file():
        raise ValueError(f"Input path is not a file: {input_file}")

    with input_file.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"Input CSV has no header: {input_file}")

        header_map = build_header_map(reader.fieldnames, INPUT_ALIASES)
        require_columns(input_file, header_map, INPUT_ALIASES)

        jobs: list[dict[str, str]] = []
        seen_input_ids: set[str] = set()

        for line_number, raw_row in enumerate(reader, start=2):
            if not any(str(part or "").strip() for part in raw_row.values()):
                continue

            job_id = value(raw_row, header_map, "ID")
            if not job_id:
                print(
                    f"WARNING: skipping input row {line_number} because ID is blank.",
                    file=sys.stderr,
                )
                continue
            if job_id in seen_input_ids:
                print(
                    f"WARNING: skipping duplicate input ID {job_id!r}.",
                    file=sys.stderr,
                )
                continue
            seen_input_ids.add(job_id)

            jobs.append(
                {
                    "ID": job_id,
                    "Job Title": value(raw_row, header_map, "Job Title"),
                    "Position Type": value(raw_row, header_map, "Position Type"),
                    "Position Subtype": value(
                        raw_row, header_map, "Position Subtype"
                    ),
                    "Seniority": value(raw_row, header_map, "Seniority"),
                    "URL": value(raw_row, header_map, "URL"),
                    "Fit Score": value(raw_row, header_map, "Fit Score"),
                }
            )

    if random_order:
        # A local generator avoids changing randomness elsewhere in Python.
        random.Random(random_seed).shuffle(jobs)
    else:
        # Python's sort is stable, so equal scores retain their CSV order.
        jobs.sort(key=lambda row: parse_fit_score(row["Fit Score"]), reverse=True)
    return jobs


def load_evaluated_ids(evaluation_file: Path) -> set[str]:
    if not evaluation_file.exists() or evaluation_file.stat().st_size == 0:
        return set()

    with evaluation_file.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(
                f"Existing evaluation CSV has no header: {evaluation_file}"
            )

        header_map = build_header_map(reader.fieldnames, EVALUATION_ALIASES)
        require_columns(evaluation_file, header_map, EVALUATION_ALIASES)

        return {
            value(row, header_map, "ID")
            for row in reader
            if value(row, header_map, "ID")
        }


def prepare_evaluation_writer(
    evaluation_file: Path,
) -> tuple[object, csv.DictWriter]:
    evaluation_file.parent.mkdir(parents=True, exist_ok=True)
    new_file = not evaluation_file.exists() or evaluation_file.stat().st_size == 0

    if new_file:
        handle = evaluation_file.open(
            "w", newline="", encoding="utf-8-sig"
        )
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        handle.flush()
        os.fsync(handle.fileno())
        return handle, writer

    # Validate the existing physical header, then append using that same order.
    with evaluation_file.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        fieldnames = reader.fieldnames
        if fieldnames is None:
            raise ValueError(
                f"Existing evaluation CSV has no header: {evaluation_file}"
            )
        header_map = build_header_map(fieldnames, EVALUATION_ALIASES)
        require_columns(evaluation_file, header_map, EVALUATION_ALIASES)

    handle = evaluation_file.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    return handle, writer


def row_for_existing_header(
    job: Mapping[str, str],
    grade: str,
    physical_fields: Iterable[str],
) -> dict[str, str]:
    """Create an append row compatible with new or earlier header spellings."""

    header_map = build_header_map(physical_fields, EVALUATION_ALIASES)
    canonical_values = {
        "ID": job["ID"],
        "Job Title": job["Job Title"],
        "Position Type": job["Position Type"],
        "Position Subtype": job["Position Subtype"],
        "Seniority": job["Seniority"],
        "URL": job["URL"],
        "Grade": grade,
    }
    return {
        physical: canonical_values[canonical]
        for canonical, physical in header_map.items()
    }


def prompt_for_grade() -> str:
    print("\nEvaluate job fitness:")
    for grade, label in GRADE_LABELS.items():
        print(f"  {grade} - {label}")
    print("  s - Skip for now")
    print("  q - Save and quit")

    while True:
        answer = input("Choice: ").strip().casefold()
        if answer in GRADE_LABELS or answer in {"s", "q"}:
            return answer
        print("Please enter 1, 2, 3, 4, s, or q.")


def review_jobs(
    input_file: Path,
    evaluation_file: Path,
    *,
    random_order: bool = False,
    random_seed: int | None = None,
) -> int:
    jobs = load_jobs(
        input_file,
        random_order=random_order,
        random_seed=random_seed,
    )
    evaluated_ids = load_evaluated_ids(evaluation_file)
    pending_jobs = [job for job in jobs if job["ID"] not in evaluated_ids]

    print(f"Input file: {input_file}")
    if random_order:
        seed_note = f" (seed {random_seed})" if random_seed is not None else ""
        print(f"Review order: Random{seed_note}")
    else:
        print("Review order: Highest Fit Score first")
    print(f"Jobs in input: {len(jobs)}")
    print(f"Already evaluated: {len(jobs) - len(pending_jobs)}")
    print(f"Remaining in this file: {len(pending_jobs)}")

    if not pending_jobs:
        print("Nothing to review. Every ID in this file already has a grade.")
        return 0

    graded_this_run = 0
    skipped_this_run = 0
    handle, writer = prepare_evaluation_writer(evaluation_file)

    try:
        for position, job in enumerate(pending_jobs, start=1):
            print("\n" + "=" * 72)
            print(f"Job {position}/{len(pending_jobs)}")
            print(f"Fit Score:       {job['Fit Score'] or 'Not informed'}")
            print(f"Job Title:       {job['Job Title']}")
            print(f"Position Type:   {job['Position Type']}")
            print(f"Position Subtype:{' ' if job['Position Subtype'] else ''}{job['Position Subtype'] or 'Not informed'}")
            print(f"Seniority:       {job['Seniority'] or 'Not informed'}")
            print(f"URL:             {job['URL']}")

            if job["URL"]:
                opened = webbrowser.open_new_tab(job["URL"])
                if not opened:
                    print(
                        "WARNING: the browser did not confirm that it opened the URL.",
                        file=sys.stderr,
                    )
            else:
                print("WARNING: this row has no URL.", file=sys.stderr)

            answer = prompt_for_grade()
            if answer == "q":
                break
            if answer == "s":
                skipped_this_run += 1
                continue

            writer.writerow(
                row_for_existing_header(job, answer, writer.fieldnames)
            )
            handle.flush()
            os.fsync(handle.fileno())
            evaluated_ids.add(job["ID"])
            graded_this_run += 1
            print(f"Recorded: {answer} - {GRADE_LABELS[answer]}")

    except (KeyboardInterrupt, EOFError):
        print("\nReview stopped. All completed grades have been saved.")
    finally:
        handle.close()

    print("\nReview session complete.")
    print(f"Grades recorded this run: {graded_this_run}")
    print(f"Jobs skipped for now: {skipped_this_run}")
    print(f"Evaluation file: {evaluation_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Open jobs from a CSV in descending Fit Score order and save "
            "manual grades without re-evaluating existing IDs."
        )
    )
    parser.add_argument(
        "csv_file",
        help=(
            "CSV filename inside the default r_research\\data folder, or an "
            "absolute CSV path. The .csv suffix may be omitted."
        ),
    )
    parser.add_argument(
        "--random",
        action="store_true",
        dest="random_order",
        help="Review the remaining jobs in random order instead of by Fit Score.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "Optional reproducible random seed. This may be used only with "
            "--random."
        ),
    )
    parser.add_argument(
        "--evaluation-file",
        type=Path,
        default=DEFAULT_EVALUATION_FILE,
        help=f"Evaluation register (default: {DEFAULT_EVALUATION_FILE})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.seed is not None and not args.random_order:
        parser.error("--seed requires --random")
    input_file = resolve_input_path(args.csv_file)

    try:
        return review_jobs(
            input_file,
            args.evaluation_file,
            random_order=args.random_order,
            random_seed=args.seed,
        )
    except (OSError, csv.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
