"""An in-memory spreadsheet, for the demo server (`--demo`).

The point is that a reviewer can exercise all nine tools without a Google
account, a service-account key, or access to anyone's private sheets. Every
layout quirk the real sheets have is reproduced in the fixture, so what gets
demonstrated is the actual hard part rather than a happy path.

Two behaviours of the real API are reproduced because code has been wrong about
both: trailing empty cells are trimmed from every row returned, and trailing
empty rows are trimmed from the range. Padding them out here would make the
demo disagree with production in exactly the place the ragged-row handling
lives.

Errors match the real client's too — an unknown id is `SPREADSHEET_NOT_FOUND`,
an unknown tab is `TAB_NOT_FOUND` — so the demo exercises the same failure paths
and not a cheerful subset of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sheets_mcp.errors import SpreadsheetNotFound, TabNotFound
from sheets_mcp.profiles.models import column_index
from sheets_mcp.sheets.client import SpreadsheetInfo

_A1 = re.compile(r"^(?:'(?P<tab>[^']*)'|(?P<bare>[^!]*))!(?P<body>.*)$")
_CELL = re.compile(r"^(?P<col>[A-Z]*)(?P<row>\d*)$")


@dataclass
class MemorySpreadsheet:
    """One spreadsheet: a title, a single tab, and its rows."""

    title: str
    tab: str
    rows: list[list[str]] = field(default_factory=list)


class InMemorySheets:
    """A `SheetsBackend` backed by dictionaries instead of Google."""

    def __init__(self, sheets: dict[str, MemorySpreadsheet]) -> None:
        self._sheets = sheets
        # Nothing to share with, and PERMISSION_DENIED cannot occur here.
        self.client_email: str | None = None

    async def spreadsheet_info(self, spreadsheet_id: str) -> SpreadsheetInfo:
        sheet = self._require(spreadsheet_id)
        return SpreadsheetInfo(title=sheet.title, tabs=(sheet.tab,))

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]:
        sheet = self._require(spreadsheet_id)
        first_col, last_col, first_row, last_row = self._parse(a1_range, sheet)

        out: list[list[str]] = []
        for row in sheet.rows[first_row - 1 : last_row]:
            span = range(first_col, last_col + 1)
            sliced = [row[index] if index < len(row) else "" for index in span]
            while sliced and not sliced[-1]:
                sliced.pop()
            out.append(sliced)
        while out and not any(cell.strip() for cell in out[-1]):
            out.pop()
        return out

    async def write_range(
        self, spreadsheet_id: str, a1_range: str, values: list[list[str]]
    ) -> dict[str, Any]:
        self._apply(spreadsheet_id, a1_range, values)
        return {"updatedRange": a1_range, "updatedRows": len(values)}

    async def batch_write(
        self, spreadsheet_id: str, updates: list[tuple[str, list[list[str]]]]
    ) -> dict[str, Any]:
        for a1_range, values in updates:
            self._apply(spreadsheet_id, a1_range, values)
        return {"totalUpdatedCells": sum(len(row) for _, values in updates for row in values)}

    def _apply(self, spreadsheet_id: str, a1_range: str, values: list[list[str]]) -> None:
        sheet = self._require(spreadsheet_id)
        first_col, _, first_row, _ = self._parse(a1_range, sheet)
        for offset, line in enumerate(values):
            index = first_row - 1 + offset
            while len(sheet.rows) <= index:
                sheet.rows.append([])
            target = sheet.rows[index]
            while len(target) < first_col + len(line):
                target.append("")
            for position, value in enumerate(line):
                target[first_col + position] = value

    def _require(self, spreadsheet_id: str) -> MemorySpreadsheet:
        sheet = self._sheets.get(spreadsheet_id)
        if sheet is None:
            raise SpreadsheetNotFound(spreadsheet_id)
        return sheet

    def _parse(self, a1_range: str, sheet: MemorySpreadsheet) -> tuple[int, int, int, int]:
        match = _A1.match(a1_range)
        if match is None:
            raise TabNotFound("", "", [sheet.tab])
        tab = match.group("tab") or match.group("bare") or ""
        if tab != sheet.tab:
            raise TabNotFound(tab, "", [sheet.tab])

        body = match.group("body")
        if ":" not in body:
            body = f"{body}:{body}"
        first, last = body.split(":", 1)
        return (
            column_index(_part(first, "col") or "A"),
            column_index(_part(last, "col") or "N"),
            int(_part(first, "row") or 1),
            int(_part(last, "row") or max(len(sheet.rows), 1)),
        )


def _part(token: str, which: str) -> str:
    match = _CELL.match(token.strip().upper())
    return match.group(which) if match else ""
