"""MongoDB Store on PyMongo's native asyncio client. One client is shared by the whole process."""

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, UpdateMany, UpdateOne
from pymongo.errors import PyMongoError

from stockwatch.core.diff import ChangeSet
from stockwatch.models import ACTIVE_STATUSES, Category, KnownProduct, RunRecord, RunStatus, RuntimeSettings
from stockwatch.storage.base import PRODUCT_SORTS, SEEDED_CATEGORY_FIELDS

log = logging.getLogger(__name__)

RUN_RETENTION = timedelta(days=30)
SETTINGS_ID = "runtime"


def _from_doc[M: (Category, RunRecord)](model: type[M], doc: dict[str, Any]) -> M:
    doc["id"] = doc.pop("_id")
    return model.model_validate(doc)


def _to_doc(model: Category | RunRecord) -> dict[str, Any]:
    doc = model.model_dump()
    doc["_id"] = doc.pop("id")
    return doc


class MongoStore:
    def __init__(self, client: AsyncMongoClient, db_name: str) -> None:
        self.client = client
        self.db = client[db_name]
        self.products = self.db["products"]
        self.categories = self.db["categories"]
        self.runs = self.db["runs"]
        self.settings = self.db["settings"]

    @classmethod
    async def connect(cls, uri: str, default_db: str, *, wait_seconds: float = 120) -> "MongoStore":
        client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
        db_name = client.get_default_database(default_db).name
        store = cls(client, db_name)
        deadline = asyncio.get_running_loop().time() + wait_seconds
        delay = 2.0
        while not await store.ping():
            if asyncio.get_running_loop().time() > deadline:
                await client.close()
                raise ConnectionError("MongoDB is not reachable")
            log.warning("MongoDB not reachable; retrying in %.0fs", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
        await store.ensure_indexes()
        log.info("Connected to MongoDB database %r", db_name)
        return store

    async def ensure_indexes(self) -> None:
        await self.products.create_index(
            [("site", ASCENDING), ("category", ASCENDING), ("url", ASCENDING)], unique=True
        )
        await self.products.create_index(
            [("site", ASCENDING), ("category", ASCENDING), ("in_stock", ASCENDING)]
        )
        await self.products.create_index([("first_seen_at", DESCENDING)])
        await self.runs.create_index(
            [("queued_at", ASCENDING)], expireAfterSeconds=int(RUN_RETENTION.total_seconds())
        )
        await self.runs.create_index([("category_id", ASCENDING), ("queued_at", DESCENDING)])
        await self.runs.create_index([("status", ASCENDING)])

    async def close(self) -> None:
        await self.client.close()

    async def ping(self) -> bool:
        try:
            await self.client.admin.command("ping")
            return True
        except PyMongoError as exc:
            log.debug("MongoDB ping failed: %s", exc)
            return False

    # -- settings -------------------------------------------------------------------------------

    async def load_settings(self, default: RuntimeSettings) -> RuntimeSettings:
        doc = await self.settings.find_one({"_id": SETTINGS_ID}, {"_id": 0})
        if doc is None:
            await self.save_settings(default)
            return default
        return RuntimeSettings.model_validate({**default.model_dump(), **doc})

    async def save_settings(self, settings: RuntimeSettings) -> None:
        await self.settings.replace_one({"_id": SETTINGS_ID}, settings.model_dump(), upsert=True)

    # -- categories -----------------------------------------------------------------------------

    async def seed_categories(self, categories: list[Category]) -> None:
        ops = []
        for cat in categories:
            doc = _to_doc(cat)
            seeded = {f: doc.pop(f) for f in SEEDED_CATEGORY_FIELDS}
            doc.pop("_id")
            ops.append(UpdateOne({"_id": cat.id}, {"$set": seeded, "$setOnInsert": doc}, upsert=True))
        if ops:
            await self.categories.bulk_write(ops, ordered=False)

    async def list_categories(self) -> list[Category]:
        return [_from_doc(Category, d) async for d in self.categories.find().sort("_id", ASCENDING)]

    async def get_category(self, category_id: str) -> Category | None:
        doc = await self.categories.find_one({"_id": category_id})
        return _from_doc(Category, doc) if doc else None

    async def update_category(self, category_id: str, fields: dict[str, Any]) -> Category | None:
        doc = await self.categories.find_one_and_update(
            {"_id": category_id}, {"$set": fields}, return_document=True
        )
        return _from_doc(Category, doc) if doc else None

    # -- runs -----------------------------------------------------------------------------------

    async def insert_run(self, run: RunRecord) -> None:
        await self.runs.insert_one(_to_doc(run))

    async def update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        await self.runs.update_one({"_id": run_id}, {"$set": fields})

    async def list_runs(
        self, *, site=None, category_id=None, status=None, limit: int = 50
    ) -> list[RunRecord]:
        query: dict[str, Any] = {}
        if site:
            query["site"] = site
        if category_id:
            query["category_id"] = category_id
        if status:
            query["status"] = status
        cursor = self.runs.find(query).sort("queued_at", DESCENDING).limit(limit)
        return [_from_doc(RunRecord, d) async for d in cursor]

    async def interrupt_active_runs(self, now: datetime) -> int:
        result = await self.runs.update_many(
            {"status": {"$in": list(ACTIVE_STATUSES)}},
            {"$set": {"status": RunStatus.INTERRUPTED, "finished_at": now}},
        )
        return result.modified_count

    async def run_counts(self, since: datetime) -> dict[str, int]:
        pipeline = [
            {"$match": {"queued_at": {"$gte": since}}},
            {"$group": {"_id": "$status", "n": {"$sum": 1}}},
        ]
        return {d["_id"]: d["n"] async for d in await self.runs.aggregate(pipeline)}

    # -- products -------------------------------------------------------------------------------

    async def load_products(self, site: str, category: str) -> dict[str, KnownProduct]:
        cursor = self.products.find(
            {"site": site, "category": category}, {"_id": 0, "url": 1, "name": 1, "price": 1, "in_stock": 1}
        )
        return {
            d["url"]: KnownProduct(d["url"], d.get("name", ""), d.get("price", 0), bool(d.get("in_stock")))
            async for d in cursor
        }

    async def apply_changes(self, site: str, category: str, changes: ChangeSet, now: datetime) -> None:
        """Persist a ChangeSet in a single unordered bulk write."""

        def key(url: str) -> dict[str, str]:
            return {"site": site, "category": category, "url": url}

        def entry(price: int, in_stock: bool) -> dict[str, Any]:
            return {"price_history": {"price": price, "at": now, "in_stock": in_stock}}

        ops: list[UpdateOne | UpdateMany] = [
            UpdateOne(
                key(item.url),
                {
                    "$set": {
                        "name": item.name,
                        "price": item.price,
                        "in_stock": True,
                        "last_seen_at": now,
                        "price_updated_at": now,
                    },
                    "$setOnInsert": {"first_seen_at": now},
                    "$push": entry(item.price, True),
                },
                upsert=True,
            )
            for item in changes.new
        ]
        for prev, item in changes.restocked:
            fields = {"name": item.name, "price": item.price, "in_stock": True, "last_seen_at": now}
            if prev.price != item.price:
                fields["price_updated_at"] = now
            ops.append(UpdateOne(key(item.url), {"$set": fields, "$push": entry(item.price, True)}))
        for _, item in changes.price_changed:
            ops.append(
                UpdateOne(
                    key(item.url),
                    {
                        "$set": {
                            "name": item.name,
                            "price": item.price,
                            "last_seen_at": now,
                            "price_updated_at": now,
                        },
                        "$push": entry(item.price, True),
                    },
                )
            )
        for item in changes.renamed:
            ops.append(UpdateOne(key(item.url), {"$set": {"name": item.name, "last_seen_at": now}}))
        if changes.unchanged:
            ops.append(
                UpdateMany(
                    {"site": site, "category": category, "url": {"$in": [i.url for i in changes.unchanged]}},
                    {"$set": {"last_seen_at": now}},
                )
            )
        for known in changes.sold_out:
            ops.append(
                UpdateOne(
                    key(known.url),
                    {
                        "$set": {"in_stock": False, "sold_at": now},
                        "$push": entry(known.price, False),
                    },
                )
            )
        if ops:
            await self.products.bulk_write(ops, ordered=False)

    async def list_products(
        self,
        *,
        site=None,
        category=None,
        in_stock=None,
        q=None,
        sort: str = "newest",
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        query: dict[str, Any] = {}
        if site:
            query["site"] = site
        if category:
            query["category"] = category
        if in_stock is not None:
            query["in_stock"] = in_stock
        if q:
            query["name"] = {"$regex": re.escape(q), "$options": "i"}
        field, direction = PRODUCT_SORTS.get(sort, PRODUCT_SORTS["newest"])
        cursor = (
            self.products.find(query, {"_id": 0, "price_history": {"$slice": -10}})
            .sort([(field, direction), ("url", ASCENDING)])
            .skip(skip)
            .limit(limit)
        )
        items = await cursor.to_list()
        total = await self.products.count_documents(query)
        return items, total

    async def product_counts(self) -> dict[str, int]:
        pipeline = [
            {
                "$group": {
                    "_id": None,
                    "total": {"$sum": 1},
                    "in_stock": {"$sum": {"$cond": ["$in_stock", 1, 0]}},
                }
            }
        ]
        docs = await (await self.products.aggregate(pipeline)).to_list()
        return (
            {"total": docs[0]["total"], "in_stock": docs[0]["in_stock"]}
            if docs
            else {"total": 0, "in_stock": 0}
        )
