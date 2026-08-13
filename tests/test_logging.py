from __future__ import annotations

import logging
from collections.abc import Mapping

from sheets_mcp.logging import REDACTED, SecretScrubbingFilter

SECRET = "9f2c1a7e4b8d3f6a"


def make_record(msg: str, args: tuple[object, ...] | Mapping[str, object] | None = ()) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_scrubs_secret_carried_in_args() -> None:
    # uvicorn logs '%s - "%s %s HTTP/%s" %d' with the path as an argument,
    # so scrubbing only the format string would miss it entirely.
    record = make_record('%s - "%s %s HTTP/%s" %d', ("127.0.0.1", "POST", f"/mcp/{SECRET}", "1.1", 200))
    SecretScrubbingFilter((SECRET,)).filter(record)
    assert SECRET not in record.getMessage()
    assert REDACTED in record.getMessage()


def test_scrubs_secret_in_the_format_string() -> None:
    record = make_record(f"connecting to /mcp/{SECRET}")
    SecretScrubbingFilter((SECRET,)).filter(record)
    assert SECRET not in record.getMessage()


def test_args_stay_a_tuple_so_formatters_keep_working() -> None:
    # Rendering the message and clearing record.args also hides the secret,
    # but uvicorn's access formatter then fails and the line is emitted as a
    # stdlib "Logging error" traceback instead.
    record = make_record('%s - "%s %s HTTP/%s" %d', ("127.0.0.1", "POST", f"/mcp/{SECRET}", "1.1", 200))
    SecretScrubbingFilter((SECRET,)).filter(record)
    assert isinstance(record.args, tuple)
    assert len(record.args) == 5
    assert record.args[4] == 200  # non-string args pass through untouched


def test_no_secrets_configured_is_a_noop() -> None:
    record = make_record("plain message")
    SecretScrubbingFilter(()).filter(record)
    assert record.getMessage() == "plain message"


def test_filter_always_lets_the_record_through() -> None:
    record = make_record(f"/mcp/{SECRET}")
    assert SecretScrubbingFilter((SECRET,)).filter(record) is True
