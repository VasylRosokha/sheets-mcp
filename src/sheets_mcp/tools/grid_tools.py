"""`set_grid_value` and `create_period_block` (§8.8, §8.9).

These are the only tools that can modify a cell that already holds something,
which makes them the ones §3.5's skipped copies would actually have protected.
Two things stand in for that protection: `dry_run`, and a response that always
carries the cell's previous content — an accidental overwrite is then reversible
from the tool result alone, without opening version history.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sheets_mcp import dates, durations
from sheets_mcp.errors import (
    ColumnNotFound,
    DayOutOfRange,
    PeriodBlockMissing,
    PeriodExists,
    UnparseableCell,
    ValidationError,
    WrongLayout,
)
from sheets_mcp.layouts import grid
from sheets_mcp.logging import get_logger
from sheets_mcp.profiles.models import GridProfile, ValueType, column_letter
from sheets_mcp.runtime import Runtime
from sheets_mcp.tools.list_profiles import WRITE_TOOL

log = get_logger(__name__)

_SCAN_RANGE = "A:N"
_DURATION_TYPES = (ValueType.DURATION_TALLY, ValueType.DURATION_HOURS)


async def set_grid_value(
    runtime: Runtime,
    profile_name: str,
    column: str,
    value: float | str,
    *,
    when: str | None = None,
    mode: str = "set",
    dry_run: bool = False,
) -> dict[str, Any]:
    profile = _require_grid(runtime, profile_name)
    if mode not in ("set", "increment"):
        raise ValidationError(f"mode must be 'set' or 'increment', not {mode!r}")

    target = _resolve_date(when, profile, runtime)
    client = runtime.client()
    rows = await client.read_range(profile.spreadsheet_id, f"'{profile.tab}'!{_SCAN_RANGE}")

    period = _find_period(rows, profile, target)
    column_at = _resolve_column(period, profile, column)
    row_number = period.day_row(target.day)
    if row_number is None:
        raise DayOutOfRange(target.day, period.name, period.last_day_row - period.first_day_row + 1)

    address = f"'{profile.tab}'!{column_letter(column_at)}{row_number}"
    previous = _cell(rows, row_number - 1, column_at)
    label = period.labels[period.label_columns.index(column_at)]

    new_text, reported = _new_value(profile, previous, value, mode=mode, address=address)

    result: dict[str, Any] = {
        "profile": profile.name,
        "period": period.name,
        "date": target.isoformat(),
        "day": target.day,
        "date_was_supplied": when is not None,
        "column": label,
        "address": address,
        "mode": mode,
        # The previous value is the whole safety story for this tool: it is what
        # makes an accidental overwrite reversible without version history.
        "previous_value": previous,
        "new_value": new_text,
        "dry_run": dry_run,
    }
    if reported is not None:
        result["hours"] = reported

    if dry_run:
        result["note"] = "Nothing was written. Call again with dry_run false to apply this."
        return result

    await client.write_range(profile.spreadsheet_id, address, [[new_text]])
    written = await client.read_range(profile.spreadsheet_id, address)
    result["written"] = written[0][0] if written and written[0] else ""
    # `when_given` is logged separately from the resolved date: when an entry
    # lands on the wrong day, the only thing worth knowing is whether the
    # caller supplied a date or the server chose one.
    log.info(
        "grid_value_written",
        profile=profile.name,
        address=address,
        mode=mode,
        when_given=when,
        resolved=target.isoformat(),
    )
    return result


async def create_period_block(
    runtime: Runtime,
    profile_name: str,
    *,
    period: str | None = None,
    labels: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    profile = _require_grid(runtime, profile_name)
    year, month = _resolve_period(period, runtime)

    client = runtime.client()
    rows = await client.read_range(profile.spreadsheet_id, f"'{profile.tab}'!{_SCAN_RANGE}")

    wanted = profile.period_name_for(month)
    for existing in grid.scan_periods(rows, profile):
        if existing.name.casefold() == wanted.casefold():
            raise PeriodExists(wanted, existing.header_row)

    plan = grid.plan_period_block(
        rows,
        profile,
        tab=profile.tab,
        year=year,
        month=month,
        labels=tuple(labels) if labels else None,
    )

    result: dict[str, Any] = {
        "profile": profile.name,
        "period": plan.period,
        "year": year,
        "days": plan.last_day_row - plan.first_day_row + 1,
        "range": plan.a1_range,
        "labels": list(plan.labels),
        "header_row": plan.header_row,
        "first_day_row": plan.first_day_row,
        "dry_run": dry_run,
    }

    if dry_run:
        result["preview"] = plan.rows[:4]
        result["note"] = "Nothing was written. Call again with dry_run false to apply this."
        return result

    await client.write_range(profile.spreadsheet_id, plan.a1_range, plan.rows)
    check = grid.scan_periods(
        await client.read_range(profile.spreadsheet_id, f"'{profile.tab}'!{_SCAN_RANGE}"), profile
    )
    result["verified"] = any(p.name.casefold() == plan.period.casefold() for p in check)
    log.info("period_block_created", profile=profile.name, period=plan.period, range=plan.a1_range)
    return result


def _require_grid(runtime: Runtime, name: str) -> GridProfile:
    profile = runtime.require_profile(name)
    if not isinstance(profile, GridProfile):
        raise WrongLayout(profile.name, str(profile.layout), WRITE_TOOL[str(profile.layout)])
    return profile


def _find_period(rows: list[list[str]], profile: GridProfile, target: date) -> grid.Period:
    wanted = profile.period_name_for(target.month)
    periods = grid.scan_periods(rows, profile)
    for period in periods:
        if period.name.casefold() == wanted.casefold():
            return period
    raise PeriodBlockMissing(wanted, [p.name for p in periods])


def _resolve_column(period: grid.Period, profile: GridProfile, wanted: str) -> int:
    """Match against the block's *live* labels, then the configured aliases.

    The live label row is authoritative (§7.4). A configured key like
    `programming` is accepted too, but only as a route to the label — the
    configuration never decides which column is written.
    """
    aliases: tuple[str, ...] = ()
    for configured in profile.columns:
        if wanted.casefold() in {
            configured.key.casefold(),
            configured.label.casefold(),
            *(alias.casefold() for alias in configured.aliases),
        }:
            aliases = (configured.label, *configured.aliases)
            break

    found = grid.resolve_column(period, wanted, aliases)
    if found is None:
        raise ColumnNotFound(wanted, period.name, list(period.labels))
    return found


def _new_value(
    profile: GridProfile,
    previous: str,
    value: float | str,
    *,
    mode: str,
    address: str,
) -> tuple[str, float | None]:
    if profile.value_type not in _DURATION_TYPES:
        if mode == "increment":
            raise ValidationError(
                f"increment applies to duration columns; {profile.name!r} holds "
                f"{profile.value_type} values, so use mode 'set'."
            )
        return str(value), None

    hours = _as_hours(value)
    if mode == "increment":
        current = durations.parse(previous)
        if current is None:
            raise UnparseableCell(address, previous)
        hours += current

    # Rounded after the addition, so 0.25 + 0.25 records half an hour rather
    # than two roundings to zero (§7.3).
    hours = durations.round_to_step(hours, profile.duration_step)
    return durations.render(hours, style=str(profile.value_type)), hours


def _as_hours(value: float | str) -> float:
    if isinstance(value, int | float):
        return float(value)
    parsed = durations.parse(str(value))
    if parsed is None:
        raise ValidationError(
            f"could not read {value!r} as a duration. Give a number of hours, or text like '1.5h'."
        )
    return parsed


def _resolve_date(when: str | None, profile: GridProfile, runtime: Runtime) -> date:
    today = dates.today(runtime.settings.timezone)
    if when is None:
        return today
    token = when.strip().casefold()
    if token in ("", "today"):
        return today
    if token == "yesterday":
        return date.fromordinal(today.toordinal() - 1)
    parsed = dates.parse(when, ["YYYY-MM-DD", "DD.MM.YYYY", "DD.MM.YY"])
    if parsed is None:
        raise ValidationError(
            f"could not read {when!r} as a date. Use today, yesterday, DD.MM.YYYY, or YYYY-MM-DD."
        )
    return parsed


def _resolve_period(period: str | None, runtime: Runtime) -> tuple[int, int]:
    """Accept `2026-08`, `08.2026`, or nothing for the current month."""
    today = dates.today(runtime.settings.timezone)
    if period is None or not period.strip():
        return today.year, today.month

    text = period.strip()
    for separator in ("-", "."):
        if separator in text:
            left, _, right = text.partition(separator)
            if left.isdigit() and right.isdigit():
                year, month = (int(left), int(right)) if len(left) == 4 else (int(right), int(left))
                if 1 <= month <= 12 and 2000 <= year <= 2100:
                    return year, month
    raise ValidationError(f"could not read {period!r} as a period. Use YYYY-MM, e.g. 2026-08.")


def _cell(rows: list[list[str]], row_index: int, column_index: int) -> str:
    if row_index >= len(rows):
        return ""
    row = rows[row_index]
    return row[column_index] if column_index < len(row) else ""


__all__ = ["create_period_block", "set_grid_value"]
