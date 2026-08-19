"""`update_row` — correcting a row that already exists (§8.5).

Every other write tool in this server is append-only, and that property is what
made skipping §3.5's verify-against-copies survivable: the worst a mistargeted
write could do was leave a stray row at the bottom of the sheet. This tool is
the first that can overwrite history, so it carries the guards the others did
not need:

- the target must be a row the layout recognises, not a separator, a title, or
  empty space below the data;
- a block's date row is refused unless the date itself is what is being changed,
  because renaming it into an exercise silently merges two sessions;
- only the named cells are written, via one batch of single-cell ranges, so
  nothing between them is touched;
- `expect` compares against the live sheet first and aborts on any difference,
  which is the only defence against the row having moved since it was read.
"""

from __future__ import annotations

import re
from typing import Any

from sheets_mcp import dates
from sheets_mcp.errors import (
    ProtectedRow,
    RowConflict,
    RowNotFound,
    ValidationError,
    WrongLayout,
)
from sheets_mcp.layouts import dated_block
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

_SCAN_RANGE = "A:N"
_COLUMN_LETTER = re.compile(r"^[A-Z]{1,2}$")


async def update_row(
    runtime: Runtime,
    profile_name: str,
    row: int,
    cells: dict[str, Any],
    *,
    expect: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    profile = runtime.require_profile(profile_name)
    if isinstance(profile, GridProfile):
        # set_grid_value owns grid cells. It rounds to the profile's step,
        # renders the configured notation, and can increment; routing a raw
        # string past it would put "2 hours" into a column of "2h".
        raise WrongLayout(profile.name, str(profile.layout), "set_grid_value")

    if row < 1:
        raise ValidationError(f"row must be 1 or greater, not {row}")
    if not cells:
        raise ValidationError(
            "cells is empty; pass the columns to change, e.g. {\"B\": \"85x8x3\"}. "
            "Copy the keys from the cells map that find_row or query_rows returned."
        )

    targets = {_resolve_key(key, profile): str(value) for key, value in cells.items()}
    expected = {_resolve_key(key, profile): str(value) for key, value in (expect or {}).items()}
    _check_within_layout(targets.keys() | expected.keys(), profile)

    client = runtime.client()
    sheet = await client.read_range(profile.spreadsheet_id, f"'{profile.tab}'!{_SCAN_RANGE}")

    last_populated = _last_populated_row(sheet)
    if row > last_populated:
        raise RowNotFound(row, last_populated)

    _check_row_is_writable(row, sheet, profile, targets)

    current = sheet[row - 1] if row - 1 < len(sheet) else []
    mismatches = {
        column_letter(index): (want, _cell(current, index))
        for index, want in expected.items()
        if _cell(current, index) != want.strip()
    }
    if mismatches:
        raise RowConflict(row, mismatches)

    start, end = _span(profile)
    a1_row_range = f"'{profile.tab}'!{column_letter(start)}{row}:{column_letter(end)}{row}"

    changes = [
        {
            "cell": f"{column_letter(index)}{row}",
            "column": column_letter(index),
            "from": _cell(current, index),
            "to": value,
        }
        for index, value in sorted(targets.items())
    ]

    result: dict[str, Any] = {
        "profile": profile.name,
        "row": row,
        "range": a1_row_range,
        "changes": changes,
        "expect_checked": bool(expected),
        "dry_run": dry_run,
    }
    if not expected:
        result["note"] = (
            "No expect was given, so this overwrote whatever was there. The previous text is "
            "in changes[].from — relay it, since an overwrite is not undoable from here."
        )

    if dry_run:
        result["note"] = "Nothing was written. Call again with dry_run false to apply this."
        return result

    updates = [
        (f"'{profile.tab}'!{column_letter(index)}{row}", [[value]])
        for index, value in sorted(targets.items())
    ]
    await client.batch_write(profile.spreadsheet_id, updates)

    # Read the whole row back rather than the cells written, so the response
    # describes the row as it now stands — which is what the owner will be told.
    after = await client.read_range(profile.spreadsheet_id, a1_row_range)
    landed = after[0] if after else []
    result["row_after"] = {
        column_letter(start + offset): value.strip()
        for offset, value in enumerate(landed)
        if value.strip()
    }
    result["verified"] = all(
        _cell(landed, index - start) == value.strip() for index, value in targets.items()
    )

    log.info(
        "row_updated",
        profile=profile.name,
        row=row,
        columns=[column_letter(index) for index in sorted(targets)],
        expect_checked=bool(expected),
        verified=result["verified"],
    )
    return result


def _check_row_is_writable(
    row: int,
    sheet: list[list[str]],
    profile: Profile,
    targets: dict[int, str],
) -> None:
    """Refuse rows the layout depends on structurally.

    These are not permission checks — the API would accept every one of them.
    They are checks that the sheet still parses afterwards. A write that lands
    successfully and breaks the reader is the failure mode with no error
    message and no obvious moment of breakage.
    """
    if isinstance(profile, TableProfile):
        if row == 1:
            raise ProtectedRow(
                row,
                "is the header row",
                "Changing a header renames a column for every past row at once. "
                "Edit it in the Sheets app, and update profiles.yaml to match.",
            )
        return

    if not isinstance(profile, DatedBlockProfile):
        return

    blocks = dated_block.scan_blocks(sheet, profile)
    date_at = column_index(profile.date_column)

    for block in blocks:
        if block.row == row:
            if set(targets) != {date_at}:
                raise ProtectedRow(
                    row,
                    f"is the date row of the {block.raw_date} block",
                    f"Only column {profile.date_column} can be changed here. Writing anything "
                    "else into it merges this session into the one above, and nothing reports "
                    "that. Correct the exercise rows below it instead.",
                )
            if dates.parse(targets[date_at], profile.date_formats) is None:
                raise ProtectedRow(
                    row,
                    f"is a date row, and {targets[date_at]!r} is not a date this profile reads",
                    f"Use {profile.write_date_format}.",
                )
            return
        if any(item.row == row for item in block.items):
            return

    raise ProtectedRow(
        row,
        "is not part of any dated block — it is a title, a blank separator, or a stray row",
        "Blank rows separate sessions, so writing into one joins two blocks together. "
        "Use find_row or query_rows to get the row number of the entry you mean.",
    )


def _resolve_key(key: str, profile: Profile) -> int:
    """Accept a column letter, or a configured column key on a table profile."""
    token = key.strip()
    if _COLUMN_LETTER.match(token.upper()):
        return column_index(token.upper())

    if isinstance(profile, TableProfile):
        for index, column in enumerate(profile.columns):
            if column.key.casefold() == token.casefold():
                return index

    if isinstance(profile, TableProfile):
        known = ", ".join(column.key for column in profile.columns)
        raise ValidationError(f"{key!r} is neither a column letter nor a column key. Keys: {known}.")
    raise ValidationError(
        f"{key!r} is not a spreadsheet column letter. Use the keys from the cells map that "
        "find_row or query_rows returned, such as 'A' or 'B'."
    )


def _check_within_layout(columns: set[int], profile: Profile) -> None:
    start, end = _span(profile)
    outside = sorted(index for index in columns if index < start or index > end)
    if outside:
        listed = ", ".join(column_letter(index) for index in outside)
        raise ValidationError(
            f"Column(s) {listed} are outside this profile's columns "
            f"({column_letter(start)}–{column_letter(end)}). This server only writes where the "
            "profile says the data lives."
        )


def _span(profile: Profile) -> tuple[int, int]:
    if isinstance(profile, DatedBlockProfile):
        used = [
            column_index(profile.date_column),
            column_index(profile.item_column),
            *(column_index(letter) for letter in profile.value_columns),
        ]
        return min(used), max(used)
    if isinstance(profile, TableProfile):
        return 0, len(profile.columns) - 1
    raise ValidationError(f"profile {profile.name!r} has no updatable row span")


def _last_populated_row(rows: list[list[str]]) -> int:
    for offset in range(len(rows) - 1, -1, -1):
        if any(cell.strip() for cell in rows[offset]):
            return offset + 1
    return 0


def _cell(row: list[str], index: int) -> str:
    return row[index].strip() if 0 <= index < len(row) else ""
