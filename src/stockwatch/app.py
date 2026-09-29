"""FastAPI application: wires config, store, notifier, adapters and the engine together."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from stockwatch.api.routes import router
from stockwatch.config import Config
from stockwatch.core.engine import Engine
from stockwatch.models import RuntimeSettings
from stockwatch.notify import Notifier, build_notifier
from stockwatch.sites import SiteAdapter, load_adapters
from stockwatch.storage import Store, open_store

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"


def create_app(
    config: Config | None = None,
    *,
    store: Store | None = None,
    notifier: Notifier | None = None,
    adapters: dict[str, SiteAdapter] | None = None,
    engine_options: dict | None = None,
) -> FastAPI:
    """Arguments other than `config` exist for tests; normally everything is built from config."""
    config = config or Config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_store = store or await open_store(config)
        app_notifier = notifier or build_notifier(config)
        options = {
            "default_settings": RuntimeSettings(max_concurrent_runs=config.default_max_concurrent_runs),
            "tick_seconds": config.scheduler_tick_seconds,
            "run_timeout": config.run_timeout_seconds,
            **(engine_options or {}),
        }
        engine = Engine(
            app_store, app_notifier, adapters if adapters is not None else load_adapters(), **options
        )
        app.state.engine = engine
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()
            await app_notifier.aclose()
            await app_store.close()

    app = FastAPI(title="Stockwatch", version="2.0.0", lifespan=lifespan)
    app.include_router(router)
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="dashboard")
    return app
