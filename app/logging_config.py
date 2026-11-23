"""Central stdout logging configuration for local development and demos."""
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()  # Load LOG_LEVEL before configuring the root logger.


def configure_logging() -> None:
    """Configure one readable console handler without duplicating handlers."""
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(handler)
    root.setLevel(level)
    # Keep useful application events visible without flooding the console with
    # low-level HTTP transport and model-library internals.
    for noisy in ("httpx", "httpcore", "google", "sentence_transformers", "urllib3"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


configure_logging()
