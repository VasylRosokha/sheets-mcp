"""The profile registry: named bindings from a friendly name to a sheet layout.

Split from `config.py`, which holds process configuration read from the
environment. The two answer different questions — how this process runs, and
what spreadsheets it knows about — and only one of them is a file the user
edits.
"""

from sheets_mcp.profiles.loader import ProfileNotFoundError, load_registry
from sheets_mcp.profiles.models import (
    DatedBlockProfile,
    GridProfile,
    Layout,
    Profile,
    ProfileRegistry,
    TableProfile,
    ValueType,
)

__all__ = [
    "DatedBlockProfile",
    "GridProfile",
    "Layout",
    "Profile",
    "ProfileNotFoundError",
    "ProfileRegistry",
    "TableProfile",
    "ValueType",
    "load_registry",
]
