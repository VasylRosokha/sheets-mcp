from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sheets_mcp.profiles.loader import (
    ProfileConfigError,
    load_registry,
    load_registry_if_present,
)
from sheets_mcp.profiles.models import (
    DatedBlockProfile,
    GridProfile,
    ProfileRegistry,
    column_index,
)

TRAINING: dict[str, Any] = {
    "name": "training",
    "aliases": ["workout", "тренування"],
    "layout": "dated-block",
    "description": "Training log.",
    "spreadsheet_id": "sheet-1",
    "tab": "Лист1",
    "date_column": "A",
    "item_column": "A",
    "value_columns": ["B", "C"],
    "date_formats": ["DD.MM.YYYY", "DD.MM.YY"],
    "write_date_format": "DD.MM.YYYY",
}

STUDY: dict[str, Any] = {
    "name": "study",
    "aliases": ["habits"],
    "layout": "grid",
    "description": "Habit grid.",
    "spreadsheet_id": "sheet-2",
    "tab": "2026",
    "day_column": "B",
    "period_header_offset": -2,
    "label_row_offset": -1,
    "period_names": [
        "Січень",
        "Лютий",
        "Березень",
        "Квітень",
        "Травень",
        "Червень",
        "Липень",
        "Серпень",
        "Вересень",
        "Жовтень",
        "Листопад",
        "Грудень",
    ],
    "columns": [{"key": "programming", "label": "Програмування", "aliases": ["код"]}],
}


def registry(*profiles: dict[str, Any]) -> ProfileRegistry:
    return ProfileRegistry.model_validate({"profiles": list(profiles)})


def test_both_layouts_parse_into_their_own_types() -> None:
    reg = registry(TRAINING, STUDY)
    assert isinstance(reg.profiles[0], DatedBlockProfile)
    assert isinstance(reg.profiles[1], GridProfile)


def test_resolve_matches_name_alias_and_case() -> None:
    reg = registry(TRAINING, STUDY)
    assert reg.resolve("training") is reg.profiles[0]
    assert reg.resolve("WORKOUT") is reg.profiles[0]
    assert reg.resolve("  Тренування  ") is reg.profiles[0]
    assert reg.resolve("habits") is reg.profiles[1]
    assert reg.resolve("nonsense") is None


def test_an_alias_claimed_twice_is_rejected() -> None:
    # Otherwise which profile wins depends on the order of the file.
    clash = {**STUDY, "aliases": ["workout"]}
    with pytest.raises(ValueError, match="claimed by both"):
        registry(TRAINING, clash)


def test_an_alias_colliding_with_another_name_is_rejected() -> None:
    clash = {**STUDY, "aliases": ["training"]}
    with pytest.raises(ValueError, match="claimed by both"):
        registry(TRAINING, clash)


def test_placeholder_tab_is_rejected_with_an_actionable_message() -> None:
    with pytest.raises(ValueError, match="still the placeholder"):
        registry({**TRAINING, "tab": "<verify actual tab name>"})


def test_unknown_key_is_rejected_rather_than_ignored() -> None:
    # A misspelled key would otherwise be silently dropped and the setting
    # would appear not to work.
    with pytest.raises(ValueError, match="date_colum"):
        registry({**TRAINING, "date_colum": "A"})


def test_write_date_format_must_be_readable() -> None:
    broken = {**TRAINING, "write_date_format": "YYYY-MM-DD"}
    with pytest.raises(ValueError, match="cannot read back"):
        registry(broken)


def test_bad_column_letter_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a spreadsheet column letter"):
        registry({**TRAINING, "date_column": "A1"})


def test_column_letters_are_normalised_to_upper_case() -> None:
    reg = registry({**TRAINING, "date_column": "a", "value_columns": ["b", "c"]})
    profile = reg.profiles[0]
    assert isinstance(profile, DatedBlockProfile)
    assert profile.date_column == "A"
    assert profile.value_columns == ["B", "C"]


def test_positive_grid_offset_is_rejected() -> None:
    # A positive offset reads a day row as the label row and writes into the
    # wrong column for the whole month.
    with pytest.raises(ValueError, match="counts rows above"):
        registry({**STUDY, "label_row_offset": 1})


def test_duplicate_column_keys_are_rejected() -> None:
    dupe = {**STUDY, "columns": [{"key": "a", "label": "A"}, {"key": "a", "label": "B"}]}
    with pytest.raises(ValueError, match="duplicate column key"):
        registry(dupe)


