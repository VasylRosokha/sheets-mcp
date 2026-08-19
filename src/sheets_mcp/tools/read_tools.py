"""`query_rows` and `find_row` — reading the sheet back (§8.4, §8.6).

Until these existed the server could write and could describe, but could not
answer "what did I train last week". That gap was not theoretical: diagnosing a
misfiled entry meant calling `set_grid_value` with `dry_run` seven times purely
to see what each cell already held, because reporting `previous_value` was the
only read path in the whole surface.

Both tools report an absolute sheet `row` for everything they return, and a
`cells` map keyed by column letter. That pairing is the contract with
`update_row`: what comes out of a read is exactly the shape that goes into a
correction, so a model can copy `cells` into `expect` and edit one entry
without constructing anything.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sheets_mcp import dates, durations
from sheets_mcp.errors import ValidationError, WrongLayout
from sheets_mcp.layouts import dated_block, grid
from sheets_mcp.logging import get_logger
from sheets_mcp.profiles.models import (
    DatedBlockProfile,
    GridProfile,
    Profile,
    TableProfile,
    column_index,
    column_letter,
)
from sheets_mcp.runtime import Runtime

log = get_logger(__name__)

# Same range and reasoning as describe_profile: unbounded rows because the
# training sheet is already past row 350, bounded columns because N is wider
# than any configured layout and an open range would pull in whatever a future
# column holds.
_SCAN_RANGE = "A:N"

_MAX_LIMIT = 200
_ORDERS = ("asc", "desc")


async def query_rows(
    runtime: Runtime,
    profile_name: str,
    *,
    since: str | None = None,
    until: str | None = None,
    contains: str | None = None,
    limit: int = 20,
    order: str = "desc",
) -> dict[str, Any]:
    profile = runtime.require_profile(profile_name)
    _check_limit(limit)
    if order not in _ORDERS:
        raise ValidationError(f"order must be 'asc' or 'desc', not {order!r}")

    lower = _bound(since, profile, "since")
    upper = _bound(until, profile, "until")
    if lower is not None and upper is not None and lower > upper:
        raise ValidationError(f"since ({lower.isoformat()}) is after until ({upper.isoformat()})")

    rows = await runtime.client().read_range(
        profile.spreadsheet_id, f"'{profile.tab}'!{_SCAN_RANGE}"
    )

    result: dict[str, Any] = {
        "profile": profile.name,
        "layout": str(profile.layout),
        "window": {"since": _iso(lower), "until": _iso(upper)},
        "order": order,
    }

    if isinstance(profile, DatedBlockProfile):
        result.update(_query_dated_block(rows, profile, lower, upper, contains, limit, order))
    elif isinstance(profile, GridProfile):
        result.update(_query_grid(rows, profile, lower, upper, contains, limit, order, runtime))
    elif isinstance(profile, TableProfile):
        result.update(_query_table(rows, profile, contains, limit, order))

    return result


async def find_row(
    runtime: Runtime,
    profile_name: str,
    query: str,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    profile = runtime.require_profile(profile_name)
    if not query.strip():
        raise ValidationError("query must not be empty; pass part of the text you are looking for")
    _check_limit(limit, ceiling=50)

    if isinstance(profile, GridProfile):
        # A grid's rows are day numbers and durations — there is no text in them
        # to match, so a substring search would always return nothing and look
        # like an empty sheet rather than an inapplicable tool.
        raise WrongLayout(profile.name, str(profile.layout), "query_rows")

    rows = await runtime.client().read_range(
        profile.spreadsheet_id, f"'{profile.tab}'!{_SCAN_RANGE}"
    )

    matches: list[dict[str, Any]] = []
    if isinstance(profile, DatedBlockProfile):
        matches = _find_dated_block(rows, profile, query)
    elif isinstance(profile, TableProfile):
        matches = _find_table(rows, profile, query)

    # Best score first, then most recent. The tie-break carries the weight in
    # practice: "fix the bench entry" matches every bench row in the sheet
    # equally well, and the one meant is almost always the last.
    matches.sort(key=lambda match: (-float(match["score"]), -int(match["row"])))

    return {
        "profile": profile.name,
        "layout": str(profile.layout),
        "query": query,
        "matched": len(matches),
        "returned": min(len(matches), limit),
        "matches": matches[:limit],
    }


# --- dated-block ------------------------------------------------------------


def _query_dated_block(
    rows: list[list[str]],
    profile: DatedBlockProfile,
    lower: date | None,
    upper: date | None,
    contains: str | None,
    limit: int,
    order: str,
) -> dict[str, Any]:
    span = _span(profile)
    needle = contains.strip().casefold() if contains and contains.strip() else None

    selected: list[dict[str, Any]] = []
    for block in dated_block.scan_blocks(rows, profile):
        if lower is not None and block.date < lower:
            continue
        if upper is not None and block.date > upper:
            continue

        items = [
            {
                "row": item.row,
                "name": item.name,
                "values": list(item.values),
                "cells": _cells(rows, item.row, span),
            }
            for item in block.items
            if needle is None or needle in item.name.casefold()
        ]
        if needle is not None and not items:
            # A block whose every item was filtered out is not a result. Keeping
            # it would report a date on which the thing asked about did not
            # happen, which reads as though it did.
            continue

        selected.append(
            {
                "date": block.raw_date,
                "iso": block.date.isoformat(),
                "row": block.row,
                "items": items,
            }
        )

    ordered = selected if order == "asc" else list(reversed(selected))
    return {
        "blocks_matched": len(selected),
        "blocks_returned": min(len(selected), limit),
        "blocks": ordered[:limit],
        "note": "limit counts dated blocks, not rows; every item carries the sheet row that holds it.",
    }


def _find_dated_block(rows: list[list[str]], profile: DatedBlockProfile, query: str) -> list[dict[str, Any]]:
    span = _span(profile)
    needle = query.strip().casefold()

    matches: list[dict[str, Any]] = []
    for block in dated_block.scan_blocks(rows, profile):
        for item in block.items:
            score = _score(needle, item.name)
            # Values are searched too, so "85x8x3" finds the row it was typed
            # into even when the exercise name was not the part remembered.
            for value in item.values:
                score = max(score, _score(needle, value))
            if score <= 0:
                continue
            matches.append(
                {
                    "row": item.row,
                    "date": block.raw_date,
                    "iso": block.date.isoformat(),
                    "name": item.name,
                    "values": list(item.values),
                    "cells": _cells(rows, item.row, span),
                    "score": round(score, 3),
                }
            )
    return matches


# --- grid -------------------------------------------------------------------


def _query_grid(
    rows: list[list[str]],
    profile: GridProfile,
    lower: date | None,
    upper: date | None,
    contains: str | None,
    limit: int,
    order: str,
    runtime: Runtime,
) -> dict[str, Any]:
    today = dates.today(runtime.settings.timezone)
    periods = grid.assign_dates(grid.scan_periods(rows, profile), profile, today)
    wanted = contains.strip().casefold() if contains and contains.strip() else None

    days: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    by_period: dict[str, dict[str, float]] = {}
    unreadable: list[dict[str, str]] = []
    undated: list[str] = []

    for dated in periods:
        period = dated.period
        if dated.month is None or dated.year is None:
            # Reported rather than silently dropped: a block this code cannot
            # place is invisible to every date filter, and a total that quietly
            # omits a month is worse than one that says which month it omitted.
            undated.append(period.name)
            continue

        for day in range(1, period.last_day_row - period.first_day_row + 2):
            when = dated.date_for(day)
            if when is None:
                continue
            if lower is not None and when < lower:
                continue
            if upper is not None and when > upper:
                continue

            sheet_row = period.first_day_row + day - 1
            raw = rows[sheet_row - 1] if sheet_row - 1 < len(rows) else []

            hours: dict[str, float] = {}
            cells: dict[str, str] = {}
            for label, column in zip(period.labels, period.label_columns, strict=True):
                if wanted is not None and wanted not in label.casefold():
                    continue
                text = raw[column].strip() if column < len(raw) else ""
                if not text:
                    continue
                cells[column_letter(column)] = text
                parsed = durations.parse(text)
                if parsed is None:
                    unreadable.append({"cell": f"{column_letter(column)}{sheet_row}", "text": text})
                    continue
                hours[label] = round(hours.get(label, 0.0) + parsed, 3)
                totals[label] = round(totals.get(label, 0.0) + parsed, 3)
                bucket = by_period.setdefault(period.name, {})
                bucket[label] = round(bucket.get(label, 0.0) + parsed, 3)

            if not cells:
                # An empty day is the overwhelming majority of a habit grid and
                # carries no information; returning them would bury the answer.
                continue

            days.append(
                {
                    "date": when.isoformat(),
                    "day": day,
                    "period": period.name,
                    "row": sheet_row,
                    "hours": hours,
                    "cells": cells,
                }
            )

    ordered = days if order == "asc" else list(reversed(days))
    result: dict[str, Any] = {
        "unit": "hours",
        "days_matched": len(days),
        "days_returned": min(len(days), limit),
        "days": ordered[:limit],
        # Summed over everything the window matched, not over the truncated
        # page. A limit that changed the total would make "how many hours in
        # July" answerable wrongly by asking for fewer rows.
        "totals": totals,
        "totals_by_period": by_period,
        "note": "Totals cover the whole window; limit truncates only the day list.",
    }
    if unreadable:
        result["unreadable_cells"] = unreadable
        result["unreadable_note"] = (
            "These cells hold text no duration notation explains, so they are excluded "
            "from the totals. Report them rather than assuming zero."
        )
    if undated:
        result["undated_periods"] = undated
    return result


# --- table ------------------------------------------------------------------


def _query_table(
    rows: list[list[str]],
    profile: TableProfile,
    contains: str | None,
    limit: int,
    order: str,
) -> dict[str, Any]:
    needle = contains.strip().casefold() if contains and contains.strip() else None
    span = (0, len(profile.columns) - 1)

    selected: list[dict[str, Any]] = []
    for offset, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        if needle is not None and not any(needle in cell.casefold() for cell in row):
            continue
        selected.append(
            {
                "row": offset,
                "values": _record(row, profile),
                "cells": _cells(rows, offset, span),
            }
        )

    ordered = selected if order == "asc" else list(reversed(selected))
    return {
        "rows_matched": len(selected),
        "rows_returned": min(len(selected), limit),
        "rows": ordered[:limit],
    }


def _find_table(rows: list[list[str]], profile: TableProfile, query: str) -> list[dict[str, Any]]:
    needle = query.strip().casefold()
    span = (0, len(profile.columns) - 1)

    matches: list[dict[str, Any]] = []
    for offset, row in enumerate(rows[1:], start=2):
        score = max((_score(needle, cell) for cell in row), default=0.0)
        if score <= 0:
            continue
        matches.append(
            {
                "row": offset,
                "values": _record(row, profile),
                "cells": _cells(rows, offset, span),
                "score": round(score, 3),
            }
        )
    return matches


def _record(row: list[str], profile: TableProfile) -> dict[str, str]:
    return {
        column.key: (row[index].strip() if index < len(row) else "")
        for index, column in enumerate(profile.columns)
    }


# --- shared -----------------------------------------------------------------


def _span(profile: DatedBlockProfile) -> tuple[int, int]:
    """The zero-based column range the layout occupies."""
    used = [
        column_index(profile.date_column),
        column_index(profile.item_column),
        *(column_index(letter) for letter in profile.value_columns),
    ]
    return min(used), max(used)


def _cells(rows: list[list[str]], sheet_row: int | None, span: tuple[int, int]) -> dict[str, str]:
    """Non-empty cells of one row, keyed by column letter.

    Empty cells are omitted rather than returned blank. `update_row` compares
    `expect` against what is actually there, and a map full of `""` would
    invite a model to assert emptiness it never verified.
    """
    if sheet_row is None or sheet_row - 1 >= len(rows) or sheet_row < 1:
        return {}
    row = rows[sheet_row - 1]
    start, end = span
    return {
        column_letter(index): row[index].strip()
        for index in range(start, min(end + 1, len(row)))
        if row[index].strip()
    }


def _score(needle: str, text: str) -> float:
    """How well `needle` matches a cell: 1.0 exact, otherwise coverage.

    Deliberately not a fuzzy edit distance. The queries here are fragments a
    person remembers — "bench", "жим" — and the useful signal is how much of the
    cell the fragment accounts for, which ranks a short exact-ish name above a
    long one that merely contains the word.
    """
    folded = text.strip().casefold()
    if not folded or not needle:
        return 0.0
    if folded == needle:
        return 1.0
    if needle not in folded:
        return 0.0
    return min(0.99, len(needle) / len(folded))


def _check_limit(limit: int, *, ceiling: int = _MAX_LIMIT) -> None:
    if limit < 1 or limit > ceiling:
        raise ValidationError(f"limit must be between 1 and {ceiling}, not {limit}")


def _bound(token: str | None, profile: Profile, which: str) -> date | None:
    """Parse a `since`/`until` bound, accepting ISO or the profile's own format."""
    if token is None or not token.strip():
        return None
    formats = ["YYYY-MM-DD"]
    if isinstance(profile, DatedBlockProfile):
        formats = [*profile.date_formats, "YYYY-MM-DD"]
    parsed = dates.parse(token, formats)
    if parsed is None:
        raise ValidationError(
            f"could not read {which}={token!r} as a date. Use YYYY-MM-DD, or "
            f"{'/'.join(formats[:-1]) or 'the sheet format'}."
        )
    return parsed


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None
