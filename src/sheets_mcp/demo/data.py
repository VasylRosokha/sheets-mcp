"""Synthetic sheets for the demo server.

Deliberately not a happy path. The fixture reproduces every property of the real
spreadsheets that made this project harder than a wrapper around
`values.update`, because those are the parts worth demonstrating:

- **Two date formats in one column.** Older blocks are `DD.MM.YY`, newer ones
  `DD.MM.YYYY`, exactly as a person typing over two years produces.
- **A near-duplicate exercise name.** "Pull-ups" and "Pull-ups, reverse grip"
  are different exercises; "Pull ups" would be a third one forever. This is what
  `recent_item_names` exists to prevent.
- **Columns that get renamed between periods.** Reading becomes Crypto, then
  English becomes "x" — the §7.5 renames that make a cached column index write
  to the wrong column and report success.
- **Two duration notations.** Older blocks use tally marks, newer ones hours.
  Both are always readable; only writing follows configuration.
- **A stray row below the last block** holding a day number and a value, with no
  period header above it. Day numbers alone therefore cannot mark a block.
- **No block for the current month.** So the first write attempt is refused with
  a remedy, `describe_profile` reports `current_period_exists: false`, and
  `create_period_block` has something to do. The affordance chain is the demo.

Everything is generated relative to today, so the demo is never stale and the
grid's month lengths are real ones.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

from sheets_mcp.demo.registry import registry_yaml
from sheets_mcp.sheets.memory import InMemorySheets, MemorySpreadsheet

TRAINING_ID = "demo-training-spreadsheet"
STUDY_ID = "demo-study-spreadsheet"

# Older sessions are written the short way. Both are read; only the long form is
# ever written back.
_SHORT_FORM_BEFORE_DAYS = 21

_SESSIONS: tuple[tuple[int, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    (44, (("Bench press", ("20x20", "40x20", "60x12", "80x8x3")), ("Squat", ("60x10", "100x5x5")))),
    (37, (("Pull-ups", ("10x3",)), ("Dips", ("12x3",)))),
    (30, (("Bench press", ("20x20", "40x20", "60x12", "82.5x8x3")), ("Deadlift", ("100x5", "140x3x3")))),
    (23, (("Pull-ups", ("10x3",)), ("Pull-ups, reverse grip", ("8x3",)), ("Dips", ("12x5",)))),
    (16, (("Overhead press", ("40x8x3",)), ("Bench press", ("20x20", "40x20", "60x12", "85x6x3")))),
    (9, (("Squat", ("60x10", "110x5x5")), ("Deadlift", ("100x5", "150x3x3")))),
    (4, (("Pull-ups", ("11x3",)), ("Dips", ("14x3",)))),
    (2, (("Bench press", ("20x20", "40x20", "60x12", "85x8x3")),)),
)

# Label sets per block, oldest first. Two renames, both taken from the real
# sheet: a column repurposed, and one abbreviated to a single letter.
_LABEL_SETS: tuple[tuple[str, ...], ...] = (
    ("Programming", "Reading", "English"),
    ("Programming", "Reading", "English"),
    ("Programming", "Crypto", "English"),
    ("Programming", "Crypto", "x"),
)

# Which days carry what, per block. Tally in the older two, hours in the newer.
_ENTRIES: tuple[tuple[tuple[int, int, str], ...], ...] = (
    ((2, 0, "|"), (5, 0, "||"), (9, 1, "|"), (14, 0, "|-"), (21, 0, "||"), (27, 2, "|")),
    ((1, 0, "||"), (6, 0, "|"), (11, 1, "|-"), (18, 0, "|||"), (24, 0, "|"), (25, 2, "|")),
    ((3, 0, "2h"), (7, 0, "1h"), (12, 1, "0.5h"), (16, 0, "1.5h"), (22, 0, "3h")),
    ((2, 0, "1h"), (8, 0, "2h"), (13, 1, "1h"), (19, 0, "2.5h"), (26, 0, "1.5h"), (28, 2, "1h")),
)

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def build_backend(today: date | None = None) -> InMemorySheets:
    """Both demo spreadsheets, positioned relative to `today`."""
    when = today or date.today()
    return InMemorySheets(
        {
            TRAINING_ID: MemorySpreadsheet(
                title="Training log (demo)", tab="Log", rows=_training_rows(when)
            ),
            STUDY_ID: MemorySpreadsheet(
                title="Habit grid (demo)", tab="Habits", rows=_study_rows(when)
            ),
        }
    )


def _training_rows(today: date) -> list[list[str]]:
    rows: list[list[str]] = [["Training log"]]
    for days_ago, items in _SESSIONS:
        when = today - timedelta(days=days_ago)
        # Short form for the older half, long form for the recent half.
        pattern = "%d.%m.%y" if days_ago > _SHORT_FORM_BEFORE_DAYS else "%d.%m.%Y"
        rows.append([])  # blank separator, as the layout requires
        rows.append([when.strftime(pattern)])
        for name, values in items:
            rows.append([name, *values])
    # The leading blank before the first block is not wanted.
    return rows[:1] + rows[2:]


def _study_rows(today: date) -> list[list[str]]:
    rows: list[list[str]] = [["Habit grid"]]

    # Four blocks ending with *last* month. The current month is deliberately
    # absent, so create_period_block has a reason to exist in the demo.
    for offset, (labels, entries) in enumerate(zip(_LABEL_SETS, _ENTRIES, strict=True)):
        months_back = len(_LABEL_SETS) - offset
        year, month = _shift(today.year, today.month, -months_back)
        days = calendar.monthrange(year, month)[1]

        rows.append([])
        # The period name sits in the first label column only: Sheets reports a
        # merged range's value in its first cell and empty everywhere else.
        rows.append(["", "Day", _MONTHS[month - 1], "", ""])
        rows.append(["", "", *labels])

        by_day = {(day, column): text for day, column, text in entries}
        for day in range(1, days + 1):
            cells = [by_day.get((day, index), "") for index in range(3)]
            rows.append(["", str(day), *cells])

    # Two blank rows, then a stray day number with a value and no header above
    # it. Reading it as a block would invent a period; the scanner requires a
    # header, which is why it does not.
    rows.append([])
    rows.append([])
    rows.append(["", "1", "2h", "", ""])
    return rows


def _shift(year: int, month: int, by: int) -> tuple[int, int]:
    index = (year * 12 + month - 1) + by
    return index // 12, index % 12 + 1


__all__ = ["STUDY_ID", "TRAINING_ID", "build_backend", "registry_yaml"]
