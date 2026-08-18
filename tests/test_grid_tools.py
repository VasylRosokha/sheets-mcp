from __future__ import annotations

from typing import Any, cast

import pytest

from sheets_mcp.config import Settings
from sheets_mcp.errors import (
    ColumnNotFound,
    DayOutOfRange,
    PeriodBlockMissing,
    PeriodExists,
    UnparseableCell,
    ValidationError,
    WrongLayout,
)
from sheets_mcp.runtime import Runtime
from sheets_mcp.sheets.client import SheetsClient, SpreadsheetInfo
from sheets_mcp.tools import create_period_block, set_grid_value

ENV = {"MCP_API_KEY": "k", "TZ": "Europe/Prague"}

# July block: header at row 2, labels at 3, days 4..34. Note Крипта where the
# configuration says Читання — the §7.5 rename, and the reason labels are read
# live rather than cached.
SHEET: list[list[str]] = [
    ["", "", "", ""],
    ["", "День", "Липень", ""],
    ["", "", "Програмування", "Крипта"],
    *[["", str(day), "||" if day == 1 else "", "|" if day == 2 else ""] for day in range(1, 32)],
]


class FakeClient:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = [list(row) for row in rows]
        self.writes: list[tuple[str, list[list[str]]]] = []
        self.client_email = "sheets-mcp@example.iam.gserviceaccount.com"

    async def spreadsheet_info(self, spreadsheet_id: str) -> SpreadsheetInfo:
        return SpreadsheetInfo(title="Навчання 2026", tabs=("Лист1",))

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]:
        for written_range, values in self.writes:
            if written_range == a1_range:
                return values
        return self.rows

    async def write_range(
        self, spreadsheet_id: str, a1_range: str, values: list[list[str]]
    ) -> dict[str, Any]:
        self.writes.append((a1_range, values))
        return {"updatedRange": a1_range}


def runtime_with(client: FakeClient) -> Runtime:
    runtime = Runtime(Settings.from_env(ENV), registry_path="profiles.yaml")
    runtime._client = cast(SheetsClient, client)  # noqa: SLF001
    return runtime


JULY = "2026-07-01"


# --- set_grid_value ---------------------------------------------------------


async def test_writes_a_single_cell_at_the_right_address() -> None:
    client = FakeClient(SHEET)
    result = await set_grid_value(runtime_with(client), "study", "Програмування", 2, when="2026-07-03")
    # Day 3 is the third day row, which starts at row 4.
    assert result["address"] == "'Лист1'!C6"
    assert result["new_value"] == "2h"
    assert client.writes == [("'Лист1'!C6", [["2h"]])]


async def test_the_live_label_wins_over_the_configured_one() -> None:
    # July's second column is Крипта in the sheet but Читання in profiles.yaml.
    # Asking for the configured label must NOT silently write into that column.
    client = FakeClient(SHEET)
    with pytest.raises(ColumnNotFound, match="Крипта"):
        await set_grid_value(runtime_with(client), "study", "Читання", 1, when=JULY)
    assert client.writes == []


async def test_the_live_label_is_accepted() -> None:
    client = FakeClient(SHEET)
    result = await set_grid_value(runtime_with(client), "study", "Крипта", 1, when=JULY)
    assert result["column"] == "Крипта"
    assert result["address"] == "'Лист1'!D4"


async def test_a_configured_alias_resolves_to_the_live_label() -> None:
    client = FakeClient(SHEET)
    result = await set_grid_value(runtime_with(client), "study", "код", 1, when=JULY)
    assert result["column"] == "Програмування"


async def test_the_previous_value_is_always_reported() -> None:
    # This is what makes an accidental overwrite reversible from the tool
    # result alone, which is what replaced §3.5's skipped copies.
    client = FakeClient(SHEET)
    result = await set_grid_value(runtime_with(client), "study", "Програмування", 5, when=JULY)
    assert result["previous_value"] == "||"
    assert result["new_value"] == "5h"


