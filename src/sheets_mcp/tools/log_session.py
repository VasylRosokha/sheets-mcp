"""`log_session` — writes a dated session (§8.7).

Append-only by construction: the first written row is always below every
populated row in the sheet. That property is what makes a mistargeted write
unable to damage existing history — the worst outcome is a stray row at the
bottom, which is visible and trivially deleted. It is also why §3.5's
"verify against copies" could be skipped without the risk being unbounded.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sheets_mcp import dates
from sheets_mcp.errors import (
    DateOutOfOrder,
    TooManyValues,
    ValidationError,
    WrongLayout,
)
from sheets_mcp.layouts import dated_block
from sheets_mcp.layouts.dated_block import Item
from sheets_mcp.logging import get_logger
from sheets_mcp.profiles.models import DatedBlockProfile
from sheets_mcp.runtime import Runtime
from sheets_mcp.tools.list_profiles import WRITE_TOOL

log = get_logger(__name__)

_SCAN_RANGE = "A:N"
_MODES = ("auto", "new-block", "append-to-existing")


async def log_session(
    runtime: Runtime,
    profile_name: str,
    items: list[dict[str, Any]],
    *,
    when: str | None = None,
    mode: str = "auto",
    dry_run: bool = False,
) -> dict[str, Any]:
    profile = runtime.require_profile(profile_name)
    if not isinstance(profile, DatedBlockProfile):
        raise WrongLayout(profile.name, str(profile.layout), WRITE_TOOL[str(profile.layout)])

    if mode not in _MODES:
        raise ValidationError(f"mode must be one of {', '.join(_MODES)}, not {mode!r}")
    if not items:
        raise ValidationError("items must not be empty; a session needs at least one entry")

    parsed_items = [_parse_item(raw, index, profile) for index, raw in enumerate(items)]
    target = _resolve_date(when, profile, runtime)

    client = runtime.client()
    rows = await client.read_range(profile.spreadsheet_id, f"'{profile.tab}'!{_SCAN_RANGE}")

    blocks = dated_block.scan_blocks(rows, profile)
    if blocks and target < blocks[-1].date:
        # Appending an old date at the bottom would break the ascending order
        # the whole layout depends on, and inserting mid-sheet is out of scope.
        raise DateOutOfOrder(
            dates.render(target, profile.write_date_format),
            blocks[-1].raw_date,
        )

    plan = dated_block.plan_session(
        rows, profile, tab=profile.tab, target=target, items=parsed_items, mode=mode
    )

    result: dict[str, Any] = {
        "profile": profile.name,
        "date": dates.render(target, profile.write_date_format),
        "date_was_supplied": when is not None,
        "mode": plan.mode,
        "range": plan.a1_range,
        "rows": plan.rows,
        "dry_run": dry_run,
    }

    if dry_run:
        result["note"] = "Nothing was written. Call again with dry_run false to apply this."
        return result

    await client.write_range(profile.spreadsheet_id, plan.a1_range, plan.rows)

    # Read back rather than trusting the write. The response then describes what
    # the sheet contains, not what this process intended to put there.
    written = await client.read_range(profile.spreadsheet_id, plan.a1_range)
    result["written"] = written
    result["verified"] = written == [
        [cell for cell in row[: len(written[index])]] if index < len(written) else []
        for index, row in enumerate(plan.rows)
    ]
    log.info(
        "session_written",
        profile=profile.name,
        range=plan.a1_range,
        mode=plan.mode,
        when_given=when,
        resolved=target.isoformat(),
    )
    return result


def _parse_item(raw: dict[str, Any], index: int, profile: DatedBlockProfile) -> Item:
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValidationError(f"items[{index}] has no name; every entry needs one")

    values_raw = raw.get("values", [])
    if isinstance(values_raw, str):
        # A single value is a common shape from a model; accepting it is kinder
        # than a schema error, and unambiguous.
        values_raw = [values_raw]
    if not isinstance(values_raw, list):
        raise ValidationError(f"items[{index}].values must be a list of strings")

    values = [str(value).strip() for value in values_raw]
    if len(values) > len(profile.value_columns):
        raise TooManyValues(name, len(values), len(profile.value_columns))
    return Item(name=name, values=tuple(values))


def _resolve_date(when: str | None, profile: DatedBlockProfile, runtime: Runtime) -> date:
    """Resolve the target date (§9.2).

    `today` and `yesterday` resolve in the server's configured timezone, not the
    caller's — the phone may be anywhere, and the sheet records the owner's days.
    """
    if when is None:
        return dates.today(runtime.settings.timezone)

    token = when.strip().casefold()
    today = dates.today(runtime.settings.timezone)
    if token in ("", "today"):
        return today
    if token == "yesterday":
        return today.fromordinal(today.toordinal() - 1)

    parsed = dates.parse(when, [*profile.date_formats, "YYYY-MM-DD"])
    if parsed is None:
        raise ValidationError(
            f"could not read {when!r} as a date. Use today, yesterday, "
            f"{profile.write_date_format}, or YYYY-MM-DD."
        )
    return parsed
