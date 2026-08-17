from __future__ import annotations

from datetime import date

import pytest

from sheets_mcp.dates import DateFormatError, parse, render, to_strptime
from sheets_mcp.layouts import dated_block, grid
from sheets_mcp.profiles.models import DatedBlockProfile, GridProfile, ProfileRegistry

TRAINING = DatedBlockProfile.model_validate(
    {
        "name": "training",
        "layout": "dated-block",
        "description": "x",
        "spreadsheet_id": "s",
        "tab": "Лист1",
        "date_column": "A",
        "item_column": "A",
        "value_columns": ["B", "C", "D", "E", "F", "G"],
        "date_formats": ["DD.MM.YYYY", "DD.MM.YY"],
        "write_date_format": "DD.MM.YYYY",
    }
)

STUDY = GridProfile.model_validate(
    {
        "name": "study",
        "layout": "grid",
        "description": "x",
        "spreadsheet_id": "s",
        "tab": "Лист1",
        "day_column": "B",
        "period_header_offset": -2,
        "label_row_offset": -1,
        "columns": [
            {"key": "programming", "label": "Програмування", "aliases": ["код"]},
            {"key": "reading", "label": "Читання"},
        ],
    }
)


# --- date formats -----------------------------------------------------------


@pytest.mark.parametrize(
    ("profile_format", "expected"),
    [("DD.MM.YYYY", "%d.%m.%Y"), ("DD.MM.YY", "%d.%m.%y"), ("YYYY-MM-DD", "%Y-%m-%d")],
)
def test_format_translation(profile_format: str, expected: str) -> None:
    assert to_strptime(profile_format) == expected


def test_yyyy_wins_over_yy() -> None:
    # Matching YY first would translate YYYY to "%y%y" and parse 2026 as 20.
    assert to_strptime("YYYY") == "%Y"


def test_unrecognised_letters_are_rejected() -> None:
    with pytest.raises(DateFormatError, match="unrecognised letters"):
        to_strptime("DD.MM.YYYY at HH")


def test_both_real_formats_parse() -> None:
    # §7.5: the sheet mixes four- and two-digit years.
    assert parse("04.08.2026", TRAINING.date_formats) == date(2026, 8, 4)
    assert parse("14.05.26", TRAINING.date_formats) == date(2026, 5, 14)


def test_non_dates_return_none_rather_than_raising() -> None:
    # Scanning a column for dates makes non-dates the normal case.
    assert parse("Підтягування", TRAINING.date_formats) is None
    assert parse("", TRAINING.date_formats) is None
    assert parse("8x3", TRAINING.date_formats) is None


def test_writes_use_the_four_digit_form() -> None:
    assert render(date(2026, 8, 4), TRAINING.write_date_format) == "04.08.2026"


# --- dated-block ------------------------------------------------------------

BLOCKS = [
    ["Тренування"],
    ["31.07.2026"],
    ["Жим штанги лежачи", "20x20", "40x20", "90x4x3"],
    [],
    ["04.08.2026"],
    ["Підтягування", "8x3"],
    ["Віджимання на брусях", "12x3"],
]


def test_blocks_are_found_with_absolute_row_numbers() -> None:
    blocks = dated_block.scan_blocks(BLOCKS, TRAINING)
    assert [b.raw_date for b in blocks] == ["31.07.2026", "04.08.2026"]
    # Row 2 in the sheet, because the fixture starts at row 1 with a title.
    assert blocks[0].row == 2
    assert blocks[1].row == 5


def test_items_belong_to_the_preceding_date() -> None:
    blocks = dated_block.scan_blocks(BLOCKS, TRAINING)
    assert [i.name for i in blocks[1].items] == ["Підтягування", "Віджимання на брусях"]


def test_trailing_blank_values_are_dropped_not_padded() -> None:
    # A one-value row must not report six empty columns.
    blocks = dated_block.scan_blocks(BLOCKS, TRAINING)
    assert blocks[1].items[0].values == ("8x3",)
    assert blocks[0].items[0].values == ("20x20", "40x20", "90x4x3")


def test_interior_blanks_are_preserved() -> None:
    # Collapsing a real gap would shift values into the wrong columns.
    rows = [["04.08.2026"], ["Жим", "90x4x3", "", "40x20"]]
    blocks = dated_block.scan_blocks(rows, TRAINING)
    assert blocks[0].items[0].values == ("90x4x3", "", "40x20")


def test_rows_above_the_first_date_are_ignored() -> None:
    blocks = dated_block.scan_blocks(BLOCKS, TRAINING)
    assert all(i.name != "Тренування" for b in blocks for i in b.items)


def test_recent_item_names_are_newest_first_and_distinct() -> None:
    blocks = dated_block.scan_blocks(BLOCKS, TRAINING)
    names = dated_block.recent_item_names(blocks)
    assert names[0] == "Підтягування"
    assert names.count("Підтягування") == 1
    assert "Жим штанги лежачи" in names


# --- grid -------------------------------------------------------------------

GRID = [
    ["", "", "", ""],
    ["", "День", "Липень", ""],
    ["", "", "Програмування", "Крипта"],
    ["", "1", "||", ""],
    ["", "2", "", "|"],
    ["", "3", "", ""],
    [],
    ["", "День", "Серпень", ""],
    ["", "", "Програмування", "Читання"],
    ["", "1", "|", ""],
    ["", "2", "", ""],
]


def test_periods_are_found_with_their_own_labels() -> None:
    periods = grid.scan_periods(GRID, STUDY)
    assert [p.name for p in periods] == ["Липень", "Серпень"]
    # §7.5: July really did carry Крипта where config says Читання. Config
    # must not win, or reading hours land in a crypto column.
    assert periods[0].labels == ("Програмування", "Крипта")
    assert periods[1].labels == ("Програмування", "Читання")


def test_day_rows_are_absolute() -> None:
    periods = grid.scan_periods(GRID, STUDY)
    assert periods[1].first_day_row == 10
    assert periods[1].day_row(2) == 11


def test_day_beyond_the_block_returns_none() -> None:
    # §7.5: writing day 31 into a 30-day month must be rejected by logic, not
    # by the sheet happening to have 31 rows.
    periods = grid.scan_periods(GRID, STUDY)
    assert periods[1].day_row(3) is None


def test_a_day_run_without_a_period_header_is_not_a_block() -> None:
    # §7.5's stray trailing row: a day number and a value, no header above it.
    stray = [*GRID, [], ["", "1", "|"]]
    periods = grid.scan_periods(stray, STUDY)
    assert [p.name for p in periods] == ["Липень", "Серпень"]


def test_column_resolution_matches_label_and_alias_case_insensitively() -> None:
    periods = grid.scan_periods(GRID, STUDY)
    august = periods[1]
    assert grid.resolve_column(august, "Програмування") == 2
    assert grid.resolve_column(august, "програмування") == 2
    assert grid.resolve_column(august, "код", aliases=("Програмування",)) == 2
    assert grid.resolve_column(august, "Англійська") is None


def test_the_real_registry_drives_these_layouts() -> None:
    """Guards against the fixtures drifting from profiles.yaml."""
    from pathlib import Path

    import yaml

    raw = yaml.safe_load(Path("profiles.yaml").read_text(encoding="utf-8"))
    reg = ProfileRegistry.model_validate(raw)
    training = reg.resolve("training")
    study = reg.resolve("study")
    assert isinstance(training, DatedBlockProfile)
    assert isinstance(study, GridProfile)
    assert training.date_formats == TRAINING.date_formats
    assert study.period_header_offset == STUDY.period_header_offset
    assert study.label_row_offset == STUDY.label_row_offset
