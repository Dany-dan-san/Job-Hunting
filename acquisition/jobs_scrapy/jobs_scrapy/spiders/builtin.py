# jobs_scrapy/spiders/builtin_spider.py

from pathlib import Path
from urllib.parse import (
    urlparse,
    urlunparse,
    parse_qsl,
    urlencode,
)
from datetime import datetime
import asyncio
import csv
import hashlib
import json
import random

import scrapy
from scrapy.exceptions import CloseSpider


# =====================================================================
# PATHS
# =====================================================================

DATA_DIR = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\01_raw"
)

REGISTRY_DIR = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\01_raw\registries"
)

DATA_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

REGISTRY_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

OUTPUT_FILE = DATA_DIR / "builtin_basic.csv"

REGISTRY_FILE = REGISTRY_DIR / "builtin_registry.json"


# =====================================================================
# SEARCH PLAN
# =====================================================================

# Built In's robots.txt permits the category pages below but rejects result
# URLs containing the ``search=`` query parameter.  Broad category acquisition
# also fits this project better: role relevance is evaluated downstream by the
# taxonomy and review-queue stages.
SEARCH_URLS = [
    (
        "https://builtin.com/jobs/data-analytics"
        "?city=Prague&state=Prague&country=CZE&allLocations=true"
    ),
    (
        "https://builtin.com/jobs/engineering/software-engineering"
        "?city=Prague&state=Prague"
        "&country=CZE&allLocations=true"
    ),
]


# =====================================================================
# CSV STRUCTURE
# =====================================================================

CSV_FIELDS = [
    "ID",
    "Job Title",
    "Company",
    "Location",
    "Salary",
    "Seniority",
    "URL",
    "Mining_Date",
]


# =====================================================================
# CRAWLING TIMING
# =====================================================================

# Delay between BuiltIn result pages.
PAGE_DELAY_MIN = 5
PAGE_DELAY_MAX = 15

# Slightly longer pause when moving to a different search definition.
SEARCH_DELAY_MIN = 10
SEARCH_DELAY_MAX = 20

# Every 500 listings examined, take a substantial rest.
LONG_BREAK_EVERY = 500

# 5–10 minute long break.
LONG_BREAK_MIN = 5 * 60
LONG_BREAK_MAX = 10 * 60

# Save crawler state every N listings.
REGISTRY_CHECKPOINT_EVERY = 25

# Explicit 429 backoff.
RATE_LIMIT_DELAY_MIN = 10 * 60
RATE_LIMIT_DELAY_MAX = 20 * 60

MAX_RATE_LIMIT_RETRIES = 1


# =====================================================================
# SPIDER
# =====================================================================

