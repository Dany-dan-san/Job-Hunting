r"""Build the final enriched job registry from listing and classification CSVs.

Default inputs:
    C:\Users\demps\Job_Scanner\Data_Mining\data\register_basic.csv
    C:\Users\demps\Job_Scanner\Data_Mining\data\diagnostics\job_classes\job_classes.csv

Default output:
    C:\Users\demps\Job_Scanner\Data_Mining\data\register_enriched.csv

The script joins ``register_basic.csv`` to ``job_classes.csv`` using the job
title. Every listing from the basic register is retained. Classification fields
come from ``job_classes.csv``. Source-reported seniority from the basic register
has precedence over seniority inferred from the title; inferred seniority fills
only source blanks.

The output is rebuilt atomically and encoded as UTF-8 with a BOM so that Czech
text opens correctly in Excel. No third-party packages are required.
"""

from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


DEFAULT_REGISTER_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\02_consolidated\register_basic.csv"
)
DEFAULT_CLASSES_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\03_taxonomy\job_classes.csv"
)
DEFAULT_OUTPUT_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\04_enriched\register_enriched.csv"
)

OUTPUT_FIELDS = [
    "ID",
    "Job Title",
    "Salary",
    "Location",
    "Position Type",
    "Position Subtype",
    "Seniority",
    "Tags",
    "Technology",
    "URL",
    "Mining_Date",
]

REGISTER_FIELDS = {
    "ID",
    "Job Title",
    "Salary",
    "Location",
    "URL",
    "Mining_Date",
}
CLASS_FIELDS = {
    "Original Job Title",
    "Root Position",
    "Position Subtype",
    "Domain Tags",
    "Technology Tags",
}

MISSING_TEXT_VALUES = frozenset({"", "n/a", "na", "none", "null", "nan"})


@dataclass(frozen=True)
class JobClassification:
    original_title: str
    root_position: str
    position_subtype: str
    domain_tags: str
    technology_tags: str
    seniority: str

    def comparable_values(self) -> tuple[str, ...]:
        """Return values used to detect conflicting duplicate title records."""

        return (
            self.root_position,
            self.position_subtype,
            self.domain_tags,
            self.technology_tags,
            self.seniority,
        )


@dataclass(frozen=True)
class MergeSummary:
    register_rows: int
    matched_rows: int
    unmatched_rows: int
    blank_rows_skipped: int
    source_seniority_rows: int
    inferred_seniority_rows: int
    seniority_conflicts: int
    unused_classifications: int


def clean_value(value: object) -> str:
    """Convert one CSV value to stripped text without inventing content."""

    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(part).strip() for part in value if part).strip()
    return str(value).strip()


def is_missing(value: str | None) -> bool:
    """Recognize blank-like placeholders as missing values."""

    return clean_value(value).casefold() in MISSING_TEXT_VALUES


def normalized_header(value: str | None) -> str:
    """Normalize BOM, underscores, whitespace, and case in a CSV header."""

    text = clean_value(value).removeprefix("\ufeff").replace("_", " ")
    return " ".join(text.split()).casefold()


def build_header_map(
    fieldnames: Iterable[str | None],
    expected_fields: Iterable[str],
) -> dict[str, str]:
    """Map canonical names to their physical, case-insensitive headers."""

    physical_by_normalized: dict[str, str] = {}
    for field in fieldnames:
        if field is None:
            continue
        key = normalized_header(field)
        if key:
            physical_by_normalized[key] = field

    return {
        canonical: physical_by_normalized[normalized_header(canonical)]
        for canonical in expected_fields
        if normalized_header(canonical) in physical_by_normalized
    }


def title_key(value: str | None) -> str:
    """Create a conservative comparison key for matching the two CSVs.

    NFKC normalization reconciles equivalent Unicode forms. Case and repeated
    whitespace are ignored, while accents and punctuation remain significant
    to avoid joining genuinely different titles too aggressively.
    """

    text = unicodedata.normalize("NFKC", clean_value(value))
    return " ".join(text.split()).casefold()


def ensure_input_file(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"{label} is not a file: {path}")


def require_fields(
    path: Path,
    header_map: Mapping[str, str],
    required_fields: set[str],
) -> None:
    missing = sorted(required_fields.difference(header_map))
    if missing:
        raise ValueError(
            f"{path.name} is missing required column(s): {', '.join(missing)}"
        )


