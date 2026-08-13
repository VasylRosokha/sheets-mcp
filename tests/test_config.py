from __future__ import annotations

import pytest

from sheets_mcp.config import ConfigError, Settings


def test_refuses_to_start_with_no_auth() -> None:
    with pytest.raises(ConfigError, match="No authentication configured"):
        Settings.from_env({})


def test_empty_api_key_counts_as_unset() -> None:
    # systemd's EnvironmentFile exports `MCP_API_KEY=` for a blank line in
    # .env. Treating that as a configured key would authenticate everyone.
    with pytest.raises(ConfigError, match="No authentication configured"):
        Settings.from_env({"MCP_API_KEY": "   "})


def test_unauthenticated_requires_explicit_opt_in() -> None:
    settings = Settings.from_env({"ALLOW_UNAUTHENTICATED": "1"})
    assert settings.api_key is None
    assert settings.mcp_path == "/mcp"


def test_secret_path_is_appended_to_mcp_path() -> None:
    settings = Settings.from_env({"MCP_SECRET_PATH": "9f2c1a7e4b8d3f6a"})
    assert settings.mcp_path == "/mcp/9f2c1a7e4b8d3f6a"


@pytest.mark.parametrize("bad", ["has/slash", "has-dash", "has.dot", "has space"])
def test_secret_path_must_be_one_alphanumeric_segment(bad: str) -> None:
    with pytest.raises(ConfigError, match="single alphanumeric path segment"):
        Settings.from_env({"MCP_SECRET_PATH": bad})


def test_defaults() -> None:
    settings = Settings.from_env({"MCP_API_KEY": "k"})
    assert settings.port == 8787
    assert str(settings.timezone) == "Europe/Prague"
    assert settings.log_level == "info"


def test_rejects_unknown_timezone() -> None:
    with pytest.raises(ConfigError, match="not a known timezone"):
        Settings.from_env({"MCP_API_KEY": "k", "TZ": "Mars/Olympus_Mons"})


def test_rejects_non_numeric_port() -> None:
    with pytest.raises(ConfigError, match="PORT must be an integer"):
        Settings.from_env({"MCP_API_KEY": "k", "PORT": "eight"})


def test_rejects_unknown_log_level() -> None:
    with pytest.raises(ConfigError, match="LOG_LEVEL must be one of"):
        Settings.from_env({"MCP_API_KEY": "k", "LOG_LEVEL": "chatty"})
