r"""Extract unique job titles from the unified basic job register.

Default input:
    C:\Users\demps\Job_Scanner\Data_Mining\data\register_basic.csv

Default output:
    C:\Users\demps\Job_Scanner\Data_Mining\data\diagnostics\unique_job_titles\unique_job_titles.csv

The output is rebuilt atomically on every run. No third-party packages are
required.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


DEFAULT_INPUT_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\02_consolidated\register_basic.csv"
)
DEFAULT_OUTPUT_FILE = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\03_taxonomy"
    r"\unique_job_titles.csv"
)
JOB_TITLE_COLUMN = "Job Title"


def normalize_header(value: str | None) -> str:
    """Normalize harmless header differences, including an optional BOM."""

    if value is None:
        return ""
    value = value.removeprefix("\ufeff").replace("_", " ")
    return " ".join(value.split()).casefold()


def clean_title(value: str | None) -> str:
    """Collapse accidental whitespace while preserving the displayed title."""

    return " ".join((value or "").split())


def extract_unique_titles(input_file: Path) -> tuple[list[str], int, int]:
    """Return unique titles, the number of source rows, and blank-title rows."""

    if not input_file.exists():
        raise FileNotFoundError(f"Input file does not exist: {input_file}")
    if not input_file.is_file():
        raise ValueError(f"Input path is not a file: {input_file}")

    unique_by_key: dict[str, str] = {}
    source_rows = 0
    blank_titles = 0

    with input_file.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)

        if reader.fieldnames is None:
            raise ValueError(f"Input CSV has no header: {input_file}")

        title_header = next(
            (
                header
                for header in reader.fieldnames
                if normalize_header(header) == normalize_header(JOB_TITLE_COLUMN)
            ),
            None,
        )
        if title_header is None:
            raise ValueError(
                f'Input CSV is missing the required "{JOB_TITLE_COLUMN}" column: '
                f"{input_file}"
            )

        for row in reader:
            source_rows += 1
            title = clean_title(row.get(title_header))

            if not title:
                blank_titles += 1
                continue

            # Treat capitalization-only and surplus-whitespace differences as
            # the same title, while retaining the first displayed spelling.
            unique_by_key.setdefault(title.casefold(), title)

    titles = sorted(unique_by_key.values(), key=str.casefold)
    return titles, source_rows, blank_titles


def write_unique_titles(output_file: Path, titles: list[str]) -> None:
    """Atomically rebuild the one-column diagnostic CSV."""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = output_file.with_name(f"{output_file.name}.tmp")

    try:
        with temporary_file.open("w", newline="", encoding="utf-8-sig") as target:
            writer = csv.DictWriter(target, fieldnames=[JOB_TITLE_COLUMN])
            writer.writeheader()
            writer.writerows({JOB_TITLE_COLUMN: title} for title in titles)

        # The previous diagnostic remains intact if writing fails before this
        # point. On Windows, close the CSV in Excel before running the script.
        temporary_file.replace(output_file)
    except Exception:
        if temporary_file.exists():
            temporary_file.unlink()
        raise


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract unique Job Title values from register_basic.csv."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_FILE,
        help=f"Unified register path (default: {DEFAULT_INPUT_FILE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Diagnostic CSV path (default: {DEFAULT_OUTPUT_FILE})",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()

    try:
        titles, source_rows, blank_titles = extract_unique_titles(arguments.input)
        write_unique_titles(arguments.output, titles)
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

    duplicate_titles = source_rows - blank_titles - len(titles)
    print(f"Source rows examined: {source_rows}")
    print(f"Unique job titles written: {len(titles)}")
    print(f"Duplicate title rows excluded: {duplicate_titles}")
    print(f"Blank title rows skipped: {blank_titles}")
    print(f"Output: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
