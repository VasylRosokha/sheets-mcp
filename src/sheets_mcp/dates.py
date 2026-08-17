"""Date parsing and rendering against the profile's declared formats (§7.3).

Profiles describe dates the way a spreadsheet user would — `DD.MM.YYYY` — not
the way `strptime` does. The translation lives here so `profiles.yaml` never
has to contain a `%d`, and so an unsupported token fails with a message naming
the token rather than producing a mysterious parse failure later.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

# Longest first: YYYY must be consumed before YY can match its first half.
_TOKENS = (
    ("YYYY", "%Y"),
    ("MMMM", "%B"),
    ("DD", "%d"),
    ("MM", "%m"),
    ("YY", "%y"),
)

_TOKEN_PATTERN = re.compile("|".join(token for token, _ in _TOKENS))


class DateFormatError(ValueError):
    """Raised when a profile declares a format this module cannot translate."""


def to_strptime(profile_format: str) -> str:
    """Translate `DD.MM.YYYY` into `%d.%m.%Y`.

    Anything that is not a recognised token is passed through literally, which
    is what makes separators work without being enumerated. A bare letter is
    rejected, because it is far more likely to be a typo in a token than a
    literal the user wanted.
    """
    result: list[str] = []
    position = 0
    for match in _TOKEN_PATTERN.finditer(profile_format):
        literal = profile_format[position : match.start()]
        _reject_stray_letters(literal, profile_format)
        result.append(literal)
        result.append(dict(_TOKENS)[match.group()])
        position = match.end()

    tail = profile_format[position:]
    _reject_stray_letters(tail, profile_format)
    result.append(tail)

    translated = "".join(result)
    if "%" not in translated:
        raise DateFormatError(
            f"date format {profile_format!r} contains no date tokens; expected something like DD.MM.YYYY"
        )
    return translated


def _reject_stray_letters(literal: str, whole: str) -> None:
    stray = [char for char in literal if char.isalpha()]
    if stray:
        raise DateFormatError(
            f"date format {whole!r} contains unrecognised letters {''.join(stray)!r}; "
            "supported tokens are DD, MM, YY, YYYY, MMMM"
        )


def parse(value: str, formats: list[str]) -> date | None:
    """Parse a cell against each format in order, returning None if none match.

    None rather than an exception: the caller is usually scanning a column to
    find out *which* rows are dates, and a non-date row is the normal case
    rather than an error.
    """
    text = value.strip()
    if not text:
        return None
    for profile_format in formats:
        try:
            return datetime.strptime(text, to_strptime(profile_format)).date()
        except ValueError:
            continue
    return None


def render(value: date, profile_format: str) -> str:
    """Render a date in the profile's write format."""
    return value.strftime(to_strptime(profile_format))


def today(timezone: ZoneInfo) -> date:
    """The current date in the server's configured timezone.

    Explicitly, not via `date.today()`: the process timezone is set by `TZ`, but
    relying on it makes the behaviour untestable and silently wrong if the
    variable is ever dropped from the environment.
    """
    return datetime.now(timezone).date()
