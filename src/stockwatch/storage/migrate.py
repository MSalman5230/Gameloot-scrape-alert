"""One-time, idempotent import of the v1 `gameloot_products` collection into `products`.

The legacy collection is only read, never modified. Documents that already exist in `products`
are left alone ($setOnInsert), so re-running is harmless."""

import logging
from datetime import datetime
from typing import Any

from pymongo import UpdateOne

from stockwatch.models import utcnow

log = logging.getLogger(__name__)

LEGACY_COLLECTION = "gameloot_products"
MIGRATION_ID = "gameloot_products_v1"
BATCH = 1000


def legacy_to_product(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Map a v1 document to (filter, fields) for the new schema; None if it can't be used."""
    url, category = doc.get("link"), doc.get("type")
    if not url or not category or not isinstance(doc.get("price"), int):
        return None
    history = [
        {"price": h.get("price"), "at": h.get("at"), "in_stock": bool(h.get("inStock", True))}
        for h in doc.get("priceHistory") or []
        if isinstance(h, dict) and h.get("at") is not None
    ]
    seen_at: datetime | None = doc.get("priceUpdatedAt") or (history[-1]["at"] if history else None)
    fields = {
        "name": doc.get("name", ""),
        "price": doc["price"],
        "in_stock": bool(doc.get("inStock")),
        "first_seen_at": doc.get("firstSeenAt") or (history[0]["at"] if history else seen_at),
        "last_seen_at": seen_at,
        "price_updated_at": seen_at,
        "price_history": history,
        "migrated_from": LEGACY_COLLECTION,
    }
    return {"site": "gameloot", "category": category, "url": url}, fields


async def migrate_legacy(db) -> None:
    if await db.migrations.find_one({"_id": MIGRATION_ID}):
        return
    if LEGACY_COLLECTION not in await db.list_collection_names():
        await db.migrations.insert_one({"_id": MIGRATION_ID, "at": utcnow(), "imported": 0, "skipped": 0})
        return

    imported = skipped = 0
    ops: list[UpdateOne] = []
    async for doc in db[LEGACY_COLLECTION].find({}):
        mapped = legacy_to_product(doc)
        if mapped is None:
            skipped += 1
            continue
        key, fields = mapped
        ops.append(UpdateOne(key, {"$setOnInsert": fields}, upsert=True))
        if len(ops) >= BATCH:
            imported += (await db.products.bulk_write(ops, ordered=False)).upserted_count
            ops.clear()
    if ops:
        imported += (await db.products.bulk_write(ops, ordered=False)).upserted_count

    await db.migrations.insert_one(
        {"_id": MIGRATION_ID, "at": utcnow(), "imported": imported, "skipped": skipped}
    )
    log.info("Migrated %d products from %s (%d skipped)", imported, LEGACY_COLLECTION, skipped)