class BuiltinSpider(scrapy.Spider):

    name = "builtin"

    allowed_domains = [
        "builtin.com",
        "www.builtin.com",
    ]

    # =================================================================
    # SCRAPY SETTINGS
    # =================================================================

    custom_settings = {

        # -------------------------------------------------------------
        # NORMAL DESKTOP BROWSER IDENTITY
        # -------------------------------------------------------------

        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),

        "DEFAULT_REQUEST_HEADERS": {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        },

        "COOKIES_ENABLED": True,
        "REFERER_ENABLED": True,

        # -------------------------------------------------------------
        # LOW-CONCURRENCY CRAWLING
        # -------------------------------------------------------------

        "CONCURRENT_REQUESTS": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,

        "DOWNLOAD_DELAY": 1.0,
        "DOWNLOAD_DELAY_JITTER": 0.5,

        # -------------------------------------------------------------
        # AUTOTHROTTLE
        # -------------------------------------------------------------

        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 2.0,
        "AUTOTHROTTLE_MAX_DELAY": 60.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5,

        # -------------------------------------------------------------
        # NETWORK / RETRIES
        # -------------------------------------------------------------

        "DOWNLOAD_TIMEOUT": 30,

        "RETRY_ENABLED": True,
        "RETRY_TIMES": 2,

        # 429 is excluded because we handle it ourselves using
        # a substantially longer backoff.
        "RETRY_HTTP_CODES": [
            408,
            500,
            502,
            503,
            504,
            522,
            524,
        ],

        "ROBOTSTXT_OBEY": True,

        "LOG_LEVEL": "INFO",
    }

    # =================================================================
    # INITIALIZATION
    # =================================================================

    def __init__(
        self,
        url=None,
        *args,
        **kwargs,
    ):

        super().__init__(
            *args,
            **kwargs,
        )

        # Supplying -a url=<URL> remains supported for a one-off crawl.
        # Without it, the spider runs the complete fixed search plan above.
        self.search_urls = (
            [url]
            if url
            else list(SEARCH_URLS)
        )

        if not self.search_urls:

            raise CloseSpider(
                "The BuiltIn search plan is empty."
            )

        for search_url in self.search_urls:

            self.validate_builtin_url(
                search_url
            )

        self.current_search_index = 0
        self.start_url = self.search_urls[0]

        # -------------------------------------------------------------
        # HISTORICAL DATABASE
        # -------------------------------------------------------------

        # Existing BuiltIn job IDs.
        self.known_ids = set()

        # Existing canonical job URLs.
        self.known_urls = set()

        self.prepare_existing_csv()

        # -------------------------------------------------------------
        # RUN / RESUME STATE
        # -------------------------------------------------------------

        # Number of pages completely processed.
        self.current_page = 0

        self.total_pages = 0

        self.new_jobs = 0
        self.duplicate_jobs = 0

        self.listings_seen_total = 0

        self.next_long_break_at = (
            LONG_BREAK_EVERY
        )

        # Pages successfully completed.
        self.visited_urls = []

        self.visited_page_keys = set()

        # Exact URL from which an interrupted crawl should resume.
        self.resume_url = self.start_url

        self.resuming = False

        # Registry belongs only to this particular search plan.
        self.plan_signature = (
            self.make_plan_signature()
        )

        self.load_or_create_registry()

    # =================================================================
    # SEARCH PLAN SIGNATURE
    # =================================================================

    def make_plan_signature(self):

        return hashlib.sha256(
            "\n".join(self.search_urls).encode(
                "utf-8"
            )
        ).hexdigest()

    # =================================================================
    # URL VALIDATION
    # =================================================================

    @staticmethod
    def validate_builtin_url(
        url,
    ):

        parsed = urlparse(
            url
        )

        hostname = (
            parsed.hostname or ""
        ).lower()

        if parsed.scheme not in {
            "http",
            "https",
        }:

            raise CloseSpider(
                "URL must begin with http:// or https://"
            )

        if (
            hostname != "builtin.com"
            and not hostname.endswith(
                ".builtin.com"
            )
        ):

            raise CloseSpider(
                "This spider only accepts BuiltIn URLs."
            )

    # =================================================================
    # CSV PREPARATION
    # =================================================================

    def prepare_existing_csv(self):

        # -------------------------------------------------------------
        # CREATE CSV IF IT DOES NOT EXIST
        # -------------------------------------------------------------

        if (
            not OUTPUT_FILE.exists()
            or OUTPUT_FILE.stat().st_size == 0
        ):

            with OUTPUT_FILE.open(
                "w",
                newline="",
                encoding="utf-8-sig",
            ) as file:

                writer = csv.DictWriter(
                    file,
                    fieldnames=CSV_FIELDS,
                )

                writer.writeheader()

            self.logger.info(
                "Created new CSV: %s",
                OUTPUT_FILE,
            )

            return

        # -------------------------------------------------------------
        # LOAD EXISTING JOBS
        # -------------------------------------------------------------

        with OUTPUT_FILE.open(
            "r",
            newline="",
            encoding="utf-8-sig",
        ) as file:

            reader = csv.DictReader(
                file
            )

            existing_fields = (
                reader.fieldnames or []
            )

            # Protect against accidentally using an incompatible CSV.
            missing_fields = [
                field
                for field in CSV_FIELDS
                if field not in existing_fields
            ]

            if missing_fields:

                raise CloseSpider(
                    "Existing builtin_basic.csv has an incompatible "
                    f"schema. Missing: {missing_fields}"
                )

            for row in reader:

                existing_id = (
                    row.get("ID") or ""
                ).strip()

                if existing_id:

                    self.known_ids.add(
                        existing_id
                    )

                existing_url = (
                    row.get("URL") or ""
                ).strip()

                if existing_url:

                    self.known_urls.add(
                        self.canonicalize_job_url(
                            existing_url
                        )
                    )

        self.logger.info(
            "Loaded %s existing BuiltIn job IDs and "
            "%s existing URLs from %s",
            len(self.known_ids),
            len(self.known_urls),
            OUTPUT_FILE,
        )

    # =================================================================
    # REGISTRY
    # =================================================================

    def load_or_create_registry(
        self,
    ):

        if REGISTRY_FILE.exists():

            try:

                with REGISTRY_FILE.open(
                    "r",
                    encoding="utf-8",
                ) as file:

                    state = json.load(
                        file
                    )

                same_plan = (
                    state.get(
                        "plan_signature"
                    )
                    == self.plan_signature
                )

                unfinished = not state.get(
                    "completed",
                    False,
                )

                if (
                    same_plan
                    and unfinished
                ):

                    self.current_search_index = int(
                        state.get(
                            "current_search_index",
                            0,
                        )
                    )

                    if not (
                        0
                        <= self.current_search_index
                        < len(self.search_urls)
                    ):

                        raise ValueError(
                            "Registry contains an invalid search index."
                        )

                    self.start_url = self.search_urls[
                        self.current_search_index
                    ]

                    self.current_page = int(
                        state.get(
                            "current_page",
                            0,
                        )
                    )

                    self.total_pages = int(
                        state.get(
                            "total_pages",
                            0,
                        )
                    )

                    self.new_jobs = int(
                        state.get(
                            "new_jobs",
                            0,
                        )
                    )

                    self.duplicate_jobs = int(
                        state.get(
                            "duplicate_jobs",
                            0,
                        )
                    )

                    self.listings_seen_total = int(
                        state.get(
                            "listings_seen_total",
                            0,
                        )
                    )

                    self.next_long_break_at = int(
                        state.get(
                            "next_long_break_at",
                            (
                                (
                                    self.listings_seen_total
                                    // LONG_BREAK_EVERY
                                )
                                + 1
                            )
                            * LONG_BREAK_EVERY,
                        )
                    )

                    self.visited_urls = list(
                        state.get(
                            "visited_urls",
                            [],
                        )
                    )

                    self.visited_page_keys = {
                        self.page_registry_key(
                            visited_url
                        )
                        for visited_url
                        in self.visited_urls
                    }

                    self.resume_url = (
                        state.get(
                            "resume_url"
                        )
                        or self.start_url
                    )

                    self.resuming = True

                    self.logger.info(
                        "RESUME STATE FOUND | "
                        "search=%s/%s | "
                        "completed pages=%s | "
                        "listings seen=%s | "
                        "resume=%s",
                        self.current_search_index + 1,
                        len(self.search_urls),
                        self.current_page,
                        self.listings_seen_total,
                        self.resume_url,
                    )

                    return

            except Exception as exc:

                self.logger.warning(
                    "Could not load registry: %s. "
                    "Starting a fresh run.",
                    exc,
                )

        # No usable unfinished run.
        self.reset_registry()

    def reset_registry(
        self,
    ):

        self.current_search_index = 0
        self.start_url = self.search_urls[0]

        self.current_page = 0
        self.total_pages = 0

        self.new_jobs = 0
        self.duplicate_jobs = 0

        self.listings_seen_total = 0

        self.next_long_break_at = (
            LONG_BREAK_EVERY
        )

        self.visited_urls = []
        self.visited_page_keys = set()

        self.resume_url = (
            self.start_url
        )

        self.resuming = False

        self.save_registry(
            resume_url=self.resume_url,
            completed=False,
        )

    def save_registry(
        self,
        resume_url=None,
        completed=False,
    ):

        if resume_url is not None:

            self.resume_url = (
                resume_url
            )

        state = {
            "version": 2,

            "plan_signature": (
                self.plan_signature
            ),

            "completed": completed,

            "updated_at": (
                self.current_datetime()
            ),

            "start_url": (
                self.start_url
            ),

            "search_urls": (
                self.search_urls
            ),

            "current_search_index": (
                self.current_search_index
            ),

            "current_page": (
                self.current_page
            ),

            "total_pages": (
                self.total_pages
            ),

            "listings_seen_total": (
                self.listings_seen_total
            ),

            "next_long_break_at": (
                self.next_long_break_at
            ),

            "new_jobs": (
                self.new_jobs
            ),

            "duplicate_jobs": (
                self.duplicate_jobs
            ),

            "resume_url": (
                None
                if completed
                else self.resume_url
            ),

            "visited_urls": (
                self.visited_urls
            ),
        }

        temp_file = (
            REGISTRY_FILE.with_suffix(
                ".tmp"
            )
        )

        with temp_file.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                state,
                file,
                ensure_ascii=False,
                indent=2,
            )

        # Atomic replacement protects against partial registry writes.
        temp_file.replace(
            REGISTRY_FILE
        )

    def mark_page_visited(
        self,
        url,
    ):

        key = self.page_registry_key(
            url
        )

        if key not in self.visited_page_keys:

            self.visited_page_keys.add(
                key
            )

            self.visited_urls.append(
                url
            )

    # =================================================================
    # START / RESUME
    # =================================================================

    async def start(
        self,
    ):

        if self.resuming:

            delay = random.uniform(
                5,
                15,
            )

            self.logger.info(
                "============================================================"
            )

            self.logger.info(
                "RESUMING PREVIOUS BUILTIN RUN"
            )

            self.logger.info(
                "Resume URL: %s",
                self.resume_url,
            )

            self.logger.info(
                "Waiting %.1f seconds before resuming...",
                delay,
            )

            self.logger.info(
                "============================================================"
            )

            await asyncio.sleep(
                delay
            )

        else:

            self.logger.info(
                "============================================================"
            )

            self.logger.info(
                "STARTING BUILTIN MINING"
            )

            self.logger.info(
                "Search plan: %s URL(s)",
                len(self.search_urls),
            )

            self.logger.info(
                "SEARCH %s/%s",
                self.current_search_index + 1,
                len(self.search_urls),
            )

            self.logger.info(
                "%s",
                self.start_url,
            )

            self.logger.info(
                "============================================================"
            )

        self.save_registry(
            resume_url=self.resume_url,
        )

        yield self.make_request(
            self.resume_url,
            referer="https://builtin.com/jobs",
        )

    # =================================================================
    # REQUEST FACTORY
    # =================================================================

    def make_request(
        self,
        url,
        *,
        rate_limit_retry=0,
        referer=None,
    ):

        headers = {}

        if referer:

            headers["Referer"] = (
                referer
            )

        return scrapy.Request(
            url=url,
            callback=self.parse,
            errback=self.request_failed,
            headers=headers,
            meta={
                "handle_httpstatus_all": True,
                "rate_limit_retry": (
                    rate_limit_retry
                ),
            },
            dont_filter=True,
        )

    # =================================================================
    # MAIN PARSER
    # =================================================================

    async def parse(
        self,
        response,
    ):

        page_number = (
            self.current_page + 1
        )

        # -------------------------------------------------------------
        # 429 — EXPLICIT RATE LIMIT
        # -------------------------------------------------------------

        if response.status == 429:

            self.save_registry(
                resume_url=response.url,
            )

            retries = int(
                response.meta.get(
                    "rate_limit_retry",
                    0,
                )
            )

            if (
                retries
                >= MAX_RATE_LIMIT_RETRIES
            ):

                self.logger.error(
                    "RATE LIMIT REPEATED | "
                    "Checkpoint saved. Stopping."
                )

                raise CloseSpider(
                    "rate_limit_checkpoint_saved"
                )

            delay = (
                self.get_rate_limit_delay(
                    response
                )
            )

            self.logger.warning(
                "HTTP 429 | Backing off for %.1f minutes.",
                delay / 60,
            )

            await asyncio.sleep(
                delay
            )

            yield self.make_request(
                response.url,
                rate_limit_retry=(
                    retries + 1
                ),
                referer=response.request.headers.get(
                    b"Referer",
                    b"https://builtin.com/jobs",
                ).decode(
                    "utf-8",
                    errors="ignore",
                ),
            )

            return

        # -------------------------------------------------------------
        # ACCESS DENIED
        # -------------------------------------------------------------

        if response.status in {
            401,
            403,
        }:

            self.save_registry(
                resume_url=response.url,
            )

            self.logger.error(
                "ACCESS DENIED | HTTP %s | "
                "Checkpoint saved at %s",
                response.status,
                response.url,
            )

            raise CloseSpider(
                "access_denied_checkpoint_saved"
            )

        # -------------------------------------------------------------
        # OTHER HTTP FAILURE
        # -------------------------------------------------------------

        if not (
            200
            <= response.status
            < 400
        ):

            self.save_registry(
                resume_url=response.url,
            )

            self.logger.error(
                "ACCESS FAILED | HTTP %s | %s",
                response.status,
                response.url,
            )

            raise CloseSpider(
                "http_failure_checkpoint_saved"
            )

        # -------------------------------------------------------------
        # CHALLENGE / SECURITY PAGE
        # -------------------------------------------------------------

        if self.looks_like_challenge(
            response
        ):

            self.save_registry(
                resume_url=response.url,
            )

            self.logger.error(
                "Possible security/challenge page detected. "
                "Checkpoint saved; stopping."
            )

            raise CloseSpider(
                "challenge_checkpoint_saved"
            )

        self.logger.info(
            "PAGE %s | ACCESS_OK | %s",
            page_number,
            response.url,
        )

        # -------------------------------------------------------------
        # FIND BUILTIN JOB CARDS
        # -------------------------------------------------------------

        job_cards = response.css(
            'div[data-id="job-card"]'
        )

        self.logger.info(
            "PAGE %s | Found %s listings",
            page_number,
            len(job_cards),
        )

        # -------------------------------------------------------------
        # PROCESS JOB CARDS
        # -------------------------------------------------------------

        for card in job_cards:

            self.listings_seen_total += 1

            # =========================================================
            # ID
            # =========================================================

            raw_site_id = (
                card.attrib.get(
                    "id",
                    "",
                )
            )

            raw_site_id = (
                raw_site_id.removeprefix(
                    "job-card-"
                )
                if raw_site_id
                else ""
            )

            # =========================================================
            # URL
            # =========================================================

            relative_url = card.css(
                '[data-id="job-card-title"]::attr(href)'
            ).get()

            job_url = ""

            if relative_url:

                job_url = (
                    self.canonicalize_job_url(
                        response.urljoin(
                            relative_url
                        )
                    )
                )

            # =========================================================
            # DUPLICATE CHECK
            # =========================================================

            already_known = (
                job_url
                and job_url
                in self.known_urls
            )

            if already_known:

                self.duplicate_jobs += 1

            else:

                job_id = self.allocate_blt_id(
                    job_url or raw_site_id
                )

                # -----------------------------------------------------
                # TITLE
                # -----------------------------------------------------

                job_title = (
                    self.clean_text(
                        card.css(
                            '[data-id="job-card-title"]::text'
                        ).getall()
                    )
                )

                # -----------------------------------------------------
                # COMPANY
                # -----------------------------------------------------

                company = (
                    self.clean_text(
                        card.css(
                            '[data-id="company-title"] span::text'
                        ).getall()
                    )
                )

                # -----------------------------------------------------
                # LOCATION
                # -----------------------------------------------------

                location = (
                    self.clean_text(
                        card.xpath(
                            './/i[contains(@class, "fa-location-dot")]'
                            '/ancestor::div['
                            'contains(@class, "align-items-start")'
                            '][1]'
                            '//span['
                            'contains(@class, "font-barlow")'
                            ']/text()'
                        ).getall()
                    )
                )

                # -----------------------------------------------------
                # SALARY
                # -----------------------------------------------------

                salary = (
                    self.clean_text(
                        card.xpath(
                            './/i[contains(@class, "fa-sack-dollar")]'
                            '/ancestor::div['
                            'contains(@class, "align-items-start")'
                            '][1]'
                            '//span['
                            'contains(@class, "font-barlow")'
                            ']/text()'
                        ).getall()
                    )
                )

                # -----------------------------------------------------
                # SENIORITY
                # -----------------------------------------------------

                seniority = (
                    self.clean_text(
                        card.xpath(
                            './/i[contains(@class, "fa-trophy")]'
                            '/ancestor::div['
                            'contains(@class, "align-items-start")'
                            '][1]'
                            '//span['
                            'contains(@class, "font-barlow")'
                            ']/text()'
                        ).getall()
                    )
                )

                # -----------------------------------------------------
                # STRUCTURED BUILTIN OUTPUT
                # -----------------------------------------------------

                item = {
                    "ID": job_id,
                    "Job Title": job_title,
                    "Company": company,
                    "Location": location,
                    "Salary": salary,
                    "Seniority": seniority,
                    "URL": job_url,
                    "Mining_Date": self.current_datetime(),
                }

                # -----------------------------------------------------
                # WRITE IMMEDIATELY
                # -----------------------------------------------------

                self.append_to_csv(
                    item
                )

                if job_id:

                    self.known_ids.add(
                        job_id
                    )

                if job_url:

                    self.known_urls.add(
                        job_url
                    )

                self.new_jobs += 1

                self.logger.info(
                    "NEW JOB | %s | %s",
                    job_id,
                    job_title,
                )

                yield item

            # =========================================================
            # REGULAR REGISTRY CHECKPOINT
            # =========================================================

            if (
                self.listings_seen_total
                % REGISTRY_CHECKPOINT_EVERY
                == 0
            ):

                # Page is not yet considered completed.
                # Restarting safely revisits it, while CSV
                # deduplication prevents repeated rows.
                self.save_registry(
                    resume_url=response.url,
                )

            # =========================================================
            # LONG BREAK EVERY 500 LISTINGS
            # =========================================================

            if (
                self.listings_seen_total
                >= self.next_long_break_at
            ):

                await self.take_long_break(
                    response.url
                )

        # =================================================================
        # PAGE SUCCESSFULLY COMPLETED
        # =================================================================

        self.current_page = (
            page_number
        )

        self.total_pages += 1

        self.mark_page_visited(
            response.url
        )

        # =================================================================
        # PAGINATION
        # =================================================================

        pagination = response.css(
            "ul.pagination.d-flex.justify-content-center.my-0"
        )

        next_href = None

        if pagination:

            # Primary selector:
            # explicit semantic identifier.
            next_href = pagination.css(
                'a[aria-label="Go to Next Page"]::attr(href)'
            ).get()

            # Fallback to the exact class pattern previously identified.
            if not next_href:

                next_href = pagination.css(
                    "a.page-link.rounded."
                    "text-blue.border-primary.fw-bold"
                    "::attr(href)"
                ).get()

        # =================================================================
        # FOLLOW NEXT PAGE
        # =================================================================

        if next_href:

            next_url = (
                response.urljoin(
                    next_href
                )
            )

            next_key = (
                self.page_registry_key(
                    next_url
                )
            )

            # ---------------------------------------------------------
            # LOOP PROTECTION
            # ---------------------------------------------------------

            if (
                next_key
                in self.visited_page_keys
            ):

                self.logger.warning(
                    "Next pagination URL has already been visited. "
                    "Stopping to avoid a pagination loop."
                )

            else:

                # Save exact next destination before sleeping.
                self.save_registry(
                    resume_url=next_url,
                )

                delay = random.uniform(
                    PAGE_DELAY_MIN,
                    PAGE_DELAY_MAX,
                )

                self.logger.info(
                    "PAGE %s complete | "
                    "waiting %.1f seconds before NEXT page...",
                    page_number,
                    delay,
                )

                self.logger.info(
                    "NEXT: %s",
                    next_url,
                )

                await asyncio.sleep(
                    delay
                )

                yield self.make_request(
                    next_url,
                    referer=response.url,
                )

                return

        # =================================================================
        # FINAL PAGE
        # =================================================================

        if not pagination:

            self.logger.info(
                "No pagination element found. "
                "Search appears to be single-page."
            )

        else:

            self.logger.info(
                "Final BuiltIn pagination page reached."
            )

        # =================================================================
        # ADVANCE TO THE NEXT SEARCH
        # =================================================================

        next_search_index = self.current_search_index + 1

        if next_search_index < len(self.search_urls):

            self.current_search_index = next_search_index
            self.start_url = self.search_urls[
                self.current_search_index
            ]

            # These values describe progress inside the active search.
            # Global job/listing/page totals continue accumulating.
            self.current_page = 0
            self.visited_urls = []
            self.visited_page_keys = set()
            self.resume_url = self.start_url

            self.save_registry(
                resume_url=self.resume_url,
                completed=False,
            )

            delay = random.uniform(
                SEARCH_DELAY_MIN,
                SEARCH_DELAY_MAX,
            )

            self.logger.info(
                "SEARCH %s/%s COMPLETE | waiting %.1f seconds "
                "before SEARCH %s/%s...",
                self.current_search_index,
                len(self.search_urls),
                delay,
                self.current_search_index + 1,
                len(self.search_urls),
            )

            self.logger.info(
                "NEXT SEARCH: %s",
                self.start_url,
            )

            await asyncio.sleep(
                delay
            )

            yield self.make_request(
                self.start_url,
                referer=response.url,
            )

            return

        self.save_registry(
            resume_url=None,
            completed=True,
        )

        self.logger.info(
            "============================================================"
        )

        self.logger.info(
            "BUILTIN MINING COMPLETE"
        )

        self.logger.info(
            "============================================================"
        )

    # =================================================================
    # LONG BREAK
    # =================================================================

    async def take_long_break(
        self,
        current_page_url,
    ):

        delay = random.uniform(
            LONG_BREAK_MIN,
            LONG_BREAK_MAX,
        )

        # Advance threshold BEFORE sleeping so an interruption during
        # the rest period doesn't immediately repeat the same rest.
        self.next_long_break_at += (
            LONG_BREAK_EVERY
        )

        self.save_registry(
            resume_url=current_page_url,
        )

        self.logger.info(
            "============================================================"
        )

        self.logger.info(
            "%s LISTINGS EXAMINED | "
            "Taking a %.1f minute break.",
            self.listings_seen_total,
            delay / 60,
        )

        self.logger.info(
            "Progress has been checkpointed."
        )

        self.logger.info(
            "============================================================"
        )

        await asyncio.sleep(
            delay
        )

        self.save_registry(
            resume_url=current_page_url,
        )

    # =================================================================
    # 429 BACKOFF
    # =================================================================

    @staticmethod
    def get_rate_limit_delay(
        response,
    ):

        retry_after = (
            response.headers.get(
                b"Retry-After"
            )
        )

        if retry_after:

            try:

                seconds = int(
                    retry_after.decode(
                        "ascii"
                    ).strip()
                )

                return max(
                    seconds,
                    RATE_LIMIT_DELAY_MIN,
                )

            except (
                ValueError,
                UnicodeDecodeError,
            ):

                pass

        return random.uniform(
            RATE_LIMIT_DELAY_MIN,
            RATE_LIMIT_DELAY_MAX,
        )

    # =================================================================
    # CHALLENGE DETECTION
    # =================================================================

    @staticmethod
    def looks_like_challenge(
        response,
    ):

        # Only inspect a bounded portion of the response.
        text = (
            response.text[
                :30000
            ].lower()
        )

        indicators = (
            "verify you are human",
            "checking your browser",
            "attention required",
            "unusual traffic",
            "security check",
            "captcha",
        )

        return any(
            indicator in text
            for indicator in indicators
        )

    # =================================================================
    # PAGE REGISTRY KEY
    # =================================================================

    @staticmethod
    def page_registry_key(
        url,
    ):
        """
        Stable identity for a BuiltIn results page.

        Query parameters are sorted so the same logical page does not
        become a separate registry entry merely because parameter order
        changed.
        """

        parsed = urlparse(
            url
        )

        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )

        # Discard common tracking-only parameters.
        query_pairs = [
            (key, value)
            for key, value
            in query_pairs
            if not (
                key.lower().startswith(
                    "utm_"
                )
            )
        ]

        query_pairs.sort()

        stable_query = urlencode(
            query_pairs,
            doseq=True,
        )

        return urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path,
                "",
                stable_query,
                "",
            )
        )

    # =================================================================
    # CANONICAL JOB URL
    # =================================================================

    @staticmethod
    def canonicalize_job_url(
        url,
    ):

        if not url:

            return ""

        parsed = urlparse(
            url
        )

        hostname = (
            parsed.hostname or ""
        ).lower()

        if (
            hostname == "builtin.com"
            or hostname.endswith(
                ".builtin.com"
            )
        ):

            # BuiltIn job identity lives in the path.
            # Query strings / fragments are not needed.
            return urlunparse(
                (
                    "https",
                    parsed.netloc.lower(),
                    parsed.path,
                    "",
                    "",
                    "",
                )
            )

        return url

    # =================================================================
    # STABLE LOCAL ID
    # =================================================================

    def allocate_blt_id(
        self,
        source_key,
    ):

        # Built In's URL is used as the stable source whenever available.
        # The hash gives a pseudorandom-looking four-digit value while
        # producing the same starting value for the same vacancy.
        source_key = str(
            source_key or ""
        ).strip()

        if not source_key:

            source_key = (
                f"fallback:{self.listings_seen_total}:"
                f"{self.current_datetime()}"
            )

        digest = hashlib.sha256(
            source_key.encode("utf-8")
        ).hexdigest()

        starting_number = int(
            digest[:8],
            16,
        ) % 10000

        # Resolve the unlikely event that two URLs map to the same four
        # digits. There are exactly 10,000 possible BLT identifiers.
        for offset in range(10000):

            number = (
                starting_number + offset
            ) % 10000

            candidate = f"BLT_{number:04d}"

            if candidate not in self.known_ids:

                return candidate

        raise CloseSpider(
            "All BLT_0000-BLT_9999 identifiers are already in use."
        )

    # =================================================================
    # WRITE JOB IMMEDIATELY
    # =================================================================

    @staticmethod
    def append_to_csv(
        item,
    ):

        with OUTPUT_FILE.open(
            "a",
            newline="",
            encoding="utf-8",
        ) as file:

            writer = csv.DictWriter(
                file,
                fieldnames=CSV_FIELDS,
            )

            writer.writerow(
                item
            )

    # =================================================================
    # DATETIME
    # =================================================================

    @staticmethod
    def current_datetime(
    ):

        return (
            datetime.now()
            .astimezone()
            .isoformat(
                timespec="seconds"
            )
        )

    # =================================================================
    # CLEAN TEXT
    # =================================================================

    @staticmethod
    def clean_text(
        parts,
    ):

        if not parts:

            return ""

        text = " ".join(
            parts
        )

        text = text.replace(
            "\u200d",
            "",
        )

        return " ".join(
            text.split()
        )

    # =================================================================
    # REQUEST FAILURE
    # =================================================================

    def request_failed(
        self,
        failure,
    ):

        url = (
            failure.request.url
        )

        self.save_registry(
            resume_url=url,
        )

        self.logger.error(
            "REQUEST FAILED | %s | %s",
            url,
            failure.value,
        )

        self.logger.error(
            "Checkpoint saved. "
            "Run the spider again to resume."
        )

        raise CloseSpider(
            "network_failure_checkpoint_saved"
        )

    # =================================================================
    # FINAL STATUS
    # =================================================================

    def closed(
        self,
        reason,
    ):

        # A normal fully completed crawl has already set
        # completed=True in parse().
        #
        # Any abnormal stop keeps the current resume position.
        if reason != "finished":

            self.save_registry(
                resume_url=self.resume_url,
                completed=False,
            )

        self.logger.info(
            "============================================================"
        )

        self.logger.info(
            "BUILTIN MINING STOPPED | "
            "%s listings examined | "
            "%s new jobs | "
            "%s duplicates skipped | "
            "%s pages completed",
            self.listings_seen_total,
            self.new_jobs,
            self.duplicate_jobs,
            self.total_pages,
        )

        self.logger.info(
            "CSV: %s",
            OUTPUT_FILE,
        )

        self.logger.info(
            "Registry: %s",
            REGISTRY_FILE,
        )

        self.logger.info(
            "Reason: %s",
            reason,
        )

        self.logger.info(
            "============================================================"
        )
