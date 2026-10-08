"""Browser-backed, human-paced spider for recent Indeed Prague vacancies.

The spider mines search-result cards only. It does not open individual job
detail pages. Every job is normalized to Indeed's stable ``viewjob?jk=...``
URL, written immediately to ``indeed_basic.csv``, and deduplicated across all
search terms and later runs.

Run from the Scrapy project root:

    scrapy crawl indeed

Optional single-search mode:

    scrapy crawl indeed -a url="https://cz.indeed.com/jobs?q=data+analyst&l=Praha&fromage=14"

Required once in the active Python environment:

    pip install scrapy-playwright
    playwright install chromium
"""

from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse
import asyncio
import csv
import hashlib
import json
import random
import re

import scrapy
from scrapy.exceptions import CloseSpider
from scrapy_playwright.page import PageMethod


# =====================================================================
# PATHS AND OUTPUT SCHEMA
# =====================================================================

DATA_DIR = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\jobs_scrapy\jobs_scrapy\data"
)
REGISTRY_DIR = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\jobs_scrapy\registry"
)

DATA_DIR.mkdir(parents=True, exist_ok=True)
REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = DATA_DIR / "indeed_basic.csv"
REGISTRY_FILE = REGISTRY_DIR / "indeed_registry.json"
DIAGNOSTICS_DIR = REGISTRY_DIR / "indeed_diagnostics"
DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)

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
# SEARCH PLAN
# =====================================================================

INDEED_LOCATION = "hlavní město praha"
POSTING_AGE_DAYS = 14


def build_search_url(query):
    """Build a stable Indeed search URL without a transient opened-job key."""

    return "https://cz.indeed.com/jobs?" + urlencode(
        {
            "q": query,
            "l": INDEED_LOCATION,
            "fromage": str(POSTING_AGE_DAYS),
        }
    )


# The four user-supplied searches are retained (A1, E1, B6, B7). The other
# terms provide higher recall across the same role families as jobs_cz.
SEARCH_TERMS = [
    # A — Core data, quality, and governance
    ("A1", "Core Data", "data analyst"),
    ("A2", "Core Data", "junior data analyst"),
    ("A3", "Core Data", "data specialist"),
    ("A4", "Core Data", "data quality"),
    ("A5", "Core Data", "data governance"),
    ("A6", "Core Data", "data steward"),
    ("A7", "Core Data", "data operations"),

    # B — Engineering, BI, integration, and automation
    ("B1", "Data Engineering", "data engineer"),
    ("B2", "Data Engineering", "junior data engineer"),
    ("B3", "Data Engineering", "analytics engineer"),
    ("B4", "Data Engineering", "data integration developer"),
    ("B5", "Data Engineering", "ETL developer"),
    ("B6", "Data Engineering", "python developer"),
    ("B7", "Data Engineering", "sql developer"),
    ("B8", "Business Intelligence", "BI analyst"),
    ("B9", "Business Intelligence", "Power BI analyst"),

    # C — Master, product, and customer data
    ("C1", "Master Data", "master data analyst"),
    ("C2", "Master Data", "MDM analyst"),
    ("C3", "Customer Data", "CRM analyst"),

    # D — Research, market, and product insights
    ("D1", "Research", "research analyst"),
    ("D2", "Research", "market research analyst"),
    ("D3", "Insights", "insights analyst"),
    ("D4", "Marketing Analytics", "marketing analyst"),
    ("D5", "Product Analytics", "product analyst"),

    # E — Business, systems, process, and operations
    ("E1", "Business Analysis", "business analyst"),
    ("E2", "Business Analysis", "junior business analyst"),
    ("E3", "Systems Analysis", "systems analyst"),
    ("E4", "Process Analysis", "process analyst"),
    ("E5", "Operations Analysis", "operations analyst"),
    ("E6", "Automation", "automation analyst"),

    # F — Data science, machine learning, and AI
    ("F1", "Data Science", "data scientist"),
    ("F2", "Data Science", "junior data scientist"),
    ("F3", "Machine Learning", "machine learning engineer"),
    ("F4", "Artificial Intelligence", "AI engineer"),

    # H — Junior and graduate discovery
    ("H1", "Junior Discovery", "junior analyst"),
    ("H2", "Graduate Discovery", "graduate analyst"),
]

SEARCH_URLS = [
    (label, f"{family} | {query}", build_search_url(query))
    for label, family, query in SEARCH_TERMS
]


# =====================================================================
# PACING, CHECKPOINTS, AND SAFETY LIMITS
# =====================================================================

# Small pause after Chromium reaches the page so the result-card client code
# can finish rendering before the DOM is inspected.
INITIAL_RENDER_DELAY_MIN = 3.0
INITIAL_RENDER_DELAY_MAX = 6.0
INITIAL_RESULTS_TIMEOUT_MS = 20_000

PAGE_DELAY_MIN = 8.0
PAGE_DELAY_MAX = 18.0

# Indeed accepted the first browser search but rejected a new search opened
# roughly 30 seconds later. Keep a substantially wider interval between
# independent keyword searches.
MAIN_URL_DELAY_MIN = 45.0
MAIN_URL_DELAY_MAX = 90.0

LONG_BREAK_EVERY = 500
LONG_BREAK_MIN = 5 * 60
LONG_BREAK_MAX = 10 * 60

REGISTRY_CHECKPOINT_EVERY = 20

RATE_LIMIT_DELAY_MIN = 15 * 60
RATE_LIMIT_DELAY_MAX = 30 * 60
MAX_RATE_LIMIT_RETRIES = 1

# A first HTTP 403 receives one long cooldown. A repeated 403 still stops the
# crawl with the current search safely checkpointed.
ACCESS_DENIED_DELAY_MIN = 5 * 60
ACCESS_DENIED_DELAY_MAX = 10 * 60
MAX_ACCESS_DENIED_RETRIES = 1

# When Cloudflare visibly asks for human confirmation, keep the headed browser
# open so the user can complete it. The spider never clicks or solves the
# verification control itself.
MANUAL_VERIFICATION_TIMEOUT_SECONDS = 5 * 60
MANUAL_VERIFICATION_POLL_MS = 1_000
MANUAL_VERIFICATION_STATUS_EVERY_SECONDS = 30

