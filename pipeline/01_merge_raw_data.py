r"""Merge every spider ``*_basic.csv`` into one canonical job register.

Default input directory:
    C:\Users\demps\Job_Scanner\Data_Mining\jobs_scrapy\jobs_scrapy\data

Default output file:
    C:\Users\demps\Job_Scanner\Data_Mining\data\register_basic.csv

The output is rebuilt atomically on every run. Source rows are not appended to
an existing register, so repeated runs do not multiply the same input data.
No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_INPUT_DIR = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\01_raw"
)
DEFAULT_OUTPUT_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\02_consolidated\register_basic.csv"
)

OUTPUT_FIELDS = [
    "ID",
    "Job Title",
    "Company",
    "Location",
    "Salary",
    "Seniority",
    "URL",
    "Mining_Date",
]

# These fields must exist in every source file. Seniority and Mining_Date are
# optional because some current spiders do not provide them.
REQUIRED_SOURCE_FIELDS = {
    "ID",
    "Job Title",
    "Company",
    "Location",
    "Salary",
    "URL",
}


@dataclass(frozen=True)
class SourceSummary:
    path: Path
    rows_written: int
    blank_rows_skipped: int
    optional_fields_missing: tuple[str, ...]


def normalized_header(value: str | None) -> str:
    """Normalize harmless header formatting differences for matching."""

    if value is None:
        return ""
    value = value.removeprefix("\ufeff").replace("_", " ")
    return " ".join(value.split()).casefold()


def build_header_map(fieldnames: Iterable[str | None]) -> dict[str, str]:
    """Map canonical output fields to their physical source headers."""

    physical_by_normalized = {
        normalized_header(field): field
        for field in fieldnames
        if field is not None and normalized_header(field)
    }

    return {
        canonical: physical_by_normalized[normalized_header(canonical)]
        for canonical in OUTPUT_FIELDS
        if normalized_header(canonical) in physical_by_normalized
    }


def discover_source_files(input_dir: Path, output_file: Path) -> list[Path]:
    """Return every direct-child *_basic.csv except the output itself."""

    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {input_dir}")

    output_resolved = output_file.resolve(strict=False)
    files = sorted(
        (
            path
            for path in input_dir.glob("*_basic.csv")
            if path.is_file() and path.resolve(strict=False) != output_resolved
        ),
        key=lambda path: path.name.casefold(),
    )

    if not files:
        raise FileNotFoundError(
            f"No *_basic.csv source files were found in: {input_dir}"
        )

    return files


def canonicalize_row(
    raw_row: dict[str | None, str | list[str] | None],
    header_map: dict[str, str],
) -> dict[str, str]:
    """Project one heterogeneous input row onto the canonical schema."""

    result: dict[str, str] = {}

    for field in OUTPUT_FIELDS:
        physical_header = header_map.get(field)
        raw_value = raw_row.get(physical_header, "") if physical_header else ""

        # DictReader uses a list only for surplus malformed columns. A mapped
        # field should normally be a string, but joining keeps failure visible
        # without writing a Python list representation into the CSV.
        if isinstance(raw_value, list):
            value = " ".join(part for part in raw_value if part)
        else:
            value = raw_value or ""

        result[field] = str(value).strip()

    return result


def merge_basic_csvs(input_dir: Path, output_file: Path) -> list[SourceSummary]:
    """Rebuild output_file from all discovered source CSVs."""

    source_files = discover_source_files(input_dir, output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    temp_file = output_file.with_name(f"{output_file.name}.tmp")
    summaries: list[SourceSummary] = []

    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    repeated_ids = 0
    repeated_urls = 0
    total_rows = 0

    try:
        with temp_file.open("w", newline="", encoding="utf-8-sig") as target:
            writer = csv.DictWriter(
                target,
                fieldnames=OUTPUT_FIELDS,
                extrasaction="ignore",
            )
            writer.writeheader()

            for source_file in source_files:
                rows_written = 0
                blank_rows_skipped = 0

                with source_file.open(
                    "r",
                    newline="",
                    encoding="utf-8-sig",
                ) as source:
                    reader = csv.DictReader(source)

                    if reader.fieldnames is None:
                        raise ValueError(
                            f"Source file has no CSV header: {source_file}"
                        )

                    header_map = build_header_map(reader.fieldnames)
                    missing_required = sorted(
                        REQUIRED_SOURCE_FIELDS.difference(header_map)
                    )
                    if missing_required:
                        raise ValueError(
                            f"{source_file.name} is missing required column(s): "
                            + ", ".join(missing_required)
                        )

                    missing_optional = tuple(
                        field
                        for field in ("Seniority", "Mining_Date")
                        if field not in header_map
                    )

                    for raw_row in reader:
                        row = canonicalize_row(raw_row, header_map)

                        if not any(row.values()):
                            blank_rows_skipped += 1
                            continue

                        writer.writerow(row)
                        rows_written += 1
                        total_rows += 1

                        job_id = row["ID"]
                        if job_id:
                            if job_id in seen_ids:
                                repeated_ids += 1
                            else:
                                seen_ids.add(job_id)

                        url = row["URL"]
                        if url:
                            if url in seen_urls:
                                repeated_urls += 1
                            else:
                                seen_urls.add(url)

                summaries.append(
                    SourceSummary(
                        path=source_file,
                        rows_written=rows_written,
                        blank_rows_skipped=blank_rows_skipped,
                        optional_fields_missing=missing_optional,
                    )
                )

        # The prior register remains intact if reading or writing fails before
        # this point. On Windows, close register_basic.csv in Excel first.
        temp_file.replace(output_file)

    except Exception:
        if temp_file.exists():
            temp_file.unlink()
        raise

    print(f"Merged {len(source_files)} source file(s) into:")
    print(f"  {output_file}")
    print()

    for summary in summaries:
        details = [f"{summary.rows_written} row(s)"]
        if summary.blank_rows_skipped:
            details.append(f"{summary.blank_rows_skipped} blank row(s) skipped")
        if summary.optional_fields_missing:
            details.append(
                "blank-filled: " + ", ".join(summary.optional_fields_missing)
            )
        print(f"  {summary.path.name}: " + "; ".join(details))

    print()
    print(f"Total rows written: {total_rows}")
    print(f"Repeated IDs retained: {repeated_ids}")
    print(f"Repeated URLs retained: {repeated_urls}")

    if repeated_ids or repeated_urls:
        print(
            "Note: this merger preserves source rows. It reports exact repeats "
            "but does not perform cross-source job deduplication."
        )

    return summaries


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Merge all *_basic.csv spider outputs into register_basic.csv."
        )
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help=f"Directory containing *_basic.csv files (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Unified CSV path (default: {DEFAULT_OUTPUT_FILE})",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    try:
        merge_basic_csvs(arguments.input_dir, arguments.output)
    except PermissionError as exc:
        print(
            "ERROR: A CSV file is locked or inaccessible. Close it in Excel "
            f"and try again. Details: {exc}",
            file=sys.stderr,
        )
        return 1
    except (OSError, ValueError, csv.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
