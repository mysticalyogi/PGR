"""Fetch strategies for the rack price page.

Each strategy is tried at most once per run. There are no retry loops, no
fingerprint spoofing and no CAPTCHA handling: a blocked response is reported
as blocked. The hosted-service strategy is only used when its API key is
present in the environment (an Actions secret), and the key is never logged.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import requests

SOURCE_URL = "https://www.petro-canada.ca/en/business/rack-prices"
TIMEOUT = 60
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0 Safari/537.36 pg-rack-tracker (+https://github.com/zyujia-crypto/PGR)"
)

_BLOCK_MARKERS = re.compile(
    r"access denied|captcha|are you a robot|request unsuccessful|incapsula|"
    r"just a moment\.\.\.|cf-chl|attention required|pardon our interruption",
    re.I,
)


class FetchFailed(Exception):
    def __init__(self, method: str, reason: str, status: int | None = None, body: str = ""):
        super().__init__(f"{method}: {reason}")
        self.method = method
        self.reason = reason
        self.status = status
        self.body = body


class FetchBlocked(FetchFailed):
    pass


@dataclass
class FetchResult:
    html: str
    method: str
    status: int


def _check(method: str, status: int, body: str) -> FetchResult:
    if status in (401, 403, 429) or (status == 503 and _BLOCK_MARKERS.search(body)):
        raise FetchBlocked(method, f"HTTP {status} (blocked by site protection)", status, body)
    if status != 200:
        raise FetchFailed(method, f"HTTP {status}", status, body)
    if _BLOCK_MARKERS.search(body[:20000]) and "Prince George" not in body:
        raise FetchBlocked(method, "challenge/block page returned with HTTP 200", status, body)
    if "Prince George" not in body or not re.search(r"\bDaily\b", body):
        raise FetchFailed(method, "page lacks Daily table markers (incomplete or JS-rendered)", status, body)
    return FetchResult(body, method, status)


def fetch_direct(url: str = SOURCE_URL) -> FetchResult:
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-CA,en;q=0.8"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        raise FetchFailed("direct", f"network error: {type(e).__name__}") from None
    return _check("direct", r.status_code, r.text)


def fetch_browser(url: str = SOURCE_URL) -> FetchResult:
    try:
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise FetchFailed("browser", "playwright is not installed") from None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(locale="en-CA")
                resp = page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT * 1000)
                status = resp.status if resp else 0
                if status == 200:
                    try:
                        page.wait_for_selector("text=Prince George, BC", timeout=20000)
                    except PWError:
                        pass  # _check reports the missing marker
                body = page.content()
            finally:
                browser.close()
    except FetchFailed:
        raise
    except Exception as e:
        raise FetchFailed("browser", f"browser error: {type(e).__name__}: {str(e)[:200]}") from None
    return _check("browser", status, body)


_SERVICES = {
    # provider: (endpoint, key param, extra params)
    "scrapingbee": (
        "https://app.scrapingbee.com/api/v1/",
        "api_key",
        {"render_js": "true", "premium_proxy": "true", "country_code": "ca", "wait_for": "table"},
    ),
    "zenrows": (
        "https://api.zenrows.com/v1/",
        "apikey",
        {"js_render": "true", "premium_proxy": "true", "proxy_country": "ca", "wait_for": "table"},
    ),
}


def service_configured() -> bool:
    return bool(os.environ.get("SCRAPER_API_KEY"))


def fetch_service(url: str = SOURCE_URL) -> FetchResult:
    key = os.environ.get("SCRAPER_API_KEY", "")
    provider = os.environ.get("SCRAPER_PROVIDER", "scrapingbee").strip().lower()
    if not key:
        raise FetchFailed("service", "SCRAPER_API_KEY not configured")
    if provider not in _SERVICES:
        raise FetchFailed("service", f"unknown SCRAPER_PROVIDER {provider!r}")
    endpoint, key_param, extra = _SERVICES[provider]
    method = f"service:{provider}"
    try:
        r = requests.get(endpoint, params={key_param: key, "url": url, **extra}, timeout=TIMEOUT * 3)
    except requests.RequestException as e:
        # Do not include the exception text: it can contain the request URL with the key.
        raise FetchFailed(method, f"network error: {type(e).__name__}") from None
    if r.status_code in (401, 402, 403) and "petro-canada" not in r.text.lower():
        raise FetchFailed(method, f"service rejected request (HTTP {r.status_code}); check key/credits", r.status_code)
    return _check(method, r.status_code, r.text)


def fetch(url: str = SOURCE_URL, methods: list[str] | None = None) -> tuple[FetchResult, list[FetchFailed]]:
    """Try strategies in order; return the first success and the failures before it."""
    order = methods or ["direct", "browser"] + (["service"] if service_configured() else [])
    funcs = {"direct": fetch_direct, "browser": fetch_browser, "service": fetch_service}
    failures: list[FetchFailed] = []
    for m in order:
        try:
            return funcs[m](url), failures
        except FetchFailed as e:
            failures.append(e)
    summary = "; ".join(str(f) for f in failures)
    last = failures[-1]
    cls = FetchBlocked if all(isinstance(f, FetchBlocked) for f in failures) else FetchFailed
    raise cls("all", summary, last.status, last.body)
