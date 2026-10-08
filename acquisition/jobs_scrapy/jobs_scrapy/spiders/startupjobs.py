"""StartupJobs.cz browser-backed Scrapy spider.

The results page uses a repeatedly clickable "load more" button, so this
spider uses scrapy-playwright to keep one browser page open while it expands
the result set. Rows are appended to the CSV immediately and crawl progress
is checkpointed in a JSON registry.

Run from the Scrapy project root with:

    scrapy crawl startupjobs

An alternative StartupJobs search URL can be supplied with:

    scrapy crawl startupjobs -a url="https://www.startupjobs.cz/nabidky/..."

Required once in the active Python environment:

    pip install scrapy-playwright
    playwright install chromium
"""

from datetime import datetime
from pathlib import Path
from urllib.parse import (
    parse_qsl,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)
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
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\01_raw"
)

REGISTRY_DIR = Path(
    r"C:\Users\demps\Job_Scanner\Data_Mining\data\01_raw\registries"
)

DATA_DIR.mkdir(parents=True, exist_ok=True)
REGISTRY_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = DATA_DIR / "startupjobs_basic.csv"
REGISTRY_FILE = REGISTRY_DIR / "startupjobs_registry.json"

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
# TIMING, BACKOFF, AND CHECKPOINTS
# =====================================================================

# Initial pause after the browser has rendered the search page.
INITIAL_RENDER_DELAY_MIN = 2.5
INITIAL_RENDER_DELAY_MAX = 5.5

# Pause before each human-equivalent click on "load more".
LOAD_MORE_DELAY_MIN = 5.0
LOAD_MORE_DELAY_MAX = 15.0

# Pause between separate search URLs if more are added in the future.
MAIN_URL_DELAY_MIN = 10.0
MAIN_URL_DELAY_MAX = 20.0

# Every 500 newly encountered DOM listings, take a substantial rest.
LONG_BREAK_EVERY = 500
LONG_BREAK_MIN = 5 * 60
LONG_BREAK_MAX = 10 * 60

# Save the registry regularly even within a newly revealed batch.
REGISTRY_CHECKPOINT_EVERY = 25

# Explicit response to server-side rate limiting.
RATE_LIMIT_DELAY_MIN = 10 * 60
RATE_LIMIT_DELAY_MAX = 20 * 60
MAX_RATE_LIMIT_RETRIES = 1

# Browser waits.
INITIAL_RESULTS_TIMEOUT_MS = 30_000
LOAD_MORE_RESULT_TIMEOUT_MS = 30_000
BUTTON_ACTION_TIMEOUT_MS = 15_000

# Defensive limit against an accidental endless button loop.
MAX_LOAD_MORE_CLICKS = 1_000


# =====================================================================
# SITE SELECTORS
# =====================================================================

# Numeric StartupJobs vacancy paths look like /nabidka/107257/slug.
JOB_PATH_RE = re.compile(r"^/nabidka/(?P<site_id>\d+)(?:/|$)")

# This uses the stable, distinctive tokens from the supplied button class.
# Text checks below further distinguish it from unrelated site buttons.
LOAD_MORE_BUTTON_SELECTOR = (
    "button[class*='inline-flex']"
    "[class*='items-center']"
    "[class*='justify-center']"
    "[class*='h-12']"
    "[class*='px-5']"
    "[class*='py-3']"
    "[class*='text-lg']"
)

LOAD_MORE_TEXT_RE = re.compile(
    r"(?:zobrazit|načíst|nacist|další|dalsi|více|vice|load\s*more|show\s*more)",
    re.IGNORECASE,
)

NO_RESULTS_TEXT_RE = re.compile(
    r"(?:žádné\s+nabídky|zadne\s+nabidky|"
    r"nenašli\s+jsme|nenasli\s+jsme|"
    r"nebyly\s+nalezeny|no\s+jobs\s+found)",
    re.IGNORECASE,
)


# =====================================================================
# SPIDER
# =====================================================================


