"""The contract every site adapter implements, plus the adapter registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from stockwatch.models import ScrapedItem
from stockwatch.sites.http import BROWSER_HEADERS, ScrapeError, SiteHttp

__all__ = [
    "CategoryDef",
    "Progress",
    "ScrapeError",
    "SiteAdapter",
    "SiteHttp",
    "register",
    "registered_adapters",
]


@dataclass(frozen=True, slots=True)
class CategoryDef:
    """Default definition of a category; seeded into the DB, where interval/enabled become editable."""

    key: str
    name: str
    url: str
    interval_minutes: int


class Progress:
    """Mutable progress of a running scrape, read by the dashboard."""

    __slots__ = ("items", "pages", "phase")

    def __init__(self) -> None:
        self.pages = 0
        self.items = 0
        self.phase = "scraping"  # -> "saving" once the listing is complete

    def page_done(self, items: int) -> None:
        self.pages += 1
        self.items += items


class SiteAdapter(ABC):
    key: str
    name: str
    base_url: str
    categories: tuple[CategoryDef, ...]

    # Politeness: minimum gap between two requests to this site, and retry policy for 429/5xx/network errors.
    request_delay: float = 1.5
    max_retries: int = 2
    retry_backoff: float = 5.0
    headers: dict[str, str] = BROWSER_HEADERS

    @abstractmethod
    async def scrape_category(self, http: SiteHttp, url: str, progress: Progress) -> list[ScrapedItem]:
        """Return the complete in-stock listing at `url`, or raise ScrapeError. Never return partial data."""


_registry: dict[str, SiteAdapter] = {}


def register[T: type[SiteAdapter]](cls: T) -> T:
    adapter = cls()
    if adapter.key in _registry:
        raise ValueError(f"Duplicate site adapter key: {adapter.key}")
    _registry[adapter.key] = adapter
    return cls


def registered_adapters() -> dict[str, SiteAdapter]:
    return dict(_registry)
