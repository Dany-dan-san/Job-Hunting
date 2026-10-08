"""Human-paced JobStack.it listing spider with crash-safe resumption.

The spider mines JobStack search-result cards only; it does not open every job
detail page. It follows the site's conventional ``page=2`` pagination until
the final page and writes every new row to disk immediately.

Run from the Scrapy project root:

    scrapy crawl jobstack

Optional custom JobStack search URL:

    scrapy crawl jobstack -a url="https://www.jobstack.it/it-jobs?..."
"""

from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
import asyncio
import csv
import hashlib
import json
import random

import scrapy
from scrapy.exceptions import CloseSpider


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

OUTPUT_FILE = DATA_DIR / "jobstack_basic.csv"
REGISTRY_FILE = REGISTRY_DIR / "jobstack_registry.json"

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
# HUMAN-PACED TIMING AND CHECKPOINTS
# =====================================================================

# Delay between consecutive JobStack result pages.
PAGE_DELAY_MIN = 5.0
PAGE_DELAY_MAX = 15.0

# Delay between separate main search URLs.
MAIN_URL_DELAY_MIN = 10.0
MAIN_URL_DELAY_MAX = 20.0

# Every 500 inspected listings, rest for 5–10 minutes.
LONG_BREAK_EVERY = 500
LONG_BREAK_MIN = 5 * 60
LONG_BREAK_MAX = 10 * 60

# Persist crawl state within large result pages too.
REGISTRY_CHECKPOINT_EVERY = 25

# Long explicit response to a rate-limit signal.
RATE_LIMIT_DELAY_MIN = 10 * 60
RATE_LIMIT_DELAY_MAX = 20 * 60
MAX_RATE_LIMIT_RETRIES = 1

# Defensive protection if the website produces a pagination loop.
MAX_PAGES_PER_SEARCH = 500


# =====================================================================
# REUSABLE XPATH CLASS-TOKEN EXPRESSIONS
# =====================================================================


def has_class(class_name):
    """Return an XPath predicate matching one complete HTML class token."""

    return (
        "contains(concat(' ', normalize-space(@class), ' '), "
        f"' {class_name} ')"
    )


JOB_CARD_XPATH = f"//li[{has_class('jobposts-item')}]"

MAIN_LINK_XPATH = (
    f"./a[{has_class('jobpost-mainlink')}]"
    f" | .//a[{has_class('jobpost-mainlink')}][1]"
)

TEXT_CONTAINER_XPATH = (
    f".//div[{has_class('jobposts-item_text')}][1]"
)

SENIORITY_XPATH = (
    ".//span["
    f"{has_class('custom-profile-label--primary')} and "
    f"{has_class('custom-profile-label--dotted')} and "
    f"{has_class('custom-profile-label--lower')}"
    "]"
)

# The outer company label varies between secondary/warning styles, so the
# stable discriminator is its descendant building icon.
COMPANY_TEXT_XPATH = (
    ".//span["
    ".//span[contains(concat(' ', normalize-space(@class), ' '), "
    "' icon--cp-building')]"
    "]/span[not(@class)]//text()"
)

# The location label is reliably identified by its pin icon.
LOCATION_LABEL_XPATH = (
    ".//span["
    ".//span[contains(concat(' ', normalize-space(@class), ' '), "
    "' icon--cp-pin')]"
    "]"
)

SALARY_XPATH = (
    f".//span[{has_class('jobposts-item_salary')}][1]//text()"
)


# =====================================================================
# SPIDER
# =====================================================================


