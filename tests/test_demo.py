"""The demo server, tested as a whole.

These are the closest thing here to integration tests, and they cost nothing:
the backend is in memory, so a full create-block → write → read-back → correct
chain runs without a network, a credential, or a spreadsheet.

Their other job is to keep the fixture honest. The demo exists to show the hard
cases, so a fixture that quietly drifted into a happy path would make the demo a
worse advertisement than no demo — and nothing else would notice.
"""

from __future__ import annotations

from datetime import date

import pytest

from sheets_mcp import tools
from sheets_mcp.config import Settings
from sheets_mcp.demo import registry_yaml
from sheets_mcp.demo.data import STUDY_ID, TRAINING_ID, build_backend
from sheets_mcp.errors import (
    PeriodBlockMissing,
    ProtectedRow,
    RowConflict,
    WrongLayout,
)
from sheets_mcp.layouts import dated_block, grid
from sheets_mcp.profiles.loader import load_registry_text
from sheets_mcp.profiles.models import DatedBlockProfile, GridProfile
from sheets_mcp.runtime import Runtime

DEMO_ENV = {"SHEETS_MCP_DEMO": "1", "TZ": "Europe/Prague"}


def demo_runtime() -> Runtime:
    return Runtime(Settings.from_env(DEMO_ENV))


# --- wiring -----------------------------------------------------------------


def test_demo_mode_needs_no_authentication() -> None:
    # Requiring a key would mean inventing one before seeing anything, to
    # protect synthetic data that is regenerated on every restart.
    settings = Settings.from_env(DEMO_ENV)
    assert settings.demo is True
    assert settings.api_key is None and settings.secret_path is None


def test_the_demo_registry_is_valid() -> None:
    # Through the same loader and the same validation as a real profiles.yaml,
    # so a mistake here fails at boot the way a user's would.
    assert load_registry_text(registry_yaml, source="demo").names == ["training", "study"]


def test_the_runtime_reports_the_demo_source() -> None:
    assert demo_runtime().registry_source == "demo"


def test_no_credentials_are_needed() -> None:
    runtime = demo_runtime()
    assert runtime.settings.google_service_account_key is None
    assert runtime.client().client_email is None


# --- the fixture keeps its teeth --------------------------------------------


async def test_both_date_formats_appear_in_the_training_fixture() -> None:
    runtime = demo_runtime()
    profile = runtime.require_profile("training")
    assert isinstance(profile, DatedBlockProfile)
    rows = await runtime.client().read_range(TRAINING_ID, "'Log'!A:N")
    raw = [block.raw_date for block in dated_block.scan_blocks(rows, profile)]

    assert any(len(text) == 8 for text in raw), "no DD.MM.YY dates — the short form is untested"
    assert any(len(text) == 10 for text in raw), "no DD.MM.YYYY dates"


async def test_a_near_duplicate_exercise_name_is_present() -> None:
    # What recent_item_names exists to prevent. If the fixture only ever had
    # distinct names, the demo would never show the problem being solved.
    described = await tools.describe_profile(demo_runtime(), "training")
    names = described["recent_item_names"]
    assert "Pull-ups" in names
    assert "Pull-ups, reverse grip" in names


async def test_labels_change_between_periods() -> None:
    runtime = demo_runtime()
    profile = runtime.require_profile("study")
    assert isinstance(profile, GridProfile)
    rows = await runtime.client().read_range(STUDY_ID, "'Habits'!A:N")
    label_sets = [period.labels for period in grid.scan_periods(rows, profile)]

    assert len(set(label_sets)) > 1, "every block has the same labels; the rename is not shown"
    assert label_sets[0] != label_sets[-1]
    # Configured as Reading, renamed in the sheet. A cached column index would
    # write reading hours into the crypto column and report success.
    assert "Crypto" in label_sets[-1]
    assert "Crypto" not in [column.label for column in profile.columns]


async def test_both_duration_notations_are_present_and_both_parse() -> None:
    result = await tools.query_rows(demo_runtime(), "study", limit=200)
    cells = [text for day in result["days"] for text in day["cells"].values()]
    assert any("|" in text for text in cells), "no tally cells in the fixture"
    assert any(text.endswith("h") for text in cells), "no hour cells in the fixture"
    assert "unreadable_cells" not in result


