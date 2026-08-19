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


def test_allowed_hosts_defaults_to_local_only() -> None:
    settings = Settings.from_env({"MCP_API_KEY": "k"})
    assert settings.allowed_hosts == ("127.0.0.1:*", "localhost:*", "[::1]:*")


def test_render_hostname_is_picked_up_automatically() -> None:
    # The deployed server answers to one name it is not told about directly.
    # Without this the SDK's DNS-rebinding check rejects every real request
    # with 421 while every local test still passes.
    settings = Settings.from_env(
        {"MCP_API_KEY": "k", "RENDER_EXTERNAL_HOSTNAME": "sheets-mcp-example.onrender.com"}
    )
    assert settings.allowed_hosts[0] == "sheets-mcp-example.onrender.com"
    assert "localhost:*" in settings.allowed_hosts


def test_explicit_allowed_hosts_override_the_platform() -> None:
    settings = Settings.from_env(
        {
            "MCP_API_KEY": "k",
            "MCP_ALLOWED_HOSTS": "a.example.com, b.example.com",
            "RENDER_EXTERNAL_HOSTNAME": "ignored.onrender.com",
        }
    )
    assert settings.allowed_hosts[:2] == ("a.example.com", "b.example.com")
    assert "ignored.onrender.com" not in settings.allowed_hosts


def test_origins_are_https_for_real_hosts_and_http_for_local() -> None:
    settings = Settings.from_env(
        {"MCP_API_KEY": "k", "RENDER_EXTERNAL_HOSTNAME": "sheets-mcp-example.onrender.com"}
    )
    assert "https://sheets-mcp-example.onrender.com" in settings.allowed_origins
    assert "http://localhost:*" in settings.allowed_origins
