"""The interface the tools actually depend on.

Extracted when the demo needed an in-memory spreadsheet. Before that, `Runtime`
returned a concrete `SheetsClient` and the tests reached it by casting a double
into place — which works, and quietly means nothing checks that the double still
matches what it stands in for.

Four methods, because that is all nine tools use between them. Kept that narrow
deliberately: a protocol that mirrors the whole Sheets API would be a second
place to maintain Google's surface, and every method on it would be one more
thing a fake has to implement to be allowed to exist.
"""

from __future__ import annotations

from typing import Any, Protocol

from sheets_mcp.sheets.client import SpreadsheetInfo


class SheetsBackend(Protocol):
    """A spreadsheet this server can read and write."""

    # Named in PERMISSION_DENIED so the user knows who to share with. Optional
    # because a backend that needs no credentials has nobody to name.
    client_email: str | None

    async def spreadsheet_info(self, spreadsheet_id: str) -> SpreadsheetInfo: ...

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]: ...

    async def write_range(
        self, spreadsheet_id: str, a1_range: str, values: list[list[str]]
    ) -> dict[str, Any]: ...

    async def batch_write(
        self, spreadsheet_id: str, updates: list[tuple[str, list[list[str]]]]
    ) -> dict[str, Any]: ...
