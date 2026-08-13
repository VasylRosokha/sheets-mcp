"""Structured logging, with the credentials redacted before they can be written.

Redaction happens in a processor rather than at each call site, because the
risk is not the log line someone wrote deliberately — it is the request path
or exception repr that carries the secret along by accident (§11).
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

REDACTED = "***"

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "x-api-key",
        "api_key",
        "mcp_api_key",
        "secret_path",
        "mcp_secret_path",
        "google_service_account_key",
    }
)


def configure_logging(level: str, *, secrets: tuple[str, ...] = ()) -> None:
    """Install the structlog pipeline. Call once, at boot.

    `secrets` are literal values scrubbed from every rendered event — the
    secret path segment lands in request paths, which are otherwise logged
    verbatim.
    """
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper()),
    )

    non_empty = tuple(s for s in secrets if s)
    _scrub_stdlib_loggers(non_empty)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_keys,
            _scrubber(non_empty),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


class SecretScrubbingFilter(logging.Filter):
    """Blank known secret values in records emitted through the stdlib.

    structlog only sees what this application logs. uvicorn's access logger
    writes the full request line — including the secret path segment (§6.2) —
    through plain `logging`, so it needs scrubbing of its own.

    The format string and its arguments are scrubbed separately, leaving both
    in place. Rendering the message here and clearing `record.args` would also
    hide the secret, but uvicorn's access formatter reads named fields off the
    record and fails on an already-rendered message — the line then surfaces
    as a stdlib "Logging error" traceback instead of a log entry.
    """

    def __init__(self, secrets: tuple[str, ...]) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        if isinstance(record.msg, str):
            record.msg = _replace_all(record.msg, self._secrets)
        if isinstance(record.args, tuple):
            record.args = tuple(self._scrub(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._scrub(value) for key, value in record.args.items()}
        return True

    def _scrub(self, value: object) -> object:
        return _replace_all(value, self._secrets) if isinstance(value, str) else value


def _scrub_stdlib_loggers(secrets: tuple[str, ...]) -> None:
    """Attach the scrubber to every logger that can see a request path.

    A filter on a `Logger` runs only for records logged directly to it —
    filters are not inherited by propagation — so each one is named
    explicitly rather than relying on the root logger to cover them.
    """
    if not secrets:
        return
    log_filter = SecretScrubbingFilter(secrets)
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "mcp"):
        logging.getLogger(name).addFilter(log_filter)


def _redact_keys(
    _logger: object, _method: str, event: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """Blank any field whose *name* marks it as a credential."""
    for key in list(event):
        if key.lower() in _SENSITIVE_KEYS:
            event[key] = REDACTED
    return event


def _scrubber(
    secrets: tuple[str, ...],
) -> structlog.typing.Processor:
    """Blank any known secret *value*, wherever it appears in the event."""

    def scrub(_logger: object, _method: str, event: structlog.typing.EventDict) -> structlog.typing.EventDict:
        if not secrets:
            return event
        for key, value in event.items():
            if isinstance(value, str):
                event[key] = _replace_all(value, secrets)
        return event

    return scrub


def _replace_all(value: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        value = value.replace(secret, REDACTED)
    return value


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
