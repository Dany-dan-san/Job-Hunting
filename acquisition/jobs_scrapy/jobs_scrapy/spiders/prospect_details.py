from __future__ import annotations

import csv
import html
import json
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path
import os
import sys
from shutil import copy2

# The spider lives in the Scrapy project; shared rules live in Data_Mining/pipeline.
_root = Path(os.environ["JOB_SCANNER_ROOT"]) if os.environ.get("JOB_SCANNER_ROOT") else next(
    (p for p in Path(__file__).resolve().parents if p.name.lower() == "data_mining"), Path(__file__).resolve().parents[4])
sys.path.insert(0, str(_root / "pipeline"))
from job_policy import project_root, normalize_url, profile
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import pandas as pd
import scrapy
from bs4 import BeautifulSoup
from scrapy import signals
from scrapy.http import HtmlResponse

try:
    from scrapy_playwright.page import PageMethod
    SCRAPY_PLAYWRIGHT_AVAILABLE = True
except ImportError:  # Optional dependency: static extraction still works without it.
    PageMethod = None  # type: ignore[assignment]
    SCRAPY_PLAYWRIGHT_AVAILABLE = False


def abort_nonessential_playwright_resource(request) -> bool:
    """Skip browser assets that cannot affect vacancy text extraction.

    Keep document/script/xhr/fetch/stylesheet traffic intact because Alma vacancy
    widgets may depend on JavaScript and API calls. Images, fonts and media are
    unnecessary for the rendered DOM we parse and add substantial crawl overhead.
    """
    return getattr(request, "resource_type", "") in {"image", "font", "media"}


