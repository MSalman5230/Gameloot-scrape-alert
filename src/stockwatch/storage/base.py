"""Persistence interface used by the engine and API. Implementations: MongoStore, MemoryStore."""

from datetime import datetime
from typing import Any, Protocol

from stockwatch.core.diff import ChangeSet
from stockwatch.models import Category, KnownProduct, RunRecord, RunStatus, RuntimeSettings

PRODUCT_SORTS = {
    "newest": ("first_seen_at", -1),
    "recent": ("last_seen_at", -1),
    "name": ("name", 1),
    "price": ("price", 1),
    "-price": ("price", -1),
}

# Fields of a Category that only the seed (code) owns; everything else is user/engine state.
SEEDED_CATEGORY_FIELDS = ("site", "key", "name", "url")


class Store(Protocol):
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...

    async def load_settings(self, default: RuntimeSettings) -> RuntimeSettings: ...
    async def save_settings(self, settings: RuntimeSettings) -> None: ...

    async def seed_categories(self, categories: list[Category]) -> None:
        """Insert unknown categories; refresh code-owned fields of known ones, keeping user edits."""

    async def list_categories(self) -> list[Category]: ...
    async def get_category(self, category_id: str) -> Category | None: ...
    async def update_category(self, category_id: str, fields: dict[str, Any]) -> Category | None: ...

    async def insert_run(self, run: RunRecord) -> None: ...
    async def update_run(self, run_id: str, fields: dict[str, Any]) -> None: ...
    async def list_runs(
        self,
        *,
        site: str | None = None,
        category_id: str | None = None,
        status: RunStatus | None = None,
        limit: int = 50,
    ) -> list[RunRecord]: ...
    async def interrupt_active_runs(self, now: datetime) -> int:
        """Mark runs left queued/running by a previous process as interrupted."""

    async def run_counts(self, since: datetime) -> dict[str, int]: ...

    async def load_products(self, site: str, category: str) -> dict[str, KnownProduct]: ...
    async def apply_changes(self, site: str, category: str, changes: ChangeSet, now: datetime) -> None: ...
    async def list_products(
        self,
        *,
        site: str | None = None,
        category: str | None = None,
        in_stock: bool | None = None,
        q: str | None = None,
        sort: str = "newest",
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], int]: ...
    async def product_counts(self) -> dict[str, int]: ...
