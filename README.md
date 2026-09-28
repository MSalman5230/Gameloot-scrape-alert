# Stockwatch

Watches shop listings for stock changes, sends Telegram alerts for new, back-in-stock and sold-out items, and provides a web dashboard to control scraping and see what's happening.

It currently tracks [Gameloot.in](https://gameloot.in) (GPUs, CPUs, motherboards, RAM). It's built so that other sites can be added as plug-in adapters.

## Features

- **Dashboard** at `http://localhost:8000`:
  - Live running and queued jobs, with page progress and a Cancel button.
  - Per-category enable toggle, interval, and "Run now".
  - Pause or resume the scheduler.
  - Run history and a searchable product browser.
- **Rate-limit safe**:
  - Categories of the same site never run at the same time.
  - Requests to a site are spaced out, and 429/5xx responses are retried with backoff (`Retry-After` is honoured).
- **Global concurrency cap** on active runs across all sites. The default is 5, and you can change it live from the dashboard.
- **No false "sold" alerts**: a run only touches stored stock state after the *entire* listing has been read successfully. Any failed page or unparseable product fails the run with no writes.
- **Quiet first run**: a category's first run records a baseline and sends no alerts.
- **Price history** is stored for every product.

## Architecture

```
src/stockwatch/
  sites/       Site adapters. One module per site; every module here is auto-loaded.
    base.py      SiteAdapter contract, CategoryDef, Progress, registry
    http.py      SiteHttp: per-site client, request spacing, retries
    gameloot.py  Gameloot adapter
  core/
    engine.py    Scheduler + dispatcher (global cap, one run per site, pause, cancel)
    pipeline.py  One run: scrape -> diff -> persist (one bulk write) -> notify
    diff.py      Pure change detection
  storage/     Store interface; MongoStore (PyMongo async), MemoryStore (tests/dev), legacy migration
  notify/      Telegram notifier (with message splitting and retries), log fallback
  api/         JSON API used by the dashboard
  web/         Dashboard (static HTML/CSS/JS, no build step)
```

How scheduling works:
1. A ticker queues every enabled category whose `next_run_at` has passed.
2. The dispatcher starts a queued job only if both of these hold:
   - a global slot is free;
   - no other job of the same site is running.
3. A job waiting on a busy site is skipped, not blocking, so other sites keep flowing.
4. When a run finishes, the next run is set to `interval ± 10%`. It's saved in the DB, so restarts don't trigger a burst of runs.

## Running

### Docker (recommended)

```bash
cp .env.example .env   # then fill in MONGODB_URI, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS
docker compose up -d
```

Then open `http://<host>:8000`. The dashboard has no authentication, so only expose it on a trusted network.

### Locally

```bash
python -m venv .venv && .venv/Scripts/activate   # or: source .venv/bin/activate
pip install -e ".[dev]"
python -m stockwatch
```

For a quick try-out without MongoDB, set `MONGODB_URI=memory://`. Nothing is persisted in that mode.

### Configuration (environment / `.env`)

| Variable | Default | Notes |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` | DB name comes from the URI path, else `gamelootScrape`. `memory://` for dev. |
| `TELEGRAM_BOT_TOKEN` | – | If this or the chat IDs are missing, alerts only go to the log. |
| `TELEGRAM_CHAT_IDS` | – | Comma-separated. |
| `LOG_LEVEL` | `INFO` | |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | |
| `DEFAULT_MAX_CONCURRENT_RUNS` | `5` | Initial value only; after that it's edited in the dashboard and stored in the DB. |
| `RUN_TIMEOUT_SECONDS` | `900` | A run taking longer than this fails. |

## Data

MongoDB collections:

| Collection | Contents |
|---|---|
| `products` | One document per (site, category, url): current price and stock, timestamps, `price_history`. |
| `categories` | Scrape targets. Enabled flag and interval are user-editable; also holds last-run info and `next_run_at`. |
| `runs` | Run history (kept for 30 days by a TTL index). |
| `settings` | Runtime settings: max concurrent runs, paused. |

**Upgrading from v1:** on first start, the old `gameloot_products` collection is copied into `products`, keeping stock state and price history. So the first run after upgrading doesn't re-alert everything. The old collection is left untouched, and the migration runs only once.

## Adding a site

Create `src/stockwatch/sites/<site>.py`:

```python
from stockwatch.models import ScrapedItem
from stockwatch.sites.base import CategoryDef, Progress, ScrapeError, SiteAdapter, SiteHttp, register


@register
class ExampleShop(SiteAdapter):
    key = "exampleshop"                  # stable id, used in the DB
    name = "Example Shop"
    base_url = "https://example.com"
    request_delay = 2.0                  # seconds between requests to this site
    categories = (
        CategoryDef("gpu", "Graphics cards", "https://example.com/c/gpu", interval_minutes=20),
    )

    async def scrape_category(self, http: SiteHttp, url: str, progress: Progress) -> list[ScrapedItem]:
        items = []
        # for each page: response = await http.get(page_url)
        #   raise ScrapeError(...) on anything unexpected -- never return a partial listing
        #   progress.page_done(len(page_items))
        return items
```

That's all. The module is picked up automatically, its categories appear in the dashboard, and the engine keeps its categories from running in parallel.

Candidates noted for later: kharidistore.in and gpuheaven.com.

## Development

```bash
pytest          # offline; HTTP is mocked
ruff check src tests
```

`tests/test_mongo.py` runs against a real, disposable MongoDB when `STOCKWATCH_TEST_MONGODB_URI` is set (it creates and drops the `stockwatch_pytest` database).

## API

| Method | Path | |
|---|---|---|
| GET | `/api/status` | Engine snapshot (running, queued, limit, paused), 24h run counts, product counts |
| GET / PATCH | `/api/settings` | `{max_concurrent_runs: 1–50, paused: bool}` |
| GET | `/api/sites` | Sites with their categories and state |
| PATCH | `/api/categories/{id}` | `{enabled, interval_minutes}` |
| POST | `/api/categories/{id}/run` | Queue now (409 if already queued/running) |
| POST | `/api/runs/{id}/cancel` | Cancel a queued or scraping run |
| GET | `/api/runs` | `?site=&category_id=&status=&limit=` |
| GET | `/api/products` | `?site=&category=&in_stock=&q=&sort=&page=&page_size=` |
| GET | `/api/health` | DB ping; used by the Docker healthcheck |

Interactive docs: `/docs`.
