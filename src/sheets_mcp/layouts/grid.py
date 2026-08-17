"""Reading the `grid` layout (§7.1).

A block is a period header row, a label row, then one row per day. Blocks are
located by finding day-number runs in the day column, then stepping upward by
the profile's offsets.

Two properties of the real sheet drive the design:

- **Labels are not stable between periods** (§7.5). May replaced Читання with
  Крипта; June replaced Англійська with "x". So labels are read per block and
  never taken from config. A cached column index would write reading hours into
  a crypto column and look entirely successful doing it.
- **A stray row after the last block** contains a day number and a value. Day
  numbers alone therefore cannot mark a block; a period header must be present
  above the run, or it is not a block.
"""

from __future__ import annotations

from dataclasses import dataclass

from sheets_mcp.profiles.models import GridProfile, column_index


@dataclass(frozen=True, slots=True)
class Period:
    """One period block, as actually found in the sheet."""

    name: str
    header_row: int  # 1-based
    label_row: int
    first_day_row: int
    last_day_row: int
    labels: tuple[str, ...]
    label_columns: tuple[int, ...]  # zero-based column indices matching `labels`

    def day_row(self, day: int) -> int | None:
        """Sheet row for a day number, or None if the block does not reach it."""
        row = self.first_day_row + day - 1
        return row if row <= self.last_day_row else None


def scan_periods(rows: list[list[str]], profile: GridProfile) -> list[Period]:
    """Find every period block in a range read from sheet row 1."""
    day_at = column_index(profile.day_column)
    periods: list[Period] = []

    for start, end in _day_runs(rows, day_at):
        header_index = start + profile.period_header_offset
        label_index = start + profile.label_row_offset
        if header_index < 0 or label_index < 0:
            # The run sits too close to the top of the sheet to carry a header,
            # so it is not a block.
            continue

        header_row = rows[header_index] if header_index < len(rows) else []
        label_row = rows[label_index] if label_index < len(rows) else []

        name = _first_value_after(header_row, day_at)
        if not name:
            # No period header: this is the §7.5 stray row, not a block.
            continue

        labels, columns = _labels(label_row, day_at)
        if not labels:
            continue

        periods.append(
            Period(
                name=name,
                header_row=header_index + 1,
                label_row=label_index + 1,
                first_day_row=start + 1,
                last_day_row=end + 1,
                labels=labels,
                label_columns=columns,
            )
        )

    return periods


def resolve_column(period: Period, wanted: str, aliases: tuple[str, ...] = ()) -> int | None:
    """Find the sheet column for a label within one block.

    Matching is case-insensitive and folded, because the labels are Ukrainian
    and a capitalisation difference is not a different column.
    """
    candidates = {wanted.casefold(), *(alias.casefold() for alias in aliases)}
    for label, column in zip(period.labels, period.label_columns, strict=True):
        if label.casefold() in candidates:
            return column
    return None


def _day_runs(rows: list[list[str]], day_at: int) -> list[tuple[int, int]]:
    """Locate runs of consecutive day numbers starting at 1.

    A run starts at a cell containing `1` and continues while each following
    row increments by one. Requiring the increment is what separates a real day
    column from a column that merely contains numbers.
    """
    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(rows):
        if _day(rows[index], day_at) != 1:
            index += 1
            continue
        end = index
        expected = 2
        while end + 1 < len(rows) and _day(rows[end + 1], day_at) == expected:
            end += 1
            expected += 1
        runs.append((index, end))
        index = end + 1
    return runs


def _day(row: list[str], index: int) -> int | None:
    raw = row[index].strip() if index < len(row) else ""
    if not raw.isdigit():
        return None
    return int(raw)


def _first_value_after(row: list[str], day_at: int) -> str:
    """The period name.

    The header cell is merged across the activity columns, and Google reports a
    merged range's value only in its first cell — every other cell comes back
    empty. So the name is the first non-empty cell to the right of the day
    column, not the cell above any particular activity.
    """
    for index in range(day_at + 1, len(row)):
        value = row[index].strip()
        if value:
            return value
    return ""


def _labels(row: list[str], day_at: int) -> tuple[tuple[str, ...], tuple[int, ...]]:
    labels: list[str] = []
    columns: list[int] = []
    for index in range(day_at + 1, len(row)):
        value = row[index].strip()
        if value:
            labels.append(value)
            columns.append(index)
    return tuple(labels), tuple(columns)
