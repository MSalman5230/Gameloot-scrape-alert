import pytest

from conftest import FakeSite, RecordingNotifier
from stockwatch.core.diff import compute_changes
from stockwatch.core.pipeline import run_category
from stockwatch.models import KnownProduct, ScrapedItem
from stockwatch.notify import format_alerts
from stockwatch.sites.base import Progress, ScrapeError
from stockwatch.storage import MemoryStore

URL = "https://shop.test/a"


def item(n: int, price: int = 100) -> ScrapedItem:
    return ScrapedItem(f"https://shop.test/p{n}", f"Product {n}", price)


# -- diff --------------------------------------------------------------------------------------


def test_compute_changes_classifies_every_product():
    known = {
        p.url: p
        for p in [
            KnownProduct("https://shop.test/p1", "Product 1", 100, True),  # unchanged
            KnownProduct("https://shop.test/p2", "Product 2", 100, True),  # price drop
            KnownProduct("https://shop.test/p3", "Product 3", 100, False),  # back in stock
            KnownProduct("https://shop.test/p4", "Product 4", 100, True),  # sold
            KnownProduct("https://shop.test/p5", "Product 5", 100, False),  # still sold, ignored
            KnownProduct("https://shop.test/p6", "Old name", 100, True),  # renamed
        ]
    }
    scraped = [item(1), item(2, 90), item(3, 120), item(6), item(7)]
    changes = compute_changes(known, scraped)
    assert [i.url for i in changes.new] == ["https://shop.test/p7"]
    assert [(p.price, i.price) for p, i in changes.restocked] == [(100, 120)]
    assert [p.url for p in changes.sold_out] == ["https://shop.test/p4"]
    assert [(p.price, i.price) for p, i in changes.price_changed] == [(100, 90)]
    assert [i.name for i in changes.renamed] == ["Product 6"]
    assert [i.url for i in changes.unchanged] == ["https://shop.test/p1"]


def test_alert_format():
    changes = compute_changes(
        {"https://shop.test/p9": KnownProduct("https://shop.test/p9", "Gone", 1500, True)}, [item(1, 44999)]
    )
    new, sold = format_alerts("Gameloot", "Graphics cards", changes)
    assert new.startswith("🟢 NEW / BACK IN STOCK — Gameloot · Graphics cards (1)")
    assert "- Product 1 - ₹44,999 - https://shop.test/p1" in new
    assert sold.startswith("🔴 NO LONGER IN STOCK — Gameloot · Graphics cards (1)")
    assert "- Gone - ₹1,500 - https://shop.test/p9" in sold
    assert format_alerts("G", "C", compute_changes({}, [])) == []


# -- pipeline ----------------------------------------------------------------------------------


@pytest.fixture
def site():
    return FakeSite("shop", duration=0)


@pytest.fixture
def category(site):
    from stockwatch.models import Category

    c = site.categories[0]
    return Category(id="shop:a", site="shop", key=c.key, name=c.name, url=c.url, interval_minutes=10)


async def run(site, store, notifier, category):
    return await run_category(site, None, store, notifier, category, Progress())


async def test_first_run_is_a_silent_baseline(site, category):
    store, notifier = MemoryStore(), RecordingNotifier()
    site.listings[URL] = [item(1), item(2)]
    result = await run(site, store, notifier, category)
    assert result.baseline and result.new == 2
    assert notifier.messages == []
    assert set(await store.load_products("shop", "a")) == {item(1).url, item(2).url}


async def test_changes_are_persisted_and_notified(site, category):
    store, notifier = MemoryStore(), RecordingNotifier()
    site.listings[URL] = [item(1), item(2)]
    await run(site, store, notifier, category)

    site.listings[URL] = [item(1, 80), item(3)]  # 1 price drop, 2 sold, 3 new
    result = await run(site, store, notifier, category)
    assert (result.new, result.sold, result.price_changed, result.baseline) == (1, 1, 1, False)
    assert len(notifier.messages) == 2
    assert "Product 3" in notifier.messages[0] and "Product 2" in notifier.messages[1]

    doc1 = store.products[("shop", "a", item(1).url)]
    assert doc1["price"] == 80 and [h["price"] for h in doc1["price_history"]] == [100, 80]
    doc2 = store.products[("shop", "a", item(2).url)]
    assert doc2["in_stock"] is False and doc2["price_history"][-1]["in_stock"] is False

    site.listings[URL] = [item(1, 80), item(2, 150), item(3)]  # 2 is back
    notifier.messages.clear()
    result = await run(site, store, notifier, category)
    assert result.restocked == 1 and len(notifier.messages) == 1
    doc2 = store.products[("shop", "a", item(2).url)]
    assert doc2["in_stock"] is True and doc2["price"] == 150


async def test_failed_scrape_writes_nothing_and_alerts_nobody(site, category):
    store, notifier = MemoryStore(), RecordingNotifier()
    site.listings[URL] = [item(1), item(2)]
    await run(site, store, notifier, category)
    before = {k: dict(v) for k, v in store.products.items()}

    site.errors[URL] = "page 2 unreadable"
    with pytest.raises(ScrapeError):
        await run(site, store, notifier, category)
    assert {k: dict(v) for k, v in store.products.items()} == before
    assert notifier.messages == []


async def test_empty_listing_is_rejected_while_products_are_in_stock(site, category):
    store, notifier = MemoryStore(), RecordingNotifier()
    site.listings[URL] = [item(1), item(2)]
    await run(site, store, notifier, category)
    before = {k: dict(v) for k, v in store.products.items()}

    site.listings[URL] = []
    with pytest.raises(ScrapeError, match="Empty listing"):
        await run(site, store, notifier, category)
    assert {k: dict(v) for k, v in store.products.items()} == before
    assert notifier.messages == []


async def test_empty_listing_is_fine_when_nothing_is_in_stock(site, category):
    store, notifier = MemoryStore(), RecordingNotifier()
    site.listings[URL] = []
    result = await run(site, store, notifier, category)
    assert (result.items, result.baseline) == (0, True)


async def test_notification_failure_keeps_the_data(site, category):
    store = MemoryStore()
    site.listings[URL] = [item(1)]
    await run(site, store, RecordingNotifier(), category)
    site.listings[URL] = [item(1), item(2)]
    result = await run(site, store, RecordingNotifier(fail=True), category)
    assert result.notify_error == "telegram down"
    assert item(2).url in await store.load_products("shop", "a")


async def test_products_are_scoped_per_category(site, category):
    """A product listed in two categories must not be marked sold by the other category's run."""
    store, notifier = MemoryStore(), RecordingNotifier()
    site.listings[URL] = [item(1)]
    await run(site, store, notifier, category)
    other = category.model_copy(update={"id": "shop:b", "key": "b", "url": "https://shop.test/b"})
    site.listings["https://shop.test/b"] = [item(2)]
    await run(site, store, notifier, other)
    assert (await store.load_products("shop", "a"))[item(1).url].in_stock
