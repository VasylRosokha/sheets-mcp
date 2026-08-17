"""Tool implementations, registered onto the MCP server.

Each module holds one tool's logic as a plain async function taking a `Runtime`.
Registration happens here rather than through decorators at definition, so the
functions stay directly callable from tests without an MCP server in the way.
"""

from sheets_mcp.tools.describe_profile import describe_profile
from sheets_mcp.tools.list_profiles import list_profiles

__all__ = ["describe_profile", "list_profiles"]