MAX_PAGES_PER_SEARCH = 100

# The live run on 2026-09-12 established that Indeed allows the initial
# search URL but its robots.txt blocks the ``start=10`` pagination URL.
# Keep pagination code available for a future policy change, but do not
# schedule those prohibited URLs now. Breadth comes from the 36 independent
# permitted first-page searches instead.
FOLLOW_PAGINATION = False


# =====================================================================
# STABLE DOM SELECTORS
# =====================================================================


def has_class(class_name):
    return (
        "contains(concat(' ', normalize-space(@class), ' '), "
        f"' {class_name} ')"
    )


# Indeed also places non-visible data-jk links in the document. Restrict cards
# to a visible title link and explicitly reject tabindex=-1 trap/ghost links.
VISIBLE_TITLE_LINK_XPATH = (
    ".//a["
    f"{has_class('jcs-JobTitle')} and "
    "@data-jk and "
    "not(@tabindex='-1') and "
    "not(@aria-hidden='true')"
    "][1]"
)

JOB_CARD_XPATH = (
    "//div["
    f"{has_class('job_seen_beacon')} and "
    f"{VISIBLE_TITLE_LINK_XPATH}"
    "]"
)

COMPANY_XPATH = ".//*[@data-testid='company-name'][1]//text()"
LOCATION_XPATH = ".//*[@data-testid='text-location'][1]//text()"
SALARY_XPATH = (
    ".//*["
    f"{has_class('salary-snippet-container')}"
    "]//text()[not(ancestor::style) and not(ancestor::script)]"
)
NEXT_PAGE_XPATH = "//a[@data-testid='pagination-page-next'][@href]/@href"


# =====================================================================
# SPIDER
# =====================================================================


