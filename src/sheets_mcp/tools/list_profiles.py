"""`list_profiles` (§8.1).

Reads configuration only — no spreadsheet access, so it works before
credentials exist and cannot fail because a sheet was renamed. Claude calls it
first when it does not yet know what is available.
"""

from __future__ import annotations

from typing import Any

from sheets_mcp.profiles.models import DatedBlockProfile, GridProfile, Profile, TableProfile
from sheets_mcp.runtime import Runtime

# Which tool writes to which layout. Returned with each profile so the model
# does not have to infer it, and so a wrong guess is correctable before the
# call rather than after a WRONG_LAYOUT error (§10).
WRITE_TOOL = {
    "table": "append_rows",
    "dated-block": "log_session",
    "grid": "set_grid_value",
}

# Correcting something already written is a different tool from writing it, and
# on a grid it is the *same* tool as writing — set_grid_value overwrites a cell
# in place, so there is nothing for update_row to add and a raw string would
# bypass its rounding.
CORRECT_TOOL = {
    "table": "update_row",
    "dated-block": "update_row",
    "grid": "set_grid_value",
}


async def list_profiles(runtime: Runtime) -> dict[str, Any]:
    registry = runtime.require_registry()
    return {"profiles": [_summarise(profile) for profile in registry.profiles]}


def _summarise(profile: Profile) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "name": profile.name,
        "aliases": list(profile.aliases),
        "description": profile.description.strip(),
        "layout": str(profile.layout),
        "write_tool": WRITE_TOOL[str(profile.layout)],
        "correct_tool": CORRECT_TOOL[str(profile.layout)],
        "read_tool": "query_rows",
    }

    # The shape of what is useful differs per layout, so the extra fields do
    # too. A uniform schema here would mean `columns: null` on the layout that
    # has no columns, which reads as missing data rather than as inapplicable.
    if isinstance(profile, TableProfile):
        summary["columns"] = [column.key for column in profile.columns]
        summary["required_columns"] = profile.required_columns
    elif isinstance(profile, DatedBlockProfile):
        summary["max_value_columns"] = len(profile.value_columns)
        summary["date_formats"] = list(profile.date_formats)
        summary["note"] = (
            "Items are named in column A with free-form values beside them; "
            "the server writes value text verbatim and does not parse set notation."
        )
    elif isinstance(profile, GridProfile):
        summary["columns"] = [column.key for column in profile.columns]
        summary["configured_labels"] = [column.label for column in profile.columns]
        summary["value_type"] = str(profile.value_type)
        summary["note"] = (
            "Column labels are read live from each period block and can differ "
            "from the configured labels; call describe_profile before writing."
        )

    return summary
