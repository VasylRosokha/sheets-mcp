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

import calendar
import re
from dataclasses import dataclass
from datetime import date

from sheets_mcp.profiles.models import GridProfile, column_index, column_letter


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


@dataclass(frozen=True, slots=True)
class BlockPlan:
    """Exactly what `create_period_block` would write, computed before writing.

    Shared by the dry run and the real call, so a dry run cannot describe
    something different from what lands.
    """

    period: str
    a1_range: str
    rows: list[list[str]]
    header_row: int
    label_row: int
    first_day_row: int
    last_day_row: int
    labels: tuple[str, ...]
    label_columns: tuple[int, ...]


def plan_period_block(
    rows: list[list[str]],
    profile: GridProfile,
    *,
    tab: str,
    year: int,
    month: int,
    labels: tuple[str, ...] | None = None,
) -> BlockPlan:
    """Lay out a new month block below everything already in the sheet.

    Day rows run to the month's real length, so February gets 28 or 29 rows
    rather than the 31 every existing block carries (§7.5). That is what makes
    "day 31 of a 30-day month" refusable by structure instead of by a special
    case at write time.
    """
    day_at = column_index(profile.day_column)
    existing = scan_periods(rows, profile)

    # Default to the previous block's labels, not the configured ones: they are
    # what the sheet actually used last, and §7.5 records the two diverging.
    if labels is None:
        labels = existing[-1].labels if existing else tuple(c.label for c in profile.columns)
    label_columns = (
        existing[-1].label_columns
        if existing and len(existing[-1].label_columns) == len(labels)
        else tuple(range(day_at + 1, day_at + 1 + len(labels)))
    )

    last_populated = _last_populated_row(rows)
    # One blank separator row, matching how the existing blocks are spaced.
    header_row = last_populated + 2
    label_row = header_row - profile.period_header_offset + profile.label_row_offset
    first_day_row = header_row - profile.period_header_offset
    days = calendar.monthrange(year, month)[1]
    last_day_row = first_day_row + days - 1

    width = max([day_at, *label_columns]) + 1
    period = profile.period_name_for(month)

    header = [""] * width
    header[day_at] = _day_column_heading(rows, day_at, existing)
    # The name goes in the first label column only. Sheets reports a merged
    # range's value in its first cell and empty everywhere else, so this matches
    # how the existing merged headers read back.
    header[label_columns[0]] = period

    label_line = [""] * width
    for label, column in zip(labels, label_columns, strict=True):
        label_line[column] = label

    day_rows = []
    for day in range(1, days + 1):
        row = [""] * width
        row[day_at] = str(day)
        day_rows.append(row)

    start = min([day_at, *label_columns])
    end = max([day_at, *label_columns])
    a1_range = f"'{tab}'!{column_letter(start)}{header_row}:{column_letter(end)}{last_day_row}"

    # Rows are built with absolute column indices so the code can say
    # `row[day_at]`, but the range starts at the first used column. Sending
    # A-indexed rows into a B-anchored range is one column too wide, which
    # Google rejects with a 400 — and would have shifted every value left by one
    # if it had been accepted.
    payload = [row[start:] for row in (header, label_line, *day_rows)]
    assert len(payload[0]) == end - start + 1

    return BlockPlan(
        period=period,
        a1_range=a1_range,
        rows=payload,
        header_row=header_row,
        label_row=label_row,
        first_day_row=first_day_row,
        last_day_row=last_day_row,
        labels=labels,
        label_columns=label_columns,
    )


def _day_column_heading(rows: list[list[str]], day_at: int, existing: list[Period]) -> str:
    """Reuse whatever the previous block wrote above its day numbers.

    Usually "День". Copied rather than configured, because it is the sheet's
    own wording and nothing here needs to understand it.
    """
    if existing:
        header = rows[existing[-1].header_row - 1] if existing[-1].header_row - 1 < len(rows) else []
        if day_at < len(header) and header[day_at].strip():
            return header[day_at].strip()
    return ""