class IndeedSpider(scrapy.Spider):

    name = "indeed"

    allowed_domains = [
        "indeed.com",
        "cz.indeed.com",
    ]

    SEARCH_URLS = SEARCH_URLS

    custom_settings = {
        # Let Chromium provide its own internally consistent versioned user
        # agent and client-hint headers. A hard-coded Chrome version can drift
        # away from the actual Playwright browser binary.
        "USER_AGENT": None,
        # Scrapy 2.19's robots middleware requires a separate non-null token
        # when USER_AGENT is intentionally left to the browser.
        "ROBOTSTXT_USER_AGENT": "Mozilla/5.0",
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "cs-CZ,cs;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "max-age=0",
            "Upgrade-Insecure-Requests": "1",
        },
        "COOKIES_ENABLED": True,
        "REFERER_ENABLED": True,
        "CONCURRENT_REQUESTS": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 2.0,
        "DOWNLOAD_DELAY_JITTER": 0.5,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 3.0,
        "AUTOTHROTTLE_MAX_DELAY": 90.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.35,
        "DOWNLOAD_TIMEOUT": 45,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 2,
        # 429 is handled by the longer explicit backoff in parse().
        "RETRY_HTTP_CODES": [408, 500, 502, 503, 504, 522, 524],
        "ROBOTSTXT_OBEY": True,
        "LOG_LEVEL": "INFO",

        # Indeed rejects the plain Scrapy search request with HTTP 403 even
        # though its robots.txt request succeeds. Search pages therefore use
        # a real Chromium context. Only the HTTPS handler is replaced, which
        # prevents scrapy-playwright from launching duplicate browsers.
        "TWISTED_REACTOR": (
            "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
        ),
        "DOWNLOAD_HANDLERS": {
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_PROCESS_REQUEST_HEADERS": None,
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 60_000,
        "PLAYWRIGHT_MAX_CONTEXTS": 1,
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": 1,
        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            # A visible browser is intentional for Indeed. Its plain HTTP
            # endpoint rejected the otherwise valid desktop request.
            "headless": False,
        },
        "PLAYWRIGHT_CONTEXTS": {
            "indeed": {
                "viewport": {"width": 1366, "height": 768},
                "screen": {"width": 1366, "height": 768},
                "locale": "cs-CZ",
                "timezone_id": "Europe/Prague",
                "color_scheme": "light",
                "device_scale_factor": 1,
                "is_mobile": False,
                "has_touch": False,
                "java_script_enabled": True,
            },
        },
    }

    def __init__(self, url=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if url:
            self.validate_indeed_search_url(url)
            self.search_urls = [("CUSTOM", "Custom Indeed search", url)]
        else:
            self.search_urls = self.SEARCH_URLS

        self.known_urls = set()
        self.generated_ids = set()
        self.prepare_existing_csv()

        self.current_search_index = 0
        self.current_search_page = 0
        self.total_pages = 0
        self.completed_searches = 0

        self.new_jobs = 0
        self.duplicate_jobs = 0
        self.invalid_cards = 0
        self.listings_seen_total = 0
        self.next_long_break_at = LONG_BREAK_EVERY

        self.visited_urls = []
        self.visited_page_keys = set()

        self.resume_url = self.search_urls[0][2]
        self.resuming = False
        self.success_probe_saved = False
        self.diagnostic_sequence = 0

        self.plan_signature = self.make_plan_signature()
        self.load_or_create_registry()

    # =================================================================
    # SEARCH PLAN AND URL VALIDATION
    # =================================================================

    def make_plan_signature(self):
        # Include crawl behavior so a registry created by the older paginating
        # build cannot resume directly onto a robots-blocked ``start=`` URL.
        behavior = f"follow_pagination={FOLLOW_PAGINATION}"
        raw = behavior + "\n" + "\n".join(
            url for _, _, url in self.search_urls
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def validate_indeed_search_url(url):
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if parsed.scheme not in {"http", "https"}:
            raise CloseSpider("URL must begin with http:// or https://")

        if hostname != "indeed.com" and not hostname.endswith(".indeed.com"):
            raise CloseSpider("This spider only accepts Indeed URLs.")

        if parsed.path.rstrip("/") != "/jobs":
            raise CloseSpider("The custom URL must be an Indeed /jobs search.")

    # =================================================================
    # CSV PREPARATION
    # =================================================================

    def prepare_existing_csv(self):
        if not OUTPUT_FILE.exists() or OUTPUT_FILE.stat().st_size == 0:
            with OUTPUT_FILE.open("w", newline="", encoding="utf-8-sig") as file:
                csv.DictWriter(file, fieldnames=CSV_FIELDS).writeheader()
            self.logger.info("Created new CSV: %s", OUTPUT_FILE)
            return

        with OUTPUT_FILE.open("r", newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            old_fields = reader.fieldnames or []
            rows = list(reader)

        for row in rows:
            existing_id = (row.get("ID") or "").strip()
            if existing_id:
                self.generated_ids.add(existing_id)

            existing_url = (row.get("URL") or "").strip()
            if existing_url:
                self.known_urls.add(self.canonicalize_job_url(existing_url))

        self.logger.info(
            "Loaded %s existing Indeed URLs from %s",
            len(self.known_urls),
            OUTPUT_FILE,
        )

        if old_fields != CSV_FIELDS:
            temp_file = OUTPUT_FILE.with_name("indeed_basic_temp.csv")
            with temp_file.open("w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
                writer.writeheader()
                for row in rows:
                    writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
            temp_file.replace(OUTPUT_FILE)
            self.logger.info("Normalized the existing Indeed CSV schema.")

    # =================================================================
    # REGISTRY / RESUME STATE
    # =================================================================

    def load_or_create_registry(self):
        if REGISTRY_FILE.exists():
            try:
                with REGISTRY_FILE.open("r", encoding="utf-8") as file:
                    state = json.load(file)

                same_plan = state.get("plan_signature") == self.plan_signature
                unfinished = not state.get("completed", False)

                if same_plan and unfinished:
                    search_index = int(state.get("current_search_index", 0))
                    if not 0 <= search_index < len(self.search_urls):
                        raise ValueError("Registry search index is out of range")

                    self.current_search_index = search_index
                    self.current_search_page = int(state.get("current_search_page", 0))
                    self.total_pages = int(state.get("total_pages", 0))
                    self.completed_searches = int(state.get("completed_searches", 0))
                    self.new_jobs = int(state.get("new_jobs", 0))
                    self.duplicate_jobs = int(state.get("duplicate_jobs", 0))
                    self.invalid_cards = int(state.get("invalid_cards", 0))
                    self.listings_seen_total = int(state.get("listings_seen_total", 0))
                    self.next_long_break_at = int(
                        state.get(
                            "next_long_break_at",
                            ((self.listings_seen_total // LONG_BREAK_EVERY) + 1)
                            * LONG_BREAK_EVERY,
                        )
                    )
                    self.visited_urls = list(state.get("visited_urls", []))
                    self.visited_page_keys = {
                        self.page_registry_key(item) for item in self.visited_urls
                    }
                    self.resume_url = (
                        state.get("resume_url")
                        or self.search_urls[self.current_search_index][2]
                    )
                    self.resuming = True

                    self.logger.info(
                        "RESUME STATE FOUND | search=%s | completed pages=%s | resume=%s",
                        self.current_search_index + 1,
                        self.current_search_page,
                        self.resume_url,
                    )
                    return

            except Exception as exc:
                self.logger.warning(
                    "Could not load registry: %s. Starting a fresh run.", exc
                )

        self.reset_registry()

    def reset_registry(self):
        self.current_search_index = 0
        self.current_search_page = 0
        self.total_pages = 0
        self.completed_searches = 0
        self.new_jobs = 0
        self.duplicate_jobs = 0
        self.invalid_cards = 0
        self.listings_seen_total = 0
        self.next_long_break_at = LONG_BREAK_EVERY
        self.visited_urls = []
        self.visited_page_keys = set()
        self.resume_url = self.search_urls[0][2]
        self.resuming = False
        self.save_registry(resume_url=self.resume_url, completed=False)

    def save_registry(self, resume_url=None, completed=False):
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
            "invalid_cards": self.invalid_cards,
            "resume_url": None if completed else self.resume_url,
            "visited_urls": self.visited_urls,
        }

        temp_file = REGISTRY_FILE.with_suffix(".tmp")
        with temp_file.open("w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
        temp_file.replace(REGISTRY_FILE)

    def mark_page_visited(self, url):
        key = self.page_registry_key(url)
        if key not in self.visited_page_keys:
            self.visited_page_keys.add(key)
            self.visited_urls.append(url)

    # =================================================================
    # START AND REQUEST FACTORY
    # =================================================================

    async def start(self):
        label, description, _ = self.search_urls[self.current_search_index]

        if self.resuming:
            delay = random.uniform(MAIN_URL_DELAY_MIN, MAIN_URL_DELAY_MAX)
            self.logger.info(
                "RESUMING SEARCH %s | %s | waiting %.1f seconds",
                label,
                description,
                delay,
            )
            await asyncio.sleep(delay)
        else:
            self.logger.info(
                "STARTING SEARCH 1/%s | %s | %s",
                len(self.search_urls),
                label,
                description,
            )

        self.save_registry(resume_url=self.resume_url)
        yield self.make_request(self.resume_url)

    def make_request(
        self,
        url,
        *,
        rate_limit_retry=0,
        access_denied_retry=0,
        page=None,
    ):
        meta = {
            "handle_httpstatus_all": True,
            "rate_limit_retry": rate_limit_retry,
            "access_denied_retry": access_denied_retry,
            "playwright": True,
            "playwright_context": "indeed",
            "playwright_include_page": True,
            "playwright_page_methods": [
                PageMethod("wait_for_load_state", "domcontentloaded"),
            ],
        }

        if page is not None:
            # scrapy-playwright will navigate this existing tab instead of
            # creating a fresh page for every keyword search.
            meta["playwright_page"] = page
            self._page_handed_off = True

        referer = page.url if page is not None else "https://cz.indeed.com/"

        return scrapy.Request(
            url=url,
            callback=self.parse,
            errback=self.request_failed,
            headers={"Referer": referer},
            meta=meta,
            dont_filter=True,
        )

    # =================================================================
    # MAIN PARSER
    # =================================================================

    async def parse(self, response):
        self._page_handed_off = False
        page = response.meta.get("playwright_page")
        if page is None:
            self.save_registry(resume_url=response.url)
            raise CloseSpider("playwright_page_missing_checkpoint_saved")

        try:
            async for item in self.parse_browser_page(response, page):
                yield item
        finally:
            if not self._page_handed_off and not page.is_closed():
                await page.close()

    async def parse_browser_page(self, response, page):
        label, _, _ = self.search_urls[self.current_search_index]
        page_number = self.current_search_page + 1
        recovered_from_verification = False

        if response.status == 429:
            self.save_registry(resume_url=response.url)
            retries = int(response.meta.get("rate_limit_retry", 0))
            await self.capture_diagnostic(
                response,
                page,
                reason=f"http_429_attempt_{retries + 1}",
            )

            if retries >= MAX_RATE_LIMIT_RETRIES:
                raise CloseSpider("rate_limit_checkpoint_saved")

            delay = self.get_rate_limit_delay(response)
            self.logger.warning("HTTP 429 | backing off for %.1f minutes", delay / 60)
            await page.close()
            await asyncio.sleep(delay)
            yield self.make_request(response.url, rate_limit_retry=retries + 1)
            return

        if response.status == 403:
            self.save_registry(resume_url=response.url)

            retries = int(response.meta.get("access_denied_retry", 0))
            denial_html = await page.content()
            await self.capture_diagnostic(
                response,
                page,
                reason=f"http_403_attempt_{retries + 1}",
                html_content=denial_html,
            )

            if self.is_cloudflare_verification_page(denial_html):
                verified = await self.wait_for_manual_verification(page)
                if verified:
                    recovered_from_verification = True
                    recovered_html = await page.content()
                    await self.capture_diagnostic(
                        response,
                        page,
                        reason="manual_cloudflare_verification_passed",
                        html_content=recovered_html,
                    )
                    self.logger.info(
                        "MANUAL VERIFICATION PASSED | continuing search %s",
                        label,
                    )
                else:
                    timeout_html = await page.content()
                    await self.capture_diagnostic(
                        response,
                        page,
                        reason="manual_cloudflare_verification_timeout",
                        html_content=timeout_html,
                    )
                    raise CloseSpider(
                        "manual_verification_timeout_checkpoint_saved"
                    )

            elif retries < MAX_ACCESS_DENIED_RETRIES:
                delay = random.uniform(
                    ACCESS_DENIED_DELAY_MIN,
                    ACCESS_DENIED_DELAY_MAX,
                )
                self.logger.warning(
                    "HTTP 403 | checkpoint saved | cooling down for %.1f "
                    "minutes before one retry",
                    delay / 60,
                )
                await page.close()
                await asyncio.sleep(delay)
                yield self.make_request(
                    response.url,
                    access_denied_retry=retries + 1,
                )
                return

            else:
                raise CloseSpider("access_denied_checkpoint_saved")

        if response.status == 401:
            self.save_registry(resume_url=response.url)
            await self.capture_diagnostic(response, page, reason="http_401")
            raise CloseSpider("access_denied_checkpoint_saved")

        if (
            not recovered_from_verification
            and not 200 <= response.status < 400
        ):
            self.save_registry(resume_url=response.url)
            await self.capture_diagnostic(
                response,
                page,
                reason=f"http_{response.status}",
            )
            raise CloseSpider("http_failure_checkpoint_saved")

        initial_delay = random.uniform(
            INITIAL_RENDER_DELAY_MIN,
            INITIAL_RENDER_DELAY_MAX,
        )
        await page.wait_for_timeout(int(initial_delay * 1000))
        await self.wait_for_result_state(page)

        live_html = await page.content()

        if self.looks_like_challenge_text(live_html):
            self.save_registry(resume_url=response.url)
            await self.capture_diagnostic(
                response,
                page,
                reason="challenge_page_http_200",
                html_content=live_html,
            )

            if self.is_cloudflare_verification_page(live_html):
                verified = await self.wait_for_manual_verification(page)
                if verified:
                    live_html = await page.content()
                    await self.capture_diagnostic(
                        response,
                        page,
                        reason="manual_cloudflare_verification_passed",
                        html_content=live_html,
                    )
                    self.logger.info(
                        "MANUAL VERIFICATION PASSED | continuing search %s",
                        label,
                    )
                else:
                    timeout_html = await page.content()
                    await self.capture_diagnostic(
                        response,
                        page,
                        reason="manual_cloudflare_verification_timeout",
                        html_content=timeout_html,
                    )
                    raise CloseSpider(
                        "manual_verification_timeout_checkpoint_saved"
                    )
            else:
                raise CloseSpider("challenge_checkpoint_saved")

        selector = scrapy.Selector(text=live_html, type="html")

        if not self.success_probe_saved:
            await self.capture_diagnostic(
                response,
                page,
                reason="successful_page_baseline",
                html_content=live_html,
            )
            self.success_probe_saved = True

        self.logger.info(
            "SEARCH %s | PAGE %s | ACCESS_OK | %s",
            label,
            page_number,
            response.url,
        )

        cards = selector.xpath(JOB_CARD_XPATH)
        self.logger.info(
            "SEARCH %s | PAGE %s | Found %s visible listings",
            label,
            page_number,
            len(cards),
        )

        if not cards:
            visible_text = self.clean_text(
                selector.xpath(
                    "//body//text()[not(ancestor::script) and not(ancestor::style)]"
                ).getall()
            )
            if self.is_explicit_no_results(visible_text):
                self.logger.info("SEARCH %s | explicit no-results page", label)
                self.completed_searches += 1
                next_request = await self.move_to_next_main_search(page=page)
                if next_request:
                    yield next_request
                return

            self.save_registry(resume_url=response.url)
            await self.capture_diagnostic(
                response,
                page,
                reason="job_cards_missing",
                html_content=live_html,
            )
            raise CloseSpider("job_cards_missing_checkpoint_saved")

        page_job_keys = set()

        for card in cards:
            link_nodes = card.xpath(VISIBLE_TITLE_LINK_XPATH)
            if not link_nodes:
                self.invalid_cards += 1
                continue

            link = link_nodes[0]
            job_key = self.normalize_job_key(link.xpath("./@data-jk").get())

            # Defend against repeated desktop/mobile variants in one response.
            if not job_key or job_key in page_job_keys:
                self.invalid_cards += 1
                continue

            page_job_keys.add(job_key)
            self.listings_seen_total += 1
            job_url = self.canonicalize_job_url(response.url, job_key=job_key)

            if job_url in self.known_urls:
                self.duplicate_jobs += 1
            else:
                item = self.extract_item(card, link, job_key, job_url)

                if not item["Job Title"] or not item["Company"]:
                    self.invalid_cards += 1
                    self.logger.warning(
                        "SKIPPING INCOMPLETE CARD | jk=%s | title=%r | company=%r",
                        job_key,
                        item["Job Title"],
                        item["Company"],
                    )
                else:
                    self.known_urls.add(job_url)
                    self.append_to_csv(item)
                    self.new_jobs += 1
                    self.logger.info(
                        "NEW JOB | %s | %s | %s",
                        item["ID"],
                        item["Job Title"],
                        item["Company"],
                    )
                    yield item

            if self.listings_seen_total % REGISTRY_CHECKPOINT_EVERY == 0:
                self.save_registry(resume_url=response.url)

            if self.listings_seen_total >= self.next_long_break_at:
                await self.take_long_break(response.url)

        self.current_search_page = page_number
        self.total_pages += 1
        self.mark_page_visited(response.url)

        next_href = selector.xpath(NEXT_PAGE_XPATH).get()

        if next_href and not FOLLOW_PAGINATION:
            self.logger.info(
                "SEARCH %s | additional page available but not requested "
                "because Indeed robots.txt blocks start= pagination URLs",
                label,
            )
            next_href = None

        if next_href:
            next_url = urljoin(response.url, next_href)
            self.validate_indeed_search_url(next_url)
            next_key = self.page_registry_key(next_url)

            if next_key in self.visited_page_keys:
                self.logger.warning(
                    "Next page was already visited. Ending this search to avoid a loop."
                )
            elif self.current_search_page >= MAX_PAGES_PER_SEARCH:
                self.save_registry(resume_url=next_url)
                raise CloseSpider("pagination_safety_limit_checkpoint_saved")
            else:
                self.save_registry(resume_url=next_url)
                delay = random.uniform(PAGE_DELAY_MIN, PAGE_DELAY_MAX)
                self.logger.info(
                    "PAGE %s complete | waiting %.1f seconds before next page...",
                    page_number,
                    delay,
                )
                await asyncio.sleep(delay)
                yield self.make_request(next_url, page=page)
                return

        self.logger.info("SEARCH %s | final page reached", label)
        self.completed_searches += 1
        next_request = await self.move_to_next_main_search(page=page)
        if next_request:
            yield next_request

    async def wait_for_result_state(self, page):
        """Wait until cards, an empty result, or a challenge becomes visible."""

        deadline = asyncio.get_running_loop().time() + (
            INITIAL_RESULTS_TIMEOUT_MS / 1000
        )

        while asyncio.get_running_loop().time() < deadline:
            html = await page.content()

            if self.looks_like_challenge_text(html):
                return

            selector = scrapy.Selector(text=html, type="html")
            if selector.xpath(JOB_CARD_XPATH):
                return

            visible_text = self.clean_text(
                selector.xpath(
                    "//body//text()[not(ancestor::script) and not(ancestor::style)]"
                ).getall()
            )
            if self.is_explicit_no_results(visible_text):
                return

            await page.wait_for_timeout(500)

    async def wait_for_manual_verification(self, page):
        """Wait for the user—not the spider—to complete Cloudflare."""

        self.logger.warning(
            "CLOUDFLARE VERIFICATION REQUIRED | Complete the checkbox in the "
            "visible Chromium window within %.1f minutes. The spider will "
            "not interact with the verification control.",
            MANUAL_VERIFICATION_TIMEOUT_SECONDS / 60,
        )

        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + MANUAL_VERIFICATION_TIMEOUT_SECONDS
        next_status_at = started + MANUAL_VERIFICATION_STATUS_EVERY_SECONDS

        while loop.time() < deadline:
            if page.is_closed():
                self.logger.error(
                    "The browser page was closed during manual verification."
                )
                return False

            try:
                html = await page.content()
                selector = scrapy.Selector(text=html, type="html")

                if selector.xpath(JOB_CARD_XPATH):
                    return True

                visible_text = self.clean_text(
                    selector.xpath(
                        "//body//text()[not(ancestor::script) and "
                        "not(ancestor::style)]"
                    ).getall()
                )
                if (
                    self.is_explicit_no_results(visible_text)
                    and not self.is_cloudflare_verification_page(html)
                ):
                    return True

            except Exception as exc:
                self.logger.debug(
                    "Manual-verification poll could not inspect the page: %s",
                    exc,
                )

            now = loop.time()
            if now >= next_status_at:
                remaining = max(0, int(deadline - now))
                self.logger.warning(
                    "WAITING FOR MANUAL VERIFICATION | %s seconds remaining",
                    remaining,
                )
                next_status_at = (
                    now + MANUAL_VERIFICATION_STATUS_EVERY_SECONDS
                )

            await page.wait_for_timeout(MANUAL_VERIFICATION_POLL_MS)

        self.logger.error(
            "MANUAL VERIFICATION TIMED OUT | checkpoint remains at %s",
            self.resume_url,
        )
        return False

    # =================================================================
    # CARD EXTRACTION
    # =================================================================

    def extract_item(self, card, link, job_key, job_url):
        title = self.clean_text(
            link.xpath(".//text()[not(ancestor::style) and not(ancestor::script)]").getall()
        )
        company = self.clean_text(card.xpath(COMPANY_XPATH).getall())
        location = self.clean_text(card.xpath(LOCATION_XPATH).getall())
        salary = self.clean_text(card.xpath(SALARY_XPATH).getall())

        return {
            "ID": self.generate_job_id(),
            "Job Title": title,
            "Company": company,
            "Location": location,
            "Salary": salary,
            "URL": job_url,
            "Mining_Date": self.current_mining_datetime(),
        }

    # =================================================================
    # LONG BREAK AND NEXT MAIN SEARCH
    # =================================================================

    async def take_long_break(self, current_url):
        delay = random.uniform(LONG_BREAK_MIN, LONG_BREAK_MAX)
        self.next_long_break_at += LONG_BREAK_EVERY
        self.save_registry(resume_url=current_url)

        self.logger.info(
            "%s LISTINGS EXAMINED | taking a %.1f minute checkpointed break",
            self.listings_seen_total,
            delay / 60,
        )
        await asyncio.sleep(delay)
        self.save_registry(resume_url=current_url)

    async def move_to_next_main_search(self, page=None):
        next_index = self.current_search_index + 1

        if next_index >= len(self.search_urls):
            self.save_registry(resume_url=None, completed=True)
            self.logger.info("ALL INDEED SEARCHES COMPLETE")
            return None

        next_label, next_description, next_url = self.search_urls[next_index]
        self.current_search_index = next_index
        self.current_search_page = 0
        self.save_registry(resume_url=next_url)

        delay = random.uniform(MAIN_URL_DELAY_MIN, MAIN_URL_DELAY_MAX)
        self.logger.info(
            "Waiting %.1f seconds before search %s | %s",
            delay,
            next_label,
            next_description,
        )
        await asyncio.sleep(delay)
        return self.make_request(next_url, page=page)

    # =================================================================
    # URL, ID, TEXT, AND FILE HELPERS
    # =================================================================

    @staticmethod
    def page_registry_key(url):
        parsed = urlparse(url)
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        transient = {
            "vjk",
            "from",
            "advn",
            "adid",
            "tk",
            "xkcb",
            "fccid",
            "bb",
        }
        query_pairs = [
            (key, value)
            for key, value in query_pairs
            if key.lower() not in transient
        ]
        query_pairs.sort()

        return urlunparse(
            (
                (parsed.scheme or "https").lower(),
                (parsed.hostname or "cz.indeed.com").lower(),
                parsed.path.rstrip("/") or "/",
                "",
                urlencode(query_pairs, doseq=True),
                "",
            )
        )

    @staticmethod
    def normalize_job_key(value):
        value = (value or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{16}", value):
            return value
        return ""

    @classmethod
    def canonicalize_job_url(cls, url, job_key=None):
        key = cls.normalize_job_key(job_key)

        if not key and url:
            parsed = urlparse(url)
            values = parse_qs(parsed.query).get("jk", [])
            if values:
                key = cls.normalize_job_key(values[0])

        if key:
            return f"https://cz.indeed.com/viewjob?jk={key}"

        return url or ""

    @staticmethod
    def append_to_csv(item):
        with OUTPUT_FILE.open("a", newline="", encoding="utf-8") as file:
            csv.DictWriter(file, fieldnames=CSV_FIELDS).writerow(item)

    def generate_job_id(self):
        if len(self.generated_ids) >= 10_000:
            raise CloseSpider("All possible IND_XXXX IDs have been exhausted.")

        while True:
            job_id = f"IND_{random.randint(0, 9999):04d}"
            if job_id not in self.generated_ids:
                self.generated_ids.add(job_id)
                return job_id

    @staticmethod
    def current_mining_datetime():
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def clean_text(parts):
        if not parts:
            return ""
        text = " ".join(part for part in parts if part)
        return " ".join(text.replace("\u200d", "").split())

    @staticmethod
    def is_explicit_no_results(text):
        lowered = text.lower()
        indicators = (
            "neodpovídají žádné nabídky práce",
            "nenašli jsme žádné nabídky práce",
            "žádné nabídky práce",
            "no jobs found",
            "did not match any jobs",
        )
        return any(indicator in lowered for indicator in indicators)

    # =================================================================
    # SAFE DIAGNOSTIC CAPTURE
    # =================================================================

    async def capture_diagnostic(
        self,
        response,
        page,
        *,
        reason,
        html_content=None,
    ):
        """Save a privacy-conscious browser/response diagnostic bundle.

        Cookie values, local storage, authorization headers, and response
        Set-Cookie headers are deliberately excluded.
        """

        self.diagnostic_sequence += 1
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        label, description, _ = self.search_urls[self.current_search_index]
        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", label)
        safe_reason = re.sub(r"[^A-Za-z0-9_-]+", "_", reason)
        prefix = f"{stamp}_{self.diagnostic_sequence:02d}_{safe_label}_{safe_reason}"

        json_path = DIAGNOSTICS_DIR / f"{prefix}.json"
        html_path = DIAGNOSTICS_DIR / f"{prefix}.html"
        screenshot_path = DIAGNOSTICS_DIR / f"{prefix}.png"

        errors = []

        if html_content is None:
            try:
                html_content = await page.content()
            except Exception as exc:
                errors.append(f"page_content: {type(exc).__name__}: {exc}")
                html_content = response.text or ""

        try:
            html_path.write_text(html_content, encoding="utf-8")
            html_file = str(html_path)
        except Exception as exc:
            errors.append(f"html_write: {type(exc).__name__}: {exc}")
            html_file = None

        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
            screenshot_file = str(screenshot_path)
        except Exception as exc:
            errors.append(f"screenshot: {type(exc).__name__}: {exc}")
            screenshot_file = None

        try:
            page_title = await page.title()
        except Exception as exc:
            errors.append(f"page_title: {type(exc).__name__}: {exc}")
            page_title = ""

        try:
            browser_environment = await page.evaluate(
                """() => {
                    const navEntry = performance.getEntriesByType('navigation')[0];
                    const uaData = navigator.userAgentData;
                    return {
                        user_agent: navigator.userAgent,
                        app_version: navigator.appVersion,
                        platform: navigator.platform,
                        language: navigator.language,
                        languages: Array.from(navigator.languages || []),
                        webdriver: navigator.webdriver,
                        cookie_enabled: navigator.cookieEnabled,
                        do_not_track: navigator.doNotTrack,
                        hardware_concurrency: navigator.hardwareConcurrency,
                        device_memory: navigator.deviceMemory ?? null,
                        max_touch_points: navigator.maxTouchPoints,
                        user_agent_data: uaData ? {
                            brands: Array.from(uaData.brands || []),
                            mobile: uaData.mobile,
                            platform: uaData.platform
                        } : null,
                        viewport: {
                            inner_width: window.innerWidth,
                            inner_height: window.innerHeight,
                            outer_width: window.outerWidth,
                            outer_height: window.outerHeight,
                            device_pixel_ratio: window.devicePixelRatio
                        },
                        screen: {
                            width: screen.width,
                            height: screen.height,
                            available_width: screen.availWidth,
                            available_height: screen.availHeight,
                            color_depth: screen.colorDepth,
                            pixel_depth: screen.pixelDepth
                        },
                        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
                        visibility_state: document.visibilityState,
                        navigation: navEntry ? {
                            type: navEntry.type,
                            duration_ms: Math.round(navEntry.duration),
                            redirect_count: navEntry.redirectCount,
                            transfer_size: navEntry.transferSize,
                            encoded_body_size: navEntry.encodedBodySize,
                            decoded_body_size: navEntry.decodedBodySize,
                            response_status: navEntry.responseStatus ?? null
                        } : null
                    };
                }"""
            )
        except Exception as exc:
            errors.append(f"browser_environment: {type(exc).__name__}: {exc}")
            browser_environment = {}

        try:
            cookies = await page.context.cookies()
            cookie_summary = {
                "count": len(cookies),
                "domains": sorted(
                    {
                        str(cookie.get("domain", ""))
                        for cookie in cookies
                        if cookie.get("domain")
                    }
                ),
            }
        except Exception as exc:
            errors.append(f"cookie_summary: {type(exc).__name__}: {exc}")
            cookie_summary = {"count": None, "domains": []}

        try:
            selector = scrapy.Selector(text=html_content, type="html")
            visible_text = self.clean_text(
                selector.xpath(
                    "//body//text()[not(ancestor::script) and not(ancestor::style)]"
                ).getall()
            )
        except Exception as exc:
            errors.append(f"visible_text: {type(exc).__name__}: {exc}")
            visible_text = ""

        markers = [
            marker
            for marker in (
                "additional verification required",
                "verify you are human",
                "checking your browser",
                "unusual traffic",
                "security check",
                "captcha",
                "press and hold",
                "access denied",
                "forbidden",
            )
            if marker in html_content[:100_000].lower()
        ]

        try:
            final_page_url = page.url
        except Exception:
            final_page_url = ""

        report = {
            "captured_at": self.current_mining_datetime(),
            "reason": reason,
            "search": {
                "index": self.current_search_index,
                "label": label,
                "description": description,
            },
            "http": {
                "status": response.status,
                "requested_url": response.request.url,
                "response_url": response.url,
                "final_page_url": final_page_url,
                "request_headers": self.safe_headers(
                    response.request.headers,
                    request=True,
                ),
                "response_headers": self.safe_headers(response.headers),
            },
            "page": {
                "title": page_title,
                "html_characters": len(html_content),
                "visible_text_characters": len(visible_text),
                "visible_text_sample": visible_text[:6_000],
                "challenge_markers": markers,
                "html_file": html_file,
                "screenshot_file": screenshot_file,
            },
            "browser_environment": browser_environment,
            "cookie_summary": cookie_summary,
            "crawl_state": {
                "completed_searches": self.completed_searches,
                "listings_seen_total": self.listings_seen_total,
                "new_jobs": self.new_jobs,
                "duplicate_jobs": self.duplicate_jobs,
                "rate_limit_retry": int(
                    response.meta.get("rate_limit_retry", 0)
                ),
                "access_denied_retry": int(
                    response.meta.get("access_denied_retry", 0)
                ),
            },
            "capture_errors": errors,
        }

        log_method = (
            self.logger.info
            if reason == "successful_page_baseline"
            else self.logger.warning
        )
        log_method(
            "DIAGNOSTIC PROFILE | reason=%s | status=%s | title=%r | "
            "webdriver=%r | user_agent=%r | cookies=%s | markers=%s",
            reason,
            response.status,
            page_title,
            browser_environment.get("webdriver"),
            browser_environment.get("user_agent"),
            cookie_summary.get("count"),
            markers,
        )

        try:
            with json_path.open("w", encoding="utf-8") as file:
                json.dump(report, file, ensure_ascii=False, indent=2)
            log_method("DIAGNOSTIC SAVED | %s", json_path)
        except Exception as exc:
            self.logger.error(
                "Could not save diagnostic manifest: %s: %s",
                type(exc).__name__,
                exc,
            )

    @staticmethod
    def safe_headers(headers, *, request=False):
        """Return diagnostically useful headers without credentials/cookies."""

        request_allowlist = {
            "accept",
            "accept-language",
            "cache-control",
            "referer",
            "sec-ch-ua",
            "sec-ch-ua-mobile",
            "sec-ch-ua-platform",
            "sec-fetch-dest",
            "sec-fetch-mode",
            "sec-fetch-site",
            "upgrade-insecure-requests",
            "user-agent",
        }
        response_allowlist = {
            "cache-control",
            "cf-ray",
            "content-length",
            "content-type",
            "date",
            "location",
            "retry-after",
            "server",
            "via",
            "x-cache",
            "x-cache-hits",
            "x-served-by",
            "x-timer",
        }

        allowlist = request_allowlist if request else response_allowlist
        result = {}

        for raw_name in headers.keys():
            name = raw_name.decode("latin-1") if isinstance(raw_name, bytes) else str(raw_name)
            lowered = name.lower()
            if lowered not in allowlist and not (
                not request and lowered.startswith("x-indeed-")
            ):
                continue

            values = headers.getlist(raw_name)
            decoded = []
            for value in values:
                if isinstance(value, bytes):
                    decoded.append(value.decode("latin-1", errors="replace"))
                else:
                    decoded.append(str(value))
            result[name] = decoded

        return result

    async def capture_transport_diagnostic(self, failure, page=None):
        """Record failures occurring before a normal HTTP response exists."""

        self.diagnostic_sequence += 1
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        label, description, _ = self.search_urls[self.current_search_index]
        safe_label = re.sub(r"[^A-Za-z0-9_-]+", "_", label)
        prefix = (
            f"{stamp}_{self.diagnostic_sequence:02d}_{safe_label}_"
            "transport_failure"
        )
        json_path = DIAGNOSTICS_DIR / f"{prefix}.json"
        html_path = DIAGNOSTICS_DIR / f"{prefix}.html"
        screenshot_path = DIAGNOSTICS_DIR / f"{prefix}.png"

        request = failure.request
        errors = []
        page_info = {
            "present": page is not None,
            "closed": None,
            "url": None,
            "title": None,
            "html_file": None,
            "screenshot_file": None,
        }

        if page is not None:
            try:
                page_info["closed"] = page.is_closed()
            except Exception as exc:
                errors.append(f"page_state: {type(exc).__name__}: {exc}")

            if page_info["closed"] is False:
                try:
                    page_info["url"] = page.url
                    page_info["title"] = await page.title()
                except Exception as exc:
                    errors.append(f"page_identity: {type(exc).__name__}: {exc}")

                try:
                    html_content = await page.content()
                    html_path.write_text(html_content, encoding="utf-8")
                    page_info["html_file"] = str(html_path)
                except Exception as exc:
                    errors.append(f"page_content: {type(exc).__name__}: {exc}")

                try:
                    await page.screenshot(
                        path=str(screenshot_path),
                        full_page=True,
                    )
                    page_info["screenshot_file"] = str(screenshot_path)
                except Exception as exc:
                    errors.append(f"screenshot: {type(exc).__name__}: {exc}")

        try:
            traceback_text = failure.getTraceback()
        except Exception as exc:
            errors.append(f"traceback: {type(exc).__name__}: {exc}")
            traceback_text = ""

        report = {
            "captured_at": self.current_mining_datetime(),
            "reason": "transport_failure",
            "search": {
                "index": self.current_search_index,
                "label": label,
                "description": description,
            },
            "request": {
                "url": request.url,
                "method": request.method,
                "headers": self.safe_headers(request.headers, request=True),
                "rate_limit_retry": int(
                    request.meta.get("rate_limit_retry", 0)
                ),
                "access_denied_retry": int(
                    request.meta.get("access_denied_retry", 0)
                ),
                "playwright_requested": bool(
                    request.meta.get("playwright", False)
                ),
            },
            "failure": {
                "type": type(failure.value).__name__,
                "text": str(failure.value),
                "representation": repr(failure.value),
                "traceback": traceback_text,
            },
            "page": page_info,
            "crawl_state": {
                "completed_searches": self.completed_searches,
                "listings_seen_total": self.listings_seen_total,
                "new_jobs": self.new_jobs,
                "duplicate_jobs": self.duplicate_jobs,
            },
            "capture_errors": errors,
        }

        try:
            with json_path.open("w", encoding="utf-8") as file:
                json.dump(report, file, ensure_ascii=False, indent=2)
            self.logger.warning("TRANSPORT DIAGNOSTIC SAVED | %s", json_path)
        except Exception as exc:
            self.logger.error(
                "Could not save transport diagnostic: %s: %s",
                type(exc).__name__,
                exc,
            )

    # =================================================================
    # RATE LIMIT, CHALLENGE, FAILURE, AND FINAL STATUS
    # =================================================================

    @staticmethod
    def get_rate_limit_delay(response):
        retry_after = response.headers.get(b"Retry-After")
        if retry_after:
            try:
                return max(
                    int(retry_after.decode("ascii").strip()),
                    RATE_LIMIT_DELAY_MIN,
                )
            except (ValueError, UnicodeDecodeError):
                pass

        return random.uniform(RATE_LIMIT_DELAY_MIN, RATE_LIMIT_DELAY_MAX)

    @staticmethod
    def looks_like_challenge_text(text):
        sample = text[:60_000].lower()
        indicators = (
            "additional verification required",
            "verify you are human",
            "je nutné další ověření",
            "potvrďte, že jste člověk",
            "checking your browser",
            "unusual traffic",
            "security check",
            "complete the captcha",
            "captcha challenge",
            "press and hold",
        )
        return any(indicator in sample for indicator in indicators)

    @staticmethod
    def is_cloudflare_verification_page(text):
        sample = text[:200_000].lower()
        cloudflare_present = "cloudflare" in sample
        verification_markers = (
            "je nutné další ověření",
            "potvrďte, že jste člověk",
            "additional verification required",
            "verify you are human",
            "ray id",
            "challenges.cloudflare.com",
        )
        return cloudflare_present and any(
            marker in sample for marker in verification_markers
        )

    async def request_failed(self, failure):
        url = failure.request.url
        page = failure.request.meta.get("playwright_page")
        await self.capture_transport_diagnostic(failure, page=page)

        if page is not None and not page.is_closed():
            await page.close()

        self.save_registry(resume_url=url)
        self.logger.error("REQUEST FAILED | %s | %s", url, failure.value)
        raise CloseSpider("network_failure_checkpoint_saved")

    def closed(self, reason):
        if reason != "finished":
            self.save_registry(resume_url=self.resume_url, completed=False)

        self.logger.info(
            "MINING STOPPED | %s listings examined | %s new jobs | "
            "%s duplicates skipped | %s invalid/hidden cards ignored | "
            "%s pages completed | %s/%s searches completed | reason=%s",
            self.listings_seen_total,
            self.new_jobs,
            self.duplicate_jobs,
            self.invalid_cards,
            self.total_pages,
            self.completed_searches,
            len(self.search_urls),
            reason,
        )
        self.logger.info("CSV: %s", OUTPUT_FILE)
        self.logger.info("Registry: %s", REGISTRY_FILE)
