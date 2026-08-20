"""The schemas MCP publishes — the actual contract with the model.

A tool's signature is not an implementation detail here. It is what the model is
told it may send, and the difference between `str` and `Literal[...]` is the
difference between "the allowed values are described in the docstring" and "the
allowed values are in the schema your call is checked against".

These tests compare the published enums against the constants the runtime
validation uses, so the two cannot drift. They drifted once already: the spec
declared all three as Literal from the start and the implementation shipped them
as plain strings, which worked — it just cost a round trip to say no.
"""

from __future__ import annotations

from typing import Any

import pytest

from sheets_mcp.server import mcp
from sheets_mcp.tools.grid_tools import _MODES as GRID_MODES
from sheets_mcp.tools.log_session import _MODES as SESSION_MODES
from sheets_mcp.tools.read_tools import _ORDERS


@pytest.fixture(scope="module")
async def schemas() -> dict[str, dict[str, Any]]:
    return {tool.name: tool.input_schema for tool in await mcp.list_tools()}


def _enum(schema: dict[str, Any], param: str) -> list[str]:
    prop = schema["properties"][param]
    if "$ref" in prop:
        key = prop["$ref"].rsplit("/", 1)[-1]
        prop = schema["$defs"][key]
    return list(prop["enum"])


async def test_every_tool_is_published(schemas: dict[str, dict[str, Any]]) -> None:
    assert set(schemas) == {
        "ping",
        "list_profiles",
        "describe_profile",
        "query_rows",
        "find_row",
        "log_session",
        "set_grid_value",
        "create_period_block",
        "update_row",
    }


@pytest.mark.parametrize(
    ("tool", "param", "allowed"),
    [
        ("set_grid_value", "mode", GRID_MODES),
        ("log_session", "mode", SESSION_MODES),
        ("query_rows", "order", _ORDERS),
    ],
)
async def test_constrained_arguments_publish_an_enum(
    schemas: dict[str, dict[str, Any]], tool: str, param: str, allowed: tuple[str, ...]
) -> None:
    assert _enum(schemas[tool], param) == list(allowed)


async def test_the_defaults_are_legal_values(schemas: dict[str, dict[str, Any]]) -> None:
    # A default outside its own enum is accepted by pydantic and rejected by the
    # tool at runtime — every call that omitted the argument would fail.
    for tool, param in (("set_grid_value", "mode"), ("log_session", "mode"), ("query_rows", "order")):
        default = schemas[tool]["properties"][param]["default"]
        assert default in _enum(schemas[tool], param)


async def test_every_tool_carries_a_description(schemas: dict[str, dict[str, Any]]) -> None:
    # §9.1: the description *is* the prompt. A tool without one is invisible to
    # the model except by name.
    for tool in await mcp.list_tools():
        assert tool.description and len(tool.description) > 80, tool.name
