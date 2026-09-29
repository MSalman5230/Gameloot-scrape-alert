"""Domain models shared by adapters, the engine, storage and the API."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


ACTIVE_STATUSES = (RunStatus.QUEUED, RunStatus.RUNNING)


class Trigger(StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class ScrapedItem:
    """One in-stock product as listed on a site right now."""

    url: str
    name: str
    price: int


@dataclass(frozen=True, slots=True)
class KnownProduct:
    """The stored state of a product, as far as change detection needs it."""

    url: str
    name: str
    price: int
    in_stock: bool


class Category(BaseModel):
    """A scrape target (one listing of one site) and its scheduling state."""

    id: str  # "<site>:<key>"
    site: str
    key: str
    name: str
    url: str
    enabled: bool = True
    interval_minutes: int = Field(ge=1, le=24 * 60)
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: RunStatus | None = None
    last_error: str | None = None
    last_duration_ms: int | None = None
    last_items: int | None = None


class RunRecord(BaseModel):
    id: str
    category_id: str
    site: str
    category: str
    trigger: Trigger
    status: RunStatus
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    pages: int = 0
    items: int = 0
    new: int = 0
    restocked: int = 0
    sold: int = 0
    price_changed: int = 0
    baseline: bool = False
    error: str | None = None
    notify_error: str | None = None


class RuntimeSettings(BaseModel):
    """Settings that can be changed while the app runs."""

    max_concurrent_runs: int = Field(5, ge=1, le=50)
    paused: bool = False
