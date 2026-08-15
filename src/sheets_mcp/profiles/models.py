"""Pydantic models for `profiles.yaml` (§7.2).

Everything here is validated once, at boot. A profile that survives this module
is structurally trustworthy for the rest of the process — column letters are
real letters, offsets point upward, aliases are unambiguous — so no tool needs
to re-check any of it at call time.

What this file cannot check is whether the sheet actually looks the way the
profile claims. That is §7.4's job and it happens live, against the sheet,
immediately before a write.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_COLUMN_LETTER = re.compile(r"^[A-Z]{1,2}$")

# A tab left as `<verify actual tab name>` is the single most likely thing to
# be wrong in a hand-written profile, and it fails deep inside a Sheets API
# call with a message about ranges. Catching the shape here says what to do.
_PLACEHOLDER = re.compile(r"^<.*>$")


class Layout(StrEnum):
    """How a sheet is physically organised (§7.1)."""

    TABLE = "table"
    DATED_BLOCK = "dated-block"
    GRID = "grid"


class ValueType(StrEnum):
    """How a value is coerced on the way in and rendered on the way out (§7.3)."""

    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    DATE = "date"
    DURATION_TALLY = "duration-tally"
    FREEFORM = "freeform"


class _Strict(BaseModel):
    """Reject unknown keys everywhere.

    A misspelled key in YAML is silently ignored by a permissive model, and the
    profile then behaves as though the setting were never written. `extra=forbid`
    turns that into a boot failure naming the key.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


def column_index(letter: str) -> int:
    """Convert a spreadsheet column letter to a zero-based index. `A` → 0, `AA` → 26."""
    index = 0
    for char in letter:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _validate_column(value: str) -> str:
    letter = value.strip().upper()
    if not _COLUMN_LETTER.match(letter):
        raise ValueError(f"{value!r} is not a spreadsheet column letter (A–ZZ)")
    return letter


class TableColumn(_Strict):
    """One column of a `table` profile, bound to a header cell."""

    key: str
    header: str
    type: ValueType = ValueType.STRING
    required: bool = False
    aliases: list[str] = Field(default_factory=list)


class GridColumn(_Strict):
    """One activity column of a `grid` profile.

    `label` is the text expected in the block's label row, but it is a hint for
    matching rather than a fact: §7.5 records this sheet renaming columns
    between months. The live label row always wins.
    """

    key: str
    label: str
    aliases: list[str] = Field(default_factory=list)


class _BaseProfile(_Strict):
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str
    spreadsheet_id: str
    tab: str

    @field_validator("tab")
    @classmethod
    def _tab_is_filled_in(cls, value: str) -> str:
        if _PLACEHOLDER.match(value.strip()):
            raise ValueError(
                f"tab is still the placeholder {value!r} — open the spreadsheet and "
                "copy the tab label from the bottom of the window"
            )
        if not value.strip():
            raise ValueError("tab must not be empty")
        return value

    @field_validator("name")
    @classmethod
    def _name_is_a_single_word(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be empty")
        if any(char.isspace() for char in value):
            raise ValueError(f"name {value!r} must not contain whitespace; put spellings in aliases")
        return value


class TableProfile(_BaseProfile):
    """A conventional header-plus-rows sheet. Writes append at the bottom."""

    layout: Literal[Layout.TABLE]
    columns: list[TableColumn] = Field(min_length=1)

    @model_validator(mode="after")
    def _keys_are_unique(self) -> Self:
        _reject_duplicates((column.key for column in self.columns), what="column key")
        return self

    @property
    def required_columns(self) -> list[str]:
        return [column.key for column in self.columns if column.required]


class DatedBlockProfile(_BaseProfile):
    """Sessions separated by blank rows, each headed by a date (§7.1)."""

    layout: Literal[Layout.DATED_BLOCK]
    date_column: str
    item_column: str
    value_columns: list[str] = Field(min_length=1)
    date_formats: list[str] = Field(min_length=1)
    write_date_format: str
    block_separator: Literal["blank-row"] = "blank-row"

    @field_validator("date_column", "item_column")
    @classmethod
    def _normalise_columns(cls, value: str) -> str:
        return _validate_column(value)

    @field_validator("value_columns")
    @classmethod
    def _normalise_value_columns(cls, value: list[str]) -> list[str]:
        return [_validate_column(letter) for letter in value]

    @model_validator(mode="after")
    def _write_format_is_readable(self) -> Self:
        # Writing in a format the reader does not accept produces a sheet this
        # server can append to but not parse — and the damage only surfaces on
        # the next read, long after the write that caused it.
        if self.write_date_format not in self.date_formats:
            raise ValueError(
                f"write_date_format {self.write_date_format!r} is not among date_formats "
                f"{self.date_formats!r}, so this profile would write dates it cannot read back"
            )
        _reject_duplicates(self.value_columns, what="value column")
        return self


class GridProfile(_BaseProfile):
    """A matrix repeated per period; writes are single-cell updates (§7.1)."""

    layout: Literal[Layout.GRID]
    day_column: str
    period_header_offset: int
    label_row_offset: int
    value_type: ValueType = ValueType.DURATION_TALLY
    duration_step: float = 0.5
    write_canonical: bool = True
    columns: list[GridColumn] = Field(min_length=1)
    note: str | None = None

    @field_validator("day_column")
    @classmethod
    def _normalise_day_column(cls, value: str) -> str:
        return _validate_column(value)

    @field_validator("period_header_offset", "label_row_offset")
    @classmethod
    def _offsets_point_upward(cls, value: int) -> int:
        # Both are measured from the first day row, and both rows sit above it.
        # A positive offset would silently read a day row as a label row.
        if value >= 0:
            raise ValueError(f"offset {value} must be negative: it counts rows above the first day row")
        return value

    @field_validator("duration_step")
    @classmethod
    def _step_is_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("duration_step must be greater than zero")
        return value

    @model_validator(mode="after")
    def _keys_are_unique(self) -> Self:
        _reject_duplicates((column.key for column in self.columns), what="column key")
        return self


Profile = Annotated[TableProfile | DatedBlockProfile | GridProfile, Field(discriminator="layout")]


class ProfileRegistry(BaseModel):
    """The whole of `profiles.yaml`."""

    model_config = ConfigDict(extra="forbid")

    profiles: list[Profile] = Field(min_length=1)

    @model_validator(mode="after")
    def _names_and_aliases_are_unambiguous(self) -> Self:
        # Every name and every alias share one namespace, because `resolve`
        # accepts either. Two profiles claiming the same alias would make the
        # winner depend on file order, which is not a thing anyone should have
        # to know.
        seen: dict[str, str] = {}
        for profile in self.profiles:
            for token in (profile.name, *profile.aliases):
                key = token.casefold()
                if key in seen:
                    raise ValueError(
                        f"{token!r} is claimed by both {seen[key]!r} and {profile.name!r}; "
                        "names and aliases share one namespace"
                    )
                seen[key] = profile.name
        return self

    def resolve(self, token: str) -> Profile | None:
        """Find a profile by name or alias, case-insensitively.

        Case folding rather than lowercasing: the aliases are Ukrainian as often
        as English, and `casefold` is the one that handles non-ASCII correctly.
        """
        wanted = token.strip().casefold()
        for profile in self.profiles:
            if wanted == profile.name.casefold():
                return profile
            if any(wanted == alias.casefold() for alias in profile.aliases):
                return profile
        return None

    @property
    def names(self) -> list[str]:
        return [profile.name for profile in self.profiles]


def _reject_duplicates(values: Iterable[str], *, what: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {what} {value!r}")
        seen.add(value)
