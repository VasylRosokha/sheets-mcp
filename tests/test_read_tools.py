from __future__ import annotations

from datetime import date

import pytest
from tests.fakes import FakeSheet, runtime_with

from sheets_mcp.errors import ValidationError, WrongLayout
from sheets_mcp.layouts import grid
from sheets_mcp.layouts.grid import Period
from sheets_mcp.profiles.models import GridProfile
from sheets_mcp.tools import find_row, query_rows

# Rows 1..8. Two blocks either side of a month boundary, with one exercise
# appearing in both — which is the case find_row has to rank rather than just
# locate.
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

# Explicit years in the period headers so these assertions do not depend on the
# day the suite runs. The inference path, which the real sheet needs because its
# headers are bare month names, is covered separately below.
STUDY: list[list[str]] = [
    [],
    ["", "День", "Липень 2026", ""],
    ["", "", "Програмування", "Читання"],
    *[
        ["", str(day), "||" if day == 1 else ("2h" if day == 2 else ""), "|" if day == 2 else ""]
        for day in range(1, 32)
    ],
    [],
    ["", "День", "Серпень 2026", ""],
    ["", "", "Програмування", "Читання"],
    *[["", str(day), "1.5h" if day == 3 else "", ""] for day in range(1, 32)],
]


def training() -> FakeSheet:
    return FakeSheet(TRAINING, title="Тренування2026")


def study() -> FakeSheet:
    return FakeSheet(STUDY, title="Навчання 2026")


# --- query_rows: dated-block ------------------------------------------------


async def test_blocks_come_back_newest_first_with_their_sheet_rows() -> None:
    result = await query_rows(runtime_with(training()), "training")
    assert [block["date"] for block in result["blocks"]] == ["04.08.2026", "31.07.2026"]
    assert result["blocks"][0]["row"] == 6
    assert [item["row"] for item in result["blocks"][0]["items"]] == [7, 8]


async def test_order_asc_reverses_it() -> None:
    result = await query_rows(runtime_with(training()), "training", order="asc")
    assert [block["date"] for block in result["blocks"]] == ["31.07.2026", "04.08.2026"]


async def test_limit_counts_blocks_not_rows() -> None:
    result = await query_rows(runtime_with(training()), "training", limit=1)
    assert result["blocks_matched"] == 2
    assert result["blocks_returned"] == 1
    # One block, but that block still carries both of its item rows.
    assert len(result["blocks"]) == 1
    assert len(result["blocks"][0]["items"]) == 2


async def test_since_and_until_bound_the_window() -> None:
    result = await query_rows(runtime_with(training()), "training", since="2026-08-01")
    assert [block["date"] for block in result["blocks"]] == ["04.08.2026"]

    result = await query_rows(runtime_with(training()), "training", until="2026-07-31")
    assert [block["date"] for block in result["blocks"]] == ["31.07.2026"]


async def test_bounds_accept_the_sheets_own_date_format() -> None:
    result = await query_rows(runtime_with(training()), "training", since="04.08.2026")
    assert [block["date"] for block in result["blocks"]] == ["04.08.2026"]


async def test_contains_filters_items_and_drops_blocks_left_empty() -> None:
    result = await query_rows(runtime_with(training()), "training", contains="присід")
    assert result["blocks_matched"] == 1
    assert result["blocks"][0]["date"] == "31.07.2026"
    # The other exercise in that block is not returned; only the match is.
    assert [item["name"] for item in result["blocks"][0]["items"]] == ["Присідання"]


async def test_cells_are_keyed_by_column_letter_and_omit_blanks() -> None:
    result = await query_rows(runtime_with(training()), "training", limit=1)
    bench = result["blocks"][0]["items"][1]
    assert bench["cells"] == {"A": "Жим штанги лежачи", "B": "82.5x8x3", "C": "40x20"}


async def test_the_cells_map_is_the_shape_update_row_expects() -> None:
    # Not a formality: the two tools are only usable together if a read's output
    # drops straight into a correction's input without being reshaped.
    from sheets_mcp.tools.update_row import _resolve_key

    result = await query_rows(runtime_with(training()), "training", limit=1)
    profile = runtime_with(training()).require_profile("training")
    for key in result["blocks"][0]["items"][0]["cells"]:
        assert _resolve_key(key, profile) >= 0


