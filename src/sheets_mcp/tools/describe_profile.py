"""`describe_profile` (§8.2).

Read live from the sheet, not from config. The point is to let the model match
conventions it cannot infer from `profiles.yaml` — the exact spelling of an
exercise, which labels a period block actually carries, whether this month's
block exists yet. Config says what the sheet is supposed to look like; this
says what it does look like.
"""

from __future__ import annotations

from typing import Any

from sheets_mcp import dates
from sheets_mcp.errors import TabNotFound
from sheets_mcp.layouts import dated_block, grid
from sheets_mcp.profiles.models import DatedBlockProfile, GridProfile, TableProfile
from sheets_mcp.runtime import Runtime
from sheets_mcp.tools.list_profiles import WRITE_TOOL

# Read from row 1 so reported row numbers are absolute sheet rows, and wide
# enough to cover the grid's activity columns. Bounded rather than open-ended
# because an unbounded range on a sparse sheet returns a great deal of nothing.
_SCAN_RANGE = "A1:N400"

# Enough history for the model to see the naming convention without turning the
# response into a transcript of the whole sheet.
_RECENT_BLOCKS = 10


async def describe_profile(runtime: Runtime, profile_name: str) -> dict[str, Any]:
    profile = runtime.require_profile(profile_name)
    client = runtime.client()

    info = await client.spreadsheet_info(profile.spreadsheet_id)
    if profile.tab not in info.tabs:
        # Checked here rather than left to the range read, so the error can list
        # the tabs that do exist instead of complaining about range syntax.
        raise TabNotFound(profile.tab, profile.spreadsheet_id, list(info.tabs))

    rows = await client.read_range(profile.spreadsheet_id, f"'{profile.tab}'!{_SCAN_RANGE}")

    described: dict[str, Any] = {
        "name": profile.name,
        "layout": str(profile.layout),
        "spreadsheet_title": info.title,
        "tab": profile.tab,
        "write_tool": WRITE_TOOL[str(profile.layout)],
    }

    if isinstance(profile, DatedBlockProfile):
        described.update(_describe_dated_block(rows, profile))
    elif isinstance(profile, GridProfile):
        described.update(_describe_grid(rows, profile, runtime))
    elif isinstance(profile, TableProfile):
        described.update(_describe_table(rows, profile))

    return described


def _describe_dated_block(rows: list[list[str]], profile: DatedBlockProfile) -> dict[str, Any]:
    blocks = dated_block.scan_blocks(rows, profile)
    if not blocks:
        return {
            "block_count": 0,
            "note": "No dated blocks found. Check date_formats against the sheet's actual dates.",
        }

    last = blocks[-1]
    return {
        "block_count": len(blocks),
        "last_block_date": last.raw_date,
        "last_block_row": last.row,
        "last_block_items": [{"name": item.name, "values": list(item.values)} for item in last.items],
        # The field that stops near-duplicate spellings accumulating (§8.2).
        "recent_item_names": dated_block.recent_item_names(blocks, block_count=_RECENT_BLOCKS),
        "max_value_columns": len(profile.value_columns),
        "date_format_for_writes": profile.write_date_format,
    }


def _describe_grid(rows: list[list[str]], profile: GridProfile, runtime: Runtime) -> dict[str, Any]:
    periods = grid.scan_periods(rows, profile)
    if not periods:
        return {
            "period_count": 0,
            "note": "No period blocks found. Check day_column and the row offsets.",
        }

    last = periods[-1]
    # Reported so the model knows whether to write or to create a block first,
    # rather than attempting a write and interpreting PERIOD_BLOCK_MISSING.
    current = dates.today(runtime.settings.timezone).strftime("%B")
    return {
        "period_count": len(periods),
        "periods": [period.name for period in periods],
        "last_period": last.name,
        "last_period_labels": list(last.labels),
        "last_period_day_rows": [last.first_day_row, last.last_day_row],
        "current_period_name_in_server_locale": current,
        "current_period_exists": any(period.name.casefold() == current.casefold() for period in periods),
        "value_type": str(profile.value_type),
        "duration_step": profile.duration_step,
        "labels_are_read_live": True,
    }


def _describe_table(rows: list[list[str]], profile: TableProfile) -> dict[str, Any]:
    live = [cell.strip() for cell in rows[0]] if rows else []
    expected = [column.header for column in profile.columns]
    body = [row for row in rows[1:] if any(cell.strip() for cell in row)]
    return {
        "columns": [
            {
                "key": column.key,
                "header": column.header,
                "type": str(column.type),
                "required": column.required,
            }
            for column in profile.columns
        ],
        "live_headers": live,
        "headers_match": live[: len(expected)] == expected,
        "row_count": len(body),
        "sample_rows": [_as_record(row, profile) for row in body[-3:]],
    }


def _as_record(row: list[str], profile: TableProfile) -> dict[str, str]:
    return {
        column.key: (row[index].strip() if index < len(row) else "")
        for index, column in enumerate(profile.columns)
    }
