"""In-process Store: used by tests and by `MONGODB_URI=memory://` for local dev. Nothing is persisted."""

import copy
from datetime import datetime
from typing import Any

from stockwatch.core.diff import ChangeSet
from stockwatch.models import (
    ACTIVE_STATUSES,
    Category,
    KnownProduct,
    RunRecord,
    RunStatus,
    RuntimeSettings,
)
from stockwatch.storage.base import PRODUCT_SORTS, SEEDED_CATEGORY_FIELDS


class MemoryStore:
    def __init__(self) -> None:
        self.settings: RuntimeSettings | None = None
        self.categories: dict[str, Category] = {}
        self.runs: dict[str, RunRecord] = {}
        self.products: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def close(self) -> None:
        pass

    async def ping(self) -> bool:
        return True

    async def load_settings(self, default: RuntimeSettings) -> RuntimeSettings:
        if self.settings is None:
            self.settings = default
        return self.settings.model_copy()

    async def save_settings(self, settings: RuntimeSettings) -> None:
        self.settings = settings.model_copy()

    async def seed_categories(self, categories: list[Category]) -> None:
        for cat in categories:
            existing = self.categories.get(cat.id)
            if existing is None:
                self.categories[cat.id] = cat.model_copy()
            else:
                self.categories[cat.id] = existing.model_copy(
                    update={f: getattr(cat, f) for f in SEEDED_CATEGORY_FIELDS}
                )

    async def list_categories(self) -> list[Category]:
        return [c.model_copy() for c in sorted(self.categories.values(), key=lambda c: c.id)]

    async def get_category(self, category_id: str) -> Category | None:
        cat = self.categories.get(category_id)
        return cat.model_copy() if cat else None

    async def update_category(self, category_id: str, fields: dict[str, Any]) -> Category | None:
        cat = self.categories.get(category_id)
        if cat is None:
            return None
        self.categories[category_id] = cat.model_copy(update=fields)
        return self.categories[category_id].model_copy()

    async def insert_run(self, run: RunRecord) -> None:
        self.runs[run.id] = run.model_copy()

    async def update_run(self, run_id: str, fields: dict[str, Any]) -> None:
        if run_id in self.runs:
            self.runs[run_id] = self.runs[run_id].model_copy(update=fields)

    async def list_runs(
        self, *, site=None, category_id=None, status=None, limit: int = 50
    ) -> list[RunRecord]:
        runs = [
            r
            for r in self.runs.values()
            if (site is None or r.site == site)
            and (category_id is None or r.category_id == category_id)
            and (status is None or r.status == status)
        ]
        runs.sort(key=lambda r: r.queued_at, reverse=True)
        return [r.model_copy() for r in runs[:limit]]

    async def interrupt_active_runs(self, now: datetime) -> int:
        stale = [r for r in self.runs.values() if r.status in ACTIVE_STATUSES]
        for r in stale:
            await self.update_run(r.id, {"status": RunStatus.INTERRUPTED, "finished_at": now})
        return len(stale)

    async def run_counts(self, since: datetime) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.runs.values():
            if r.queued_at >= since:
                counts[r.status] = counts.get(r.status, 0) + 1
        return counts

    async def load_products(self, site: str, category: str) -> dict[str, KnownProduct]:
        return {
            doc["url"]: KnownProduct(doc["url"], doc["name"], doc["price"], doc["in_stock"])
            for (s, c, _), doc in self.products.items()
            if s == site and c == category
        }

    async def apply_changes(self, site: str, category: str, changes: ChangeSet, now: datetime) -> None:
        for item in changes.new:
            self.products[(site, category, item.url)] = {
                "site": site,
                "category": category,
                "url": item.url,
                "name": item.name,
                "price": item.price,
                "in_stock": True,
                "first_seen_at": now,
                "last_seen_at": now,
                "price_updated_at": now,
                "price_history": [{"price": item.price, "at": now, "in_stock": True}],
            }
        updated = [item for _, item in (*changes.restocked, *changes.price_changed)]
        for item in updated:
            doc = self.products[(site, category, item.url)]
            if doc["price"] != item.price:
                doc["price_updated_at"] = now
            doc.update(name=item.name, price=item.price, in_stock=True, last_seen_at=now)
            doc["price_history"].append({"price": item.price, "at": now, "in_stock": True})
        for item in changes.renamed:
            self.products[(site, category, item.url)].update(name=item.name, last_seen_at=now)
        for item in changes.unchanged:
            self.products[(site, category, item.url)]["last_seen_at"] = now
        for known in changes.sold_out:
            doc = self.products[(site, category, known.url)]
            doc.update(in_stock=False, sold_at=now)
            doc["price_history"].append({"price": known.price, "at": now, "in_stock": False})

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
        needle = q.lower() if q else None
        docs = [
            d
            for d in self.products.values()
            if (site is None or d["site"] == site)
            and (category is None or d["category"] == category)
            and (in_stock is None or d["in_stock"] == in_stock)
            and (needle is None or needle in d["name"].lower())
        ]
        key, direction = PRODUCT_SORTS.get(sort, PRODUCT_SORTS["newest"])
        docs.sort(key=lambda d: d.get(key) or 0, reverse=direction < 0)
        page = copy.deepcopy(docs[skip : skip + limit])
        for d in page:
            d["price_history"] = d["price_history"][-10:]
        return page, len(docs)

    async def product_counts(self) -> dict[str, int]:
        docs = self.products.values()
        return {"total": len(docs), "in_stock": sum(1 for d in docs if d["in_stock"])}
