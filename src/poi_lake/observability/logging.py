"""structlog wiring.

  * Production:  JSONRenderer  → one JSON object per line, ready for Loki / ELK.
  * Development: ConsoleRenderer → coloured, human-readable.

Routes BOTH structlog-native loggers AND stdlib ``logging.getLogger(__name__)``
loggers through the same processor pipeline, so existing modules don't need
to change to participate.
"""

from __future__ import annotations

import logging
import os
import sys

import structlog

_CONFIGURED = False


def configure_logging(env: str | None = None, level: str | None = None) -> None:
    """Idempotent — safe to call multiple times (workers + api + cli)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    env = (env or os.getenv("APP_ENV") or "development").lower()
    level_name = (level or os.getenv("APP_LOG_LEVEL") or "INFO").upper()
    level_int = getattr(logging, level_name, logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    pre_chain = [
        # Add common fields to *every* event from any logger.
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
    ]

    # The renderer selects between JSON (prod) and a coloured console
    # (dev). Stack traces get expanded by ``format_exc_info`` regardless.
    if env == "production":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer(
            sort_keys=False
        )
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            # ``wrap_for_formatter`` hands off to stdlib's ProcessorFormatter,
            # which then runs the renderer below.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Foreign log records (everything from stdlib loggers) need the
        # pre-chain re-applied so they carry timestamp / level / logger.
        foreign_pre_chain=pre_chain,
        processor=renderer,
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level_int)

    # Quiet a few noisy libraries.
    for name in ("httpx", "httpcore", "asyncio", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(max(level_int, logging.WARNING))

    _CONFIGURED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger. Use this in new code; ``logging.getLogger``
    still works and routes through the same renderer."""
    configure_logging()
    return structlog.get_logger(name)
