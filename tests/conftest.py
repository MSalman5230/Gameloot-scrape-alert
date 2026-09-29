import asyncio
from collections import defaultdict

import pytest

from stockwatch.core.engine import Engine
from stockwatch.models import ScrapedItem
from stockwatch.sites.base import CategoryDef, ScrapeError, SiteAdapter
from stockwatch.storage import MemoryStore


class RecordingNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.messages: list[str] = []
        self.fail = fail

    async def send(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("telegram down")
        self.messages.append(text)

    async def aclose(self) -> None:
        pass


class Concurrency:
    """Tracks how many scrapes run at once, overall and per site."""

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.site_active: dict[str, int] = defaultdict(int)
        self.site_peak: dict[str, int] = defaultdict(int)

    def enter(self, site: str) -> None:
        self.active += 1
        self.site_active[site] += 1
        self.peak = max(self.peak, self.active)
        self.site_peak[site] = max(self.site_peak[site], self.site_active[site])

    def leave(self, site: str) -> None:
        self.active -= 1
        self.site_active[site] -= 1


class FakeSite(SiteAdapter):
    """Adapter whose scrapes finish after `duration`, or when their gate is released."""

    def __init__(self, key: str, categories=("a",), *, tracker: Concurrency | None = None, duration=0.02):
        self.key = key
        self.name = key.title()
        self.base_url = f"https://{key}.test"
        self.categories = tuple(CategoryDef(c, c.upper(), f"https://{key}.test/{c}", 10) for c in categories)
        self.tracker = tracker or Concurrency()
        self.duration = duration
        self.gates: dict[str, asyncio.Event] | None = None
        self.listings: dict[str, list[ScrapedItem]] = {}
        self.errors: dict[str, str] = {}

    def gate_all(self) -> None:
        self.gates = {c.url: asyncio.Event() for c in self.categories}

    def release(self, key: str) -> None:
        self.gates[f"{self.base_url}/{key}"].set()

    async def scrape_category(self, http, url, progress):
        self.tracker.enter(self.key)
        try:
            if self.gates is not None:
                await self.gates[url].wait()
            else:
                await asyncio.sleep(self.duration)
            if url in self.errors:
                raise ScrapeError(self.errors[url])
            items = self.listings.get(url, [ScrapedItem(f"{url}/p1", "Product 1", 100)])
            progress.page_done(len(items))
            return items
        finally:
            self.tracker.leave(self.key)


class NullHttp:
    async def aclose(self) -> None:
        pass


def make_engine(*adapters: SiteAdapter, store=None, notifier=None, **options) -> Engine:
    options = {
        "tick_seconds": None,
        "startup_stagger": 0,
        "jitter": 0,
        "http_factory": lambda a: NullHttp(),
        **options,
    }
    return Engine(
        store or MemoryStore(),
        notifier or RecordingNotifier(),
        {a.key: a for a in adapters},
        **options,
    )


async def wait_for(predicate, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def wait_idle(engine: Engine, timeout: float = 5.0) -> None:
    await wait_for(lambda: not engine.snapshot()["running"] and not engine.snapshot()["queued"], timeout)


@pytest.fixture
def tracker() -> Concurrency:
    return Concurrency()
