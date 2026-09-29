"""Entry point: `python -m stockwatch` (or the `stockwatch` script)."""

import logging

import uvicorn

from stockwatch.app import create_app
from stockwatch.config import Config


def main() -> None:
    config = Config()
    logging.basicConfig(
        level=config.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it logs every request at INFO
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        log_level=config.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
