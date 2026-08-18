from __future__ import annotations

from datetime import date
from typing import Any, cast

import pytest

from sheets_mcp.config import Settings
from sheets_mcp.errors import DateOutOfOrder, TooManyValues, ValidationError, WrongLayout
from sheets_mcp.runtime import Runtime
from sheets_mcp.sheets.client import SheetsClient, SpreadsheetInfo
from sheets_mcp.tools import log_session

ENV = {"MCP_API_KEY": "k", "TZ": "Europe/Prague"}

# Row 1 title, then two blocks. Last populated row is 7.
SHEET = [
    ["Тренування"],
    ["31.07.2026"],
    ["Жим штанги лежачи", "20x20", "40x20", "90x4x3"],
    [],
    ["04.08.2026"],
    ["Підтягування", "8x3"],
    ["Віджимання на брусях", "12x3"],
]


class FakeClient:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = [list(row) for row in rows]
        self.writes: list[tuple[str, list[list[str]]]] = []
        self.client_email = "sheets-mcp@example.iam.gserviceaccount.com"

    async def spreadsheet_info(self, spreadsheet_id: str) -> SpreadsheetInfo:
        return SpreadsheetInfo(title="Тренування2026", tabs=("Лист1",))

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]:
        # A read of a written range returns what was written, so the tool's
        # read-back verification exercises the same path it would live.
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
    # cast rather than a Protocol: FakeClient implements only the three methods
    # these tests reach, and widening SheetsClient into an interface to please a
    # test would put indirection in production code for no production reason.
    runtime._client = cast(SheetsClient, client)  # noqa: SLF001
    return runtime


def item(name: str, *values: str) -> dict[str, Any]:
    return {"name": name, "values": list(values)}


# --- placement --------------------------------------------------------------


async def test_new_block_leaves_a_blank_separator_row() -> None:
    # Last populated row is 7, so the blank stays at 8 and the date lands at 9.
    client = FakeClient(SHEET)
    result = await log_session(
        runtime_with(client), "training", [item("Присідання", "100x5x5")], when="17.08.2026"
    )
    assert result["mode"] == "new-block"
    assert result["range"] == "'Лист1'!A9:G10"
    assert result["rows"][0][0] == "17.08.2026"
    assert result["rows"][1][0] == "Присідання"


async def test_appends_to_the_existing_block_when_the_date_matches() -> None:
    client = FakeClient(SHEET)
    result = await log_session(
        runtime_with(client), "training", [item("Присідання", "100x5x5")], when="04.08.2026"
    )
    # No new date row, and it starts immediately after the last item at row 7.
    assert result["mode"] == "append-to-existing"
    assert result["range"] == "'Лист1'!A8:G8"
    assert result["rows"] == [["Присідання", "100x5x5", "", "", "", "", ""]]


async def test_writes_never_touch_an_existing_row() -> None:
    # The property that makes skipping §3.5's copies survivable: every written
    # row is below everything already in the sheet.
    client = FakeClient(SHEET)
    for when in ("04.08.2026", "17.08.2026"):
        result = await log_session(runtime_with(client), "training", [item("X", "1")], when=when)
        first_written = int(result["range"].split("!A")[1].split(":")[0])
        assert first_written > 7


async def test_values_land_in_their_own_columns_left_to_right() -> None:
    client = FakeClient(SHEET)
    result = await log_session(
        runtime_with(client), "training", [item("Жим", "20x20", "40x20", "90x4x3")], when="17.08.2026"
    )
    assert result["rows"][1] == ["Жим", "20x20", "40x20", "90x4x3", "", "", ""]


async def test_values_are_written_verbatim() -> None:
    # §7.3: the server does not parse set notation. 8x3 is 8 reps by 3 sets for
    # pull-ups but 8kg by 3 reps if read as weighted, and that ambiguity is not
    # resolvable here.
    client = FakeClient(SHEET)
    result = await log_session(
        runtime_with(client), "training", [item("Підтягування", "8x3")], when="17.08.2026"
    )
    assert result["rows"][1][1] == "8x3"


async def test_a_single_string_value_is_accepted() -> None:
    client = FakeClient(SHEET)
    result = await log_session(
        runtime_with(client), "training", [{"name": "Присідання", "values": "100x5"}], when="17.08.2026"
    )
    assert result["rows"][1][1] == "100x5"


# --- refusals ---------------------------------------------------------------


async def test_a_date_before_the_last_block_is_refused() -> None:
    # Appending an old date at the bottom would break the ordering the layout
    # depends on, and mid-sheet insertion is out of scope.
    client = FakeClient(SHEET)
    with pytest.raises(DateOutOfOrder, match="04.08.2026"):
        await log_session(runtime_with(client), "training", [item("X", "1")], when="01.08.2026")
    assert client.writes == []


async def test_too_many_values_names_the_limit() -> None:
    client = FakeClient(SHEET)
    with pytest.raises(TooManyValues, match="room for 6"):
        await log_session(
            runtime_with(client),
            "training",
            [item("X", "1", "2", "3", "4", "5", "6", "7")],
            when="17.08.2026",
        )
    assert client.writes == []


