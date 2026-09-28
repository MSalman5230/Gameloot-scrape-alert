"""MongoStore integration tests. They need a disposable MongoDB and run only when
STOCKWATCH_TEST_MONGODB_URI is set (e.g. mongodb://localhost:27017). The database used is dropped."""

import os
from datetime import timedelta

import pytest

from conftest import FakeSite, make_engine, wait_idle
from stockwatch.core.diff import compute_changes
from stockwatch.models import RunStatus, ScrapedItem, utcnow
from stockwatch.storage.migrate import LEGACY_COLLECTION, migrate_legacy

URI = os.getenv("STOCKWATCH_TEST_MONGODB_URI")
pytestmark = pytest.mark.skipif(not URI, reason="STOCKWATCH_TEST_MONGODB_URI not set")
DB = "stockwatch_pytest"


@pytest.fixture
async def store():
    from stockwatch.storage.mongo import MongoStore

    s = await MongoStore.connect(URI, DB, wait_seconds=5)
    await s.client.drop_database(s.db.name)
    await s.ensure_indexes()
    yield s
    await s.client.drop_database(s.db.name)
    await s.close()


def item(n, price=100):
    return ScrapedItem(f"https://shop.test/p{n}", f"Product {n}", price)


async def test_product_lifecycle(store):
    now = utcnow()
    await store.apply_changes("shop", "a", compute_changes({}, [item(1), item(2)]), now)
    known = await store.load_products("shop", "a")
    assert set(known) == {item(1).url, item(2).url}

    later = now + timedelta(minutes=5)
    await store.apply_changes("shop", "a", compute_changes(known, [item(1, 90)]), later)
    known = await store.load_products("shop", "a")
    assert known[item(1).url].price == 90 and known[item(2).url].in_stock is False

    await store.apply_changes("shop", "a", compute_changes(known, [item(1, 90), item(2, 120)]), later)
    items, total = await store.list_products(site="shop", in_stock=True, sort="-price")
    assert total == 2 and [i["price"] for i in items] == [120, 90]
    assert [h["price"] for h in items[0]["price_history"]] == [100, 100, 120]
    assert await store.product_counts() == {"total": 2, "in_stock": 2}
    assert (await store.list_products(q="product 1"))[1] == 1


async def test_engine_against_mongo(store):
    engine = make_engine(FakeSite("shop", ("a", "b")), store=store)
    await engine.start()
    await engine.update_settings(max_concurrent_runs=3)
    await engine.enqueue("shop:a")
    await engine.enqueue("shop:b")
    await wait_idle(engine)
    runs = await store.list_runs()
    assert [r.status for r in runs] == [RunStatus.SUCCESS, RunStatus.SUCCESS]
    assert (await store.run_counts(utcnow() - timedelta(hours=1))) == {"success": 2}
    assert (await store.load_settings(engine.settings)).max_concurrent_runs == 3
    cat = await store.get_category("shop:a")
    assert cat.last_status == RunStatus.SUCCESS and cat.next_run_at > cat.last_run_at
    await engine.stop()


async def test_migration_is_idempotent(store):
    t = utcnow().replace(microsecond=0)
    await store.db[LEGACY_COLLECTION].insert_many(
        [
            {
                "name": "A",
                "price": 5,
                "link": "https://g/a",
                "inStock": True,
                "type": "gpu",
                "firstSeenAt": t,
                "priceHistory": [{"price": 5, "at": t, "inStock": True}],
            },
            {"name": "B", "price": 7, "link": "https://g/b", "inStock": False, "type": "cpu"},
            {"name": "broken"},
        ]
    )
    await migrate_legacy(store.db)
    await migrate_legacy(store.db)
    assert await store.products.count_documents({}) == 2
    marker = await store.db.migrations.find_one()
    assert (marker["imported"], marker["skipped"]) == (2, 1)
    assert (await store.load_products("gameloot", "cpu"))["https://g/b"].in_stock is False
