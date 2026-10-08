# jobs_scrapy/spiders/jobs_cz_spider.py

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

DATA_DIR.mkdir(parents=True, exist_ok=True)
REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = DATA_DIR / "jobs_basic.csv"

REGISTRY_FILE = REGISTRY_DIR / "jobs_cz_registry.json"


CSV_FIELDS = [
    "ID",
    "Job Title",
    "Company",
    "Location",
    "Salary",
    "URL",
    "Mining_Date",
]


# =====================================================================
# CRAWLING TIMING
# =====================================================================

# Between pagination pages of one search.
PAGE_DELAY_MIN = 5
PAGE_DELAY_MAX = 15

# Between main search families A -> B -> C...
MAIN_URL_DELAY_MIN = 10
MAIN_URL_DELAY_MAX = 20

# Every 500 listings examined, take a substantial rest.
LONG_BREAK_EVERY = 500

# Seconds: 5–10 minutes.
LONG_BREAK_MIN = 5 * 60
LONG_BREAK_MAX = 10 * 60

# Persist crawler state during large pages too.
REGISTRY_CHECKPOINT_EVERY = 25

# If Jobs.cz explicitly rate-limits us, back off considerably.
RATE_LIMIT_DELAY_MIN = 10 * 60
RATE_LIMIT_DELAY_MAX = 20 * 60

MAX_RATE_LIMIT_RETRIES = 1


# =====================================================================
# SPIDER
# =====================================================================