@pytest.mark.parametrize(("letter", "index"), [("A", 0), ("B", 1), ("Z", 25), ("AA", 26), ("AB", 27)])
def test_column_index(letter: str, index: int) -> None:
    assert column_index(letter) == index


def test_missing_file_is_an_error_when_loaded_directly(tmp_path: Path) -> None:
    with pytest.raises(ProfileConfigError, match="No profile registry"):
        load_registry(tmp_path / "nope.yaml")


def test_missing_file_is_tolerated_by_the_boot_time_loader(tmp_path: Path) -> None:
    # Phase 1 ran with no registry at all; an absent file must not take the
    # service down, while an invalid one still must.
    assert load_registry_if_present(tmp_path / "nope.yaml") is None


def test_invalid_file_still_fails_even_via_the_tolerant_loader(tmp_path: Path) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text("profiles:\n  - name: broken\n", encoding="utf-8")
    with pytest.raises(ProfileConfigError, match="is invalid"):
        load_registry_if_present(path)


def test_malformed_yaml_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text("profiles: [oops\n", encoding="utf-8")
    with pytest.raises(ProfileConfigError, match="not valid YAML"):
        load_registry(path)


def test_empty_file_is_reported_clearly(tmp_path: Path) -> None:
    path = tmp_path / "profiles.yaml"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ProfileConfigError, match="empty"):
        load_registry(path)


def test_the_shipped_example_is_valid_apart_from_its_placeholders() -> None:
    """The example must stay loadable, or it is documentation rather than a template."""
    import yaml

    raw = yaml.safe_load(Path("profiles.example.yaml").read_text(encoding="utf-8"))
    for profile in raw["profiles"]:
        profile["tab"] = "FilledIn"
    reg = ProfileRegistry.model_validate(raw)
    assert reg.names == ["training", "study"]
    assert reg.resolve("gym") is not None
    assert reg.resolve("код") is None  # a column alias, not a profile alias


def test_grid_requires_all_twelve_period_names() -> None:
    # Eleven would silently shift every month after the gap.
    short = {**STUDY, "period_names": ["Січень"]}
    with pytest.raises(ValueError, match="at least 12|at most 12"):
        registry(short)


def test_period_name_comes_from_config_not_the_locale() -> None:
    # strftime("%B") answers in the container's locale and would return
    # "August" for a sheet whose header says "Серпень" — with no local symptom,
    # because the developer's locale is not the deployment's.
    reg = registry(STUDY)
    study = reg.profiles[0]
    assert isinstance(study, GridProfile)
    assert study.period_name_for(8) == "Серпень"
    assert study.period_name_for(1) == "Січень"
    assert study.period_name_for(12) == "Грудень"


def test_duplicate_period_names_are_rejected() -> None:
    dupe = {**STUDY, "period_names": ["Січень"] * 12}
    with pytest.raises(ValueError, match="duplicate period name"):
        registry(dupe)


# --- the registry supplied inline through the environment -------------------


INLINE = """
profiles:
  - name: inline
    layout: dated-block
    description: A registry that never touched the filesystem.
    spreadsheet_id: 1inline
    tab: Sheet1
    date_column: A
    item_column: A
    value_columns: [B]
    date_formats: ["DD.MM.YYYY"]
    write_date_format: "DD.MM.YYYY"
"""


def test_profiles_yaml_env_supplies_the_registry_without_a_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # What lets a public repository ship placeholder spreadsheet ids while the
    # deployment points at the real sheets.
    monkeypatch.setenv("PROFILES_YAML", INLINE)
    assert load_registry().names == ["inline"]


def test_an_explicit_path_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Otherwise a developer with PROFILES_YAML exported would silently run the
    # whole suite against their own sheets rather than the fixtures.
    monkeypatch.setenv("PROFILES_YAML", INLINE)
    assert load_registry("profiles.yaml").names == ["training", "study"]


def test_an_invalid_inline_registry_names_the_variable_not_a_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROFILES_YAML", "profiles: []")
    with pytest.raises(ProfileConfigError, match=r"\$PROFILES_YAML is invalid"):
        load_registry()


def test_an_empty_variable_falls_through_to_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    # Render exports an unset variable as the empty string, which must not read
    # as "a registry with no profiles".
    monkeypatch.setenv("PROFILES_YAML", "   ")
    assert load_registry_if_present("profiles.yaml") is not None
