"""Polite HTTP access to one site: request spacing, retries with backoff, Retry-After."""

import asyncio
import logging
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger(__name__)

# Plain clients announce themselves as python-httpx/*; many shops answer 403. Mimic a desktop browser.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 120.0


class ScrapeError(Exception):
    """The listing could not be read completely. The run must not touch stored stock state."""


class SiteHttp:
    """Wraps one AsyncClient per site. Every request to the site goes through `get`."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        request_delay: float = 1.5,
        max_retries: int = 2,
        retry_backoff: float = 5.0,
    ) -> None:
        self.client = client
        self.request_delay = request_delay
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self._lock = asyncio.Lock()
        self._last_request = float("-inf")

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self._last_request + self.request_delay - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """GET with spacing and retries. Returns any final response (the caller judges the status);
        raises ScrapeError only when the request itself keeps failing."""
        for attempt in range(self.max_retries + 1):
            await self._throttle()
            last = attempt == self.max_retries
            try:
                response = await self.client.get(url, **kwargs)
            except httpx.HTTPError as exc:
                if last:
                    raise ScrapeError(f"Request failed for {url}: {exc!r}") from exc
                delay = self.retry_backoff * 2**attempt
                log.warning("Request error for %s (%r); retrying in %.0fs", url, exc, delay)
            else:
                if response.status_code not in RETRYABLE_STATUS or last:
                    return response
                delay = _retry_after(response)
                if delay is None:
                    delay = self.retry_backoff * 2**attempt
                log.warning("HTTP %s for %s; retrying in %.0fs", response.status_code, url, delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        await self.client.aclose()


def _retry_after(response: httpx.Response) -> float | None:
    """Retry-After as seconds, clamped to [0, MAX_RETRY_AFTER]; it may be seconds or an HTTP-date."""
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return max(0.0, min(seconds, MAX_RETRY_AFTER))  # this order also maps NaN to 0


def make_site_http(adapter) -> SiteHttp:
    client = httpx.AsyncClient(
        headers=adapter.headers,
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
        http2=False,
    )
    return SiteHttp(
        client,
        request_delay=adapter.request_delay,
        max_retries=adapter.max_retries,
        retry_backoff=adapter.retry_backoff,
    )