class StartupJobsSpider(scrapy.Spider):

    name = "startupjobs"

    allowed_domains = [
        "startupjobs.cz",
        "www.startupjobs.cz",
        "back.startupjobs.cz",
    ]

    SEARCH_URLS = [
        (
            "A",
            "Prague data, ML, programming, and back-end roles",
            "https://www.startupjobs.cz/nabidky/"
            "back-end-vyvojar,data-analytik,data-engineer,data-scientist,"
            "machine-learning,programator"
            "?lokalita=Praha:ChIJi3lwCZyTC0cRkEAWZg-vAAQ:20km",
        ),
    ]

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    )

    custom_settings = {
        "USER_AGENT": USER_AGENT,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "cs-CZ,cs;q=0.9,en-US;q=0.8,en;q=0.7",
            "Upgrade-Insecure-Requests": "1",
        },
        "COOKIES_ENABLED": True,
        "REFERER_ENABLED": True,
        "CONCURRENT_REQUESTS": 1,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_DELAY": 1.0,
        "DOWNLOAD_DELAY_JITTER": 0.5,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 2.0,
        "AUTOTHROTTLE_MAX_DELAY": 60.0,
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 0.5,
        "DOWNLOAD_TIMEOUT": 60,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 2,
        # 429 is handled explicitly with a much longer backoff.
        "RETRY_HTTP_CODES": [408, 500, 502, 503, 504, 522, 524],
        "ROBOTSTXT_OBEY": True,
        "LOG_LEVEL": "INFO",

        # scrapy-playwright integration for the JavaScript-only button.
        "TWISTED_REACTOR": (
            "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
        ),
        "DOWNLOAD_HANDLERS": {
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT": 45_000,
        "PLAYWRIGHT_MAX_CONTEXTS": 1,
        "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": 1,
        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            "headless": True,
        },
        "PLAYWRIGHT_CONTEXTS": {
            "startupjobs": {
                "user_agent": USER_AGENT,
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

    # =================================================================
    # INITIALIZATION
    # =================================================================

    def __init__(self, url=None, *args, **kwargs):

        super().__init__(*args, **kwargs)

        if url:
            self.validate_startupjobs_url(url)
            self.search_urls = [
                ("CUSTOM", "Custom StartupJobs search", url),
            ]
        else:
            self.search_urls = self.SEARCH_URLS

        self.known_urls = set()
        self.generated_ids = set()
        self.csv_rows = []
        self.rows_by_url = {}
        self.prepare_existing_csv()

        self.current_search_index = 0
        self.current_search_clicks = 0
        self.total_load_more_clicks = 0
        self.completed_searches = 0

        self.new_jobs = 0
        self.duplicate_jobs = 0
        self.listings_seen_total = 0
        self.next_long_break_at = LONG_BREAK_EVERY

        self.visited_urls = []
        self.resume_url = self.search_urls[0][2]
        self.resuming = False
        self.cookie_consent_handled = False

        self.plan_signature = self.make_plan_signature()
        self.load_or_create_registry()

    # =================================================================
    # SEARCH PLAN AND URL VALIDATION
    # =================================================================

    def make_plan_signature(self):

        raw = "\n".join(url for _, _, url in self.search_urls)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def validate_startupjobs_url(url):

        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if parsed.scheme not in {"http", "https"}:
            raise CloseSpider("URL must begin with http:// or https://")

        if hostname != "startupjobs.cz" and not hostname.endswith(
            ".startupjobs.cz"
        ):
            raise CloseSpider(
                "This spider only accepts startupjobs.cz search URLs."
            )

        if not parsed.path.startswith("/nabidky"):
            raise CloseSpider(
                "The custom URL must be a StartupJobs /nabidky search URL."
            )

    # =================================================================
    # CSV PREPARATION
    # =================================================================

    def prepare_existing_csv(self):

        if not OUTPUT_FILE.exists() or OUTPUT_FILE.stat().st_size == 0:
            with OUTPUT_FILE.open(
                "w", newline="", encoding="utf-8-sig"
            ) as file:
                csv.DictWriter(file, fieldnames=CSV_FIELDS).writeheader()

            self.logger.info("Created new CSV: %s", OUTPUT_FILE)
            return

        with OUTPUT_FILE.open(
            "r", newline="", encoding="utf-8-sig"
        ) as file:
            reader = csv.DictReader(file)
            old_fields = reader.fieldnames or []
            rows = list(reader)

        for row in rows:
            normalized_row = {
                field: row.get(field, "") for field in CSV_FIELDS
            }
            self.csv_rows.append(normalized_row)

            existing_id = (row.get("ID") or "").strip()
            if existing_id:
                self.generated_ids.add(existing_id)

            existing_url = (row.get("URL") or "").strip()
            if existing_url:
                canonical_url = self.canonicalize_job_url(existing_url)
                self.known_urls.add(canonical_url)
                self.rows_by_url[canonical_url] = normalized_row

        self.logger.info(
            "Loaded %s existing StartupJobs URLs from %s",
            len(self.known_urls),
            OUTPUT_FILE,
        )

        # Upgrade or normalize an older dedicated StartupJobs CSV.
        if old_fields != CSV_FIELDS:
            temp_file = OUTPUT_FILE.with_name("startupjobs_basic_temp.csv")

            with temp_file.open(
                "w", newline="", encoding="utf-8-sig"
            ) as file:
                writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {field: row.get(field, "") for field in CSV_FIELDS}
                    )

            temp_file.replace(OUTPUT_FILE)
            self.logger.info("Normalized the existing StartupJobs CSV schema.")

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
                    self.current_search_index = int(
                        state.get("current_search_index", 0)
                    )
                    self.current_search_clicks = int(
                        state.get("current_search_clicks", 0)
                    )
                    self.total_load_more_clicks = int(
                        state.get("total_load_more_clicks", 0)
                    )
                    self.completed_searches = int(
                        state.get("completed_searches", 0)
                    )
                    self.new_jobs = int(state.get("new_jobs", 0))
                    self.duplicate_jobs = int(
                        state.get("duplicate_jobs", 0)
                    )
                    self.listings_seen_total = int(
                        state.get("listings_seen_total", 0)
                    )
                    self.next_long_break_at = int(
                        state.get(
                            "next_long_break_at",
                            (
                                (self.listings_seen_total // LONG_BREAK_EVERY)
                                + 1
                            )
                            * LONG_BREAK_EVERY,
                        )
                    )
                    self.visited_urls = list(
                        state.get("visited_urls", [])
                    )
                    self.resume_url = (
                        state.get("resume_url")
                        or self.search_urls[self.current_search_index][2]
                    )
                    self.resuming = True

                    self.logger.info(
                        "RESUME STATE FOUND | search=%s | "
                        "load-more clicks to replay=%s | resume=%s",
                        self.current_search_index + 1,
                        self.current_search_clicks,
                        self.resume_url,
                    )
                    return

            except Exception as exc:
                self.logger.warning(
                    "Could not load registry: %s. Starting a fresh run.",
                    exc,
                )

        self.reset_registry()

    def reset_registry(self):

        self.current_search_index = 0
        self.current_search_clicks = 0
        self.total_load_more_clicks = 0
        self.completed_searches = 0
        self.new_jobs = 0
        self.duplicate_jobs = 0
        self.listings_seen_total = 0
        self.next_long_break_at = LONG_BREAK_EVERY
        self.visited_urls = []
        self.resume_url = self.search_urls[0][2]
        self.resuming = False
        self.save_registry(resume_url=self.resume_url, completed=False)

    def save_registry(self, resume_url=None, completed=False):

        if resume_url is not None:
            self.resume_url = resume_url

        state = {
            "version": 2,
            "plan_signature": self.plan_signature,
            "completed": completed,
            "updated_at": self.current_mining_datetime(),
            "current_search_index": self.current_search_index,
            "current_search_clicks": self.current_search_clicks,
            "total_load_more_clicks": self.total_load_more_clicks,
            "completed_searches": self.completed_searches,
            "listings_seen_total": self.listings_seen_total,
            "next_long_break_at": self.next_long_break_at,
            "new_jobs": self.new_jobs,
            "duplicate_jobs": self.duplicate_jobs,
            "resume_url": None if completed else self.resume_url,
            "visited_urls": self.visited_urls,
        }

        temp_file = REGISTRY_FILE.with_suffix(".tmp")
        with temp_file.open("w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
        temp_file.replace(REGISTRY_FILE)

    def mark_url_visited(self, url):

        normalized = self.page_registry_key(url)
        known = {self.page_registry_key(item) for item in self.visited_urls}
        if normalized not in known:
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

    def make_request(self, url, *, rate_limit_retry=0):

        return scrapy.Request(
            url=url,
            callback=self.parse,
            errback=self.request_failed,
            headers={"Referer": "https://www.startupjobs.cz/"},
            meta={
                "handle_httpstatus_all": True,
                "rate_limit_retry": rate_limit_retry,
                "playwright": True,
                "playwright_context": "startupjobs",
                "playwright_include_page": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_load_state", "domcontentloaded"),
                ],
            },
            dont_filter=True,
        )

    # =================================================================
    # MAIN BROWSER PARSER
    # =================================================================

    async def parse(self, response):

        page = response.meta.get("playwright_page")
        if page is None:
            self.save_registry(resume_url=response.url)
            raise CloseSpider("playwright_page_missing_checkpoint_saved")

        try:
            async for item in self.parse_browser_page(response, page):
                yield item
        finally:
            if not page.is_closed():
                await page.close()

    async def parse_browser_page(self, response, page):

        label, description, _ = self.search_urls[self.current_search_index]

        # -------------------------------------------------------------
        # HTTP AND CHALLENGE HANDLING
        # -------------------------------------------------------------

        if response.status == 429:
            self.save_registry(resume_url=response.url)
            retries = int(response.meta.get("rate_limit_retry", 0))

            if retries >= MAX_RATE_LIMIT_RETRIES:
                raise CloseSpider("rate_limit_checkpoint_saved")

            delay = self.get_rate_limit_delay(response)
            self.logger.warning(
                "HTTP 429 | backing off for %.1f minutes", delay / 60
            )
            await page.close()
            await asyncio.sleep(delay)
            yield self.make_request(
                response.url, rate_limit_retry=retries + 1
            )
            return

        if response.status in {401, 403}:
            self.save_registry(resume_url=response.url)
            raise CloseSpider("access_denied_checkpoint_saved")

        if not 200 <= response.status < 400:
            self.save_registry(resume_url=response.url)
            raise CloseSpider("http_failure_checkpoint_saved")

        initial_delay = random.uniform(
            INITIAL_RENDER_DELAY_MIN, INITIAL_RENDER_DELAY_MAX
        )
        await page.wait_for_timeout(int(initial_delay * 1000))

        # CookieYes can display a full-page .cky-overlay after the page has
        # rendered. Dismiss it before attempting to hover/click the results.
        await self.dismiss_cookie_banner(page)

        initial_html = await page.content()
        if self.looks_like_challenge_text(initial_html):
            self.save_registry(resume_url=response.url)
            raise CloseSpider("challenge_checkpoint_saved")

        self.mark_url_visited(response.url)
        self.logger.info(
            "SEARCH %s | ACCESS_OK | %s | %s", label, description, response.url
        )

        # A browser restart begins with zero physical clicks. The registry
        # tells us how many successful clicks must be replayed to reconstruct
        # the interrupted page state.
        resume_target_clicks = self.current_search_clicks
        live_clicks = 0
        dom_seen_urls = set()

        await self.wait_for_initial_result_state(page)

        items, discovered = await self.process_current_dom(
            page, response.url, dom_seen_urls
        )
        for item in items:
            yield item

        if discovered == 0:
            html = await page.content()
            visible_text = self.clean_text(
                scrapy.Selector(text=html).xpath("//body//text()").getall()
            )
            if NO_RESULTS_TEXT_RE.search(visible_text):
                self.logger.info("SEARCH %s | explicit no-results state", label)
                next_request = await self.finish_current_search()
                if next_request:
                    yield next_request
                return

            self.save_registry(resume_url=response.url)
            self.logger.error(
                "No StartupJobs cards appeared and no explicit empty-results "
                "message was found. Checkpoint saved instead of accepting an "
                "unverified empty page."
            )
            raise CloseSpider("results_not_rendered_checkpoint_saved")

        # -------------------------------------------------------------
        # REPLAY SAVED CLICKS, THEN CONTINUE UNTIL BUTTON EXHAUSTION
        # -------------------------------------------------------------

        while True:
            if live_clicks >= MAX_LOAD_MORE_CLICKS:
                self.save_registry(resume_url=response.url)
                raise CloseSpider("load_more_safety_limit_checkpoint_saved")

            await self.dismiss_cookie_banner(page)
            button = await self.find_load_more_button(page)
            if button is None:
                if live_clicks < resume_target_clicks:
                    self.save_registry(resume_url=response.url)
                    self.logger.error(
                        "Could not replay saved click %s/%s because the load-more "
                        "button disappeared.",
                        live_clicks + 1,
                        resume_target_clicks,
                    )
                    raise CloseSpider("resume_replay_incomplete_checkpoint_saved")

                self.logger.info(
                    "SEARCH %s COMPLETE | %s load-more clicks", label, live_clicks
                )
                next_request = await self.finish_current_search()
                if next_request:
                    yield next_request
                return

            delay = random.uniform(LOAD_MORE_DELAY_MIN, LOAD_MORE_DELAY_MAX)

            if live_clicks < resume_target_clicks:
                self.logger.info(
                    "Replaying saved load-more click %s/%s after %.1f seconds...",
                    live_clicks + 1,
                    resume_target_clicks,
                    delay,
                )
            else:
                self.logger.info(
                    "Waiting %.1f seconds before load-more click %s...",
                    delay,
                    live_clicks + 1,
                )

            await asyncio.sleep(delay)
            await self.dismiss_cookie_banner(page)
            before_count = await self.unique_job_link_count(page)

            try:
                await button.scroll_into_view_if_needed(
                    timeout=BUTTON_ACTION_TIMEOUT_MS
                )
                await button.hover(timeout=BUTTON_ACTION_TIMEOUT_MS)
                await page.wait_for_timeout(random.randint(250, 850))
                await button.click(timeout=BUTTON_ACTION_TIMEOUT_MS)
            except Exception as exc:
                # A late CookieYes overlay was the observed failure mode.
                # Dismiss and retry once with a freshly resolved locator.
                if "cky-overlay" in str(exc):
                    dismissed = await self.dismiss_cookie_banner(page)
                    retry_button = await self.find_load_more_button(page)
                    if dismissed and retry_button is not None:
                        try:
                            await retry_button.scroll_into_view_if_needed(
                                timeout=BUTTON_ACTION_TIMEOUT_MS
                            )
                            await retry_button.click(
                                timeout=BUTTON_ACTION_TIMEOUT_MS
                            )
                        except Exception as retry_exc:
                            exc = retry_exc
                        else:
                            exc = None

                if exc is not None:
                    self.save_registry(resume_url=response.url)
                    self.logger.error("LOAD-MORE CLICK FAILED | %s", exc)
                    raise CloseSpider(
                        "load_more_click_failed_checkpoint_saved"
                    )

            grew = await self.wait_for_job_growth(page, before_count)
            if not grew:
                self.save_registry(resume_url=response.url)
                self.logger.error(
                    "Load-more click produced no additional unique job URL. "
                    "Checkpoint saved to avoid a silent loop."
                )
                raise CloseSpider("load_more_no_growth_checkpoint_saved")

            live_clicks += 1

            # Do not regress a more advanced saved checkpoint while replaying.
            if live_clicks >= resume_target_clicks:
                self.current_search_clicks = live_clicks

            if live_clicks > resume_target_clicks:
                self.total_load_more_clicks += 1

            items, _ = await self.process_current_dom(
                page, response.url, dom_seen_urls
            )
            for item in items:
                yield item

            self.save_registry(resume_url=response.url)

    # =================================================================
    # DOM PROCESSING
    # =================================================================

    async def wait_for_initial_result_state(self, page):

        deadline = asyncio.get_running_loop().time() + (
            INITIAL_RESULTS_TIMEOUT_MS / 1000
        )

        while asyncio.get_running_loop().time() < deadline:
            if await self.unique_job_link_count(page) > 0:
                return

            content = await page.content()
            visible_text = self.clean_text(
                scrapy.Selector(text=content).xpath("//body//text()").getall()
            )
            if NO_RESULTS_TEXT_RE.search(visible_text):
                return

            await page.wait_for_timeout(500)

    async def process_current_dom(self, page, base_url, dom_seen_urls):

        selector = scrapy.Selector(text=await page.content(), type="html")
        anchors = selector.css("a[href*='/nabidka/']")
        items = []
        discovered = 0
        corrected_rows = 0

        for anchor in anchors:
            href = (anchor.attrib.get("href") or "").strip()
            job_url = self.canonicalize_job_url(
                self.absolute_url(base_url, href)
            )

            if not self.is_job_url(job_url) or job_url in dom_seen_urls:
                continue

            dom_seen_urls.add(job_url)
            discovered += 1
            self.listings_seen_total += 1

            card = self.find_card_container(anchor)
            company = self.extract_company(anchor, card)
            title = self.extract_title(anchor, card, job_url, company)

            if job_url in self.known_urls:
                self.duplicate_jobs += 1
                if self.repair_existing_row(job_url, title, company):
                    corrected_rows += 1
            else:
                self.known_urls.add(job_url)

                item = {
                    "ID": self.generate_job_id(job_url),
                    "Job Title": title,
                    "Company": company,
                    "Location": "Prague",
                    "Salary": "",
                    "URL": job_url,
                    "Mining_Date": self.current_mining_datetime(),
                }

                self.append_to_csv(item)
                self.csv_rows.append(dict(item))
                self.rows_by_url[job_url] = self.csv_rows[-1]
                self.new_jobs += 1
                items.append(item)

                self.logger.info(
                    "NEW JOB | %s | %s | %s",
                    item["ID"],
                    title,
                    company,
                )

            if self.listings_seen_total % REGISTRY_CHECKPOINT_EVERY == 0:
                self.save_registry(resume_url=base_url)

            if self.listings_seen_total >= self.next_long_break_at:
                await self.take_long_break(base_url)

        if corrected_rows:
            self.rewrite_csv()
            self.logger.info(
                "Repaired %s existing CSV row(s) using the current DOM.",
                corrected_rows,
            )

        self.logger.info(
            "DOM SNAPSHOT | %s newly encountered cards | %s unique cards visible",
            discovered,
            len(dom_seen_urls),
        )
        return items, discovered

    @classmethod
    def find_card_container(cls, anchor):

        # On the current StartupJobs layout, the vacancy link wraps the whole
        # card. Detect that first so a higher list container cannot leak data
        # from a preceding vacancy.
        leaf_values = {
            cls.clean_text([text])
            for text in anchor.xpath(".//*[not(*)]/text()").getall()
            if cls.clean_text([text])
        }
        if len(leaf_values) >= 2:
            return anchor

        # Accept an ancestor only when it contains this one vacancy link.
        candidates = [
            anchor.xpath("ancestor::article[1]"),
            anchor.xpath("ancestor::*[@data-testid='job-card'][1]"),
            anchor.xpath("ancestor::li[1]"),
        ]

        for candidate in candidates:
            if candidate and cls.job_link_count(candidate[0]) == 1:
                return candidate[0]

        for position in range(1, 7):
            divs = anchor.xpath(f"ancestor::div[{position}]")
            if not divs:
                continue
            div = divs[0]
            text = cls.clean_text(div.xpath(".//text()").getall())
            if 10 <= len(text) <= 1_500 and cls.job_link_count(div) == 1:
                return div

        return anchor

    @classmethod
    def job_link_count(cls, container):

        return sum(
            1
            for href in container.css("a[href*='/nabidka/']::attr(href)").getall()
            if JOB_PATH_RE.match(urlparse(href).path)
        )

    @classmethod
    def extract_title(cls, anchor, card, job_url, company=""):

        selectors = [
            card.css("[data-testid*='title'] ::text").getall(),
            card.css("[data-test*='title'] ::text").getall(),
        ]

        for parts in selectors:
            value = cls.clean_text(parts)
            if value:
                return cls.normalize_job_title(value, company)

        # Prefer the first heading rather than joining title and company if
        # a card happens to render both as headings.
        for heading in anchor.xpath(
            ".//*[self::h1 or self::h2 or self::h3 or self::h4]"
        ):
            value = cls.clean_text(heading.xpath(".//text()").getall())
            if value and value.casefold() != company.casefold():
                return cls.normalize_job_title(value, company)

        for heading in card.xpath(
            ".//*[self::h1 or self::h2 or self::h3 or self::h4]"
        ):
            value = cls.clean_text(heading.xpath(".//text()").getall())
            if value and value.casefold() != company.casefold():
                return cls.normalize_job_title(value, company)

        aria_label = cls.clean_text(
            [anchor.attrib.get("aria-label", ""), anchor.attrib.get("title", "")]
        )
        if aria_label:
            return cls.normalize_job_title(aria_label, company)

        anchor_text = cls.clean_text(anchor.xpath(".//text()").getall())
        if anchor_text:
            normalized = cls.normalize_job_title(anchor_text, company)
            if normalized:
                return normalized

        # Last-resort readable title from the vacancy URL slug.
        path_parts = [part for part in urlparse(job_url).path.split("/") if part]
        if len(path_parts) >= 3:
            return unquote(path_parts[2]).replace("-", " ").strip().title()

        return ""

    @classmethod
    def normalize_job_title(cls, value, company=""):

        value = cls.clean_text([value])

        if company and value.casefold().startswith(company.casefold()):
            value = value[len(company):].lstrip(" -–—|:")

        metadata_patterns = (
            r"\s+HOT(?:\s|$)",
            r"\s+\d[\d\s.,]*\s*[-–]\s*\d[\d\s.,]*\s*(?:Kč|CZK|€)",
            r"\s+(?:Hybrid|Onsite|On-site)(?=\s*,|\s+Remote|\s+Praha|"
            r"\s+Brno|\s+Bratislava|\s+Full-time|\s+Part-time)",
            r"\s+Remote(?=\s+Praha|\s+Brno|\s+Bratislava|\s+Full-time|"
            r"\s+Part-time)",
            r"\s+(?:Full-time|Part-time|Internship)(?:\s|,|$)",
        )

        cut_positions = []
        for pattern in metadata_patterns:
            match = re.search(pattern, value, re.IGNORECASE)
            if match:
                cut_positions.append(match.start())

        if cut_positions:
            value = value[:min(cut_positions)]

        # A decorative leading hash is rendered separately on some cards but
        # becomes text when the whole anchor is read.
        value = re.sub(r"^#\s*", "", value)

        return value.strip(" -–—|,")

    @classmethod
    def extract_company(cls, anchor, card):

        selectors = [
            card.css("[data-testid*='company'] ::text").getall(),
            card.css("[data-test*='company'] ::text").getall(),
            card.css("[itemprop='hiringOrganization'] ::text").getall(),
            card.css("a[href*='/firma/'] ::text").getall(),
            card.css("a[href*='/firmy/'] ::text").getall(),
            card.css("a[href*='/company/'] ::text").getall(),
            card.xpath(
                ".//*[contains(translate(@class, "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                "'company') or contains(translate(@class, "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                "'firma')]//text()"
            ).getall(),
        ]

        for parts in selectors:
            value = cls.clean_text(parts)
            if value:
                return value

        # Company logos commonly expose the company name through alt text.
        for alt in card.css("img::attr(alt)").getall():
            value = cls.clean_text([alt])
            value = re.sub(
                r"(?:\s+[-–|]?\s*)?(?:logo|logotyp|obrázek|image)$",
                "",
                value,
                flags=re.IGNORECASE,
            ).strip()
            if value:
                return value

        # Resilient fallback for a changed Tailwind class name: inspect short
        # leaf text nodes near the title and reject known metadata/UI labels.
        ignored_re = re.compile(
            r"^(?:praha|prague|remote|on-site|onsite|hybrid|"
            r"full[- ]?time|part[- ]?time|freelance|stáž|internship|"
            r"junior(?:ní)?|medior(?:ní)?|senior(?:ní)?|"
            r"hot|nové|new|uložit|save|aktualizováno.*|přidáno.*|"
            r"\d+\s*(?:tis\.?|k|czk|kč).*)$",
            re.IGNORECASE,
        )

        for raw in card.xpath(
            ".//*[self::p or self::span or self::div][not(*)]/text()"
        ).getall():
            value = cls.clean_text([raw])
            if not value:
                continue
            if ignored_re.match(value):
                continue
            if not 2 <= len(value) <= 120:
                continue
            if len(value.split()) > 10:
                continue
            return value

        return ""

    def repair_existing_row(self, job_url, title, company):

        row = self.rows_by_url.get(job_url)
        if row is None:
            return False

        changed = False
        replacements = {
            "Job Title": title,
            "Company": company,
            "Location": "Prague",
            "Salary": "",
            "URL": job_url,
        }

        for field, value in replacements.items():
            if field in {"Job Title", "Company"} and not value:
                continue
            if row.get(field, "") != value:
                row[field] = value
                changed = True

        return changed

    def rewrite_csv(self):

        temp_file = OUTPUT_FILE.with_name("startupjobs_basic_rewrite.csv")
        with temp_file.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(self.csv_rows)
        temp_file.replace(OUTPUT_FILE)

    # =================================================================
    # LOAD-MORE BUTTON CONTROL
    # =================================================================

    async def dismiss_cookie_banner(self, page):

        # Once a consent choice has succeeded in this browser context, do not
        # repeatedly scan/click CookieYes controls before every load-more step.
        if self.cookie_consent_handled:
            return True

        # The banner is injected asynchronously. If it is not visible yet,
        # leave the flag unset so a later pre-click check can still catch it.
        if not await self.cookie_overlay_visible(page):
            return True

        selectors = (
            "#cky-btn-reject",
            ".cky-btn-reject",
            "button[data-cky-tag='reject-button']",
            "button[aria-label*='Reject']",
            "button[aria-label*='Odmítnout']",
            "#cky-btn-accept",
            ".cky-btn-accept",
            "button[data-cky-tag='accept-button']",
            ".cky-btn-close",
        )

        for selector in selectors:
            candidates = page.locator(selector)
            for index in range(await candidates.count()):
                candidate = candidates.nth(index)
                try:
                    if await candidate.is_visible():
                        label = self.clean_text([await candidate.inner_text()])
                        await candidate.click(force=True, timeout=5_000)
                        try:
                            await page.locator(".cky-overlay").wait_for(
                                state="hidden", timeout=5_000
                            )
                        except Exception:
                            pass
                        self.logger.info(
                            "COOKIE CONSENT DISMISSED | %s",
                            label or selector,
                        )
                        self.cookie_consent_handled = True
                        return True
                except Exception:
                    continue

        return not await self.cookie_overlay_visible(page)

    @staticmethod
    async def cookie_overlay_visible(page):

        overlays = page.locator(".cky-overlay")
        for index in range(await overlays.count()):
            try:
                if await overlays.nth(index).is_visible():
                    return True
            except Exception:
                continue
        return False

    async def find_load_more_button(self, page):

        candidates = page.locator(LOAD_MORE_BUTTON_SELECTOR)
        visible = []

        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                if await candidate.is_visible() and await candidate.is_enabled():
                    visible.append(candidate)
                    text = self.clean_text([await candidate.inner_text()])
                    if LOAD_MORE_TEXT_RE.search(text):
                        return candidate
            except Exception:
                continue

        # The supplied class is unique on the results page. This fallback
        # keeps the spider working if StartupJobs changes only the button copy.
        if len(visible) == 1:
            return visible[0]

        return None

    async def unique_job_link_count(self, page):

        hrefs = await page.locator("a[href*='/nabidka/']").evaluate_all(
            "els => els.map(el => el.href)"
        )
        return len(
            {
                self.canonicalize_job_url(href)
                for href in hrefs
                if self.is_job_url(href)
            }
        )

    async def wait_for_job_growth(self, page, before_count):

        deadline = asyncio.get_running_loop().time() + (
            LOAD_MORE_RESULT_TIMEOUT_MS / 1000
        )

        while asyncio.get_running_loop().time() < deadline:
            if await self.unique_job_link_count(page) > before_count:
                return True
            await page.wait_for_timeout(500)

        return False

    # =================================================================
    # SEARCH COMPLETION AND LONG BREAK
    # =================================================================

    async def finish_current_search(self):

        self.completed_searches += 1
        next_index = self.current_search_index + 1

        if next_index >= len(self.search_urls):
            self.save_registry(resume_url=None, completed=True)
            self.logger.info("ALL STARTUPJOBS SEARCHES COMPLETE")
            return None

        next_label, next_description, next_url = self.search_urls[next_index]
        self.current_search_index = next_index
        self.current_search_clicks = 0
        self.save_registry(resume_url=next_url)

        delay = random.uniform(MAIN_URL_DELAY_MIN, MAIN_URL_DELAY_MAX)
        self.logger.info(
            "Waiting %.1f seconds before search %s | %s",
            delay,
            next_label,
            next_description,
        )
        await asyncio.sleep(delay)
        return self.make_request(next_url)

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

    # =================================================================
    # URL, ID, TEXT, AND FILE HELPERS
    # =================================================================

    @staticmethod
    def page_registry_key(url):

        parsed = urlparse(url)
        query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
        query_pairs = [
            (key, value)
            for key, value in query_pairs
            if key.lower() not in {"utm_source", "utm_medium", "utm_campaign"}
        ]
        query_pairs.sort()

        return urlunparse(
            (
                (parsed.scheme or "https").lower(),
                parsed.netloc.lower(),
                parsed.path.rstrip("/") or "/",
                "",
                urlencode(query_pairs, doseq=True),
                "",
            )
        )

    @staticmethod
    def absolute_url(base_url, href):

        return urljoin(base_url, href)

    @staticmethod
    def is_job_url(url):

        if not url:
            return False
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        return (
            (hostname == "startupjobs.cz" or hostname.endswith(".startupjobs.cz"))
            and JOB_PATH_RE.match(parsed.path) is not None
        )

    @staticmethod
    def canonicalize_job_url(url):

        if not url:
            return ""

        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if hostname == "startupjobs.cz" or hostname.endswith(".startupjobs.cz"):
            return urlunparse(
                (
                    "https",
                    "www.startupjobs.cz",
                    parsed.path.rstrip("/"),
                    "",
                    "",
                    "",
                )
            )

        return url

    @staticmethod
    def append_to_csv(item):

        with OUTPUT_FILE.open("a", newline="", encoding="utf-8") as file:
            csv.DictWriter(file, fieldnames=CSV_FIELDS).writerow(item)

    def generate_job_id(self, job_url):

        match = JOB_PATH_RE.match(urlparse(job_url).path)
        if match:
            job_id = f"SJ_{match.group('site_id')}"
            if job_id not in self.generated_ids:
                self.generated_ids.add(job_id)
                return job_id

        # Only used for a malformed/non-numeric future vacancy path or a
        # historical ID collision.
        while True:
            job_id = f"SJ_{random.randint(0, 999999):06d}"
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

        sample = text[:50_000].lower()
        indicators = (
            "verify you are human",
            "checking your browser",
            "attention required",
            "unusual traffic",
            "security check",
            "captcha",
        )
        return any(indicator in sample for indicator in indicators)

    async def request_failed(self, failure):

        url = failure.request.url
        page = failure.request.meta.get("playwright_page")
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
            "%s duplicates skipped | %s load-more clicks | "
            "%s/%s searches completed | reason=%s",
            self.listings_seen_total,
            self.new_jobs,
            self.duplicate_jobs,
            self.total_load_more_clicks,
            self.completed_searches,
            len(self.search_urls),
            reason,
        )
        self.logger.info("CSV: %s", OUTPUT_FILE)
        self.logger.info("Registry: %s", REGISTRY_FILE)
