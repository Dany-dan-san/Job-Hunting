"""Run the Job Scanner acquisition and transformation pipeline in order."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(r"C:\Users\demps\Job_Scanner\Data_Mining")
SCRAPY_PROJECT = ROOT / "acquisition" / "jobs_scrapy"
PIPELINE_DIR = ROOT / "pipeline"
REGISTRY_DIR = ROOT / "data" / "01_raw" / "registries"

VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)

SPIDERS = [
    ("Built In", "builtin", "builtin_registry.json"),
    ("Jobs.cz", "jobs_cz", "jobs_cz_registry.json"),
    ("JobStack", "jobstack", "jobstack_registry.json"),
    ("StartupJobs", "startupjobs", "startupjobs_registry.json"),
]

PIPELINE_SCRIPTS = [
    "01_merge_raw_data.py",
    "01b_extract_unique_titles.py",
    "02_unify_job_titles.py",
    "03_classify_jobs_titles.py",
    "04_enrich_register.py",
    "05_build_review_queues.py",
]


def run(command: list[str], *, cwd: Path, label: str) -> None:
    """Run one stage and stop immediately if it fails."""

    print(f"\n{'=' * 72}")
    print(f"STARTING: {label}")
    print(f"{'=' * 72}", flush=True)

    started = datetime.now()
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"

    subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
    )

    elapsed = datetime.now() - started
    print(f"COMPLETED: {label} ({elapsed})", flush=True)


def verify_spider_completed(registry_name: str, spider_name: str) -> None:
    """Detect checkpointed spider failures that Scrapy may report as exit 0."""

    registry_path = REGISTRY_DIR / registry_name
    if not registry_path.exists():
        print(f"NOTE: No registry completion check available for {spider_name}.")
        return

    with registry_path.open("r", encoding="utf-8") as file:
        state = json.load(file)

    if "completed" in state and not state["completed"]:
        resume_url = state.get("resume_url", "unknown")
        raise RuntimeError(
            f"Spider '{spider_name}' stopped before completing its search plan. "
            f"Checkpoint: {resume_url}"
        )


def validate_project() -> None:
    """Fail early when the runner is copied to an incomplete directory."""

    required_paths = [
        PYTHON,
        SCRAPY_PROJECT / "scrapy.cfg",
        *(
            SCRAPY_PROJECT / "jobs_scrapy" / "spiders" / f"{spider_name}.py"
            for _, spider_name, _ in SPIDERS
        ),
        *(PIPELINE_DIR / name for name in PIPELINE_SCRIPTS),
    ]

    missing = [path for path in required_paths if not path.exists()]
    if missing:
        formatted = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"Required file(s) not found:\n{formatted}")


def main() -> int:
    validate_project()

    print("JOB SCANNER — COMPLETE AUTOMATED RUN")
    print(f"Python: {PYTHON}")
    print(f"Started: {datetime.now().isoformat(timespec='seconds')}")

    try:
        for label, spider_name, registry_name in SPIDERS:
            run(
                [str(PYTHON), "-m", "scrapy", "crawl", spider_name],
                cwd=SCRAPY_PROJECT,
                label=f"Spider — {label}",
            )
            verify_spider_completed(registry_name, spider_name)

        for script_name in PIPELINE_SCRIPTS:
            run(
                [str(PYTHON), str(PIPELINE_DIR / script_name)],
                cwd=PIPELINE_DIR,
                label=f"Pipeline — {script_name}",
            )

    except (subprocess.CalledProcessError, RuntimeError) as error:
        print(f"\nPIPELINE STOPPED: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nPIPELINE INTERRUPTED BY USER", file=sys.stderr)
        return 130

    print(f"\n{'=' * 72}")
    print("JOB SCANNER PIPELINE COMPLETED SUCCESSFULLY")
    print(f"Finished: {datetime.now().isoformat(timespec='seconds')}")
    print("Review queues are ready in data\\05_review_queues.")
    print("Stages 06_review_prospects.py and 07_filter_prospects.py were not run.")
    print(f"{'=' * 72}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