class ProspectDetailsSpider(scrapy.Spider):
    """
    Enrich URLs already admitted to prospects_gross.csv.

    This spider intentionally performs *factual extraction only*.
    Human/job-fit interpretation belongs in the downstream enrichment/review stages.

    Default project layout expected when this file lives at:
        Data_Mining/acquisition/jobs_scrapy/jobs_scrapy/spiders/prospect_details.py

    Defaults:
        input  -> Data_Mining/data/07_prospects/prospects_gross.csv
        output -> Data_Mining/data/07_prospects/prospects_enriched_raw.csv

    Override paths from the command line if needed:
        scrapy crawl prospect_details \
            -a input_csv="C:/.../prospects_gross.csv" \
            -a output_csv="C:/.../prospects_enriched_raw.csv"

    Useful switches:
        -a refresh=1          Re-fetch URLs already present in output.
        -a limit=10           Only process first N pending URLs (test mode).
        -a source=builtin     Only process one source.
    """

    name = "prospect_details"
    allowed_domains = [
        "jobs.cz",
        "builtin.com",
        "startupjobs.cz",
        "jobstack.it",
        "indeed.com",
        "indeed.cz",
    ]

    custom_settings = {
        # Small detail crawl; be deliberately conservative.
        "ROBOTSTXT_OBEY": True,
        "CONCURRENT_REQUESTS": 2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 2.5,
        "DOWNLOAD_DELAY_JITTER": 0.5,
        "DOWNLOAD_TIMEOUT": 30,
        "RETRY_TIMES": 2,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 2.0,
        "AUTOTHROTTLE_MAX_DELAY": 20.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.75,
        "COOKIES_ENABLED": True,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_LEVEL": "INFO",
        "HTTPERROR_ALLOW_ALL": True,
    }

    OUTPUT_FIELDS = [
        # Identity / lineage
        "ID",
        "Source",
        "Input_URL",
        "Final_URL",
        "Job_Title_Input",
        "Company_Input",
        "Seniority_Input",
        "Grade_Input",
        "Mining_Date",
        # Page identity
        "Job_Title_Page",
        "Company_Page",
        "Canonical_URL",
        # Publication evidence
        "Published_At",
        "Published_Lower_Bound",
        "Published_Upper_Bound",
        "Publication_Method",
        "Publication_Confidence",
        "Repost_Text",
        "Valid_Through",
        # Availability / crawl evidence
        "HTTP_Status",
        "Current_Status",
        "Status_Evidence",
        "Extraction_Status",
        "Extraction_Error",
        "Extraction_Date",
        # Factual vacancy content
        "Description",
        "Requirements",
        "Location_Detail",
        "Remote_Mode",
        "Employment_Type",
        "Salary_Detail",
        "Seniority_Detail",
        "Languages",
        "Skills",
        "Applicant_Countries",
        # Technical diagnostics
        "Parser",
        "Structured_Data_Found",
        "Page_Language",
        "First_Seen_At", "Last_Seen_At", "Location_Input", "Position_Type_Input", "Position_Subtype_Input",
        "Secondary_Subtype_Input", "Role_Family", "Provisional_Shortlisting_Score",
        "Required_Skills", "Preferred_Skills", "Content_Completeness", "Content_Source",
        "Qualifications", "Education_Requirements", "Experience_Requirements",
        "Last_Successful_Extraction_At", "Last_Attempt_Status", "Last_Attempt_Error", "Parser_Version",
    ]

    USER_AGENTS = [
        # Ordinary current desktop UA strings; rotate modestly rather than spoof exotic devices.
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0",
    ]

    @classmethod
    def update_settings(cls, settings):
        """Enable scrapy-playwright only when the optional package is installed.

        The Playwright download handler inherits Scrapy's normal HTTP(S) handler, so
        ordinary requests remain ordinary Scrapy downloads. Only requests carrying
        ``meta["playwright"] = True`` are browser-rendered.
        """
        super().update_settings(settings)
        if not SCRAPY_PLAYWRIGHT_AVAILABLE:
            return

        handlers = dict(settings.getdict("DOWNLOAD_HANDLERS"))
        handlers["https"] = "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler"
        handlers["http"] = "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler"
        settings.set("DOWNLOAD_HANDLERS", handlers, priority="spider")
        settings.set("PLAYWRIGHT_BROWSER_TYPE", "chromium", priority="spider")
        settings.set("PLAYWRIGHT_LAUNCH_OPTIONS", {"headless": True}, priority="spider")
        settings.set("PLAYWRIGHT_MAX_PAGES_PER_CONTEXT", 1, priority="spider")
        settings.set(
            "PLAYWRIGHT_ABORT_REQUEST",
            abort_nonessential_playwright_resource,
            priority="spider",
        )

    def __init__(
        self,
        input_csv: Optional[str] = None,
        output_csv: Optional[str] = None,
        refresh: str = "0",
        limit: Optional[str] = None,
        source: Optional[str] = None,
        refresh_days: Optional[str] = None,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.project_root = self._discover_data_mining_root()
        self.input_csv = Path(input_csv) if input_csv else (
            self.project_root / "data" / "07_prospects" / "prospects_gross.csv"
        )
        self.output_csv = Path(output_csv) if output_csv else (
            self.project_root / "data" / "07_prospects" / "prospects_enriched_raw.csv"
        )
        self.refresh = str(refresh).strip().lower() in {"1", "true", "yes", "y"}
        self.limit = int(limit) if limit not in (None, "", "none") else None
        self.source_filter = source.strip().lower() if source else None
        self.refresh_days = float(refresh_days) if refresh_days is not None else float(profile()["refresh_days"])

        self.output_csv.parent.mkdir(parents=True, exist_ok=True)
        self.diagnostics_root = self.project_root / "diagnostics" / "prospect_details"
        self._fh = None
        self._writer = None
        self._seen_output_urls: set[str] = set()
        self._rows_written = 0

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(spider.spider_closed, signal=signals.spider_closed)
        return spider

    def _discover_data_mining_root(self) -> Path:
        return project_root()

    def spider_opened(self, spider):
        if not self.input_csv.exists():
            raise FileNotFoundError(f"Input prospects file not found: {self.input_csv}")

        # Resume registry is the output itself. This keeps the spider independent of crawl_runs.csv.
        # Only the *latest* record for each URL matters. Failed extractions are deliberately
        # NOT treated as completed, so a corrected parser can retry them automatically.
        if self.output_csv.exists() and self.output_csv.stat().st_size > 0:
            try:
                existing = pd.read_csv(self.output_csv, dtype=str).fillna("")
                if "Input_URL" in existing.columns:
                    existing["Input_URL"] = existing["Input_URL"].map(normalize_url)
                    existing = existing[existing["Input_URL"] != ""]
                    latest = existing.drop_duplicates(subset=["Input_URL"], keep="last")
                    self._seen_output_urls = {
                        r["Input_URL"] for _, r in latest.iterrows() if not self._should_refresh(r)
                    }
            except Exception as exc:
                self.logger.warning("Could not read existing output for resume: %s", exc)

        if self.output_csv.exists() and self.output_csv.stat().st_size > 0:
            with self.output_csv.open(encoding="utf-8-sig", newline="") as old:
                header = next(csv.reader(old), [])
            if header != self.OUTPUT_FIELDS:
                backup = self.output_csv.with_suffix(".pre-v2.csv")
                if not backup.exists(): copy2(self.output_csv, backup)
                self._compact_output_latest()
                with self.output_csv.open(encoding="utf-8-sig", newline="") as migrated:
                    if next(csv.reader(migrated), []) != self.OUTPUT_FIELDS:
                        raise RuntimeError("Output schema migration failed; refusing to append")
        file_exists = self.output_csv.exists() and self.output_csv.stat().st_size > 0
        self._fh = self.output_csv.open("a", newline="", encoding="utf-8-sig")
        self._writer = csv.DictWriter(self._fh, fieldnames=self.OUTPUT_FIELDS, extrasaction="ignore")
        if not file_exists:
            self._writer.writeheader()
            self._fh.flush()

        self.logger.info("Input:  %s", self.input_csv)
        self.logger.info("Output: %s", self.output_csv)
        self.logger.info("Resume URLs already present: %d", len(self._seen_output_urls))
        if SCRAPY_PLAYWRIGHT_AVAILABLE:
            self.logger.info("Jobs.cz browser fallback: enabled (scrapy-playwright / Chromium)")
        else:
            self.logger.info(
                "Jobs.cz browser fallback: unavailable; install scrapy-playwright + Chromium "
                "to render Alma widget pages"
            )

    def spider_closed(self, spider, reason):
        if self._fh:
            self._fh.flush()
            self._fh.close()
        self._compact_output_latest()
        self.logger.info("prospect_details finished (%s); rows written this run: %d", reason, self._rows_written)

    async def start(self):
        df = pd.read_csv(self.input_csv, dtype=str).fillna("")
        required = {"URL", "ID", "Mining_Date"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns in {self.input_csv}: {sorted(missing)}")

        # Deduplicate exact URLs while preserving first occurrence/order.
        df["URL"] = df["URL"].map(normalize_url)
        df = df[df["URL"] != ""].drop_duplicates(subset=["URL"], keep="first")

        pending = []
        for _, row in df.iterrows():
            url = row["URL"]
            source = self.detect_source(url)

            if self.source_filter and source != self.source_filter:
                continue
            if not self.refresh and url in self._seen_output_urls:
                continue

            pending.append((row.to_dict(), source))

        if self.limit is not None:
            pending = pending[: self.limit]

        self.logger.info("Unique input URLs: %d; scheduled this run: %d", len(df), len(pending))

        for row, source in pending:
            url = row["URL"]
            headers = self._request_headers(url)
            yield scrapy.Request(
                url=url,
                callback=self.parse_detail,
                errback=self.errback_detail,
                headers=headers,
                meta={
                    "input_row": row,
                    "source": source,
                    "handle_httpstatus_all": True,
                    "download_timeout": 30,
                },
                dont_filter=True,
            )

    def _request_headers(self, url: str) -> dict[str, str]:
        parsed = urlparse(url)
        return {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8,cs;q=0.5",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Referer": f"{parsed.scheme}://{parsed.netloc}/",
        }

    @staticmethod
    def detect_source(url: str) -> str:
        host = urlparse(url).netloc.lower()
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

    def parse_detail(self, response: HtmlResponse):
        row = response.meta["input_row"]
        source = response.meta["source"]
        record = self._base_record(row, source, response)

        try:
            if response.status >= 400:
                # A Jobs.cz /rpd/<id>/ URL that now resolves to 404 is normally useful
                # lifecycle evidence rather than a transient parser failure: the public
                # vacancy route has disappeared. Record it as CLOSED/PARTIAL so resume
                # mode does not hammer the same dead advert on every run. Other HTTP
                # errors remain retryable failures.
                if response.status in {404, 410}:
                    record["Current_Status"] = "CLOSED"
                    record["Status_Evidence"] = f"Vacancy URL returned HTTP {response.status}"
                    record["Extraction_Status"] = "partial"
                    record["Extraction_Error"] = "Vacancy no longer available at this public route."
                else:
                    record["Current_Status"] = "HTTP_ERROR"
                    record["Status_Evidence"] = f"HTTP {response.status}"
                    record["Extraction_Status"] = "failed"
                    record["Extraction_Error"] = f"HTTP {response.status}"
                self._save_diagnostic_html(response, record)
                self._write_record(record)
                return

            if source == "builtin":
                extracted = self.parse_builtin(response)
            elif source == "startupjobs":
                extracted = self.parse_startupjobs(response)
            elif source == "jobstack":
                extracted = self.parse_jobstack(response)
            elif source == "jobscz":
                extracted = self.parse_jobscz(response)
            elif source == "indeed":
                extracted = self.parse_indeed(response)
            else:
                extracted = self.parse_generic(response)

            # Jobs.cz can expose either a client-side Alma widget shell or only a short
            # fragment in an otherwise normal page. Escalate any non-closed PARTIAL
            # Jobs.cz extraction to Playwright; complete static pages remain lightweight.
            if (
                source == "jobscz"
                and not response.meta.get("playwright_fallback")
                and self._needs_jobscz_playwright(extracted)
            ):
                if SCRAPY_PLAYWRIGHT_AVAILABLE:
                    return self._jobscz_playwright_request(response, row, source, extracted)
                extracted["Extraction_Error"] = self.append_note(
                    extracted.get("Extraction_Error", ""),
                    "Browser fallback unavailable (install scrapy-playwright and Chromium).",
                )

            record.update({k: v for k, v in extracted.items() if v not in (None, "")})
            self._finalize_status(record)
            if record.get("Extraction_Status") in {"partial", "failed", "blocked"}:
                self._save_diagnostic_html(response, record)
            self._write_record(record)

        except Exception as exc:
            self.logger.exception("Extraction failed for %s", response.url)
            record["Extraction_Status"] = "failed"
            record["Extraction_Error"] = f"{type(exc).__name__}: {exc}"
            self._save_diagnostic_html(response, record)
            self._write_record(record)

    def _needs_jobscz_playwright(self, extracted: dict[str, str]) -> bool:
        """Escalate incomplete Jobs.cz detail pages to a real browser.

        This includes both known Alma widget shells and ordinary Jobs.cz/Alma pages
        where static HTML exposes only a short fragment. Closed vacancies never need
        a browser retry.
        """
        return (
            extracted.get("Parser") in {
                "jobscz_lmc_widget",
                "jobscz_react_widget",
                "jobscz_alma",
            }
            and extracted.get("Extraction_Status") == "partial"
            and extracted.get("Current_Status") != "CLOSED"
        )

    def _jobscz_playwright_request(
        self,
        response: HtmlResponse,
        row: dict[str, Any],
        source: str,
        static_extracted: dict[str, str],
    ) -> scrapy.Request:
        """Re-fetch one incomplete Jobs.cz vacancy in a real browser.

        This request is intentionally single-use and is scheduled only after static
        extraction proves insufficient.
        """
        assert PageMethod is not None
        self.logger.info(
            "[jobscz] escalating incomplete page to Playwright | %s",
            self.clean(row.get("Job Title", "")),
        )
        return scrapy.Request(
            url=response.url,
            callback=self.parse_jobscz_playwright,
            errback=self.errback_jobscz_playwright,
            headers=self._request_headers(response.url),
            dont_filter=True,
            meta={
                "input_row": row,
                "source": source,
                "static_extracted": static_extracted,
                "playwright_fallback": True,
                "playwright": True,
                "playwright_context": "jobscz",
                "playwright_page_goto_kwargs": {
                    "wait_until": "domcontentloaded",
                    "timeout": 30000,
                },
                "playwright_page_methods": [
                    # Alma's widget normally fills shortly after DOMContentLoaded. A
                    # bounded wait is safer than networkidle on pages with analytics.
                    PageMethod("wait_for_timeout", 3500),
                ],
                "handle_httpstatus_all": True,
                "download_timeout": 45,
            },
        )

    def parse_jobscz_playwright(self, response: HtmlResponse):
        row = response.meta["input_row"]
        source = response.meta.get("source", "jobscz")
        static_extracted = dict(response.meta.get("static_extracted") or {})
        record = self._base_record(row, source, response)

        try:
            if response.status >= 400:
                static_extracted["Extraction_Status"] = "partial"
                static_extracted["Extraction_Error"] = self.append_note(
                    static_extracted.get("Extraction_Error", ""),
                    f"Playwright fallback returned HTTP {response.status}.",
                )
                record.update({k: v for k, v in static_extracted.items() if v not in (None, "")})
                self._save_diagnostic_html(response, record, suffix="playwright")
                self._write_record(record)
                return

            rendered = self.parse_jobscz(response)
            merged = dict(static_extracted)

            # Browser-rendered values generally supersede static shell values. Keep the
            # longer description if one side only contains an OpenGraph teaser.
            for key, value in rendered.items():
                if value in (None, ""):
                    continue
                if key == "Description":
                    if len(self.clean(value)) >= len(self.clean(merged.get(key, ""))):
                        merged[key] = value
                else:
                    merged[key] = value

            if rendered.get("Extraction_Status") == "ok" and rendered.get("Content_Completeness") == "FULL":
                base_parser = rendered.get("Parser") or static_extracted.get("Parser") or "jobscz_widget"
                merged["Parser"] = f"{base_parser}_playwright"
                merged["Extraction_Status"] = "ok"
                merged["Extraction_Error"] = ""
            else:
                base_parser = static_extracted.get("Parser") or rendered.get("Parser") or "jobscz_widget"
                merged["Parser"] = f"{base_parser}_playwright_partial"
                merged["Extraction_Status"] = "partial"
                merged["Extraction_Error"] = self.append_note(
                    static_extracted.get("Extraction_Error", ""),
                    "Playwright rendered the page, but the full vacancy body was still unavailable.",
                )

            record.update({k: v for k, v in merged.items() if v not in (None, "")})
            self._finalize_status(record)
            if record.get("Extraction_Status") != "ok":
                self._save_diagnostic_html(response, record, suffix="playwright")
            self._write_record(record)

        except Exception as exc:
            self.logger.exception("Playwright Jobs.cz extraction failed for %s", response.url)
            static_extracted["Extraction_Status"] = "partial"
            static_extracted["Extraction_Error"] = self.append_note(
                static_extracted.get("Extraction_Error", ""),
                f"Playwright parse failed: {type(exc).__name__}: {exc}",
            )
            record.update({k: v for k, v in static_extracted.items() if v not in (None, "")})
            self._save_diagnostic_html(response, record, suffix="playwright")
            self._write_record(record)

    def errback_jobscz_playwright(self, failure):
        request = failure.request
        row = request.meta.get("input_row", {})
        source = request.meta.get("source", "jobscz")
        static_extracted = dict(request.meta.get("static_extracted") or {})
        record = self._base_record(row, source, None)
        record["Final_URL"] = request.url
        static_extracted["Extraction_Status"] = "partial"
        static_extracted["Extraction_Error"] = self.append_note(
            static_extracted.get("Extraction_Error", ""),
            f"Playwright fallback failed: {failure.getErrorMessage()}",
        )
        record.update({k: v for k, v in static_extracted.items() if v not in (None, "")})
        self._write_record(record)

    def errback_detail(self, failure):
        request = failure.request
        row = request.meta.get("input_row", {})
        source = request.meta.get("source", "unknown")
        record = self._base_record(row, source, None)
        record["Final_URL"] = request.url
        record["Current_Status"] = "REQUEST_FAILED"
        record["Extraction_Status"] = "failed"
        record["Extraction_Error"] = failure.getErrorMessage()
        record["Status_Evidence"] = type(failure.value).__name__
        self._write_record(record)

    def _base_record(self, row: dict[str, Any], source: str, response: Optional[HtmlResponse]) -> dict[str, str]:
        mining_date = self.clean(row.get("Mining_Date", ""))
        return {
            "ID": self.clean(row.get("ID", "")),
            "Source": source,
            "Input_URL": self.clean(row.get("URL", "")),
            "Final_URL": response.url if response is not None else "",
            "Job_Title_Input": self.clean(row.get("Job Title", "")),
            "Company_Input": self.clean(row.get("Company", "")),
            "Seniority_Input": self.clean(row.get("Seniority", "")),
            "Grade_Input": self.clean(row.get("Grade", "")),
            "Mining_Date": mining_date,
            "Published_Upper_Bound": self.clean(row.get("First_Seen_At")) or mining_date,
            "Publication_Method": "first_seen_only" if mining_date else "",
            "Publication_Confidence": "UPPER_BOUND_ONLY" if mining_date else "UNKNOWN",
            "HTTP_Status": str(response.status) if response is not None else "",
            "Page_Language": self.clean(response.xpath("/html/@lang").get()) if response is not None else "",
            "Extraction_Date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "Extraction_Status": "partial",
            "Parser": source,
            "Structured_Data_Found": "0",
            "First_Seen_At": self.clean(row.get("First_Seen_At")) or mining_date,
            "Last_Seen_At": self.clean(row.get("Last_Seen_At")) or mining_date,
            "Location_Input": self.clean(row.get("Location")),
            "Position_Type_Input": self.clean(row.get("Position.Type")),
            "Position_Subtype_Input": self.clean(row.get("Position.Subtype")),
            "Secondary_Subtype_Input": self.clean(row.get("Secondary Subtype")),
            "Role_Family": self.clean(row.get("Role_Family")),
            "Provisional_Shortlisting_Score": self.clean(row.get("Provisional.Shortlisting.Score")),
            "Parser_Version": "prospect_details_v2.0",
        }

    def _should_refresh(self, row) -> bool:
        attempted = self.parse_datetime(self.clean(row.get("Extraction_Date", "")))
        if attempted is None: return True
        age_hours = max(0, (datetime.now(timezone.utc) - attempted).total_seconds() / 3600)
        status = self.clean(row.get("Last_Attempt_Status") or row.get("Extraction_Status", "")).lower()
        if self.clean(row.get("Parser_Version")) != "prospect_details_v2.0": return True
        if status in {"failed", ""}: return True
        if status == "blocked": return age_hours >= profile()["blocked_retry_hours"]
        if status == "partial": return age_hours >= profile()["partial_retry_hours"]
        return age_hours >= self.refresh_days * 24

    # ------------------------------------------------------------------
    # Source parsers
    # ------------------------------------------------------------------

    def parse_builtin(self, response: HtmlResponse) -> dict[str, str]:
        job = self.find_jobposting_jsonld(response)
        out: dict[str, str] = {"Parser": "builtin_jsonld"}

        if job:
            out.update(self.from_jobposting(job))
            out["Structured_Data_Found"] = "1"
            out["Canonical_URL"] = self.clean(response.xpath("//link[@rel='canonical']/@href").get())
            if out.get("Published_At"):
                out["Publication_Method"] = "jsonld_datePosted"
                out["Publication_Confidence"] = "EXACT"
                out["Published_Lower_Bound"] = out["Published_At"]
                out["Published_Upper_Bound"] = out["Published_At"]

        # Repost text is secondary evidence only. Never let a cosmetic DOM change
        # invalidate an otherwise rich JSON-LD extraction. XPath 1.0 does not allow
        # `/normalize-space(.)` as a location step, so read matching text nodes instead.
        repost = ""
        try:
            repost_candidates = response.xpath("//text()[contains(., 'Reposted')]").getall()
            repost = next((self.clean(x) for x in repost_candidates if self.clean(x)), "")
            if not repost:
                repost = self.clean(response.xpath("//i[contains(@title,'Job Posted')]/@title").get())
        except Exception as exc:
            self.logger.debug("Could not extract BuiltIn repost text for %s: %s", response.url, exc)
        out["Repost_Text"] = repost
        out["Canonical_URL"] = self.clean(response.xpath("//link[@rel='canonical']/@href").get())

        # Visible fallback title/company if JSON-LD ever changes.
        out.setdefault("Job_Title_Page", self.clean(response.css("h1::text").get()))
        return out

    def parse_startupjobs(self, response: HtmlResponse) -> dict[str, str]:
        """Extract StartupJobs using a three-level fallback chain.

        1) Schema.org JobPosting JSON-LD (preferred public representation)
        2) Nuxt __NUXT_DATA__ embedded application state
        3) Server-rendered article/body HTML

        The Nuxt fallback is especially useful for older or differently rendered offers
        where StartupJobs omits the JobPosting JSON-LD block but still ships the offer
        object to the browser.
        """
        out: dict[str, str] = {
            "Parser": "startupjobs_html",
            "Structured_Data_Found": "0",
            "Canonical_URL": self.clean(response.xpath("//link[@rel='canonical']/@href").get()),
        }

        job = self.find_jobposting_jsonld(response)
        nuxt_offer = self.find_startupjobs_nuxt_offer(response)

        if job:
            out.update(self.from_jobposting(job))
            out["Parser"] = "startupjobs_jsonld"
            out["Structured_Data_Found"] = "1"
            if out.get("Published_At"):
                out["Publication_Method"] = "jsonld_datePosted"
                out["Publication_Confidence"] = "EXACT"
                out["Published_Lower_Bound"] = out["Published_At"]
                out["Published_Upper_Bound"] = out["Published_At"]

        # Even when JSON-LD exists, Nuxt often provides richer factual fields such as
        # seniority, languages and required skills. JSON-LD remains authoritative for
        # fields it already supplied; Nuxt fills gaps.
        if nuxt_offer:
            nuxt_fields = self.from_startupjobs_nuxt_offer(nuxt_offer)
            for key, value in nuxt_fields.items():
                if value not in (None, "") and not out.get(key):
                    out[key] = value

            if not job:
                out["Parser"] = "startupjobs_nuxt"
                out["Structured_Data_Found"] = "1"
                if out.get("Published_At"):
                    out["Publication_Method"] = "startupjobs_nuxt_createdAt"
                    out["Publication_Confidence"] = "EXACT"
                    out["Published_Lower_Bound"] = out["Published_At"]
                    out["Published_Upper_Bound"] = out["Published_At"]

        # Last-resort SSR/HTML extraction. This intentionally fills only missing
        # factual fields so it cannot overwrite richer structured data.
        rendered = self.parse_startupjobs_rendered(response)
        for key, value in rendered.items():
            if value not in (None, "") and not out.get(key):
                out[key] = value

        # Prefer explicit status from the resolved Nuxt offer.
        state = self.clean(nuxt_offer.get("status", "")).lower() if nuxt_offer else ""
        if not state:
            # Conservative fallback for page variants where the compact Nuxt payload
            # is present but could not be resolved.
            m = re.search(r'"status"\s*:\s*"(published|expired|closed|draft)"', response.text, flags=re.I)
            state = m.group(1).lower() if m else ""

        if state:
            if state == "published":
                out["Current_Status"] = "OPEN"
            elif state in {"expired", "closed"}:
                out["Current_Status"] = "CLOSED"
            out["Status_Evidence"] = f"startupjobs status={state}"

        return out

    def parse_jobstack(self, response: HtmlResponse) -> dict[str, str]:
        out: dict[str, str] = {
            "Parser": "jobstack_html",
            "Structured_Data_Found": "0",
        }

        out["Job_Title_Page"] = self.clean(response.css("h1.jobpost-h1-2023::text").get())
        if not out["Job_Title_Page"]:
            out["Job_Title_Page"] = self.title_from_html_title(response)

        meta_desc = response.xpath("//meta[@name='description']/@content").get()

        # Main description: use the block headed 'Popis pozice'.
        desc_nodes = response.css("div.jobpost-description-2023 div.wysiwyg-content")
        out["Description"] = self.selector_text(desc_nodes[0]) if desc_nodes else self.clean(meta_desc)
        out["Content_Source"] = "vacancy_body" if desc_nodes else "meta_teaser"
        out["Content_Completeness"] = "FULL" if desc_nodes and len(out["Description"]) >= 500 else "PARTIAL"

        # Requirement area is well structured on current JobStack pages.
        req_box = response.css("div.jobpost-2023-requirements-box")
        out["Requirements"] = self.selector_text(req_box[0]) if req_box else ""

        # Seniority labels.
        seniors = response.css("ul.jobpost-basic-requirements-2023 span.custom-profile-label::text").getall()
        out["Seniority_Detail"] = self.join_unique(seniors)

        # Extract named label groups from the requirements box.
        out["Languages"] = self.extract_jobstack_group(response, "Jazyky")
        base_skills = self.extract_jobstack_group(response, "Základní dovednosti")
        bonus_skills = self.extract_jobstack_group(response, "Výhodou")
        out["Required_Skills"] = base_skills
        out["Preferred_Skills"] = bonus_skills
        out["Skills"] = f"{base_skills} | {bonus_skills}" if base_skills or bonus_skills else ""

        # Information table is label/value based and therefore robust to row order.
        info = self.extract_table_by_header(response, "div.jobpost-2023-informations table")
        out["Salary_Detail"] = self.first_dict_value(info, ["Mzda", "Odměna", "Plat"])
        out["Remote_Mode"] = self.first_dict_value(info, ["Vzdálená práce", "Remote", "Home office"])
        out["Employment_Type"] = self.join_unique([
            self.first_dict_value(info, ["Typ smlouvy"]),
            self.first_dict_value(info, ["Typ pracovního úvazku", "Úvazek"]),
        ], sep=" | ")
        out["Location_Detail"] = self.first_dict_value(info, ["Místo pracoviště", "Lokalita", "Místo"])

        # Company heading near the company profile section.
        company_candidates = response.css("div.jobpost-account-2023 h2::text").getall()
        out["Company_Page"] = self.clean(company_candidates[0]) if company_candidates else ""

        # Reply link is strong evidence that this static page still offers an application route.
        reply = response.css("a[href*='/jobpostreply/']::attr(href)").get()
        if reply:
            out["Current_Status"] = "OPEN"
            out["Status_Evidence"] = "JobStack application link present"

        return out

    def parse_jobscz(self, response: HtmlResponse) -> dict[str, str]:
        """Extract Jobs.cz and employer-branded Alma Career pages conservatively.

        Jobs.cz currently exposes at least three public detail families:

        * normal/server-rendered Jobs.cz pages (often /fp/...);
        * employer pages using the legacy/current LMC Career Widget;
        * employer pages using the newer React ``data-widget="main"`` shell.

        The latter two can return only a loader/widget shell to a plain HTTP client.
        In that case we preserve all reliable head metadata and explicitly report a
        PARTIAL extraction rather than treating the page as broken.
        """
        out: dict[str, str] = {
            "Parser": "jobscz_alma",
            "Structured_Data_Found": "0",
        }

        structured_job = self.find_jobposting_jsonld(response)
        if structured_job:
            out.update(self.from_jobposting(structured_job))
            out["Structured_Data_Found"] = "1"
            out["Content_Source"] = "jsonld_description"
        out["Job_Title_Page"] = out.get("Job_Title_Page") or self.jobscz_title(response)
        out["Canonical_URL"] = self.clean(response.xpath("//link[@rel='canonical']/@href").get())

        meta_desc = self.clean(
            response.xpath("//meta[@property='og:description']/@content").get()
            or response.xpath("//meta[@name='description']/@content").get()
        )
        if not out.get("Description") and self.meaningful_text(meta_desc, min_len=25):
            out["Description"] = meta_desc
            out["Content_Source"] = "meta_teaser"
            out["Content_Completeness"] = "TEASER"

        html_title = self.clean(response.css("title::text").get())
        out["Company_Page"] = self.company_from_jobscz_title(html_title)

        body = response.text
        page_text = self.clean(" ".join(response.xpath("//body//text()").getall()))
        page_text_lower = page_text.lower()

        # Identify Alma integration generation. Keep the parser name explicit because
        # it is valuable downstream when we assess extraction completeness.
        widget_kind = ""
        if "__LMC_CAREER_WIDGET__" in body:
            widget_kind = "lmc"
            out["Parser"] = "jobscz_lmc_widget"
        elif 'data-widget="main"' in body or "data-widget='main'" in body:
            widget_kind = "react"
            out["Parser"] = "jobscz_react_widget"

        # Extract the public widget configuration when present. We deliberately do not
        # call undocumented backend endpoints here; the visible apiKey/widgetId simply
        # confirms that this is a client-rendered vacancy rather than an empty page.
        if widget_kind == "lmc":
            m = re.search(r'__LMC_CAREER_WIDGET__\.push\((\{.*?\})\)\s*;', body, flags=re.S)
            if m:
                try:
                    cfg = json.loads(m.group(1))
                    if cfg.get("host") and not out.get("Company_Page"):
                        host = self.clean(cfg.get("host"))
                        out["Company_Page"] = host.split(".jobs.cz")[0].replace("-", " ").strip()
                except Exception:
                    pass

        # First preference: a rendered vacancy container. Server-rendered Jobs.cz pages
        # often expose the full advert here; widget pages usually expose only a loader.
        vacancy_nodes = response.xpath(
            "//*[@id='vacancy-detail' or @id='widget_container' or "
            "contains(concat(' ', normalize-space(@class), ' '), ' vacancy-detail ')]"
        )
        rendered_text = ""
        for node in vacancy_nodes:
            candidate = self.selector_text(node)
            if len(candidate) > len(rendered_text):
                rendered_text = candidate

        # Strip obvious loader/shell wording from the quality decision.
        shell_only = (
            not rendered_text
            or len(rendered_text) < 250
            or rendered_text.lower() in {"loading", "načítání"}
        )

        # A short server-rendered fragment is not enough to call a vacancy fully
        # enriched. This catches normal Jobs.cz pages that expose only the opening
        # paragraph in static HTML.
        substantive_body = (not shell_only) and len(rendered_text) >= 500
        structured_body = out.get("Structured_Data_Found") == "1" and len(out.get("Description", "")) >= 500
        if structured_body and not substantive_body:
            rendered_text = out["Description"]
            substantive_body = True

        if substantive_body:
            # Prefer full body content over the short OpenGraph teaser.
            out["Description"] = rendered_text
            out["Content_Source"] = "jsonld_description" if structured_body else "vacancy_body"
            out["Content_Completeness"] = "FULL"
            out["Extraction_Status"] = "ok"

            # Best-effort extraction of labelled facts from server-rendered Alma pages.
            # These selectors are intentionally broad and only fill values when visible.
            salary = self.first_text_matching(response, [
                r"\b\d[\d \u00a0.]*\s*(?:–|-|až)\s*\d[\d \u00a0.]*\s*Kč",
                r"\b\d[\d \u00a0.]*\s*Kč\s*/?\s*(?:měsíc|hod|hodinu|month)",
            ])
            if salary:
                out["Salary_Detail"] = salary

            location = self.text_after_labels(response, [
                "Místo pracoviště", "Lokalita", "Pracoviště", "Location", "Workplace"
            ])
            if location:
                out["Location_Detail"] = location

            employment = self.text_after_labels(response, [
                "Typ pracovního poměru", "Typ úvazku", "Úvazek", "Employment type"
            ])
            if employment:
                out["Employment_Type"] = employment

            languages = self.text_after_labels(response, [
                "Požadované jazyky", "Jazyky", "Languages", "Language skills"
            ])
            if languages:
                out["Languages"] = languages
        else:
            # A specific vacancy title/teaser plus a widget shell is not an extraction
            # failure. It simply means the full advert is rendered client-side.
            if widget_kind:
                out["Extraction_Status"] = "partial"
                out["Extraction_Error"] = (
                    "Alma Career vacancy body is client-side rendered; static HTTP "
                    "response exposes title/company/teaser but not the full detail."
                )
            elif self.meaningful_text(out.get("Description", ""), min_len=25):
                # Normal Jobs.cz pages sometimes expose only an intro/teaser in the
                # container we can identify reliably. Keep that evidence, but do not
                # let downstream finalization promote it to a complete extraction.
                out["Extraction_Status"] = "partial"
                out["Extraction_Error"] = (
                    "Jobs.cz static response exposed only a short vacancy fragment; "
                    "full detail was not confidently identified."
                )

        # Browser-rendered employer pages often expose a live application control.
        # This is stronger availability evidence than the mere presence of widget metadata.
        apply_texts = [
            self.clean(x)
            for x in response.xpath("//*[self::a or self::button]//text()").getall()
            if self.clean(x)
        ]
        apply_blob = " | ".join(apply_texts).lower()
        has_apply_control = any(
            token in apply_blob
            for token in [
                "odpovědět", "reagovat", "mám zájem", "apply", "apply now",
                "respond to this job", "send application",
            ]
        )

        # Explicit lifecycle markers always override weaker availability assumptions.
        closed_markers = [
            "pozice již není aktivní",
            "nabídka již není aktivní",
            "tato pozice již není dostupná",
            "pozice již byla obsazena",
            "vacancy is no longer available",
            "position is no longer available",
            "this vacancy is no longer available",
        ]
        if any(marker in page_text_lower for marker in closed_markers):
            out["Current_Status"] = "CLOSED"
            out["Status_Evidence"] = "Explicit closed-vacancy text on Jobs.cz/Alma page"
        elif has_apply_control and substantive_body:
            out["Current_Status"] = "OPEN"
            out["Status_Evidence"] = "Rendered vacancy body + application control present"
        elif widget_kind and out.get("Job_Title_Page") and self.meaningful_text(out.get("Description", ""), min_len=25):
            # Do not call this definitively OPEN: employer microsites can occasionally
            # retain metadata after a vacancy closes. Still, a 200 detail route carrying
            # a specific vacancy title/teaser is useful positive evidence.
            out["Current_Status"] = "LIKELY_OPEN"
            out["Status_Evidence"] = f"HTTP 200 specific vacancy metadata + Alma {widget_kind} widget shell"

        return out

    def parse_indeed(self, response: HtmlResponse) -> dict[str, str]:
        # Indeed is kept isolated because markup and anti-bot responses change frequently.
        out = self.parse_generic(response)
        out["Parser"] = "indeed_generic"
        text = self.clean(" ".join(response.xpath("//body//text()").getall())).lower()
        if any(x in text for x in ["additional verification required", "verify you are human", "access denied"]):
            out["Extraction_Status"] = "blocked"
            out["Extraction_Error"] = "Indeed anti-bot / verification page detected"
            out["Current_Status"] = "UNKNOWN"
            out["Status_Evidence"] = "anti-bot response"
        return out

    def parse_generic(self, response: HtmlResponse) -> dict[str, str]:
        job = self.find_jobposting_jsonld(response)
        out: dict[str, str] = {"Parser": "generic"}
        if job:
            out.update(self.from_jobposting(job))
            out["Structured_Data_Found"] = "1"
        else:
            out["Job_Title_Page"] = self.clean(response.css("h1::text").get()) or self.title_from_html_title(response)
            out["Content_Source"] = "meta_teaser"
            out["Content_Completeness"] = "TEASER"
            out["Description"] = self.clean(
                response.xpath("//meta[@property='og:description']/@content").get()
                or response.xpath("//meta[@name='description']/@content").get()
            )
        return out

    # ------------------------------------------------------------------
    # JSON-LD helpers
    # ------------------------------------------------------------------

    def find_jobposting_jsonld(self, response: HtmlResponse) -> Optional[dict[str, Any]]:
        candidates = []
        for raw in response.xpath("//script[contains(translate(@type,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'ld+json')]/text()").getall():
            try: data = json.loads(html.unescape(raw.strip()))
            except (ValueError, TypeError): continue
            for obj in self.walk_json_objects(data):
                typ = obj.get("@type")
                if any(str(x).lower() == "jobposting" for x in (typ if isinstance(typ,list) else [typ])): candidates.append(obj)
        target = normalize_url(response.url)
        for obj in candidates:
            url = obj.get("url")
            if isinstance(url,str) and normalize_url(url)==target: return obj
        if len(candidates)==1: return candidates[0]
        visible = self.clean(response.css("h1::text").get()).casefold()
        matches = [obj for obj in candidates if self.clean(obj.get("title")).casefold()==visible and visible]
        return matches[0] if len(matches)==1 else None

    def walk_json_objects(self, value: Any) -> Iterable[dict[str, Any]]:
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from self.walk_json_objects(child)
        elif isinstance(value, list):
            for child in value:
                yield from self.walk_json_objects(child)

    def from_jobposting(self, job: dict[str, Any]) -> dict[str, str]:
        org = job.get("hiringOrganization") or {}
        if isinstance(org, str):
            company = org
        else:
            company = org.get("name", "") if isinstance(org, dict) else ""

        countries = []
        for x in self.as_list(job.get("applicantLocationRequirements")):
            if isinstance(x, dict):
                countries.append(x.get("name", ""))

        locations = []
        for loc in self.as_list(job.get("jobLocation")):
            if not isinstance(loc, dict):
                continue
            address = loc.get("address") or {}
            if isinstance(address, dict):
                parts = [
                    address.get("streetAddress"),
                    address.get("addressLocality"),
                    address.get("addressRegion"),
                    address.get("postalCode"),
                    address.get("addressCountry"),
                ]
                val = ", ".join(self.clean(p) for p in parts if self.clean(p))
                if val:
                    locations.append(val)

        remote = ""
        if str(job.get("jobLocationType", "")).upper() == "TELECOMMUTE":
            remote = "Remote / telecommute"

        return {
            "Job_Title_Page": self.clean(job.get("title")),
            "Company_Page": self.clean(company),
            "Description": self.html_to_text(job.get("description", "")),
            "Requirements": self.join_unique([self.html_to_text(job.get(k, "")) for k in ["qualifications", "experienceRequirements", "educationRequirements"]], sep="\n"),
            "Qualifications": self.html_to_text(job.get("qualifications", "")),
            "Education_Requirements": self.html_to_text(job.get("educationRequirements", "")),
            "Experience_Requirements": self.html_to_text(job.get("experienceRequirements", "")),
            "Skills": self.value_to_text(job.get("skills", "")),
            "Content_Source": "jsonld_description",
            "Content_Completeness": "FULL" if len(self.html_to_text(job.get("description", ""))) >= 500 else "PARTIAL",
            "Location_Detail": self.join_unique(locations),
            "Remote_Mode": remote,
            "Employment_Type": self.value_to_text(job.get("employmentType")),
            "Salary_Detail": self.salary_to_text(job.get("baseSalary")),
            "Applicant_Countries": self.join_unique(countries),
            "Published_At": self.clean(job.get("datePosted")),
            "Valid_Through": self.clean(job.get("validThrough")),
        }

    # ------------------------------------------------------------------
    # StartupJobs Nuxt / rendered fallbacks
    # ------------------------------------------------------------------

    def find_startupjobs_nuxt_offer(self, response: HtmlResponse) -> Optional[dict[str, Any]]:
        """Resolve the compact devalue-style __NUXT_DATA__ payload and return the offer object.

        Nuxt serializes object values as integer references into one top-level array.
        We do not need to deserialize the whole application state; we identify offer-shaped
        raw dictionaries first and resolve only the matching candidate.
        """
        raw = response.xpath("//script[@id='__NUXT_DATA__']/text()").get()
        if not raw:
            raw = response.xpath("//script[@data-nuxt-data='nuxt-app']/text()").get()
        if not raw:
            return None

        try:
            root = json.loads(raw)
        except Exception:
            return None
        if not isinstance(root, list):
            return None

        cache: dict[int, Any] = {}
        resolving: set[int] = set()
        wrapper_tags = {"ShallowReactive", "Reactive", "Ref", "ShallowRef"}

        def is_ref(value: Any) -> bool:
            return (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value < len(root)
            )

        def resolve_ref(index: int, depth: int = 0) -> Any:
            if depth > 40 or index in resolving:
                return None
            if index in cache:
                return cache[index]
            resolving.add(index)
            try:
                value = resolve_value(root[index], depth + 1)
                cache[index] = value
                return value
            finally:
                resolving.discard(index)

        def resolve_value(value: Any, depth: int = 0) -> Any:
            if depth > 40:
                return None
            if isinstance(value, dict):
                return {
                    key: resolve_ref(val, depth + 1) if is_ref(val) else resolve_value(val, depth + 1)
                    for key, val in value.items()
                }
            if isinstance(value, list):
                if (
                    len(value) == 2
                    and isinstance(value[0], str)
                    and value[0] in wrapper_tags
                    and is_ref(value[1])
                ):
                    return resolve_ref(value[1], depth + 1)
                return [
                    resolve_ref(val, depth + 1) if is_ref(val) else resolve_value(val, depth + 1)
                    for val in value
                ]
            return value

        # Offer records in current StartupJobs Nuxt state have this stable semantic core.
        required_keys = {"createdAt", "company", "status", "slug", "name", "description"}
        candidates: list[dict[str, Any]] = []
        for idx, value in enumerate(root):
            if not isinstance(value, dict) or not required_keys.issubset(value.keys()):
                continue
            resolved = resolve_ref(idx)
            if isinstance(resolved, dict):
                candidates.append(resolved)

        if not candidates:
            return None

        url_slug = response.url.rstrip("/").split("/")[-1].casefold()
        for candidate in candidates:
            slug = self.clean(candidate.get("slug", "")).casefold()
            if slug and slug == url_slug:
                return candidate

        # Usually there is exactly one offer-shaped object. Prefer a candidate whose title
        # matches the visible h1 before falling back to the first one.
        visible_title = self.clean(response.css("h1::text").get()).casefold()
        if visible_title:
            for candidate in candidates:
                title = self.localized_value(candidate.get("name")).casefold()
                if title and title == visible_title:
                    return candidate
        return candidates[0] if len(candidates) == 1 else None

    def from_startupjobs_nuxt_offer(self, offer: dict[str, Any]) -> dict[str, str]:
        company = offer.get("company") or {}
        company_name = company.get("name", "") if isinstance(company, dict) else company

        locations: list[str] = []
        for loc in self.as_list(offer.get("locations")):
            if not isinstance(loc, dict):
                continue
            name = self.localized_value(loc.get("name"))
            if not name:
                pieces = [
                    self.localized_value(loc.get("place")),
                    self.localized_value(loc.get("district")),
                    self.localized_value(loc.get("country")),
                ]
                name = ", ".join(x for x in pieces if x)
            if name:
                locations.append(name)

        salary = offer.get("salary") or {}
        salary_text = ""
        if isinstance(salary, dict):
            lo = self.clean(salary.get("minimum", ""))
            hi = self.clean(salary.get("maximum", ""))
            currency = self.clean(salary.get("currency", ""))
            measure = self.clean(salary.get("measure", ""))
            amount = " - ".join(x for x in [lo, hi] if x)
            salary_text = self.clean(" ".join(x for x in [amount, currency, measure.upper()] if x))

        languages: list[str] = []
        lang_block = offer.get("languages") or {}
        if isinstance(lang_block, dict):
            selected = lang_block.get("en") or lang_block.get("cs") or []
        else:
            selected = lang_block
        for lang in self.as_list(selected):
            if isinstance(lang, dict):
                name = self.clean(lang.get("name", ""))
                level = self.clean(lang.get("level", ""))
                languages.append(f"{name} (level {level})" if name and level else name)
            else:
                languages.append(self.clean(lang))

        skills: list[str] = []
        for skill in self.as_list(offer.get("skills")):
            if isinstance(skill, dict):
                name = self.clean(skill.get("name", ""))
                typ = self.clean(skill.get("type", ""))
                skills.append(f"{name} [{typ}]" if name and typ else name)
            else:
                skills.append(self.clean(skill))

        collaborations = [self.clean(x).lower() for x in self.as_list(offer.get("collaborations")) if self.clean(x)]
        work_modes = [x for x in collaborations if x in {"remote", "hybrid", "onsite", "on-site"}]

        seniorities = [self.clean(x) for x in self.as_list(offer.get("seniorities")) if self.clean(x)]

        return {
            "Job_Title_Page": self.localized_value(offer.get("name")),
            "Company_Page": self.clean(company_name),
            "Description": self.best_startupjobs_description(offer),
            "Content_Source": "nuxt_description" if self.is_meaningful_description(self.localized_value(offer.get("description"))) else "nuxt_teaser",
            "Content_Completeness": "FULL" if len(self.html_to_text(self.localized_value(offer.get("description")))) >= 500 else "PARTIAL",
            "Location_Detail": self.join_unique(locations),
            "Remote_Mode": self.join_unique(work_modes),
            "Salary_Detail": salary_text,
            "Seniority_Detail": self.join_unique(seniorities),
            "Languages": self.join_unique(languages),
            "Skills": self.join_unique(skills),
            "Published_At": self.clean(offer.get("createdAt", "")),
        }

    def parse_startupjobs_rendered(self, response: HtmlResponse) -> dict[str, str]:
        """Final StartupJobs fallback using server-rendered vacancy HTML only."""
        out: dict[str, str] = {}
        out["Job_Title_Page"] = self.clean(response.css("article h1::text").get() or response.css("h1::text").get())

        # The current SSR layout renders the actual vacancy prose as the direct div child
        # after the article header. Avoid taking the entire page/nav text.
        body_candidates = response.css("article > div")
        if body_candidates:
            candidate = self.selector_text(body_candidates[0])
            if self.is_meaningful_description(candidate):
                out["Description"] = candidate
                out["Content_Source"] = "vacancy_body"
                out["Content_Completeness"] = "FULL" if len(candidate) >= 500 else "PARTIAL"
        if not out.get("Description"):
            meta_candidate = self.clean(
                response.xpath("//meta[@property='og:description']/@content").get()
                or response.xpath("//meta[@name='description']/@content").get()
            )
            if self.is_meaningful_description(meta_candidate):
                out["Description"] = meta_candidate
                out["Content_Source"] = "meta_teaser"
                out["Content_Completeness"] = "TEASER"

        # Company headings on StartupJobs are h6 elements with the company name in a span.
        company_candidates = [self.clean(x) for x in response.css("main h6 span::text").getall() if self.clean(x)]
        if company_candidates:
            out["Company_Page"] = company_candidates[0]
        return out

    def localized_value(self, value: Any) -> str:
        if isinstance(value, dict):
            for key in ("en", "cs"):
                if self.clean(value.get(key, "")):
                    return self.clean(value.get(key, ""))
            for candidate in value.values():
                if self.clean(candidate):
                    return self.clean(candidate)
            return ""
        return self.clean(value)

    def is_meaningful_description(self, value: Any) -> bool:
        """Reject ellipsis/placeholders and other non-descriptions.

        Expired StartupJobs pages sometimes retain the vacancy object but replace the
        long description with ``...``. Treating that as real content makes downstream
        review misleading, so only accept text with actual lexical substance.
        """
        text = self.html_to_text(value)
        if not text:
            return False

        compact = re.sub(r"[\s\.\u2026\-–—_:;,*]+", "", text).casefold()
        if not compact:
            return False

        placeholders = {
            "...",
            "…",
            "-",
            "n/a",
            "na",
            "none",
            "null",
            "undefined",
        }
        if text.strip().casefold() in placeholders:
            return False

        # A real vacancy summary should contain at least a little sentence-like text.
        alpha_count = sum(ch.isalpha() for ch in text)
        word_count = len(re.findall(r"\b\w+\b", text, flags=re.UNICODE))
        return alpha_count >= 30 and word_count >= 6

    def best_startupjobs_description(self, offer: dict[str, Any]) -> str:
        """Choose the richest surviving StartupJobs description without fabricating text.

        Current vacancies normally expose the full HTML body in ``description``.
        Expired vacancies may replace that value with ``...`` while preserving a
        shorter description in ``descriptionShort`` or sharing metadata. We consider
        those alternatives and select the longest meaningful text. If StartupJobs no
        longer supplies substantive prose, return an empty string.
        """
        candidates: list[Any] = []

        candidates.append(self.localized_value(offer.get("description")))
        candidates.append(self.localized_value(offer.get("descriptionShort")))

        share = offer.get("share")
        if isinstance(share, dict):
            candidates.append(self.localized_value(share.get("description")))

        # Some API versions wrap descriptive copy under adjacent semantic keys. Keep
        # this deliberately narrow so company boilerplate is not mistaken for the job
        # description.
        for key in ("content", "body", "jobDescription", "offerDescription"):
            if key in offer:
                candidates.append(self.localized_value(offer.get(key)))

        scored: list[tuple[int, str]] = []
        for candidate in candidates:
            text = self.html_to_text(candidate)
            if self.is_meaningful_description(text):
                scored.append((len(text), text))

        if not scored:
            return ""
        return max(scored, key=lambda item: item[0])[1]

    # ------------------------------------------------------------------
    # HTML/source-specific helpers
    # ------------------------------------------------------------------

    def extract_jobstack_group(self, response: HtmlResponse, heading: str) -> str:
        # Find an h3 and collect label spans until the next h3.
        nodes = response.xpath(
            f"//h3[contains(normalize-space(.), {self.xpath_literal(heading)})]"
        )
        if not nodes:
            return ""
        h3 = nodes[0]
        # Walk actual siblings so the extractor is independent of XPath current().
        result = []
        root = h3.root
        sib = root.getnext()
        while sib is not None and str(getattr(sib, "tag", "")).lower() != "h3":
            text = " ".join(sib.itertext()) if hasattr(sib, "itertext") else ""
            text = self.clean(text)
            if text:
                result.append(text)
            sib = sib.getnext()
        return self.join_unique(result)

    def extract_table_by_header(self, response: HtmlResponse, table_css: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in response.css(f"{table_css} tr"):
            key = self.clean(" ".join(row.css("th *::text, th::text").getall()))
            val = self.clean(" ".join(row.css("td *::text, td::text").getall()))
            if key and val:
                result[key] = val
        return result

    @staticmethod
    def first_dict_value(mapping: dict[str, str], fragments: list[str]) -> str:
        for fragment in fragments:
            f = fragment.lower()
            for key, value in mapping.items():
                if f in key.lower():
                    return value
        return ""

    def first_text_matching(self, response: HtmlResponse, patterns: list[str]) -> str:
        """Return the first visible text fragment matching one of the regex patterns."""
        texts = [self.clean(x) for x in response.xpath("//body//text()").getall()]
        for txt in texts:
            if not txt:
                continue
            for pattern in patterns:
                if re.search(pattern, txt, flags=re.I):
                    return txt
        return ""

    def text_after_labels(self, response: HtmlResponse, labels: list[str]) -> str:
        """Best-effort value extraction from common label/value HTML patterns."""
        for label in labels:
            nodes = response.xpath(
                "//*[self::dt or self::th or self::strong or self::b or self::span or self::div or self::p]"
                "[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ', "
                "'abcdefghijklmnopqrstuvwxyzáčďéěíňóřšťúůýž'), $needle)]",
                needle=label.lower(),
            )
            for node in nodes[:5]:
                # dl/table/list style sibling
                sibling = node.xpath("following-sibling::*[1]")
                if sibling:
                    val = self.selector_text(sibling[0])
                    if self.meaningful_text(val, min_len=2) and len(val) <= 300:
                        return val
                # label and value inside one parent container
                parent = node.xpath("parent::*[1]")
                if parent:
                    whole = self.selector_text(parent[0])
                    val = self.clean(re.sub(re.escape(label), "", whole, count=1, flags=re.I))
                    if self.meaningful_text(val, min_len=2) and len(val) <= 300:
                        return val
        return ""

    def jobscz_title(self, response: HtmlResponse) -> str:
        og = self.clean(response.xpath("//meta[@property='og:title']/@content").get())
        title = og or self.clean(response.css("title::text").get())
        # Common employer-site suffixes.
        for sep in [" - Vacancy detail", " - Detail pozice", " | Detail pozice", " | Vacancy detail"]:
            if sep.lower() in title.lower():
                idx = title.lower().find(sep.lower())
                return self.clean(title[:idx])
        return self.clean(re.split(r"\s+[|]\s+", title, maxsplit=1)[0])

    def company_from_jobscz_title(self, title: str) -> str:
        # Example: "Role - Vacancy detail | Aon" / "Role - Detail pozice | ČSOB"
        if "|" in title:
            tail = self.clean(title.rsplit("|", 1)[1])
            generic = {"jobs.cz", "prace.cz", "job detail", "detail pozice", "vacancy detail"}
            if tail and tail.lower() not in generic:
                return tail
        return ""

    def title_from_html_title(self, response: HtmlResponse) -> str:
        title = self.clean(response.css("title::text").get())
        return self.clean(re.split(r"\s+[|\-]\s+", title, maxsplit=1)[0])

    # ------------------------------------------------------------------
    # Finalization / output
    # ------------------------------------------------------------------

    def _finalize_status(self, record: dict[str, str]) -> None:
        if not record.get("Current_Status"):
            valid = self.parse_datetime(record.get("Valid_Through", ""))
            if valid is not None:
                record["Current_Status"] = "LIKELY_CLOSED" if valid < datetime.now(timezone.utc) else "LIKELY_OPEN"
                record["Status_Evidence"] = f"validThrough: {record.get('Valid_Through','')}"
        record.setdefault("Current_Status", "UNKNOWN")
        # A teaser never proves that requirements were fully retrieved.
        completeness = record.get("Content_Completeness", "")
        if not completeness:
            description = record.get("Description", "")
            completeness = "FULL" if record.get("Content_Source") in {"vacancy_body", "jsonld_description", "nuxt_description"} and len(description) >= 500 else "PARTIAL" if description else "UNAVAILABLE"
            record["Content_Completeness"] = completeness
        if record.get("Extraction_Status") not in {"failed", "blocked"}:
            record["Extraction_Status"] = "ok" if completeness == "FULL" and not record.get("Extraction_Error") else "partial"
        if record.get("Published_At"):
            record["Publication_Method"] = record.get("Publication_Method") if record.get("Publication_Method") not in {"", "first_seen_only"} else "structured_datePosted"
            record["Publication_Confidence"] = "EXACT"
            record["Published_Lower_Bound"] = record["Published_At"]
            record["Published_Upper_Bound"] = record["Published_At"]


    def _save_diagnostic_html(
        self, response: HtmlResponse, record: dict[str, Any], suffix: str = ""
    ) -> None:
        """Save the exact HTML for partial/failed records to aid source-parser debugging."""
        try:
            source = self.clean(record.get("Source", "unknown")) or "unknown"
            record_id = self.clean(record.get("ID", ""))
            if not record_id:
                record_id = response.url.rstrip("/").split("/")[-1] or "unknown"
            safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", record_id)[:120]
            folder = self.diagnostics_root / source
            folder.mkdir(parents=True, exist_ok=True)
            safe_suffix = re.sub(r"[^A-Za-z0-9._-]+", "_", self.clean(suffix))[:40]
            filename = f"{safe_id}_{safe_suffix}.html" if safe_suffix else f"{safe_id}.html"
            path = folder / filename
            path.write_bytes(response.body)
            self.logger.info("Saved diagnostic HTML: %s", path)
        except Exception as exc:
            self.logger.warning("Could not save diagnostic HTML for %s: %s", response.url, exc)

    def _compact_output_latest(self) -> None:
        """Preserve prior good content; keep latest attempt/status separately."""
        if not self.output_csv.exists() or not self.output_csv.stat().st_size: return
        try:
            df = pd.read_csv(self.output_csv, dtype=str, keep_default_na=False)
            if "Input_URL" not in df.columns: raise ValueError("Missing Input_URL")
            df["Input_URL"] = df["Input_URL"].map(normalize_url)
            df = df[df["Input_URL"] != ""]
            rows = []
            content = ["Description", "Requirements", "Languages", "Skills", "Required_Skills", "Preferred_Skills", "Qualifications", "Education_Requirements", "Experience_Requirements", "Content_Source", "Content_Completeness"]
            for _, group in df.groupby("Input_URL", sort=False):
                record = group.iloc[-1].to_dict()
                good = group[group.get("Extraction_Status", pd.Series("",index=group.index)).eq("ok")]
                if not good.empty:
                    last_good = good.iloc[-1]
                    record["Last_Successful_Extraction_At"] = last_good.get("Extraction_Date", "")
                    if record.get("Extraction_Status") in {"failed", "blocked"}:
                        for key in content: record[key] = last_good.get(key, "")
                rows.append(record)
            out = pd.DataFrame(rows).reindex(columns=self.OUTPUT_FIELDS, fill_value="").fillna("")
            tmp = self.output_csv.with_suffix(self.output_csv.suffix + ".tmp")
            out.to_csv(tmp,index=False,encoding="utf-8-sig");tmp.replace(self.output_csv)
        except Exception:
            self.logger.exception("Could not migrate/compact output; existing file was retained")

    def _write_record(self, record: dict[str, Any]) -> None:
        if not self._writer or not self._fh:
            raise RuntimeError("Output writer not initialized")
        record["Last_Attempt_Status"] = record.get("Extraction_Status", "")
        record["Last_Attempt_Error"] = record.get("Extraction_Error", "")
        if record.get("Extraction_Status") == "ok": record["Last_Successful_Extraction_At"] = record.get("Extraction_Date", "")
        long_fields = {"Description", "Requirements", "Qualifications", "Education_Requirements", "Experience_Requirements"}
        normalized = {field: self.long_text(record.get(field, "")) if field in long_fields else self.clean(record.get(field, "")) for field in self.OUTPUT_FIELDS}
        self._writer.writerow(normalized)
        self._fh.flush()  # preserve progress after every vacancy
        self._rows_written += 1
        if normalized.get("Input_URL"):
            self._seen_output_urls.add(normalized["Input_URL"])

        self.logger.info(
            "[%s] %s | %s | %s",
            normalized.get("Source"),
            normalized.get("Extraction_Status"),
            normalized.get("Current_Status"),
            normalized.get("Job_Title_Page") or normalized.get("Job_Title_Input"),
        )

    # ------------------------------------------------------------------
    # Generic utilities
    # ------------------------------------------------------------------

    @staticmethod
    def append_note(existing: Any, note: Any) -> str:
        left = re.sub(r"\s+", " ", str(existing or "")).strip()
        right = re.sub(r"\s+", " ", str(note or "")).strip()
        if not left:
            return right
        if not right or right in left:
            return left
        return f"{left} {right}"

    def meaningful_text(self, value: Any, min_len: int = 1) -> bool:
        """Return True only for non-placeholder text with useful content.

        This is intentionally lightweight: it is used for short labelled values as
        well as descriptions, so unlike ``is_meaningful_description`` it does not
        require sentence-length prose.
        """
        text = self.clean(value)
        if not text or len(text) < max(1, int(min_len)):
            return False

        lowered = text.casefold().strip()
        placeholders = {
            "...", "…", "..", "-", "--", "—", "–",
            "n/a", "na", "none", "null", "undefined",
            "loading", "načítání",
        }
        if lowered in placeholders:
            return False

        # Reject punctuation/whitespace-only fragments while allowing compact values
        # such as "VŠ", "IT", "B2" or "50k".
        return any(ch.isalnum() for ch in text)

    @staticmethod
    def clean(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple, set)):
            try:
                value = json.dumps(value, ensure_ascii=False)
            except Exception:
                value = str(value)
        text = html.unescape(str(value))
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def html_to_text(self, value: Any) -> str:
        if not value: return ""
        if isinstance(value, (dict,list)): return self.long_text(self.value_to_text(value))
        soup = BeautifulSoup(str(value), "html.parser")
        for tag in soup.find_all(["script", "style"]): tag.decompose()
        for tag in soup.find_all(["p", "li", "div", "h1", "h2", "h3", "h4", "br"]):
            tag.insert_before("\n"); tag.insert_after("\n")
        return self.long_text(soup.get_text(" "))

    @staticmethod
    def long_text(value: Any) -> str:
        raw = html.unescape(str(value or ""))
        return "\n".join(re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.splitlines() if line.strip())

    def selector_text(self, selector) -> str:
        try: return self.html_to_text(selector.get())
        except Exception: return ""

    def join_unique(self, values: Iterable[Any], sep: str = "; ") -> str:
        seen = set()
        out = []
        for value in values:
            v = self.long_text(value) if sep == "\n" else self.clean(value)
            if not v:
                continue
            key = v.casefold()
            if key not in seen:
                seen.add(key)
                out.append(v)
        return sep.join(out)

    @staticmethod
    def as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def value_to_text(self, value: Any) -> str:
        if isinstance(value, list):
            return self.join_unique(value)
        return self.clean(value)

    def salary_to_text(self, salary: Any) -> str:
        if not salary:
            return ""
        if isinstance(salary, str):
            return self.clean(salary)
        if not isinstance(salary, dict):
            return self.clean(salary)

        currency = self.clean(salary.get("currency", ""))
        value = salary.get("value", salary)
        if isinstance(value, dict):
            lo = self.clean(value.get("minValue", ""))
            hi = self.clean(value.get("maxValue", ""))
            unit = self.clean(value.get("unitText", ""))
            amount = " - ".join(x for x in [lo, hi] if x)
            return self.clean(" ".join(x for x in [amount, currency, unit] if x))
        return self.clean(value)

    @staticmethod
    def parse_datetime(value: str) -> Optional[datetime]:
        if not value:
            return None
        s = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def xpath_literal(value: str) -> str:
        if "'" not in value:
            return f"'{value}'"
        if '"' not in value:
            return f'"{value}"'
        parts = value.split("'")
        return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"