def _last_populated_row(rows: list[list[str]]) -> int:
    for offset in range(len(rows) - 1, -1, -1):
        if any(cell.strip() for cell in rows[offset]):
            return offset + 1
    return 0


# --- dating the blocks ------------------------------------------------------
#
# A period header says "Серпень". It does not say which year, and the sheet has
# no column that does. But every date filter and every "how many hours in July"
# question needs one, so it has to be inferred — and inferred in a way that
# survives a sheet that crosses New Year, which this one will in five months.


_EXPLICIT_YEAR = re.compile(r"(19|20)\d{2}")


@dataclass(frozen=True, slots=True)
class DatedPeriod:
    """A block with a calendar year attached, where one could be worked out."""

    period: Period
    year: int | None
    month: int | None

    def date_for(self, day: int) -> date | None:
        if self.year is None or self.month is None:
            return None
        if day > calendar.monthrange(self.year, self.month)[1]:
            return None
        return date(self.year, self.month, day)


def assign_dates(periods: list[Period], profile: GridProfile, today: date) -> list[DatedPeriod]:
    """Attach a year to each block, newest first, walking backwards.

    A header carrying a four-digit year is believed outright. Otherwise the
    newest block is assumed to be the current year — unless its month is still
    ahead of today, which means it belongs to last year — and each earlier block
    inherits that year, decrementing whenever the month stops descending. Two
    consecutive blocks named Грудень and Січень are then eleven months apart in
    the right direction rather than eleven in the wrong one.

    A header whose month cannot be recognised gets `month=None` rather than a
    guess. Its rows are then excluded from date-filtered results, which is the
    honest outcome: a block this code cannot place is one it cannot filter.
    """
    months = [_month_of(period.name, profile) for period in periods]
    explicit = [_explicit_year(period.name) for period in periods]

    years: list[int | None] = [None] * len(periods)

    # Seed from the newest block that carries a month at all.
    seed = next((index for index in range(len(periods) - 1, -1, -1) if months[index] is not None), None)
    if seed is None:
        return [
            DatedPeriod(period=period, year=explicit[index], month=None)
            for index, period in enumerate(periods)
        ]

    seed_month = months[seed]
    assert seed_month is not None
    year = explicit[seed] if explicit[seed] is not None else _seed_year(seed_month, today)
    years[seed] = year

    previous_month = seed_month
    for index in range(seed - 1, -1, -1):
        month = months[index]
        if month is None:
            years[index] = None
            continue
        if explicit[index] is not None:
            year = explicit[index]
        elif month >= previous_month:
            # Walking backwards, months should descend. One that does not means
            # the calendar rolled over between these two blocks.
            year = (year or today.year) - 1
        years[index] = year
        previous_month = month

    # Anything below the seed can only be later, so the same rule runs forward.
    year = years[seed]
    previous_month = seed_month
    for index in range(seed + 1, len(periods)):
        month = months[index]
        if month is None:
            years[index] = None
            continue
        if explicit[index] is not None:
            year = explicit[index]
        elif month <= previous_month:
            year = (year or today.year) + 1
        years[index] = year
        previous_month = month

    return [
        DatedPeriod(period=period, year=years[index], month=months[index])
        for index, period in enumerate(periods)
    ]


def _month_of(header: str, profile: GridProfile) -> int | None:
    """Which configured month name the header carries, 1-based.

    Substring rather than equality: a header may read "Серпень 2026", and the
    year is exactly the extra text that makes an equality check fail.
    """
    folded = header.casefold()
    for index, name in enumerate(profile.period_names):
        if name.casefold() in folded:
            return index + 1
    return None


def _explicit_year(header: str) -> int | None:
    match = _EXPLICIT_YEAR.search(header)
    return int(match.group()) if match else None


def _seed_year(month: int, today: date) -> int:
    """The newest block is this year, unless its month has not arrived yet."""
    return today.year if month <= today.month else today.year - 1