# --- query_rows: grid -------------------------------------------------------


async def test_grid_returns_only_days_that_hold_something() -> None:
    result = await query_rows(runtime_with(study()), "study")
    assert result["days_matched"] == 3
    assert {day["day"] for day in result["days"]} == {1, 2, 3}


async def test_grid_parses_both_notations_into_hours() -> None:
    result = await query_rows(runtime_with(study()), "study", order="asc")
    by_day = {day["day"]: day["hours"] for day in result["days"]}
    assert by_day[1] == {"Програмування": 2.0}      # "||" tally
    assert by_day[2] == {"Програмування": 2.0, "Читання": 1.0}
    assert by_day[3] == {"Програмування": 1.5}      # "1.5h"


async def test_grid_totals_cover_the_window_not_the_page() -> None:
    # The trap this exists to close: a model asking for one day and summing what
    # it sees would report 1.5 hours for the whole window.
    result = await query_rows(runtime_with(study()), "study", limit=1)
    assert result["days_returned"] == 1
    assert result["totals"] == {"Програмування": 5.5, "Читання": 1.0}


async def test_grid_totals_split_by_period() -> None:
    result = await query_rows(runtime_with(study()), "study")
    assert result["totals_by_period"]["Липень 2026"] == {"Програмування": 4.0, "Читання": 1.0}
    assert result["totals_by_period"]["Серпень 2026"] == {"Програмування": 1.5}


async def test_grid_dates_are_real_calendar_dates() -> None:
    result = await query_rows(runtime_with(study()), "study", order="asc")
    assert [day["date"] for day in result["days"]] == ["2026-07-01", "2026-07-02", "2026-08-03"]


async def test_grid_since_until_filter_across_blocks() -> None:
    result = await query_rows(runtime_with(study()), "study", since="2026-08-01")
    assert [day["date"] for day in result["days"]] == ["2026-08-03"]
    assert result["totals"] == {"Програмування": 1.5}


async def test_grid_contains_narrows_to_one_activity() -> None:
    result = await query_rows(runtime_with(study()), "study", contains="читання")
    assert result["totals"] == {"Читання": 1.0}


async def test_an_unreadable_cell_is_reported_and_left_out_of_the_totals() -> None:
    sheet = study()
    sheet.rows[3][2] = "два"  # July day 1, Програмування
    result = await query_rows(runtime_with(sheet), "study")
    assert result["unreadable_cells"] == [{"cell": "C4", "text": "два"}]
    assert result["totals"]["Програмування"] == 3.5  # 2h + 1.5h, the "два" excluded


async def test_a_block_whose_month_is_unrecognisable_is_named_not_dropped_silently() -> None:
    sheet = study()
    sheet.rows[1][2] = "Q3"
    result = await query_rows(runtime_with(sheet), "study")
    assert result["undated_periods"] == ["Q3"]
    assert result["totals"] == {"Програмування": 1.5}


# --- query_rows: refusals ---------------------------------------------------


async def test_a_bad_order_is_refused() -> None:
    with pytest.raises(ValidationError, match="order must be"):
        await query_rows(runtime_with(training()), "training", order="newest")


async def test_a_limit_outside_the_range_is_refused() -> None:
    with pytest.raises(ValidationError, match="between 1 and 200"):
        await query_rows(runtime_with(training()), "training", limit=500)


async def test_since_after_until_is_refused_rather_than_returning_nothing() -> None:
    # Silently returning an empty list would read as "you did nothing that week".
    with pytest.raises(ValidationError, match="is after"):
        await query_rows(
            runtime_with(training()), "training", since="2026-08-10", until="2026-08-01"
        )


async def test_an_unparseable_bound_names_the_accepted_formats() -> None:
    with pytest.raises(ValidationError, match="DD.MM.YYYY"):
        await query_rows(runtime_with(training()), "training", since="last tuesday")


# --- find_row ---------------------------------------------------------------


async def test_find_row_ranks_the_most_recent_of_equal_matches_first() -> None:
    result = await find_row(runtime_with(training()), "training", "жим")
    assert result["matched"] == 2
    assert [match["row"] for match in result["matches"]] == [8, 3]


