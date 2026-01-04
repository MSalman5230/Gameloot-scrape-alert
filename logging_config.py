import logging
import os

logging_format = os.getenv(
    "LOG_FORMAT",
    "%(asctime)s - %(levelname)s - %(message)s",
)
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=log_level, format=logging_format)
