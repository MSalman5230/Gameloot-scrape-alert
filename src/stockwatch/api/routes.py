"""JSON API consumed by the dashboard."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from stockwatch.core.engine import AlreadyActive, Engine, NotCancellable
from stockwatch.models import Category, RunRecord, RunStatus, RuntimeSettings, Trigger, utcnow

router = APIRouter(prefix="/api")


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


EngineDep = Annotated[Engine, Depends(get_engine)]


class SettingsPatch(BaseModel):
    max_concurrent_runs: int | None = Field(None, ge=1, le=50)
    paused: bool | None = None


class CategoryPatch(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(None, ge=1, le=24 * 60)


def _category_view(engine: Engine, cat: Category) -> dict[str, Any]:
    job = engine.active_job(cat.id)
    state = None
    if job:
        state = "running" if job.started_at else "queued"
    return {**cat.model_dump(), "active": state, "active_run_id": job.run_id if job else None}


@router.get("/health")
async def health(engine: EngineDep):
    ok = await engine.store.ping()
    return JSONResponse({"ok": ok}, status_code=200 if ok else 503)


@router.get("/status")
async def get_status(engine: EngineDep) -> dict[str, Any]:
    return {"server_time": utcnow(), "engine": engine.snapshot(), **await engine.stats()}


@router.get("/settings")
async def get_settings(engine: EngineDep) -> RuntimeSettings:
    return engine.settings


@router.patch("/settings")
async def patch_settings(patch: SettingsPatch, engine: EngineDep) -> RuntimeSettings:
    return await engine.update_settings(**patch.model_dump(exclude_none=True))


@router.get("/sites")
async def list_sites(engine: EngineDep) -> list[dict[str, Any]]:
    categories = await engine.store.list_categories()
    return [
        {
            "key": adapter.key,
            "name": adapter.name,
            "base_url": adapter.base_url,
            "categories": [_category_view(engine, c) for c in categories if c.site == adapter.key],
        }
        for adapter in engine.adapters.values()
    ]


@router.patch("/categories/{category_id}")
async def patch_category(category_id: str, patch: CategoryPatch, engine: EngineDep) -> dict[str, Any]:
    try:
        cat = await engine.update_category(category_id, **patch.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown category") from None
    return _category_view(engine, cat)


@router.post("/categories/{category_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_category_now(category_id: str, engine: EngineDep) -> dict[str, str]:
    try:
        job = await engine.enqueue(category_id, Trigger.MANUAL)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown category") from None
    except AlreadyActive:
        raise HTTPException(status.HTTP_409_CONFLICT, "Category is already queued or running") from None
    return {"run_id": job.run_id}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, engine: EngineDep) -> dict[str, bool]:
    try:
        await engine.cancel(run_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Run is not queued or running") from None
    except NotCancellable:
        raise HTTPException(status.HTTP_409_CONFLICT, "Run is already saving its results") from None
    return {"ok": True}


@router.get("/runs")
async def list_runs(
    engine: EngineDep,
    site: str | None = None,
    category_id: str | None = None,
    run_status: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[RunRecord]:
    return await engine.store.list_runs(site=site, category_id=category_id, status=run_status, limit=limit)


@router.get("/products")
async def list_products(
    engine: EngineDep,
    site: str | None = None,
    category: str | None = None,
    in_stock: bool | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: Literal["newest", "recent", "name", "price", "-price"] = "newest",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    items, total = await engine.store.list_products(
        site=site,
        category=category,
        in_stock=in_stock,
        q=q or None,
        sort=sort,
        skip=(page - 1) * page_size,
        limit=page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