async def test_increment_reads_the_old_notation_and_writes_the_new_one() -> None:
    # Day 1 holds "||" from before the format change. Adding an hour must give
    # 3h, not append a stroke.
    client = FakeClient(SHEET)
    result = await set_grid_value(
        runtime_with(client), "study", "Програмування", 1, when=JULY, mode="increment"
    )
    assert result["previous_value"] == "||"
    assert result["new_value"] == "3h"
    assert result["hours"] == 3.0


async def test_increment_on_an_empty_cell_starts_from_zero() -> None:
    client = FakeClient(SHEET)
    result = await set_grid_value(
        runtime_with(client), "study", "Програмування", 1.5, when="2026-07-05", mode="increment"
    )
    assert result["previous_value"] == ""
    assert result["new_value"] == "1.5h"


async def test_increment_refuses_an_unreadable_cell() -> None:
    sheet = [list(row) for row in SHEET]
    sheet[3][2] = "два"
    client = FakeClient(sheet)
    with pytest.raises(UnparseableCell, match="два"):
        await set_grid_value(runtime_with(client), "study", "Програмування", 1, when=JULY, mode="increment")
    assert client.writes == []


async def test_input_is_rounded_and_the_rounded_value_reported() -> None:
    client = FakeClient(SHEET)
    result = await set_grid_value(runtime_with(client), "study", "Програмування", 0.66, when="2026-07-05")
    assert result["hours"] == 0.5
    assert result["new_value"] == "0.5h"


async def test_text_hours_are_accepted() -> None:
    client = FakeClient(SHEET)
    result = await set_grid_value(runtime_with(client), "study", "Програмування", "1.5h", when="2026-07-05")
    assert result["new_value"] == "1.5h"


async def test_a_missing_period_block_names_the_tool_that_creates_it() -> None:
    client = FakeClient(SHEET)
    with pytest.raises(PeriodBlockMissing, match="create_period_block"):
        await set_grid_value(runtime_with(client), "study", "Програмування", 1, when="2026-08-05")
    assert client.writes == []


async def test_a_day_beyond_the_block_is_refused() -> None:
    short = SHEET[:3] + [["", str(day), "", ""] for day in range(1, 31)]
    client = FakeClient(short)
    with pytest.raises(DayOutOfRange, match="runs to day 30"):
        await set_grid_value(runtime_with(client), "study", "Програмування", 1, when="2026-07-31")


async def test_the_dated_block_profile_is_refused() -> None:
    client = FakeClient(SHEET)
    with pytest.raises(WrongLayout, match="log_session"):
        await set_grid_value(runtime_with(client), "training", "x", 1)


async def test_dry_run_writes_nothing() -> None:
    client = FakeClient(SHEET)
    result = await set_grid_value(runtime_with(client), "study", "Програмування", 3, when=JULY, dry_run=True)
    assert client.writes == []
    assert result["new_value"] == "3h"
    assert result["previous_value"] == "||"


# --- create_period_block ----------------------------------------------------


async def test_creates_a_block_below_everything_with_the_right_day_count() -> None:
    client = FakeClient(SHEET)
    result = await create_period_block(runtime_with(client), "study", period="2026-08")
    assert result["period"] == "Серпень"
    assert result["days"] == 31
    # July ends at row 34, so a separator stays at 35 and the header lands at 36.
    assert result["header_row"] == 36
    assert result["first_day_row"] == 38


async def test_day_rows_match_the_months_real_length() -> None:
    # §7.5: every existing block has 31 rows regardless of the month. New ones
    # do not, which is what makes day 31 of a 30-day month refusable.
    client = FakeClient(SHEET)
    result = await create_period_block(runtime_with(client), "study", period="2026-09")
    assert result["days"] == 30
    result = await create_period_block(runtime_with(FakeClient(SHEET)), "study", period="2027-02")
    assert result["days"] == 28


async def test_labels_carry_forward_from_the_previous_block() -> None:
    # Not from the configuration: the sheet used Крипта last, and that is what
    # continuity means here.
    client = FakeClient(SHEET)
    result = await create_period_block(runtime_with(client), "study", period="2026-08")
    assert result["labels"] == ["Програмування", "Крипта"]


