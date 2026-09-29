# Stockwatch

Watches [Gameloot.in](https://gameloot.in) listings (GPUs, CPUs, motherboards, RAM) and sends Telegram alerts when items are new, back in stock, or sold out. A web dashboard at `http://<host>:8000` lets you see runs and products, toggle categories, change intervals and trigger scrapes.

## Run with Docker

Set `MONGODB_URI`, `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_IDS` in `docker-compose.yml` (or in a `.env` file next to it), then:

```bash
docker compose up -d --remove-orphans
```

The dashboard has no authentication, so keep it on a trusted network.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
python -m stockwatch
```

Configure through environment variables or a `.env` file (see `.env.example`). Set `MONGODB_URI=memory://` to try it without MongoDB (nothing is saved).

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` | DB name comes from the URI path, else `gamelootScrape`. |
| `TELEGRAM_BOT_TOKEN` | – | Without the token and chat IDs, alerts only go to the log. |
| `TELEGRAM_CHAT_IDS` | – | Comma-separated. |
| `PORT` | `8000` | |
| `LOG_LEVEL` | `INFO` | |
