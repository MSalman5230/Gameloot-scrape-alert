"""One scrape run of one category: scrape -> diff -> persist -> notify."""

import logging
from dataclasses import dataclass

from stockwatch.core.diff import compute_changes
from stockwatch.models import Category, utcnow
from stockwatch.notify import Notifier, format_alerts
from stockwatch.sites.base import Progress, ScrapeError, SiteAdapter, SiteHttp
from stockwatch.storage import Store

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RunResult:
    pages: int
    items: int
    new: int
    restocked: int
    sold: int
    price_changed: int
    baseline: bool
    notify_error: str | None = None


async def run_category(
    adapter: SiteAdapter,
    http: SiteHttp,
    store: Store,
    notifier: Notifier,
    category: Category,
    progress: Progress,
) -> RunResult:
    """Raises ScrapeError (or anything else) before touching storage if the listing is incomplete."""
    items = await adapter.scrape_category(http, category.url, progress)

    progress.phase = "saving"
    known = await store.load_products(category.site, category.key)
    if not items and any(p.in_stock for p in known.values()):
        # A shop glitch showing "no products" would otherwise mark the whole category sold at once.
        raise ScrapeError(f"Empty listing at {category.url} while products are in stock; not trusting it")
    changes = compute_changes(known, items)
    await store.apply_changes(category.site, category.key, changes, utcnow())

    # First run of a category just records the baseline; alerting on every item would be noise.
    baseline = not known
    notify_error = None
    if not baseline:
        for message in format_alerts(adapter.name, category.name, changes):
            try:
                await notifier.send(message)
            except Exception as exc:
                log.error("Notification failed for %s: %s", category.id, exc)
                notify_error = str(exc)

    log.info(
        "%s: %d items over %d pages | new %d, restocked %d, sold %d, price changed %d%s",
        category.id,
        len(items),
        progress.pages,
        len(changes.new),
        len(changes.restocked),
        len(changes.sold_out),
        len(changes.price_changed),
        " (baseline)" if baseline else "",
    )
    return RunResult(
        pages=progress.pages,
        items=len(items),
        new=len(changes.new),
        restocked=len(changes.restocked),
        sold=len(changes.sold_out),
        price_changed=len(changes.price_changed),
        baseline=baseline,
        notify_error=notify_error,
    )