class JobStackSpider(scrapy.Spider):

    name = "jobstack"

    allowed_domains = [
        "jobstack.it",
        "www.jobstack.it",
    ]

    SEARCH_URLS = [
        (
            "A",
            "All Prague IT jobs",
            "https://www.jobstack.it/it-jobs?location=Praha&isDetail=0",
        ),
        (
            "B",
            "Prague IT jobs - position type 1",
            "https://www.jobstack.it/it-jobs?positiontype=1&location=Praha&isDetail=0",
        ),
        (
            "C",
            "Prague IT jobs - position type 79",
            "https://www.jobstack.it/it-jobs?positiontype=79&location=Praha&isDetail=0",
        ),
    ]

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
            "Accept-Language": "cs-CZ,cs;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "max-age=0",
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
        "DOWNLOAD_TIMEOUT": 30,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 2,
        # HTTP 429 receives the longer explicit backoff below.
        "RETRY_HTTP_CODES": [408, 500, 502, 503, 504, 522, 524],
        "ROBOTSTXT_OBEY": True,
        "LOG_LEVEL": "INFO",
    }

    # =================================================================
    # INITIALIZATION
    # =================================================================

    def __init__(self, url=None, *args, **kwargs):

        super().__init__(*args, **kwargs)

        if url:
            self.validate_jobstack_search_url(url)
            self.search_urls = [
                ("CUSTOM", "Custom JobStack search", url),
            ]
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
        self.listings_seen_total = 0
        self.next_long_break_at = LONG_BREAK_EVERY

        self.visited_urls = []
        self.visited_page_keys = set()

        self.resume_url = self.search_urls[0][2]
        self.resuming = False

        self.plan_signature = self.make_plan_signature()
        self.load_or_create_registry()

    # =================================================================
    # SEARCH PLAN AND URL VALIDATION
    # =================================================================

    def make_plan_signature(self):

        raw = "\n".join(url for _, _, url in self.search_urls)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def validate_jobstack_search_url(url):

        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if parsed.scheme not in {"http", "https"}:
            raise CloseSpider("URL must begin with http:// or https://")

        if hostname != "jobstack.it" and not hostname.endswith(".jobstack.it"):
            raise CloseSpider("This spider only accepts jobstack.it URLs.")

        if not parsed.path.rstrip("/").endswith("/it-jobs"):
            raise CloseSpider("The custom URL must be a JobStack /it-jobs search.")

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
            existing_id = (row.get("ID") or "").strip()
            if existing_id:
                self.generated_ids.add(existing_id)

            existing_url = (row.get("URL") or "").strip()
            if existing_url:
                self.known_urls.add(self.canonicalize_job_url(existing_url))

        self.logger.info(
            "Loaded %s existing JobStack URLs from %s",
            len(self.known_urls),
            OUTPUT_FILE,
        )

        # Upgrade a prior seven-column JobStack CSV by adding Seniority.
        if old_fields != CSV_FIELDS:
            temp_file = OUTPUT_FILE.with_name("jobstack_basic_temp.csv")

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
            self.logger.info("Normalized the existing JobStack CSV schema.")

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
                    self.current_search_page = int(
                        state.get("current_search_page", 0)
                    )
                    self.total_pages = int(state.get("total_pages", 0))
                    self.completed_searches = int(
                        state.get("completed_searches", 0)
                    )
                    self.new_jobs = int(state.get("new_jobs", 0))
                    self.duplicate_jobs = int(state.get("duplicate_jobs", 0))
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
                        "RESUME STATE FOUND | search=%s | completed pages=%s | "
                        "resume=%s",
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

    def make_request(self, url, *, rate_limit_retry=0):

        return scrapy.Request(
            url=url,
            callback=self.parse,
            errback=self.request_failed,
            headers={"Referer": "https://www.jobstack.it/it-jobs"},
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

        label, _, _ = self.search_urls[self.current_search_index]
        page_number = self.current_search_page + 1

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

        if self.looks_like_challenge(response):
            self.save_registry(resume_url=response.url)
            raise CloseSpider("challenge_checkpoint_saved")

        self.logger.info(
            "SEARCH %s | PAGE %s | ACCESS_OK | %s",
            label,
            page_number,
            response.url,
        )

        # -------------------------------------------------------------
        # FIND AND PROCESS LISTINGS
        # -------------------------------------------------------------

        cards = response.xpath(JOB_CARD_XPATH)
        self.logger.info(
            "SEARCH %s | PAGE %s | Found %s listings",
            label,
            page_number,
            len(cards),
        )

        if not cards:
            visible_text = self.clean_text(response.xpath("//body//text()").getall())
            if self.is_explicit_no_results(visible_text):
                self.logger.info("SEARCH %s | explicit no-results page", label)
                self.completed_searches += 1
                next_request = await self.move_to_next_main_search()
                if next_request:
                    yield next_request
                return

            self.save_registry(resume_url=response.url)
            raise CloseSpider("job_cards_missing_checkpoint_saved")

        for card in cards:
            self.listings_seen_total += 1

            link = card.xpath(MAIN_LINK_XPATH)
            href = link.xpath("./@href").get() if link else None

            if href:
                job_url = self.canonicalize_job_url(urljoin(response.url, href))

                if job_url in self.known_urls:
                    self.duplicate_jobs += 1
                else:
                    self.known_urls.add(job_url)
                    item = self.extract_item(card, link[0], job_url)
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

        # -------------------------------------------------------------
        # PAGE COMPLETION AND PAGINATION
        # -------------------------------------------------------------

        self.current_search_page = page_number
        self.total_pages += 1
        self.mark_page_visited(response.url)

        next_href = response.xpath("//head/link[@rel='next']/@href").get()

        if not next_href:
            next_href = response.xpath(
                "//li[@id='page_next' and "
                "not(contains(concat(' ', normalize-space(@class), ' '), "
                "' disabled '))]/a[@href]/@href"
            ).get()

        if next_href:
            next_url = urljoin(response.url, next_href)
            self.validate_jobstack_search_url(next_url)
            next_key = self.page_registry_key(next_url)

            if next_key in self.visited_page_keys:
                self.logger.warning(
                    "Next page was already visited. Ending this search to "
                    "avoid a pagination loop."
                )
            elif self.current_search_page >= MAX_PAGES_PER_SEARCH:
                self.save_registry(resume_url=next_url)
                raise CloseSpider("pagination_safety_limit_checkpoint_saved")
            else:
                # Save the exact next position before waiting/requesting.
                self.save_registry(resume_url=next_url)
                delay = random.uniform(PAGE_DELAY_MIN, PAGE_DELAY_MAX)
                self.logger.info(
                    "PAGE %s complete | waiting %.1f seconds before next page...",
                    page_number,
                    delay,
                )
                await asyncio.sleep(delay)
                yield self.make_request(next_url)
                return

        self.logger.info("SEARCH %s | final page reached", label)
        self.completed_searches += 1
        next_request = await self.move_to_next_main_search()
        if next_request:
            yield next_request

    # =================================================================
    # CARD EXTRACTION
    # =================================================================

    def extract_item(self, card, link, job_url):

        text_container = link.xpath(TEXT_CONTAINER_XPATH)
        scope = text_container[0] if text_container else link

        title = self.clean_text(scope.xpath(".//h3[1]//text()").getall())

        company = self.clean_text(scope.xpath(COMPANY_TEXT_XPATH).getall())
        if not company:
            company = self.clean_text(
                link.xpath(
                    f".//div[{has_class('jobposts-item_image--logo')}]"
                    "//img[1]/@alt"
                ).getall()
            )

        locations = []
        for label in scope.xpath(LOCATION_LABEL_XPATH):
            value = self.clean_text(label.xpath("./span[not(@class)]//text()").getall())
            if value and value not in locations:
                locations.append(value)

        seniorities = []
        for seniority in scope.xpath(SENIORITY_XPATH):
            value = self.clean_text(seniority.xpath(".//text()").getall())
            if value and value not in seniorities:
                seniorities.append(value)

        salary = self.clean_text(scope.xpath(SALARY_XPATH).getall())

        return {
            "ID": self.generate_job_id(),
            "Job Title": title,
            "Company": company,
            "Location": "; ".join(locations),
            "Salary": salary,
            "Seniority": ", ".join(seniorities),
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

    async def move_to_next_main_search(self):

        next_index = self.current_search_index + 1

        if next_index >= len(self.search_urls):
            self.save_registry(resume_url=None, completed=True)
            self.logger.info("ALL JOBSTACK SEARCHES COMPLETE")
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
        return self.make_request(next_url)

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
    def canonicalize_job_url(url):

        if not url:
            return ""

        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        if hostname == "jobstack.it" or hostname.endswith(".jobstack.it"):
            return urlunparse(
                (
                    "https",
                    "www.jobstack.it",
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

    def generate_job_id(self):

        if len(self.generated_ids) >= 10_000:
            raise CloseSpider("All possible JST_XXXX IDs have been exhausted.")

        while True:
            job_id = f"JST_{random.randint(0, 9999):04d}"
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
            "nebyly nalezeny žádné nabídky",
            "nenalezeny žádné nabídky",
            "žádné pracovní nabídky",
            "no jobs found",
        )
        return any(indicator in lowered for indicator in indicators)

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
    def looks_like_challenge(response):

        sample = response.text[:50_000].lower()
        indicators = (
            "verify you are human",
            "checking your browser",
            "attention required",
            "unusual traffic",
            "security check",
            "complete the captcha",
            "captcha challenge",
        )
        return any(indicator in sample for indicator in indicators)

    def request_failed(self, failure):

        url = failure.request.url
        self.save_registry(resume_url=url)
        self.logger.error("REQUEST FAILED | %s | %s", url, failure.value)
        raise CloseSpider("network_failure_checkpoint_saved")

    def closed(self, reason):

        if reason != "finished":
            self.save_registry(resume_url=self.resume_url, completed=False)

        self.logger.info(
            "MINING STOPPED | %s listings examined | %s new jobs | "
            "%s duplicates skipped | %s pages completed | "
            "%s/%s searches completed | reason=%s",
            self.listings_seen_total,
            self.new_jobs,
            self.duplicate_jobs,
            self.total_pages,
            self.completed_searches,
            len(self.search_urls),
            reason,
        )
        self.logger.info("CSV: %s", OUTPUT_FILE)
        self.logger.info("Registry: %s", REGISTRY_FILE)