async def test_the_grid_profile_is_refused_with_the_right_tool_named() -> None:
    client = FakeClient(SHEET)
    with pytest.raises(WrongLayout, match="set_grid_value"):
        await log_session(runtime_with(client), "study", [item("X", "1")])
    assert client.writes == []


async def test_an_item_without_a_name_is_refused() -> None:
    client = FakeClient(SHEET)
    with pytest.raises(ValidationError, match="items\\[0\\] has no name"):
        await log_session(runtime_with(client), "training", [{"values": ["8x3"]}], when="17.08.2026")


async def test_empty_items_is_refused() -> None:
    client = FakeClient(SHEET)
    with pytest.raises(ValidationError, match="must not be empty"):
        await log_session(runtime_with(client), "training", [], when="17.08.2026")


async def test_an_unreadable_date_says_what_is_accepted() -> None:
    client = FakeClient(SHEET)
    with pytest.raises(ValidationError, match="DD.MM.YYYY"):
        await log_session(runtime_with(client), "training", [item("X", "1")], when="next tuesday")


async def test_an_unknown_mode_is_refused() -> None:
    client = FakeClient(SHEET)
    with pytest.raises(ValidationError, match="mode must be one of"):
        await log_session(runtime_with(client), "training", [item("X", "1")], mode="overwrite")


# --- dry run and verification ----------------------------------------------


async def test_dry_run_writes_nothing_but_reports_the_plan() -> None:
    client = FakeClient(SHEET)
    result = await log_session(
        runtime_with(client), "training", [item("X", "1")], when="17.08.2026", dry_run=True
    )
    assert client.writes == []
    assert result["dry_run"] is True
    assert result["range"] == "'Лист1'!A9:G10"
    assert "Nothing was written" in result["note"]


async def test_dry_run_and_real_write_agree_on_the_plan() -> None:
    # They share one code path; this guards against that being refactored apart,
    # since a dry run computing its answer differently reassures about nothing.
    dry = await log_session(
        runtime_with(FakeClient(SHEET)), "training", [item("X", "1")], when="17.08.2026", dry_run=True
    )
    wet = await log_session(runtime_with(FakeClient(SHEET)), "training", [item("X", "1")], when="17.08.2026")
    assert dry["range"] == wet["range"]
    assert dry["rows"] == wet["rows"]


async def test_the_response_reads_the_sheet_back() -> None:
    client = FakeClient(SHEET)
    result = await log_session(
        runtime_with(client), "training", [item("Присідання", "100x5x5")], when="17.08.2026"
    )
    assert result["verified"] is True
    assert result["written"][1][0] == "Присідання"


async def test_dates_are_written_in_the_four_digit_form() -> None:
    # §7.5: the sheet contains both DD.MM.YY and DD.MM.YYYY; only the long form
    # is ever written back.
    client = FakeClient(SHEET)
    result = await log_session(runtime_with(client), "training", [item("X", "1")], when="2026-08-17")
    assert result["date"] == "17.08.2026"
    assert result["rows"][0][0] == "17.08.2026"


async def test_today_and_yesterday_resolve_in_the_server_timezone() -> None:
    from zoneinfo import ZoneInfo

    from sheets_mcp import dates

    prague_today = dates.today(ZoneInfo("Europe/Prague"))
    empty = FakeClient([["Тренування"]])
    result = await log_session(runtime_with(empty), "training", [item("X", "1")], when="today")
    assert result["date"] == prague_today.strftime("%d.%m.%Y")

    yesterday = date.fromordinal(prague_today.toordinal() - 1)
    result = await log_session(runtime_with(empty), "training", [item("X", "1")], when="yesterday")
    assert result["date"] == yesterday.strftime("%d.%m.%Y")


async def test_an_empty_sheet_starts_at_row_2() -> None:
    # Nothing populated, so the separator convention still applies rather than
    # writing into row 1.
    client = FakeClient([])
    result = await log_session(runtime_with(client), "training", [item("X", "1")], when="17.08.2026")
    assert result["range"] == "'Лист1'!A2:G3"


async def test_planned_rows_are_exactly_as_wide_as_their_range() -> None:
    """Same invariant as the grid planner, which failed this on a real sheet.

    A no-op for this profile because its range starts at column A. That is the
    point: the identical bug went unnoticed here and only surfaced where the
    first used column was not A.
    """
    from sheets_mcp.profiles.models import column_index

    result = await log_session(
        runtime_with(FakeClient(SHEET)), "training", [item("X", "1")], when="17.08.2026"
    )
    body = result["range"].split("!", 1)[1]
    first, last = body.split(":")
    start = column_index("".join(c for c in first if c.isalpha()))
    end = column_index("".join(c for c in last if c.isalpha()))
    assert all(len(row) == end - start + 1 for row in result["rows"])
