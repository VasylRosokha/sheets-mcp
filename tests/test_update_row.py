from __future__ import annotations

import pytest

from sheets_mcp.errors import (
    ProtectedRow,
    RowConflict,
    RowNotFound,
    ValidationError,
    WrongLayout,
)
from sheets_mcp.tools import update_row
from tests.fakes import FakeSheet, runtime_with

# Rows 1..8, matching the read-tools fixture so a find_row result can be fed
# straight in.
TRAINING: list[list[str]] = [
    ["Тренування"],
    ["31.07.2026"],
    ["Жим штанги лежачи", "80x8x3"],
    ["Присідання", "100x5x5"],
    [],
    ["04.08.2026"],
    ["Підтягування", "8x3"],
    ["Жим штанги лежачи", "82.5x8x3", "40x20"],
]

STUDY: list[list[str]] = [
    [],
    ["", "День", "Серпень", ""],
    ["", "", "Програмування", "Читання"],
    *[["", str(day), "", ""] for day in range(1, 32)],
]


def training() -> FakeSheet:
    return FakeSheet(TRAINING, title="Тренування2026")


# --- the flagship correction ------------------------------------------------


async def test_it_changes_one_cell_and_leaves_the_rest_of_the_row_alone() -> None:
    sheet = training()
    result = await update_row(runtime_with(sheet), "training", 8, {"B": "85x8x3"})
    assert result["verified"] is True
    # C survives untouched: only the named cell was written.
    assert sheet.rows[7] == ["Жим штанги лежачи", "85x8x3", "40x20"]
    assert result["row_after"] == {"A": "Жим штанги лежачи", "B": "85x8x3", "C": "40x20"}


async def test_only_the_named_cells_are_sent_each_as_its_own_range() -> None:
    # A single range spanning them would rewrite everything in between, and
    # filling the gaps from a prior read would race with anyone editing.
    sheet = training()
    await update_row(runtime_with(sheet), "training", 8, {"A": "Жим лежачи", "C": "45x20"})
    assert sheet.batches == [("'Лист1'!A8", [["Жим лежачи"]]), ("'Лист1'!C8", [["45x20"]])]
    assert sheet.rows[7][1] == "82.5x8x3"


async def test_changes_report_both_sides_of_the_overwrite() -> None:
    result = await update_row(runtime_with(training()), "training", 8, {"B": "85x8x3"})
    assert result["changes"] == [
        {"cell": "B8", "column": "B", "from": "82.5x8x3", "to": "85x8x3"}
    ]


async def test_without_expect_the_response_says_so() -> None:
    result = await update_row(runtime_with(training()), "training", 8, {"B": "85x8x3"})
    assert result["expect_checked"] is False
    assert "not undoable" in result["note"]


# --- optimistic concurrency -------------------------------------------------


async def test_a_matching_expect_lets_the_write_through() -> None:
    sheet = training()
    result = await update_row(
        runtime_with(sheet),
        "training",
        8,
        {"B": "85x8x3"},
        expect={"A": "Жим штанги лежачи", "B": "82.5x8x3"},
    )
    assert result["expect_checked"] is True
    assert "note" not in result
    assert sheet.rows[7][1] == "85x8x3"


async def test_a_stale_expect_aborts_and_writes_nothing() -> None:
    sheet = training()
    with pytest.raises(RowConflict, match="B is '82.5x8x3', not the expected '80x8x3'"):
        await update_row(
            runtime_with(sheet), "training", 8, {"B": "85x8x3"}, expect={"B": "80x8x3"}
        )
    assert sheet.batches == []
    assert sheet.rows[7][1] == "82.5x8x3"


async def test_expect_catches_the_row_having_shifted_under_the_caller() -> None:
    # The failure this guards: a row number read a minute ago now points at a
    # different exercise because the sheet was edited in the Sheets app.
    sheet = training()
    with pytest.raises(RowConflict, match="Re-read the row"):
        await update_row(
            runtime_with(sheet),
            "training",
            7,
            {"B": "85x8x3"},
            expect={"A": "Жим штанги лежачи"},
        )
    assert sheet.batches == []


async def test_expect_may_name_a_column_that_is_not_being_written() -> None:
    sheet = training()
    await update_row(
        runtime_with(sheet), "training", 8, {"B": "85x8x3"}, expect={"C": "40x20"}
    )
    assert sheet.rows[7][1] == "85x8x3"


# --- structural refusals ----------------------------------------------------


