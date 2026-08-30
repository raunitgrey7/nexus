"""Structured logging (structlog) with console or JSON rendering."""

from __future__ import annotations

import logging
import sys

import structlog

from nexus.core.config import settings


def configure_logging(level: str | None = None, json_output: bool | None = None) -> None:
    level_name = (level or settings.log_level).upper()
    json_output = settings.log_json if json_output is None else json_output

    logging.basicConfig(
        format="%(message)s", stream=sys.stdout, level=getattr(logging, level_name, logging.INFO)
    )

    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty()))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level_name, logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