class JobsCzSpider(scrapy.Spider):

    name = "jobs_cz"

    allowed_domains = [
        "jobs.cz",
        "www.jobs.cz",
    ]

    # =================================================================
    # MAIN SEARCH URLS
    # =================================================================

    SEARCH_URLS = [

        # -------------------------------------------------------------
        # A — Core Data / Data Quality
        # -------------------------------------------------------------
        (
            "A",
            "Core Data / Data Quality",
            "https://www.jobs.cz/prace/praha/"
            "?q%5B%5D=Data%20Analyst"
            "&q%5B%5D=Data%20Quality%20Analyst"
            "&q%5B%5D=Data%20Operations%20Analyst"
            "&q%5B%5D=Business%20Data%20Analyst"
            "&q%5B%5D=Data%20Specialist"
            "&q%5B%5D=Data%20Management"
            "&q%5B%5D=Data%20Governance"
            "&q%5B%5D=Data%20Steward"
            "&q%5B%5D=Data%20Integration%20Analyst"
            "&q%5B%5D=Data%20Migration%20Analyst"
            "&q%5B%5D=Data%20Quality%20Specialist"
            "&q%5B%5D=Data%20Coordinator"
        ),

        # -------------------------------------------------------------
        # B — Data Engineering / Automation
        # -------------------------------------------------------------
        (
            "B",
            "Data Engineering / Automation",
            "https://www.jobs.cz/prace/praha/"
            "?q%5B%5D=Junior%20Data%20Engineer"
            "&q%5B%5D=Data%20Engineer"
            "&q%5B%5D=ETL%20Developer"
            "&q%5B%5D=Data%20Pipeline"
            "&q%5B%5D=Data%20Automation"
            "&q%5B%5D=Automation%20Analyst"
            "&q%5B%5D=Python%20Developer"
            "&q%5B%5D=SQL%20Developer"
            "&q%5B%5D=Data%20Integration%20Engineer"
            "&q%5B%5D=Data%20Migration"
            "&q%5B%5D=Web%20Scraping"
            "&q%5B%5D=Data%20Extraction"
        ),

        # -------------------------------------------------------------
        # C — Master / Product / Customer Data
        # -------------------------------------------------------------
        (
            "C",
            "Master / Product / Customer Data",
            "https://www.jobs.cz/prace/praha/"
            "?q%5B%5D=Master%20Data%20Analyst"
            "&q%5B%5D=Master%20Data%20Specialist"
            "&q%5B%5D=Master%20Data%20Management"
            "&q%5B%5D=MDM%20Analyst"
            "&q%5B%5D=Product%20Data%20Analyst"
            "&q%5B%5D=Product%20Data%20Specialist"
            "&q%5B%5D=Customer%20Data%20Analyst"
            "&q%5B%5D=Customer%20Data%20Specialist"
            "&q%5B%5D=CRM%20Analyst"
            "&q%5B%5D=PIM%20Specialist"
            "&q%5B%5D=Reference%20Data"
            "&q%5B%5D=Data%20Governance%20Analyst"
        ),

        # -------------------------------------------------------------
        # D — Research / Market / Insights
        # -------------------------------------------------------------
        (
            "D",
            "Research / Market / Insights",
            "https://www.jobs.cz/prace/praha/"
            "?q%5B%5D=Research%20Analyst"
            "&q%5B%5D=Market%20Research"
            "&q%5B%5D=Research%20Executive"
            "&q%5B%5D=Insights%20Analyst"
            "&q%5B%5D=Market%20Intelligence"
            "&q%5B%5D=Marketing%20Analyst"
            "&q%5B%5D=Customer%20Insights"
            "&q%5B%5D=Consumer%20Insights"
            "&q%5B%5D=Market%20Analyst"
            "&q%5B%5D=Product%20Analyst"
            "&q%5B%5D=Customer%20Research"
            "&q%5B%5D=Data%20Research%20Analyst"
        ),

        # -------------------------------------------------------------
        # E — Business / Systems / Process
        # -------------------------------------------------------------
        (
            "E",
            "Business / Systems / Process",
            "https://www.jobs.cz/prace/praha/"
            "?q%5B%5D=Business%20Analyst"
            "&q%5B%5D=Junior%20Business%20Analyst"
            "&q%5B%5D=Systems%20Analyst"
            "&q%5B%5D=Business%20Systems%20Analyst"
            "&q%5B%5D=Process%20Analyst"
            "&q%5B%5D=Technical%20Business%20Analyst"
            "&q%5B%5D=Operations%20Analyst"
            "&q%5B%5D=Implementation%20Analyst"
            "&q%5B%5D=Solution%20Analyst"
            "&q%5B%5D=Process%20Improvement"
            "&q%5B%5D=Digitalization"
            "&q%5B%5D=Automation%20Specialist"
        ),

        # -------------------------------------------------------------
        # F — ML / Data Science
        # -------------------------------------------------------------
        (
            "F",
            "ML / Data Science",
            "https://www.jobs.cz/prace/praha/"
            "?q%5B%5D=Data%20Scientist"
            "&q%5B%5D=Junior%20Data%20Scientist"
            "&q%5B%5D=Machine%20Learning"
            "&q%5B%5D=Machine%20Learning%20Engineer"
            "&q%5B%5D=ML%20Engineer"
            "&q%5B%5D=AI%20Engineer"
            "&q%5B%5D=AI%20Specialist"
            "&q%5B%5D=Applied%20AI"
            "&q%5B%5D=Predictive%20Analytics"
            "&q%5B%5D=Statistical%20Analyst"
            "&q%5B%5D=Modeling%20Analyst"
            "&q%5B%5D=Data%20Mining"
        ),

        # -------------------------------------------------------------
        # G — Entity Resolution / Matching / Cleaning
        # -------------------------------------------------------------
        (
            "G",
            "Entity Resolution / Matching / Cleaning",
            "https://www.jobs.cz/prace/praha/"
            "?q%5B%5D=Entity%20Resolution"
            "&q%5B%5D=Record%20Linkage"
            "&q%5B%5D=Data%20Matching"
            "&q%5B%5D=Deduplication"
            "&q%5B%5D=Data%20Validation"
            "&q%5B%5D=Data%20Enrichment"
            "&q%5B%5D=Data%20Reconciliation"
            "&q%5B%5D=Data%20Cleaning"
            "&q%5B%5D=Data%20Quality"
            "&q%5B%5D=Customer%20Matching"
            "&q%5B%5D=Product%20Matching"
            "&q%5B%5D=Record%20Matching"
        ),

        # -------------------------------------------------------------
        # H — Junior / Adjacent Data
        # -------------------------------------------------------------
        (
            "H",
            "Junior / Adjacent Data",
            "https://www.jobs.cz/prace/praha/"
            "?q%5B%5D=Junior%20Analyst"
            "&q%5B%5D=Graduate%20Analyst"
            "&q%5B%5D=Junior%20Data%20Analyst"
            "&q%5B%5D=Data%20Administrator"
            "&q%5B%5D=Data%20Support"
            "&q%5B%5D=Data%20Operations"
            "&q%5B%5D=Information%20Analyst"
            "&q%5B%5D=Operations%20Data%20Analyst"
            "&q%5B%5D=Data%20Management%20Analyst"
            "&q%5B%5D=Data%20Associate"
            "&q%5B%5D=Data%20Processing"
            "&q%5B%5D=Data%20Onboarding"
        ),
    ]

    # =================================================================
    # SCRAPY SETTINGS
    # =================================================================

    custom_settings = {

        "USER_AGENT": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/140.0.0.0 Safari/537.36"
        ),

        "DEFAULT_REQUEST_HEADERS": {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
        },

        "COOKIES_ENABLED": True,
        "REFERER_ENABLED": True,

        # Never parallelize Jobs.cz requests.
        "CONCURRENT_REQUESTS": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,

        # Small baseline delay still applies to automatic requests
        # such as robots.txt and retry middleware.
        "DOWNLOAD_DELAY": 1.0,
        "DOWNLOAD_DELAY_JITTER": 0.5,

        # Server-latency-aware additional throttling.
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 2.0,
        "AUTOTHROTTLE_MAX_DELAY": 60.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5,

        "DOWNLOAD_TIMEOUT": 30,

        "RETRY_ENABLED": True,
        "RETRY_TIMES": 2,

        # 429 is deliberately excluded here because this spider
        # handles it with a much longer explicit backoff.
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

    def __init__(self, url=None, *args, **kwargs):

        super().__init__(*args, **kwargs)

        # -------------------------------------------------------------
        # CUSTOM SINGLE-URL MODE OR NORMAL A-H MODE
        # -------------------------------------------------------------

        if url:

            self.validate_jobs_url(url)

            self.search_urls = [
                (
                    "CUSTOM",
                    "Custom Jobs.cz Search",
                    url,
                )
            ]

        else:

            self.search_urls = self.SEARCH_URLS

        # Historical jobs database.
        self.known_urls = set()
        self.generated_ids = set()

        self.prepare_existing_csv()

        # -------------------------------------------------------------
        # RUN / RESUME STATE
        # -------------------------------------------------------------

        self.current_search_index = 0

        # Number of COMPLETED pages in the current search.
        self.current_search_page = 0

        self.total_pages = 0
        self.completed_searches = 0

        self.new_jobs = 0
        self.duplicate_jobs = 0

        self.listings_seen_total = 0
        self.next_long_break_at = LONG_BREAK_EVERY

        self.visited_urls = []
        self.visited_page_keys = set()

        self.resume_url = self.search_urls[0][2]

        self.resuming = False

        self.plan_signature = self.make_plan_signature()

        self.load_or_create_registry()

    # =================================================================
    # SEARCH PLAN SIGNATURE
    # =================================================================

    def make_plan_signature(self):

        raw = "\n".join(
            url
            for _, _, url in self.search_urls
        )

        return hashlib.sha256(
            raw.encode("utf-8")
        ).hexdigest()

    # =================================================================
    # URL VALIDATION
    # =================================================================

    @staticmethod
    def validate_jobs_url(url):

        parsed = urlparse(url)

        hostname = (
            parsed.hostname or ""
        ).lower()

        if parsed.scheme not in {"http", "https"}:

            raise CloseSpider(
                "URL must begin with http:// or https://"
            )

        if (
            hostname != "jobs.cz"
            and not hostname.endswith(".jobs.cz")
        ):

            raise CloseSpider(
                "This spider only accepts jobs.cz URLs."
            )

    # =================================================================
    # CSV PREPARATION
    # =================================================================

    def prepare_existing_csv(self):

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

        with OUTPUT_FILE.open(
            "r",
            newline="",
            encoding="utf-8-sig",
        ) as file:

            reader = csv.DictReader(file)

            old_fields = reader.fieldnames or []
            rows = list(reader)

        for row in rows:

            existing_id = (
                row.get("ID") or ""
            ).strip()

            if existing_id:

                self.generated_ids.add(
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
            "Loaded %s existing job URLs from %s",
            len(self.known_urls),
            OUTPUT_FILE,
        )

        # Upgrade older CSVs automatically.
        if "Mining_Date" not in old_fields:

            temp_file = OUTPUT_FILE.with_name(
                "jobs_basic_temp.csv"
            )

            with temp_file.open(
                "w",
                newline="",
                encoding="utf-8-sig",
            ) as file:

                writer = csv.DictWriter(
                    file,
                    fieldnames=CSV_FIELDS,
                )

                writer.writeheader()

                for row in rows:

                    upgraded = {
                        field: row.get(field, "")
                        for field in CSV_FIELDS
                    }

                    writer.writerow(upgraded)

            temp_file.replace(
                OUTPUT_FILE
            )

            self.logger.info(
                "Added Mining_Date column to existing CSV."
            )

    # =================================================================
    # REGISTRY
    # =================================================================

    def load_or_create_registry(self):

        if REGISTRY_FILE.exists():

            try:

                with REGISTRY_FILE.open(
                    "r",
                    encoding="utf-8",
                ) as file:

                    state = json.load(file)

                same_plan = (
                    state.get("plan_signature")
                    == self.plan_signature
                )

                unfinished = not state.get(
                    "completed",
                    False,
                )

                if same_plan and unfinished:

                    self.current_search_index = int(
                        state.get(
                            "current_search_index",
                            0,
                        )
                    )

                    self.current_search_page = int(
                        state.get(
                            "current_search_page",
                            0,
                        )
                    )

                    self.total_pages = int(
                        state.get(
                            "total_pages",
                            0,
                        )
                    )

                    self.completed_searches = int(
                        state.get(
                            "completed_searches",
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
                        self.page_registry_key(url)
                        for url in self.visited_urls
                    }

                    self.resume_url = (
                        state.get("resume_url")
                        or self.search_urls[
                            self.current_search_index
                        ][2]
                    )

                    self.resuming = True

                    self.logger.info(
                        "RESUME STATE FOUND | "
                        "search=%s | completed pages=%s | "
                        "resume=%s",
                        self.current_search_index + 1,
                        self.current_search_page,
                        self.resume_url,
                    )

                    return

            except Exception as exc:

                self.logger.warning(
                    "Could not load registry: %s. "
                    "Starting a fresh run.",
                    exc,
                )

        # No valid resumable run exists.
        self.reset_registry()

    def reset_registry(self):

        self.current_search_index = 0
        self.current_search_page = 0

        self.total_pages = 0
        self.completed_searches = 0

        self.new_jobs = 0
        self.duplicate_jobs = 0

        self.listings_seen_total = 0
        self.next_long_break_at = LONG_BREAK_EVERY

        self.visited_urls = []
        self.visited_page_keys = set()

        self.resume_url = (
            self.search_urls[0][2]
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
            self.resume_url = resume_url

        state = {
            "version": 1,
            "plan_signature": self.plan_signature,
            "completed": completed,
            "updated_at": self.current_mining_datetime(),

            "current_search_index": self.current_search_index,
            "current_search_page": self.current_search_page,

            "total_pages": self.total_pages,
            "completed_searches": self.completed_searches,

            "listings_seen_total": self.listings_seen_total,
            "next_long_break_at": self.next_long_break_at,

            "new_jobs": self.new_jobs,
            "duplicate_jobs": self.duplicate_jobs,

            "resume_url": (
                None if completed else self.resume_url
            ),

            "visited_urls": self.visited_urls,
        }

        temp_file = REGISTRY_FILE.with_suffix(
            ".tmp"
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

        # Atomic replacement greatly reduces the chance of leaving a
        # corrupt registry if the process dies during the write.
        temp_file.replace(
            REGISTRY_FILE
        )

    def mark_page_visited(self, url):

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

    async def start(self):

        label, description, _ = (
            self.search_urls[
                self.current_search_index
            ]
        )

        if self.resuming:

            delay = random.uniform(
                MAIN_URL_DELAY_MIN,
                MAIN_URL_DELAY_MAX,
            )

            self.logger.info(
                "RESUMING PREVIOUS RUN | "
                "SEARCH %s | %s",
                label,
                description,
            )

            self.logger.info(
                "Resume URL: %s",
                self.resume_url,
            )

            self.logger.info(
                "Waiting %.1f seconds before resuming...",
                delay,
            )

            await asyncio.sleep(
                delay
            )

        else:

            self.logger.info(
                "============================================================"
            )

            self.logger.info(
                "STARTING SEARCH 1/%s | %s | %s",
                len(self.search_urls),
                label,
                description,
            )

            self.logger.info(
                "%s",
                self.resume_url,
            )

            self.logger.info(
                "============================================================"
            )

        self.save_registry(
            resume_url=self.resume_url,
        )

        yield self.make_request(
            self.resume_url
        )

    # =================================================================
    # REQUEST FACTORY
    # =================================================================

    def make_request(
        self,
        url,
        *,
        rate_limit_retry=0,
    ):

        return scrapy.Request(
            url=url,
            callback=self.parse,
            errback=self.request_failed,
            meta={
                "handle_httpstatus_all": True,
                "rate_limit_retry": rate_limit_retry,
            },
            dont_filter=True,
        )

    # =================================================================
    # MAIN PARSER
    # =================================================================

    async def parse(self, response):

        label, description, _ = (
            self.search_urls[
                self.current_search_index
            ]
        )

        page_number = (
            self.current_search_page + 1
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

            if retries >= MAX_RATE_LIMIT_RETRIES:

                self.logger.error(
                    "RATE LIMIT REPEATED | "
                    "Checkpoint saved. Stopping."
                )

                raise CloseSpider(
                    "rate_limit_checkpoint_saved"
                )

            delay = self.get_rate_limit_delay(
                response
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
                rate_limit_retry=retries + 1,
            )

            return

        # -------------------------------------------------------------
        # ACCESS DENIED
        # -------------------------------------------------------------

        if response.status in {401, 403}:

            self.save_registry(
                resume_url=response.url,
            )

            self.logger.error(
                "ACCESS DENIED | HTTP %s | "
                "Checkpoint saved at %s",
                response.status,
                response.url,
            )

            # Do not repeatedly challenge an access wall.
            raise CloseSpider(
                "access_denied_checkpoint_saved"
            )

        # -------------------------------------------------------------
        # OTHER FAILURE
        # -------------------------------------------------------------

        if not 200 <= response.status < 400:

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
        # DETECT CHALLENGE PAGE RETURNED AS HTTP 200
        # -------------------------------------------------------------

        if self.looks_like_challenge(
            response
        ):

            self.save_registry(
                resume_url=response.url,
            )

            self.logger.error(
                "Possible anti-automation/security challenge detected. "
                "Checkpoint saved; stopping rather than retrying."
            )

            raise CloseSpider(
                "challenge_checkpoint_saved"
            )

        self.logger.info(
            "SEARCH %s | PAGE %s | ACCESS_OK | %s",
            label,
            page_number,
            response.url,
        )

        # -------------------------------------------------------------
        # FIND LISTINGS
        # -------------------------------------------------------------

        cards = response.css(
            "article.SearchResultCard"
        )

        self.logger.info(
            "SEARCH %s | PAGE %s | Found %s listings",
            label,
            page_number,
            len(cards),
        )

        # -------------------------------------------------------------
        # PROCESS LISTINGS
        # -------------------------------------------------------------

        for card in cards:

            # Count every listing inspected, including one which later
            # proves to be already known.
            self.listings_seen_total += 1

            relative_url = card.css(
                ".SearchResultCard__titleLink::attr(href)"
            ).get()

            if relative_url:

                raw_job_url = response.urljoin(
                    relative_url
                )

                job_url = self.canonicalize_job_url(
                    raw_job_url
                )

                # -----------------------------------------------------
                # EXISTING LISTING
                # -----------------------------------------------------

                if job_url in self.known_urls:

                    self.duplicate_jobs += 1

                # -----------------------------------------------------
                # NEW LISTING
                # -----------------------------------------------------

                else:

                    self.known_urls.add(
                        job_url
                    )

                    title = self.clean_text(
                        card.css(
                            ".SearchResultCard__titleLink::text"
                        ).getall()
                    )

                    company = self.clean_text(
                        card.css(
                            '.SearchResultCard__footerItem '
                            'span[translate="no"]::text'
                        ).getall()
                    )

                    location = self.clean_text(
                        card.css(
                            '[data-test="serp-locality"]::text'
                        ).getall()
                    )

                    salary = self.clean_text(
                        card.css(
                            ".SearchResultCard__body "
                            ".Tag--success::text"
                        ).getall()
                    )

                    item = {
                        "ID": self.generate_job_id(),
                        "Job Title": title,
                        "Company": company,
                        "Location": location,
                        "Salary": salary,
                        "URL": job_url,
                        "Mining_Date": (
                            self.current_mining_datetime()
                        ),
                    }

                    # The row is physically written immediately.
                    # A crash later in the run therefore does not lose
                    # this listing.
                    self.append_to_csv(
                        item
                    )

                    self.new_jobs += 1

                    self.logger.info(
                        "NEW JOB | %s | %s",
                        item["ID"],
                        title,
                    )

                    yield item

            # ---------------------------------------------------------
            # REGULAR STATE CHECKPOINT
            # ---------------------------------------------------------

            if (
                self.listings_seen_total
                % REGISTRY_CHECKPOINT_EVERY
                == 0
            ):

                # Current page has not yet been declared complete.
                # A restart will safely revisit it, and CSV URL
                # deduplication prevents repeated rows.
                self.save_registry(
                    resume_url=response.url,
                )

            # ---------------------------------------------------------
            # 500-LISTING LONG BREAK
            # ---------------------------------------------------------

            if (
                self.listings_seen_total
                >= self.next_long_break_at
            ):

                await self.take_long_break(
                    response.url
                )

        # -------------------------------------------------------------
        # PAGE SUCCESSFULLY COMPLETED
        # -------------------------------------------------------------

        self.current_search_page = (
            page_number
        )

        self.total_pages += 1

        self.mark_page_visited(
            response.url
        )

        # -------------------------------------------------------------
        # PAGINATION
        # -------------------------------------------------------------

        pagination = response.css(
            "ul.Pagination"
        )

        next_href = None

        if pagination:

            next_href = pagination.css(
                "a.Pagination__button--next::attr(href)"
            ).get()

        # -------------------------------------------------------------
        # NEXT RESULT PAGE
        # -------------------------------------------------------------

        if next_href:

            next_url = response.urljoin(
                next_href
            )

            next_key = self.page_registry_key(
                next_url
            )

            # Defensive loop protection.
            if next_key in self.visited_page_keys:

                self.logger.warning(
                    "Next pagination URL has already been visited. "
                    "Stopping this search to avoid a loop."
                )

            else:

                # Save the exact next position BEFORE waiting/requesting.
                self.save_registry(
                    resume_url=next_url,
                )

                delay = random.uniform(
                    PAGE_DELAY_MIN,
                    PAGE_DELAY_MAX,
                )

                self.logger.info(
                    "PAGE %s complete | "
                    "waiting %.1f seconds before next page...",
                    page_number,
                    delay,
                )

                await asyncio.sleep(
                    delay
                )

                yield self.make_request(
                    next_url
                )

                return

        # -------------------------------------------------------------
        # SEARCH COMPLETE
        # -------------------------------------------------------------

        if not pagination:

            self.logger.info(
                "SEARCH %s | No pagination. "
                "Single-page search complete.",
                label,
            )

        else:

            self.logger.info(
                "SEARCH %s | Final pagination page reached.",
                label,
            )

        self.completed_searches += 1

        next_request = (
            await self.move_to_next_main_search()
        )

        if next_request:

            yield next_request

    # =================================================================
    # 500-LISTING BREAK
    # =================================================================

    async def take_long_break(
        self,
        current_page_url,
    ):

        delay = random.uniform(
            LONG_BREAK_MIN,
            LONG_BREAK_MAX,
        )

        # Move threshold forward BEFORE sleeping. If the process is
        # killed during the break, resumption does not repeat the same
        # break immediately.
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
    # NEXT MAIN SEARCH
    # =================================================================

    async def move_to_next_main_search(self):

        next_index = (
            self.current_search_index + 1
        )

        # -------------------------------------------------------------
        # EVERYTHING FINISHED
        # -------------------------------------------------------------

        if next_index >= len(
            self.search_urls
        ):

            self.save_registry(
                resume_url=None,
                completed=True,
            )

            self.logger.info(
                "============================================================"
            )

            self.logger.info(
                "ALL SEARCHES COMPLETE"
            )

            self.logger.info(
                "============================================================"
            )

            return None

        next_label, next_description, next_url = (
            self.search_urls[
                next_index
            ]
        )

        # Change state BEFORE sleeping. If the program dies during the
        # wait, it knows that the next action is the next main search.
        self.current_search_index = (
            next_index
        )

        self.current_search_page = 0

        self.save_registry(
            resume_url=next_url,
        )

        delay = random.uniform(
            MAIN_URL_DELAY_MIN,
            MAIN_URL_DELAY_MAX,
        )

        self.logger.info(
            "Waiting %.1f seconds before main search %s...",
            delay,
            next_label,
        )

        await asyncio.sleep(
            delay
        )

        self.logger.info(
            "============================================================"
        )

        self.logger.info(
            "STARTING SEARCH %s/%s | %s | %s",
            self.current_search_index + 1,
            len(self.search_urls),
            next_label,
            next_description,
        )

        self.logger.info(
            "%s",
            next_url,
        )

        self.logger.info(
            "============================================================"
        )

        return self.make_request(
            next_url
        )

    # =================================================================
    # 429 BACKOFF
    # =================================================================

    @staticmethod
    def get_rate_limit_delay(
        response,
    ):

        retry_after = response.headers.get(
            b"Retry-After"
        )

        if retry_after:

            try:

                seconds = int(
                    retry_after.decode(
                        "ascii"
                    ).strip()
                )

                # Respect at least the server-requested period.
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

        # Only inspect a limited portion of the document.
        text = response.text[:30000].lower()

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
    # SEARCH-PAGE REGISTRY KEY
    # =================================================================

    @staticmethod
    def page_registry_key(url):
        """
        Produce a stable page identity while discarding transient
        Jobs.cz tracking parameters.

        The actual URL remains in visited_urls for inspection.
        """

        parsed = urlparse(
            url
        )

        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
        )

        query_pairs = [
            (key, value)
            for key, value in query_pairs
            if key not in {
                "searchId",
                "rps",
            }
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
    def canonicalize_job_url(url):

        if not url:
            return ""

        parsed = urlparse(
            url
        )

        hostname = (
            parsed.hostname or ""
        ).lower()

        if (
            hostname == "jobs.cz"
            or hostname.endswith(".jobs.cz")
        ):

            return urlunparse(
                (
                    parsed.scheme or "https",
                    parsed.netloc.lower(),
                    parsed.path,
                    "",
                    "",
                    "",
                )
            )

        return url

    # =================================================================
    # WRITE JOB IMMEDIATELY
    # =================================================================

    @staticmethod
    def append_to_csv(item):

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
    # UNIQUE ID
    # =================================================================

    def generate_job_id(self):

        if len(self.generated_ids) >= 10000:

            raise CloseSpider(
                "All possible JB_XXXX IDs have been exhausted."
            )

        while True:

            job_id = (
                f"JB_{random.randint(0, 9999):04d}"
            )

            if job_id not in self.generated_ids:

                self.generated_ids.add(
                    job_id
                )

                return job_id

    # =================================================================
    # MINING DATETIME
    # =================================================================

    @staticmethod
    def current_mining_datetime():

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
    def clean_text(parts):

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

        url = failure.request.url

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

        # Unless completion was explicitly registered above, preserve
        # the current resume position.
        if reason != "finished":

            self.save_registry(
                resume_url=self.resume_url,
                completed=False,
            )

        self.logger.info(
            "============================================================"
        )

        self.logger.info(
            "MINING STOPPED | "
            "%s listings examined | "
            "%s new jobs | "
            "%s duplicates skipped | "
            "%s pages completed | "
            "%s/%s searches completed",
            self.listings_seen_total,
            self.new_jobs,
            self.duplicate_jobs,
            self.total_pages,
            self.completed_searches,
            len(self.search_urls),
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