def row_value(
    row: Mapping[str | None, object],
    header_map: Mapping[str, str],
    canonical_field: str,
) -> str:
    physical_field = header_map.get(canonical_field)
    if physical_field is None:
        return ""
    return clean_value(row.get(physical_field, ""))


def load_classifications(
    classes_file: Path,
) -> tuple[dict[str, JobClassification], set[str]]:
    """Load and validate one unambiguous classification per normalized title."""

    classifications: dict[str, JobClassification] = {}
    duplicate_keys: set[str] = set()
    expected_fields = CLASS_FIELDS | {"Seniority"}

    with classes_file.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"Classification file has no CSV header: {classes_file}")

        header_map = build_header_map(reader.fieldnames, expected_fields)
        require_fields(classes_file, header_map, CLASS_FIELDS)

        for line_number, raw_row in enumerate(reader, start=2):
            original_title = row_value(
                raw_row, header_map, "Original Job Title"
            )
            key = title_key(original_title)
            if not key:
                if not any(clean_value(value) for value in raw_row.values()):
                    continue
                raise ValueError(
                    f"{classes_file.name}, row {line_number}: "
                    "Original Job Title is blank"
                )

            classification = JobClassification(
                original_title=original_title,
                root_position=row_value(raw_row, header_map, "Root Position"),
                position_subtype=row_value(
                    raw_row, header_map, "Position Subtype"
                ),
                domain_tags=row_value(raw_row, header_map, "Domain Tags"),
                technology_tags=row_value(
                    raw_row, header_map, "Technology Tags"
                ),
                seniority=row_value(raw_row, header_map, "Seniority"),
            )

            previous = classifications.get(key)
            if previous is None:
                classifications[key] = classification
                continue

            duplicate_keys.add(key)
            if previous.comparable_values() != classification.comparable_values():
                raise ValueError(
                    f"{classes_file.name}, row {line_number}: conflicting "
                    f"classifications exist for title {original_title!r}"
                )

    if not classifications:
        raise ValueError(f"No classifications were found in: {classes_file}")

    return classifications, duplicate_keys


def choose_seniority(
    source_value: str,
    inferred_value: str,
) -> tuple[str, str, bool]:
    """Return seniority, its source, and whether populated inputs conflict."""

    source_missing = is_missing(source_value)
    inferred_missing = is_missing(inferred_value)

    if not source_missing:
        conflict = (
            not inferred_missing
            and title_key(source_value) != title_key(inferred_value)
        )
        return source_value, "source", conflict

    if not inferred_missing:
        return inferred_value, "inferred", False

    return "", "missing", False


