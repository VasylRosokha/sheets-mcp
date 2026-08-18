from __future__ import annotations

import pytest

from sheets_mcp import durations

# --- reading the old notation, which seven months of history use -------------


@pytest.mark.parametrize(
    ("cell", "hours"),
    [
        ("|", 1.0),
        ("||", 2.0),
        ("-", 0.5),
        ("|-", 1.5),
        ("| -", 1.5),  # the real 23 May cell; the stray space means nothing
        ("|||||", 5.0),
        ("", 0.0),
        ("   ", 0.0),
        ("-|", 1.5),  # lenient on order, though nothing writes it this way
    ],
)
def test_tally_cells_parse(cell: str, hours: float) -> None:
    assert durations.parse(cell) == hours


# --- reading the new notation ------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "hours"),
    [
        ("1h", 1.0),
        ("1.5h", 1.5),
        ("0.5h", 0.5),
        ("2H", 2.0),
        ("1,5h", 1.5),  # a Ukrainian or Czech keyboard produces the comma
        ("3", 3.0),  # a bare number typed straight into the sheet
        ("2 h", 2.0),
        ("2год", 2.0),
    ],
)
def test_hour_cells_parse(cell: str, hours: float) -> None:
    assert durations.parse(cell) == hours


def test_unreadable_cells_return_none_rather_than_zero() -> None:
    # A wrong reading silently corrupts an increment, so the caller reports
    # UNPARSEABLE_CELL with the raw text instead of guessing (§7.3).
    assert durations.parse("два") is None
    assert durations.parse("|x") is None
    assert durations.parse("1h30m") is None


def test_both_notations_are_read_regardless_of_the_write_setting() -> None:
    # Switching the profile to duration-hours must not make history unreadable.
    assert durations.parse("||") == 2.0
    assert durations.parse("2h") == 2.0


# --- writing -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("hours", "rendered"),
    [(1.0, "1h"), (1.5, "1.5h"), (0.5, "0.5h"), (2.0, "2h"), (0.0, ""), (10.5, "10.5h")],
)
def test_hours_render(hours: float, rendered: str) -> None:
    assert durations.render(hours, style="duration-hours") == rendered


@pytest.mark.parametrize(
    ("hours", "rendered"),
    [(1.0, "|"), (1.5, "|-"), (0.5, "-"), (5.0, "|||||"), (0.0, "")],
)
def test_tally_renders_canonically(hours: float, rendered: str) -> None:
    # Strict on write even though parsing is lenient: "| -" and "|-" both read
    # as 1.5, but only one of them looks deliberate.
    assert durations.render(hours, style="duration-tally") == rendered


def test_zero_renders_empty_so_a_cleared_cell_looks_cleared() -> None:
    assert durations.render(0, style="duration-hours") == ""
    assert durations.render(0, style="duration-tally") == ""


# --- rounding ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "rounded"),
    [(0.66, 0.5), (0.75, 1.0), (1.2, 1.0), (1.4, 1.5), (2.0, 2.0)],
)
def test_input_is_rounded_to_the_step(given: float, rounded: float) -> None:
    # "I studied for 40 minutes" becomes 0.5 rather than an error (§7.3).
    assert durations.round_to_step(given, 0.5) == rounded


def test_a_round_trip_survives_both_notations() -> None:
    for hours in (0.5, 1.0, 1.5, 3.0, 7.5):
        for style in ("duration-hours", "duration-tally"):
            assert durations.parse(durations.render(hours, style=style)) == hours
