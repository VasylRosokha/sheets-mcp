"""A Sheets client double that behaves like the API, not like the caller hopes.

The earlier per-file fakes returned the whole sheet for every read, which is
fine for tools that scan `A:N` and never read anything narrower. `update_row`
reads one row back to verify what landed, so a fake that ignores the range
would report success no matter what was written.

Two behaviours are reproduced deliberately because code has been wrong about
both: Google trims trailing empty cells from every row it returns, and trims
trailing empty rows from the range. Padding them out here would hide exactly
the ragged-row handling the real client has to do.
"""

from __future__ import annotations

from typing import Any, cast

from sheets_mcp.config import Settings
from sheets_mcp.profiles.models import column_index
from sheets_mcp.runtime import Runtime
from sheets_mcp.sheets.client import SheetsClient, SpreadsheetInfo

ENV = {"MCP_API_KEY": "k", "TZ": "Europe/Prague"}


class FakeSheet:
    """An in-memory spreadsheet addressed by A1 ranges."""

    def __init__(self, rows: list[list[str]], *, title: str = "Fixture", tab: str = "Лист1") -> None:
        self.rows = [list(row) for row in rows]
        self.title = title
        self.tab = tab
        self.writes: list[tuple[str, list[list[str]]]] = []
        self.batches: list[tuple[str, list[list[str]]]] = []
        self.client_email = "sheets-mcp@example.iam.gserviceaccount.com"

    async def spreadsheet_info(self, spreadsheet_id: str) -> SpreadsheetInfo:
        return SpreadsheetInfo(title=self.title, tabs=(self.tab,))

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]:
        first_column, last_column, first_row, last_row = _parse(a1_range, len(self.rows))
        out: list[list[str]] = []
        for row in self.rows[first_row - 1 : last_row]:
            span = range(first_column, last_column + 1)
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
        self.writes.append((a1_range, values))
        self._apply(a1_range, values)
        return {"updatedRange": a1_range}

    async def batch_write(
        self, spreadsheet_id: str, updates: list[tuple[str, list[list[str]]]]
    ) -> dict[str, Any]:
        for a1_range, values in updates:
            self.batches.append((a1_range, values))
            self._apply(a1_range, values)
        return {"totalUpdatedCells": sum(len(row) for _, values in updates for row in values)}

    def _apply(self, a1_range: str, values: list[list[str]]) -> None:
        first_column, _, first_row, _ = _parse(a1_range, len(self.rows))
        for offset, line in enumerate(values):
            index = first_row - 1 + offset
            while len(self.rows) <= index:
                self.rows.append([])
            target = self.rows[index]
            while len(target) < first_column + len(line):
                target.append("")
            for position, value in enumerate(line):
                target[first_column + position] = value


def _parse(a1_range: str, row_count: int) -> tuple[int, int, int, int]:
    body = a1_range.split("!", 1)[1] if "!" in a1_range else a1_range
    if ":" not in body:
        body = f"{body}:{body}"
    first, last = body.split(":", 1)
    return (
        column_index(_letters(first) or "A"),
        column_index(_letters(last) or "N"),
        int(_digits(first) or 1),
        int(_digits(last) or row_count),
    )


def _letters(token: str) -> str:
    return "".join(char for char in token if char.isalpha()).upper()


def _digits(token: str) -> str:
    return "".join(char for char in token if char.isdigit())


def runtime_with(sheet: FakeSheet, env: dict[str, str] | None = None) -> Runtime:
    runtime = Runtime(Settings.from_env(env or ENV), registry_path="profiles.yaml")
    # cast rather than a Protocol: FakeSheet implements only the methods these
    # tests reach, and widening SheetsClient into an interface to please a test
    # would put indirection into production code for no production reason.
    runtime._client = cast(SheetsClient, sheet)  # noqa: SLF001
    return runtime