def build_enriched_register(
    register_file: Path,
    classes_file: Path,
    output_file: Path,
    *,
    strict: bool = False,
) -> tuple[MergeSummary, list[str], set[str]]:
    """Merge listing rows with title classifications and write atomically."""

    ensure_input_file(register_file, "Basic register")
    ensure_input_file(classes_file, "Job classes file")

    classifications, duplicate_classification_keys = load_classifications(
        classes_file
    )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = output_file.with_name(f"{output_file.name}.tmp")

    matched_classification_keys: set[str] = set()
    unmatched_titles: list[str] = []
    register_rows = 0
    matched_rows = 0
    blank_rows_skipped = 0
    source_seniority_rows = 0
    inferred_seniority_rows = 0
    seniority_conflicts = 0

    expected_register_fields = REGISTER_FIELDS | {"Seniority"}

    try:
        with register_file.open(
            "r", newline="", encoding="utf-8-sig"
        ) as source, temp_file.open(
            "w", newline="", encoding="utf-8-sig"
        ) as target:
            reader = csv.DictReader(source)
            if reader.fieldnames is None:
                raise ValueError(f"Basic register has no CSV header: {register_file}")

            header_map = build_header_map(
                reader.fieldnames, expected_register_fields
            )
            require_fields(register_file, header_map, REGISTER_FIELDS)

            writer = csv.DictWriter(
                target,
                fieldnames=OUTPUT_FIELDS,
                extrasaction="ignore",
            )
            writer.writeheader()

            for raw_row in reader:
                if not any(clean_value(value) for value in raw_row.values()):
                    blank_rows_skipped += 1
                    continue

                register_rows += 1
                job_title = row_value(raw_row, header_map, "Job Title")
                key = title_key(job_title)
                classification = classifications.get(key)

                if classification is None:
                    if job_title and job_title not in unmatched_titles:
                        unmatched_titles.append(job_title)
                    inferred_seniority = ""
                    root_position = ""
                    position_subtype = ""
                    domain_tags = ""
                    technology_tags = ""
                else:
                    matched_rows += 1
                    matched_classification_keys.add(key)
                    inferred_seniority = classification.seniority
                    root_position = classification.root_position
                    position_subtype = classification.position_subtype
                    domain_tags = classification.domain_tags
                    technology_tags = classification.technology_tags

                source_seniority = row_value(raw_row, header_map, "Seniority")
                seniority, seniority_source, conflict = choose_seniority(
                    source_seniority, inferred_seniority
                )
                if seniority_source == "source":
                    source_seniority_rows += 1
                elif seniority_source == "inferred":
                    inferred_seniority_rows += 1
                if conflict:
                    seniority_conflicts += 1

                writer.writerow(
                    {
                        "ID": row_value(raw_row, header_map, "ID"),
                        "Job Title": job_title,
                        "Salary": row_value(raw_row, header_map, "Salary"),
                        "Location": row_value(raw_row, header_map, "Location"),
                        "Position Type": root_position,
                        "Position Subtype": position_subtype,
                        "Seniority": seniority,
                        "Tags": domain_tags,
                        "Technology": technology_tags,
                        "URL": row_value(raw_row, header_map, "URL"),
                        "Mining_Date": row_value(
                            raw_row, header_map, "Mining_Date"
                        ),
                    }
                )

        unmatched_rows = register_rows - matched_rows
        if strict and unmatched_rows:
            preview = "; ".join(unmatched_titles[:5])
            extra = max(0, len(unmatched_titles) - 5)
            if extra:
                preview += f"; and {extra} more"
            raise ValueError(
                f"Strict mode: {unmatched_rows} register row(s) lack a job "
                f"classification. Unmatched title(s): {preview}"
            )

        temp_file.replace(output_file)

    except Exception:
        temp_file.unlink(missing_ok=True)
        raise

    unused_classifications = len(
        set(classifications).difference(matched_classification_keys)
    )
    summary = MergeSummary(
        register_rows=register_rows,
        matched_rows=matched_rows,
        unmatched_rows=register_rows - matched_rows,
        blank_rows_skipped=blank_rows_skipped,
        source_seniority_rows=source_seniority_rows,
        inferred_seniority_rows=inferred_seniority_rows,
        seniority_conflicts=seniority_conflicts,
        unused_classifications=unused_classifications,
    )
    return summary, unmatched_titles, duplicate_classification_keys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Merge register_basic.csv with job_classes.csv to produce "
            "register_enriched.csv."
        )
    )
    parser.add_argument(
        "--register",
        type=Path,
        default=DEFAULT_REGISTER_FILE,
        help=f"Basic register CSV (default: {DEFAULT_REGISTER_FILE})",
    )
    parser.add_argument(
        "--classes",
        type=Path,
        default=DEFAULT_CLASSES_FILE,
        help=f"Job classification CSV (default: {DEFAULT_CLASSES_FILE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Enriched output CSV (default: {DEFAULT_OUTPUT_FILE})",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Abort without replacing the output if any register title has no "
            "classification."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        summary, unmatched_titles, duplicate_keys = build_enriched_register(
            args.register,
            args.classes,
            args.output,
            strict=args.strict,
        )
    except (OSError, csv.Error, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Enriched register created: {args.output}")
    print(f"Register rows written: {summary.register_rows}")
    print(f"Rows matched to job classes: {summary.matched_rows}")
    print(f"Rows without a job class: {summary.unmatched_rows}")
    print(f"Blank register rows skipped: {summary.blank_rows_skipped}")
    print(f"Seniority taken from register_basic.csv: {summary.source_seniority_rows}")
    print(f"Seniority inferred from job_classes.csv: {summary.inferred_seniority_rows}")
    print(f"Seniority conflicts resolved in favor of source: {summary.seniority_conflicts}")
    print(f"Unused unique-title classifications: {summary.unused_classifications}")
    print(f"Equivalent duplicate classification rows: {len(duplicate_keys)}")

    if unmatched_titles:
        print("WARNING: unmatched title examples:", file=sys.stderr)
        for title in unmatched_titles[:10]:
            print(f"  - {title}", file=sys.stderr)
        remaining = len(unmatched_titles) - 10
        if remaining > 0:
            print(f"  ... and {remaining} more", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