async def test_a_date_row_refuses_anything_but_its_date() -> None:
    # Writing an exercise name over a date merges two sessions, and the sheet
    # gives no sign of it: the reader simply stops seeing a block boundary.
    sheet = training()
    with pytest.raises(ProtectedRow, match="date row of the 04.08.2026 block"):
        await update_row(runtime_with(sheet), "training", 6, {"B": "8x3"})
    assert sheet.batches == []


async def test_a_date_row_accepts_a_corrected_date() -> None:
    sheet = training()
    result = await update_row(runtime_with(sheet), "training", 6, {"A": "05.08.2026"})
    assert result["verified"] is True
    assert sheet.rows[5][0] == "05.08.2026"


async def test_a_date_row_refuses_a_value_that_is_not_a_date() -> None:
    sheet = training()
    with pytest.raises(ProtectedRow, match="DD.MM.YYYY"):
        await update_row(runtime_with(sheet), "training", 6, {"A": "yesterday"})
    assert sheet.batches == []


async def test_a_blank_separator_row_is_refused() -> None:
    # Row 5 is the blank between the two blocks. Filling it in joins them.
    sheet = training()
    with pytest.raises(ProtectedRow, match="not part of any dated block"):
        await update_row(runtime_with(sheet), "training", 5, {"A": "Присідання"})
    assert sheet.batches == []


async def test_the_title_row_is_refused() -> None:
    sheet = training()
    with pytest.raises(ProtectedRow, match="not part of any dated block"):
        await update_row(runtime_with(sheet), "training", 1, {"A": "Щось"})
    assert sheet.batches == []


async def test_a_row_below_the_data_is_refused_and_names_the_last_one() -> None:
    sheet = training()
    with pytest.raises(RowNotFound, match="last populated row is 8"):
        await update_row(runtime_with(sheet), "training", 40, {"A": "Присідання"})
    assert sheet.batches == []


# --- argument validation ----------------------------------------------------


async def test_a_grid_profile_is_refused_and_names_set_grid_value() -> None:
    sheet = FakeSheet(STUDY, title="Навчання 2026")
    with pytest.raises(WrongLayout, match="set_grid_value"):
        await update_row(runtime_with(sheet), "study", 5, {"C": "2h"})


async def test_a_column_outside_the_profile_is_refused() -> None:
    with pytest.raises(ValidationError, match="outside this profile's columns"):
        await update_row(runtime_with(training()), "training", 8, {"J": "x"})


async def test_a_key_that_is_not_a_column_letter_is_refused() -> None:
    with pytest.raises(ValidationError, match="not a spreadsheet column letter"):
        await update_row(runtime_with(training()), "training", 8, {"weight": "85"})


async def test_empty_cells_is_refused_with_an_example() -> None:
    with pytest.raises(ValidationError, match="85x8x3"):
        await update_row(runtime_with(training()), "training", 8, {})


async def test_a_row_number_below_one_is_refused() -> None:
    with pytest.raises(ValidationError, match="row must be 1 or greater"):
        await update_row(runtime_with(training()), "training", 0, {"A": "x"})


async def test_lowercase_column_letters_are_accepted() -> None:
    sheet = training()
    await update_row(runtime_with(sheet), "training", 8, {"b": "85x8x3"})
    assert sheet.rows[7][1] == "85x8x3"


async def test_a_numeric_value_is_coerced_rather_than_rejected() -> None:
    sheet = training()
    await update_row(runtime_with(sheet), "training", 8, {"B": 85})
    assert sheet.rows[7][1] == "85"


# --- dry run ----------------------------------------------------------------


async def test_dry_run_writes_nothing_but_shows_the_change() -> None:
    sheet = training()
    result = await update_row(runtime_with(sheet), "training", 8, {"B": "85x8x3"}, dry_run=True)
    assert sheet.batches == []
    assert sheet.rows[7][1] == "82.5x8x3"
    assert result["changes"][0]["from"] == "82.5x8x3"
    assert "Nothing was written" in result["note"]


async def test_dry_run_still_enforces_expect() -> None:
    # A dry run that skipped the check would report a change the real call
    # would refuse, which is worse than not offering the dry run at all.
    with pytest.raises(RowConflict):
        await update_row(
            runtime_with(training()), "training", 8, {"B": "85"}, expect={"B": "80x8x3"}, dry_run=True
        )


async def test_dry_run_and_the_real_call_agree_on_the_change() -> None:
    dry = await update_row(runtime_with(training()), "training", 8, {"B": "85x8x3"}, dry_run=True)
    wet = await update_row(runtime_with(training()), "training", 8, {"B": "85x8x3"})
    assert dry["changes"] == wet["changes"]
    assert dry["range"] == wet["range"]
