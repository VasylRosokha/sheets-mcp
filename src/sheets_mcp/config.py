"""Process configuration, read from the environment once at boot.

Every value is validated here rather than at the point of use, so a
misconfigured host fails immediately and loudly instead of at the first
tool call — which, given the client is a phone, would surface as an
unexplained error hours later.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_PORT = 8787
DEFAULT_TIMEZONE = "Europe/Prague"
DEFAULT_LOG_LEVEL = "info"

_LOG_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})

# Hosts the MCP endpoint answers to when nothing else is configured. The SDK's
# DNS-rebinding protection rejects any other Host header with a 421, which is
# correct for a server reached at one known name and invisible in local testing
# — where these are the only names in play.
_LOCAL_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")


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
    allowed_hosts: tuple[str, ...]
    # Base64 of the service account JSON key (§5.3). Not decoded here: this
    # class stays free of Google imports so it can be constructed in tests
    # without them, and an unparseable key is the Sheets client's error to
    # report, with the guidance about `base64 -w0` that goes with it.
    google_service_account_key: str | None
    # Serve synthetic sheets from memory instead of Google (`--demo`). Kept in
    # Settings rather than checked at the call site so exactly one thing decides
    # it, and so the log line at startup records which mode the process is in.
    demo: bool

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        """Origins accepted alongside `allowed_hosts`.

        A server-to-server caller sends no `Origin` at all, which the SDK
        permits; this exists so a browser-based client is not locked out. Local
        names get `http://` because nothing issues certificates for them.
        """
        return tuple(
            f"http://{host}" if host in _LOCAL_HOSTS else f"https://{host}" for host in self.allowed_hosts
        )

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

        demo = _clean(source.get("SHEETS_MCP_DEMO")) in {"1", "true", "yes"}

        # Demo mode waives it. There is nothing to protect: the sheets are
        # generated in memory, there are no credentials in the process, and
        # requiring a key would mean a reviewer has to invent one before they
        # can see anything.
        if api_key is None and secret_path is None and not allow_open and not demo:
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
            allowed_hosts=_allowed_hosts(source),
            google_service_account_key=_clean(source.get("GOOGLE_SERVICE_ACCOUNT_KEY")),
            demo=demo,
        )


def _allowed_hosts(source: Mapping[str, str]) -> tuple[str, ...]:
    """Resolve which `Host` headers the MCP endpoint will answer to.

    `RENDER_EXTERNAL_HOSTNAME` is injected by the platform, so a Render deploy
    needs no manual configuration and cannot drift from the real hostname when
    the service is renamed. `MCP_ALLOWED_HOSTS` overrides it for anywhere else,
    and the local names are always kept so a developer's own machine works
    without special-casing.
    """
    explicit = _clean(source.get("MCP_ALLOWED_HOSTS"))
    if explicit is not None:
        configured = tuple(host.strip() for host in explicit.split(",") if host.strip())
        return (*configured, *_LOCAL_HOSTS)

    external = _clean(source.get("RENDER_EXTERNAL_HOSTNAME"))
    if external is not None:
        return (external, *_LOCAL_HOSTS)

    return _LOCAL_HOSTS


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
