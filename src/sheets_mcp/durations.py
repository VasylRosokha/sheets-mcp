"""Reading and writing duration cells in the study grid (§7.3).

Two notations exist in this sheet and both must be readable:

- **Tally**, the original: `|` is one hour, `-` is half an hour, concatenated
  largest first. Seven months of history are written this way.
- **Hours**, used for everything written from 17 August 2026 onward: `1h`,
  `1.5h`, `0.5h`.

Parsing accepts either, always. Making the reader depend on the profile's write
setting would mean flipping that setting rendered the existing history
unparseable — the sheet holds what it holds regardless of what is configured
today.

Writing follows the profile. Nothing rewrites an existing cell into the new
notation on its own: a mixed column is honest about when the change happened,
whereas a migration would touch seven months of cells to no benefit.
"""

from __future__ import annotations

import re

# Whitespace is tolerated anywhere: `| -` appears in the real sheet on 23 May
# alongside `|-` on 14 and 26 July, and they mean the same thing (§7.3).
_TALLY = re.compile(r"^[|\-\s]+$")

# A trailing unit is optional, and a comma decimal separator is accepted because
# that is what a Ukrainian or Czech keyboard produces. `год` is included for the
# same reason: it is what someone typing directly into the sheet may write.
_HOURS = re.compile(r"^(\d+(?:[.,]\d+)?)\s*(?:h|hr|hrs|год|г)?$", re.IGNORECASE)


def parse(cell: str) -> float | None:
    """Hours held in a cell, or None if it cannot be read.

    None rather than zero for unreadable content: a wrong reading silently
    corrupts an increment, so the caller reports UNPARSEABLE_CELL with the raw
    text instead of guessing (§7.3).
    """
    text = cell.strip()
    if not text:
        return 0.0

    hours = _HOURS.match(text)
    if hours is not None:
        return float(hours.group(1).replace(",", "."))

    if _TALLY.match(text):
        compact = re.sub(r"\s+", "", text)
        return compact.count("|") + compact.count("-") * 0.5

    return None


def render(hours: float, *, style: str) -> str:
    """Render hours in the profile's notation."""
    if style == "duration-tally":
        return _render_tally(hours)
    return _render_hours(hours)


def _render_hours(hours: float) -> str:
    """`1h`, `1.5h`, `0.5h`. Zero renders empty so a cleared cell looks cleared."""
    if hours <= 0:
        return ""
    if hours == int(hours):
        return f"{int(hours)}h"
    return f"{hours:g}h"


def _render_tally(hours: float) -> str:
    """Canonical tally: full hours first, then a single trailing half (§7.3).

    Strict on write even though parsing is lenient. `|-|` reads as the right
    number but looks like a mistake, and normalising on write is what keeps the
    column readable by a human.
    """
    if hours <= 0:
        return ""
    full = int(hours)
    return "|" * full + ("-" if hours - full >= 0.5 else "")


def round_to_step(hours: float, step: float) -> float:
    """Round to the profile's `duration_step`.

    "I studied for 40 minutes" becomes 0.5 rather than an error (§7.3). The
    rounded value is reported back so the caller sees what was recorded.
    """
    if step <= 0:
        return hours
    return round(hours / step) * step