async def test_the_stray_row_is_not_read_as_a_block() -> None:
    # A day number and a value below the last block, with no period header. Day
    # numbers alone cannot mark a block, and this is what proves it.
    runtime = demo_runtime()
    rows = await runtime.client().read_range(STUDY_ID, "'Habits'!A:N")
    profile = runtime.require_profile("study")
    assert isinstance(profile, GridProfile)

    periods = grid.scan_periods(rows, profile)
    assert len(periods) == 4
    # The stray row is below the last block, so it was seen and rejected rather
    # than simply being out of range.
    assert periods[-1].last_day_row < len(rows)


async def test_the_current_month_block_is_deliberately_absent() -> None:
    described = await tools.describe_profile(demo_runtime(), "study")
    assert described["current_period_exists"] is False


# --- the chain a reviewer walks ---------------------------------------------


async def test_writing_before_the_block_exists_is_refused_with_the_remedy() -> None:
    with pytest.raises(PeriodBlockMissing, match="create_period_block"):
        await tools.set_grid_value(demo_runtime(), "study", "Programming", 2)


async def test_create_block_then_write_then_read_back() -> None:
    runtime = demo_runtime()

    created = await tools.create_period_block(runtime, "study")
    # Carried forward from the sheet's last block, not from configuration.
    assert created["labels"] == ["Programming", "Crypto", "x"]

    written = await tools.set_grid_value(runtime, "study", "Programming", 2)
    assert written["new_value"] == "2h"
    assert written["previous_value"] == ""

    incremented = await tools.set_grid_value(runtime, "study", "Programming", 1, mode="increment")
    assert incremented["previous_value"] == "2h"
    assert incremented["new_value"] == "3h"
    assert incremented["address"] == written["address"]

    today = date.today().isoformat()
    read_back = await tools.query_rows(runtime, "study", since=today, until=today)
    assert read_back["totals"] == {"Programming": 3.0}


async def test_log_a_session_then_find_and_correct_it() -> None:
    runtime = demo_runtime()

    logged = await tools.log_session(runtime, "training", [{"name": "Pull-ups", "values": ["12x3"]}])
    assert logged["verified"] is True
    assert logged["date_was_supplied"] is False

    found = await tools.find_row(runtime, "training", "bench")
    assert found["matched"] >= 1
    top = found["matches"][0]

    corrected = await tools.update_row(
        runtime, "training", top["row"], {"E": "90x8x3"}, expect=top["cells"]
    )
    assert corrected["verified"] is True
    assert corrected["expect_checked"] is True
    assert corrected["changes"][0]["to"] == "90x8x3"


async def test_the_refusals_a_reviewer_will_try() -> None:
    runtime = demo_runtime()
    found = await tools.find_row(runtime, "training", "bench")
    row = int(found["matches"][0]["row"])

    with pytest.raises(RowConflict):
        await tools.update_row(runtime, "training", row, {"E": "1"}, expect={"E": "not this"})

    with pytest.raises(ProtectedRow):
        await tools.update_row(runtime, "training", row - 1, {"B": "x"})

    with pytest.raises(WrongLayout, match="set_grid_value"):
        await tools.update_row(runtime, "study", 1, {"C": "1h"})

    with pytest.raises(WrongLayout, match="query_rows"):
        await tools.find_row(runtime, "study", "programming")


async def test_each_run_starts_from_the_same_fixture() -> None:
    # Two runtimes must not share a backend: a reviewer restarting the server
    # expects the demo back, and a test that mutated a shared one would fail
    # depending on which order the suite happened to run in.
    first = demo_runtime()
    await tools.create_period_block(first, "study")
    assert (await tools.describe_profile(first, "study"))["current_period_exists"] is True

    second = demo_runtime()
    assert (await tools.describe_profile(second, "study"))["current_period_exists"] is False


def test_the_fixture_is_generated_relative_to_today() -> None:
    # Hard-coded dates would make the demo look abandoned within a month, and
    # would eventually put every training session before the grid's first block.
    old = build_backend(date(2020, 3, 15))
    new = build_backend(date(2026, 8, 19))
    assert old._sheets[TRAINING_ID].rows != new._sheets[TRAINING_ID].rows  # noqa: SLF001
