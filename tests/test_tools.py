from __future__ import annotations

from typing import Any

import pytest

from sheets_mcp.config import Settings
from sheets_mcp.errors import CredentialsMissing, ProfileNotFound, RegistryMissing, TabNotFound
from sheets_mcp.runtime import Runtime
from sheets_mcp.sheets.client import SpreadsheetInfo
from sheets_mcp.tools import describe_profile, list_profiles

ENV = {"MCP_API_KEY": "k", "TZ": "Europe/Prague"}

TRAINING_SHEET = [
    ["Тренування"],
    ["31.07.2026"],
    ["Жим штанги лежачи", "20x20", "40x20", "90x4x3"],
    [],
    ["04.08.2026"],
    ["Підтягування", "8x3"],
    ["Віджимання на брусях", "12x3"],
]

STUDY_SHEET = [
    ["", "", "", ""],
    ["", "День", "Липень", ""],
    ["", "", "Програмування", "Крипта"],
    ["", "1", "||", ""],
    ["", "2", "", "|"],
]


class FakeClient:
    """Stands in for SheetsClient. Records what was asked for."""

    def __init__(self, rows: list[list[str]], *, tabs: tuple[str, ...] = ("Лист1",)) -> None:
        self.rows = rows
        self.tabs = tabs
        self.client_email = "sheets-mcp@example.iam.gserviceaccount.com"
        self.ranges: list[str] = []

    async def spreadsheet_info(self, spreadsheet_id: str) -> SpreadsheetInfo:
        return SpreadsheetInfo(title="Тренування2026", tabs=self.tabs)

    async def read_range(self, spreadsheet_id: str, a1_range: str) -> list[list[str]]:
        self.ranges.append(a1_range)
        return self.rows


def runtime_with(client: Any | None = None) -> Runtime:
    runtime = Runtime(Settings.from_env(ENV), registry_path="profiles.yaml")
    if client is not None:
        runtime._client = client  # noqa: SLF001 — injecting the fake is the point
    return runtime


# --- list_profiles ----------------------------------------------------------


async def test_list_profiles_needs_no_credentials() -> None:
    # It reads configuration only, so it works before GOOGLE_SERVICE_ACCOUNT_KEY
    # is ever set — which is what makes it safe to call first.
    result = await list_profiles(runtime_with())
    assert [p["name"] for p in result["profiles"]] == ["training", "study"]


async def test_each_profile_names_the_tool_that_writes_to_it() -> None:
    result = await list_profiles(runtime_with())
    by_name = {p["name"]: p for p in result["profiles"]}
    assert by_name["training"]["write_tool"] == "log_session"
    assert by_name["study"]["write_tool"] == "set_grid_value"


async def test_grid_summary_warns_that_labels_are_live() -> None:
    result = await list_profiles(runtime_with())
    study = next(p for p in result["profiles"] if p["name"] == "study")
    assert "read live" in study["note"]
    assert study["configured_labels"][0] == "Програмування"


async def test_missing_registry_is_reported_not_returned_empty() -> None:
    runtime = Runtime(Settings.from_env(ENV), registry_path="/nonexistent/profiles.yaml")
    with pytest.raises(RegistryMissing):
        await list_profiles(runtime)


# --- describe_profile -------------------------------------------------------


async def test_describe_dated_block_reports_the_last_session() -> None:
    client = FakeClient(TRAINING_SHEET)
    result = await describe_profile(runtime_with(client), "training")
    assert result["spreadsheet_title"] == "Тренування2026"
    assert result["last_block_date"] == "04.08.2026"
    assert result["last_block_items"][0] == {"name": "Підтягування", "values": ["8x3"]}


async def test_describe_returns_recent_names_for_reuse() -> None:
    client = FakeClient(TRAINING_SHEET)
    result = await describe_profile(runtime_with(client), "training")
    assert "Підтягування" in result["recent_item_names"]
    assert "Жим штанги лежачи" in result["recent_item_names"]


async def test_describe_accepts_an_alias() -> None:
    client = FakeClient(TRAINING_SHEET)
    result = await describe_profile(runtime_with(client), "gym")
    assert result["name"] == "training"


async def test_describe_quotes_the_tab_name_in_the_range() -> None:
    # An unquoted Cyrillic tab name is a parse error from the API, reported as
    # a range problem rather than a tab problem.
    client = FakeClient(TRAINING_SHEET)
    await describe_profile(runtime_with(client), "training")
    assert client.ranges == ["'Лист1'!A:N"]


async def test_describe_grid_reports_live_labels_over_config() -> None:
    client = FakeClient(STUDY_SHEET)
    result = await describe_profile(runtime_with(client), "study")
    # Config says Читання; the sheet says Крипта. The sheet wins.
    assert result["last_period_labels"] == ["Програмування", "Крипта"]
    assert result["periods"] == ["Липень"]


async def test_describe_grid_says_whether_this_period_exists() -> None:
    client = FakeClient(STUDY_SHEET)
    result = await describe_profile(runtime_with(client), "study")
    # Lets the model create the block first instead of failing a write.
    assert result["current_period_exists"] is False


async def test_unknown_profile_lists_the_available_ones() -> None:
    with pytest.raises(ProfileNotFound, match="training, study"):
        await describe_profile(runtime_with(FakeClient([])), "nonsense")


async def test_missing_tab_lists_the_tabs_that_exist() -> None:
    client = FakeClient(TRAINING_SHEET, tabs=("Sheet1", "Archive"))
    with pytest.raises(TabNotFound, match="Sheet1"):
        await describe_profile(runtime_with(client), "training")


async def test_describe_without_credentials_says_so() -> None:
    # No key configured and no fake injected: the error must name the variable.
    runtime = Runtime(Settings.from_env(ENV), registry_path="profiles.yaml")
    with pytest.raises(CredentialsMissing, match="GOOGLE_SERVICE_ACCOUNT_KEY"):
        await describe_profile(runtime, "training")


async def test_current_period_is_detected_when_its_block_exists() -> None:
    # The regression this locks in: strftime("%B") returned "August" in the
    # container's locale and never matched a Ukrainian header, so this reported
    # False for every month — including months that were present. The model
    # would then have created a duplicate block on top of a real one.
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from sheets_mcp.profiles.models import GridProfile

    study = runtime_with().require_profile("study")
    assert isinstance(study, GridProfile)
    this_month = study.period_name_for(datetime.now(ZoneInfo("Europe/Prague")).month)

    sheet = [
        ["", "", "", ""],
        ["", "День", this_month, ""],
        ["", "", "Програмування", "Читання"],
        ["", "1", "|", ""],
    ]
    result = await describe_profile(runtime_with(FakeClient(sheet)), "study")
    assert result["current_period"] == this_month
    assert result["current_period_exists"] is True
