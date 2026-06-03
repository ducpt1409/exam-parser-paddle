"""Logging setup via loguru."""
from __future__ import annotations

import sys

from loguru import logger

from src.core.config import settings


def setup_logging():
    """Configure loguru logger."""
    logger.remove()

    # Console output
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File output (optional)
    if settings.log_file:
        logger.add(
            settings.log_file,
            level=settings.log_level,
            rotation="50 MB",
            retention="14 days",
            compression="zip",
        )

    return logger


# Auto-setup on import
setup_logging()