async def test_find_row_matches_values_as_well_as_names() -> None:
    result = await find_row(runtime_with(training()), "training", "100x5x5")
    assert [match["row"] for match in result["matches"]] == [4]
    assert result["matches"][0]["name"] == "Присідання"


async def test_an_exact_name_outranks_a_longer_one_containing_it() -> None:
    sheet = FakeSheet([["Тренування"], ["04.08.2026"], ["Жим штанги лежачи", "80x8x3"], ["Жим", "60x10"]])
    result = await find_row(runtime_with(sheet), "training", "жим")
    assert result["matches"][0]["row"] == 4
    assert result["matches"][0]["score"] == 1.0


async def test_find_row_carries_the_block_date_so_the_match_can_be_confirmed() -> None:
    result = await find_row(runtime_with(training()), "training", "підтягування")
    assert result["matches"][0]["date"] == "04.08.2026"
    assert result["matches"][0]["iso"] == "2026-08-04"


async def test_find_row_returns_cells_for_the_correction_call() -> None:
    result = await find_row(runtime_with(training()), "training", "підтягування")
    assert result["matches"][0]["cells"] == {"A": "Підтягування", "B": "8x3"}


async def test_no_match_is_an_empty_list_not_an_error() -> None:
    result = await find_row(runtime_with(training()), "training", "марафон")
    assert result["matched"] == 0
    assert result["matches"] == []


async def test_find_row_refuses_a_grid_and_names_query_rows() -> None:
    with pytest.raises(WrongLayout, match="query_rows"):
        await find_row(runtime_with(study()), "study", "програмування")


async def test_an_empty_query_is_refused() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        await find_row(runtime_with(training()), "training", "   ")


# --- assign_dates: the inference the real sheet depends on ------------------


def grid_profile() -> GridProfile:
    from sheets_mcp.profiles.loader import load_registry

    profile = load_registry("profiles.yaml").resolve("study")
    assert isinstance(profile, GridProfile)
    return profile


def period(name: str) -> Period:
    return Period(
        name=name,
        header_row=1,
        label_row=2,
        first_day_row=3,
        last_day_row=33,
        labels=("Програмування",),
        label_columns=(2,),
    )


def test_the_newest_block_takes_the_current_year() -> None:
    dated = grid.assign_dates([period("Липень"), period("Серпень")], grid_profile(), date(2026, 8, 19))
    assert [(entry.year, entry.month) for entry in dated] == [(2026, 7), (2026, 8)]


def test_a_newest_block_still_ahead_of_today_belongs_to_last_year() -> None:
    # Reading a sheet in January whose last block is Грудень: that December was
    # last month, not eleven months away.
    dated = grid.assign_dates([period("Грудень")], grid_profile(), date(2027, 1, 5))
    assert [(entry.year, entry.month) for entry in dated] == [(2026, 12)]


def test_the_year_decrements_where_the_months_stop_descending() -> None:
    blocks = [period("Листопад"), period("Грудень"), period("Січень"), period("Лютий")]
    dated = grid.assign_dates(blocks, grid_profile(), date(2027, 2, 10))
    assert [(entry.year, entry.month) for entry in dated] == [
        (2026, 11),
        (2026, 12),
        (2027, 1),
        (2027, 2),
    ]


def test_an_explicit_year_in_the_header_wins_over_the_inference() -> None:
    dated = grid.assign_dates([period("Липень 2024")], grid_profile(), date(2026, 8, 19))
    assert (dated[0].year, dated[0].month) == (2024, 7)


def test_an_unrecognisable_header_gets_no_month_rather_than_a_guess() -> None:
    dated = grid.assign_dates([period("Q3"), period("Серпень")], grid_profile(), date(2026, 8, 19))
    assert dated[0].month is None
    assert dated[0].date_for(1) is None
    assert (dated[1].year, dated[1].month) == (2026, 8)


def test_date_for_refuses_a_day_the_month_does_not_have() -> None:
    dated = grid.assign_dates([period("Лютий")], grid_profile(), date(2026, 3, 1))
    assert dated[0].date_for(28) == date(2026, 2, 28)
    assert dated[0].date_for(31) is None
