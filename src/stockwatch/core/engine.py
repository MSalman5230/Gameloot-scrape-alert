"""Scheduler and dispatcher.

Rules enforced here:
- at most `settings.max_concurrent_runs` runs are active at once (changeable at runtime);
- at most one run per *site* is active at once, so categories of a site never hit it in parallel;
- a category is never queued or running twice.

A job waiting for its site is skipped rather than blocking the queue, so it never holds a global slot
and jobs of other sites keep flowing. Dispatching is synchronous and happens whenever state changes
(enqueue, run finished, settings changed), so there is no polling for free slots.
"""

import asyncio
import logging
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from stockwatch.core.pipeline import run_category
from stockwatch.models import Category, RunRecord, RunStatus, RuntimeSettings, Trigger, utcnow
from stockwatch.notify import Notifier
from stockwatch.sites.base import Progress, ScrapeError, SiteAdapter
from stockwatch.sites.http import SiteHttp, make_site_http
from stockwatch.storage import Store

log = logging.getLogger(__name__)


class AlreadyActive(Exception):
    """The category is already queued or running."""


class NotCancellable(Exception):
    """The run finished scraping and is committing its results."""


@dataclass(eq=False, slots=True)
class Job:
    run_id: str
    category: Category
    trigger: Trigger
    queued_at: datetime
    started_at: datetime | None = None
    progress: Progress = field(default_factory=Progress)
    task: asyncio.Task | None = None
    # A task cancelled before its first step never runs its handlers, so cancellation is requested
    # through this flag and only delivered with Task.cancel() once the coroutine is executing.
    executing: bool = False
    cancel_requested: bool = False

    def request_cancel(self) -> None:
        self.cancel_requested = True
        if self.executing and self.task:
            self.task.cancel()

    @property
    def site(self) -> str:
        return self.category.site

    def view(self, **extra: Any) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "category_id": self.category.id,
            "site": self.site,
            "category": self.category.key,
            "name": self.category.name,
            "trigger": self.trigger,
            "queued_at": self.queued_at,
            "started_at": self.started_at,
            "pages": self.progress.pages,
            "items": self.progress.items,
            "phase": self.progress.phase,
            **extra,
        }


