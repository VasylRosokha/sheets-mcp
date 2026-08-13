"""Process configuration, read from the environment once at boot.

Every value is validated here rather than at the point of use, so a
misconfigured host fails immediately and loudly instead of at the first
tool call — which, given the client is a phone, would surface as an
unexplained error hours later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_PORT = 8787
DEFAULT_TIMEZONE = "Europe/Prague"
DEFAULT_LOG_LEVEL = "info"

_LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})


class ConfigError(RuntimeError):
    """Raised at boot when the environment is unusable."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved configuration for one process."""

    api_key: str | None
    secret_path: str | None
    port: int
    timezone: ZoneInfo
    log_level: str

    @property
    def mcp_path(self) -> str:
        """The URL path the MCP endpoint is served at.

        With a secret path configured (§6.2 option B) the token rides in the
        connector URL itself, so an unauthenticated caller cannot even find
        the endpoint to probe it.
        """
        if self.secret_path is None:
            return "/mcp"
        return f"/mcp/{self.secret_path}"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env

        api_key = _clean(source.get("MCP_API_KEY"))
        secret_path = _clean(source.get("MCP_SECRET_PATH"))
        allow_open = _clean(source.get("ALLOW_UNAUTHENTICATED")) in {"1", "true", "yes"}

        if api_key is None and secret_path is None and not allow_open:
            raise ConfigError(
                "No authentication configured. Set MCP_API_KEY (preferred), "
                "MCP_SECRET_PATH, or both. Set ALLOW_UNAUTHENTICATED=1 only for "
                "local development on a host the internet cannot reach."
            )

        if secret_path is not None and ("/" in secret_path or not secret_path.isalnum()):
            raise ConfigError(
                "MCP_SECRET_PATH must be a single alphanumeric path segment; "
                "generate one with `openssl rand -hex 32`."
            )

        return cls(
            api_key=api_key,
            secret_path=secret_path,
            port=_int(source.get("PORT"), default=DEFAULT_PORT, name="PORT"),
            timezone=_timezone(source.get("TZ")),
            log_level=_log_level(source.get("LOG_LEVEL")),
        )


def _clean(raw: str | None) -> str | None:
    """Treat an empty or whitespace-only variable as unset.

    systemd `EnvironmentFile` happily exports `MCP_API_KEY=`, which would
    otherwise read as a configured-but-empty key and authenticate everyone.
    """
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _int(raw: str | None, *, default: int, name: str) -> int:
    value = _clean(raw)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc


def _timezone(raw: str | None) -> ZoneInfo:
    name = _clean(raw) or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"TZ={name!r} is not a known timezone") from exc


def _log_level(raw: str | None) -> str:
    level = (_clean(raw) or DEFAULT_LOG_LEVEL).lower()
    if level not in _LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}, got {level!r}")
    return level