async def test_labels_can_be_overridden() -> None:
    client = FakeClient(SHEET)
    result = await create_period_block(
        runtime_with(client), "study", period="2026-08", labels=["Програмування", "Читання"]
    )
    assert result["labels"] == ["Програмування", "Читання"]


async def test_an_existing_period_is_refused_with_its_row() -> None:
    client = FakeClient(SHEET)
    with pytest.raises(PeriodExists, match="row 2"):
        await create_period_block(runtime_with(client), "study", period="2026-07")
    assert client.writes == []


async def test_the_new_block_is_readable_by_the_scanner() -> None:
    # The real check: what it writes must be something scan_periods finds, or
    # the next set_grid_value reports PERIOD_BLOCK_MISSING against a block that
    # visibly exists.
    from sheets_mcp.layouts import grid
    from sheets_mcp.profiles.models import GridProfile

    client = FakeClient(SHEET)
    runtime = runtime_with(client)
    plan = await create_period_block(runtime, "study", period="2026-08", dry_run=True)

    profile = runtime.require_profile("study")
    assert isinstance(profile, GridProfile)
    from sheets_mcp.layouts.grid import plan_period_block

    built = plan_period_block(client.rows, profile, tab="Лист1", year=2026, month=8)
    combined = [list(r) for r in client.rows]
    while len(combined) < built.header_row - 1:
        combined.append([])
    # Planned rows are anchored at the range's first column (B here), while the
    # fixture is absolute from column A. Re-pad before re-scanning, or the day
    # numbers land in A and the scanner correctly finds nothing.
    from sheets_mcp.profiles.models import column_index

    offset = column_index(profile.day_column)
    combined.extend([[""] * offset + row for row in built.rows])

    periods = grid.scan_periods(combined, profile)
    assert [p.name for p in periods] == ["Липень", "Серпень"]
    assert periods[1].labels == ("Програмування", "Крипта")
    assert plan["period"] == "Серпень"


async def test_a_malformed_period_is_refused() -> None:
    client = FakeClient(SHEET)
    with pytest.raises(ValidationError, match="YYYY-MM"):
        await create_period_block(runtime_with(client), "study", period="August")


# --- regressions from the first real write attempt ---------------------------


async def test_planned_rows_are_exactly_as_wide_as_their_range() -> None:
    """The defect that made create_period_block fail on the real sheet.

    Rows were built with absolute column indices (A..F) and sent into a range
    anchored at B, so every row was one column too wide. Google rejected it with
    a 400, and had it been accepted every value would have landed one column to
    the left. log_session was unaffected only because its range happens to start
    at A — which is why nothing caught this.
    """
    from sheets_mcp.layouts.grid import plan_period_block
    from sheets_mcp.profiles.models import GridProfile, column_index

    profile = runtime_with(FakeClient(SHEET)).require_profile("study")
    assert isinstance(profile, GridProfile)
    plan = plan_period_block(SHEET, profile, tab="Лист1", year=2026, month=8)

    body = plan.a1_range.split("!", 1)[1]
    first, last = body.split(":")
    start = column_index("".join(c for c in first if c.isalpha()))
    end = column_index("".join(c for c in last if c.isalpha()))
    expected = end - start + 1

    assert all(len(row) == expected for row in plan.rows), (
        f"range spans {expected} columns but rows are {len(plan.rows[0])} wide"
    )


async def test_the_day_number_lands_in_the_day_column() -> None:
    # The other half of the same defect: an off-by-one would have put day
    # numbers in column A and pushed every activity one column left.
    from sheets_mcp.layouts.grid import plan_period_block
    from sheets_mcp.profiles.models import GridProfile

    profile = runtime_with(FakeClient(SHEET)).require_profile("study")
    assert isinstance(profile, GridProfile)
    plan = plan_period_block(SHEET, profile, tab="Лист1", year=2026, month=8)

    # The range starts at B, so index 0 of each row *is* column B.
    assert plan.rows[0][0] == "День"
    assert plan.rows[0][1] == "Серпень"
    assert plan.rows[1][1] == "Програмування"
    assert plan.rows[2][0] == "1"
    assert plan.rows[-1][0] == "31"