class Engine:
    def __init__(
        self,
        store: Store,
        notifier: Notifier,
        adapters: dict[str, SiteAdapter],
        *,
        default_settings: RuntimeSettings | None = None,
        tick_seconds: float | None = 5.0,  # None: no background ticker (tests drive enqueue_due)
        run_timeout: float = 15 * 60,
        jitter: float = 0.1,
        startup_stagger: float = 5.0,
        http_factory=make_site_http,
    ) -> None:
        self.store = store
        self.notifier = notifier
        self.adapters = adapters
        self.settings = default_settings or RuntimeSettings()
        self._default_settings = self.settings
        self._tick_seconds = tick_seconds
        self._run_timeout = run_timeout
        self._jitter = jitter
        self._startup_stagger = startup_stagger
        self._http_factory = http_factory

        self._queue: list[Job] = []
        self._running: dict[str, Job] = {}
        self._http: dict[str, SiteHttp] = {}
        self._lock = asyncio.Lock()
        self._ticker: asyncio.Task | None = None
        self._stopping = False

    # -- lifecycle ------------------------------------------------------------------------------

    async def start(self) -> None:
        self.settings = await self.store.load_settings(self._default_settings)
        stale = await self.store.interrupt_active_runs(utcnow())
        if stale:
            log.warning("Marked %d runs from a previous process as interrupted", stale)
        await self.store.seed_categories(self._seed_categories())
        await self._stagger_overdue()
        if self._tick_seconds is not None:
            self._ticker = asyncio.create_task(self._tick_loop(), name="scheduler-ticker")
        log.info(
            "Engine started: %d sites, max %d concurrent runs%s",
            len(self.adapters),
            self.settings.max_concurrent_runs,
            " (paused)" if self.settings.paused else "",
        )

    async def stop(self) -> None:
        self._stopping = True
        tasks = [j.task for j in self._running.values() if j.task]
        for job in self._running.values():
            job.request_cancel()
        if self._ticker:
            self._ticker.cancel()
            tasks.append(self._ticker)
        await asyncio.gather(*tasks, return_exceptions=True)
        now = utcnow()
        for job in self._queue:
            await self.store.update_run(job.run_id, {"status": RunStatus.INTERRUPTED, "finished_at": now})
        self._queue.clear()
        await asyncio.gather(*(h.aclose() for h in self._http.values()), return_exceptions=True)

    def _seed_categories(self) -> list[Category]:
        return [
            Category(
                id=f"{adapter.key}:{c.key}",
                site=adapter.key,
                key=c.key,
                name=c.name,
                url=c.url,
                interval_minutes=c.interval_minutes,
            )
            for adapter in self.adapters.values()
            for c in adapter.categories
        ]

    async def _stagger_overdue(self) -> None:
        """Spread categories that are overdue after a restart instead of starting them all at once."""
        now = utcnow()
        overdue = [c for c in await self.store.list_categories() if c.enabled and _is_due(c, now)]
        for i, cat in enumerate(overdue):
            await self.store.update_category(
                cat.id, {"next_run_at": now + timedelta(seconds=i * self._startup_stagger)}
            )

    async def _tick_loop(self) -> None:
        while True:
            try:
                await self.enqueue_due()
            except Exception:
                log.exception("Scheduler tick failed")
            await asyncio.sleep(self._tick_seconds)

    # -- queueing -------------------------------------------------------------------------------

    async def enqueue_due(self) -> None:
        if self.settings.paused:
            return
        now = utcnow()
        for cat in await self.store.list_categories():
            if (
                cat.enabled
                and cat.site in self.adapters
                and _is_due(cat, now)
                and not self.active_job(cat.id)
            ):
                try:
                    await self.enqueue(cat.id, Trigger.SCHEDULE)
                except AlreadyActive:
                    pass

    async def enqueue(self, category_id: str, trigger: Trigger = Trigger.MANUAL) -> Job | None:
        """Queue a run. Raises KeyError for unknown categories and AlreadyActive for duplicates.
        A scheduled enqueue returns None if the category stopped being due in the meantime."""
        async with self._lock:
            cat = await self.store.get_category(category_id)
            if cat is None or cat.site not in self.adapters:
                raise KeyError(category_id)
            if self.active_job(category_id):
                raise AlreadyActive(category_id)
            now = utcnow()
            if trigger is Trigger.SCHEDULE and not (cat.enabled and _is_due(cat, now)):
                return None
            job = Job(run_id=uuid4().hex, category=cat, trigger=trigger, queued_at=now)
            await self.store.insert_run(
                RunRecord(
                    id=job.run_id,
                    category_id=cat.id,
                    site=cat.site,
                    category=cat.key,
                    trigger=trigger,
                    status=RunStatus.QUEUED,
                    queued_at=now,
                )
            )
            if trigger is Trigger.MANUAL:
                # Manual runs go ahead of scheduled ones, but keep their own FIFO order.
                pos = next(
                    (i for i, j in enumerate(self._queue) if j.trigger is not Trigger.MANUAL),
                    len(self._queue),
                )
                self._queue.insert(pos, job)
            else:
                self._queue.append(job)
        self._dispatch()
        return job

    def active_job(self, category_id: str) -> Job | None:
        for job in (*self._running.values(), *self._queue):
            if job.category.id == category_id:
                return job
        return None

    def _dispatch(self) -> None:
        if self._stopping:
            return
        busy = {j.site for j in self._running.values()}
        for job in list(self._queue):
            if len(self._running) >= self.settings.max_concurrent_runs:
                break
            if job.site in busy or (self.settings.paused and job.trigger is not Trigger.MANUAL):
                continue
            self._queue.remove(job)
            busy.add(job.site)
            self._start(job)

    def _start(self, job: Job) -> None:
        job.started_at = utcnow()
        self._running[job.run_id] = job
        job.task = asyncio.create_task(self._execute(job), name=f"run:{job.category.id}")

    # -- execution ------------------------------------------------------------------------------

    def _http_for(self, site: str) -> SiteHttp:
        if site not in self._http:
            self._http[site] = self._http_factory(self.adapters[site])
        return self._http[site]

    async def _execute(self, job: Job) -> None:
        cat = job.category
        fields: dict[str, Any] = {}
        cancelled = False
        job.executing = True
        try:
            if job.cancel_requested:
                raise asyncio.CancelledError
            await self.store.update_run(
                job.run_id, {"status": RunStatus.RUNNING, "started_at": job.started_at}
            )
            async with asyncio.timeout(self._run_timeout):
                result = await run_category(
                    self.adapters[cat.site],
                    self._http_for(cat.site),
                    self.store,
                    self.notifier,
                    cat,
                    job.progress,
                )
            fields = {"status": RunStatus.SUCCESS, **asdict(result)}
        except ScrapeError as exc:
            log.warning("Run %s failed: %s", cat.id, exc)
            fields = {"status": RunStatus.FAILED, "error": str(exc)}
        except TimeoutError:
            log.warning("Run %s timed out", cat.id)
            fields = {"status": RunStatus.FAILED, "error": f"Timed out after {self._run_timeout:.0f}s"}
        except asyncio.CancelledError:
            cancelled = True
            status = RunStatus.INTERRUPTED if self._stopping else RunStatus.CANCELLED
            fields = {
                "status": status,
                "error": "Stopped by shutdown" if self._stopping else "Cancelled by user",
            }
        except Exception as exc:
            log.exception("Run %s crashed", cat.id)
            fields = {"status": RunStatus.FAILED, "error": f"{type(exc).__name__}: {exc}"}

        try:
            finished = utcnow()
            fields.setdefault("pages", job.progress.pages)
            fields.setdefault("items", job.progress.items)
            fields["finished_at"] = finished
            fields["duration_ms"] = int((finished - job.started_at).total_seconds() * 1000)
            await self.store.update_run(job.run_id, fields)
            await self._reschedule(cat.id, finished, fields)
        except Exception:
            log.exception("Could not record the result of run %s", job.run_id)
        finally:
            self._running.pop(job.run_id, None)
            self._dispatch()
        if cancelled:
            raise asyncio.CancelledError

    async def _reschedule(self, category_id: str, finished: datetime, run: dict[str, Any]) -> None:
        cat = await self.store.get_category(category_id)  # fresh: interval may have been edited meanwhile
        if cat is None:
            return
        await self.store.update_category(
            category_id,
            {
                "last_run_at": finished,
                "last_status": run["status"],
                "last_error": run.get("error"),
                "last_duration_ms": run.get("duration_ms"),
                "last_items": run.get("items"),
                "next_run_at": self._next_run(cat, finished),
            },
        )

    def _next_run(self, cat: Category, after: datetime) -> datetime:
        factor = 1 + random.uniform(-self._jitter, self._jitter)
        return after + timedelta(minutes=cat.interval_minutes * factor)

    # -- control --------------------------------------------------------------------------------

    async def cancel(self, run_id: str) -> None:
        """Raises KeyError if the run isn't active, NotCancellable if it is already committing."""
        for job in self._queue:
            if job.run_id == run_id:
                self._queue.remove(job)
                now = utcnow()
                await self.store.update_run(
                    run_id, {"status": RunStatus.CANCELLED, "finished_at": now, "error": "Cancelled by user"}
                )
                cat = await self.store.get_category(job.category.id)
                if cat and _is_due(cat, now):
                    # Otherwise the next tick would queue it straight back.
                    await self.store.update_category(cat.id, {"next_run_at": self._next_run(cat, now)})
                return
        job = self._running.get(run_id)
        if job is None:
            raise KeyError(run_id)
        if job.progress.phase != "scraping":
            raise NotCancellable(run_id)
        job.request_cancel()

    async def update_settings(self, **changes: Any) -> RuntimeSettings:
        updated = RuntimeSettings.model_validate(
            {**self.settings.model_dump(), **{k: v for k, v in changes.items() if v is not None}}
        )
        await self.store.save_settings(updated)
        self.settings = updated
        log.info("Settings updated: %s", updated.model_dump())
        self._dispatch()
        return updated

    async def update_category(
        self, category_id: str, *, enabled: bool | None = None, interval_minutes: int | None = None
    ) -> Category:
        cat = await self.store.get_category(category_id)
        if cat is None:
            raise KeyError(category_id)
        fields: dict[str, Any] = {}
        if enabled is not None:
            fields["enabled"] = enabled
        if interval_minutes is not None and interval_minutes != cat.interval_minutes:
            fields["interval_minutes"] = interval_minutes
            if cat.last_run_at is not None:
                fields["next_run_at"] = cat.last_run_at + timedelta(minutes=interval_minutes)
        if not fields:
            return cat
        return await self.store.update_category(category_id, fields)

    # -- introspection --------------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        busy = {j.site for j in self._running.values()}
        full = len(self._running) >= self.settings.max_concurrent_runs

        def waiting(job: Job) -> str:
            if self.settings.paused and job.trigger is not Trigger.MANUAL:
                return "paused"
            if job.site in busy:
                return "site busy"
            return "concurrency limit" if full else "starting"

        return {
            "paused": self.settings.paused,
            "max_concurrent_runs": self.settings.max_concurrent_runs,
            "running": [j.view() for j in self._running.values()],
            "queued": [j.view(waiting=waiting(j)) for j in self._queue],
            "busy_sites": sorted(busy),
        }


def _is_due(cat: Category, now: datetime) -> bool:
    return cat.next_run_at is None or cat.next_run_at <= now
