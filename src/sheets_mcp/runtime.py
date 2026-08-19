"""Process-wide dependencies, resolved once and shared by every tool.

The registry is loaded eagerly, because an invalid one should stop the process
rather than surface as a tool error on a phone. The Sheets client is built
lazily, because credentials are only needed by tools that touch a spreadsheet —
and during Phase 1 there were none, so requiring them at boot would have taken
down a working `ping`.
"""

from __future__ import annotations

from pathlib import Path

from sheets_mcp.config import Settings
from sheets_mcp.errors import ProfileNotFound, RegistryMissing
from sheets_mcp.logging import get_logger
from sheets_mcp.profiles.loader import (
    inline_registry,
    load_registry_if_present,
    load_registry_text,
)
from sheets_mcp.profiles.models import Profile, ProfileRegistry
from sheets_mcp.sheets.backend import SheetsBackend
from sheets_mcp.sheets.client import build_client

log = get_logger(__name__)


class Runtime:
    """Everything the tools need, assembled once."""

    def __init__(self, settings: Settings, *, registry_path: str | Path | None = None) -> None:
        self.settings = settings
        # Recorded before loading, because afterwards the two sources are
        # indistinguishable — which is exactly the confusion this answers. A
        # deploy meant to read PROFILES_YAML but quietly still reading the file
        # behaves identically until the file changes, and by then the change is
        # the thing that gets blamed.
        self.registry_source = (
            "env" if registry_path is None and inline_registry() is not None else "file"
        )
        if settings.demo:
            from sheets_mcp import demo

            self.registry_source = "demo"
            self.registry: ProfileRegistry | None = load_registry_text(
                demo.registry_yaml, source="the demo registry"
            )
        else:
            self.registry = load_registry_if_present(registry_path)
        self._client: SheetsBackend | None = None

        if self.registry is None:
            self.registry_source = "none"
            log.warning("no_profile_registry", hint="restore profiles.yaml, or set PROFILES_YAML")
        else:
            log.info("profiles_loaded", profiles=self.registry.names, source=self.registry_source)

    def require_registry(self) -> ProfileRegistry:
        if self.registry is None:
            raise RegistryMissing
        return self.registry

    def require_profile(self, token: str) -> Profile:
        """Resolve a name or alias, or raise the §10 `PROFILE_NOT_FOUND`.

        The error carries the available names so a model that guessed wrong can
        correct itself without a second call to `list_profiles`.
        """
        registry = self.require_registry()
        profile = registry.resolve(token)
        if profile is None:
            raise ProfileNotFound(token, registry.names)
        return profile

    def client(self) -> SheetsBackend:
        """The Sheets client, built on first use.

        Failures are not cached. Credentials arrive from the environment, and
        re-raising on every call means a fixed configuration takes effect on the
        next request rather than needing a restart to clear a remembered error.
        """
        if self._client is None:
            if self.settings.demo:
                from sheets_mcp import demo

                self._client = demo.build_backend()
                log.info("demo_sheets_ready")
            else:
                self._client = build_client(self.settings.google_service_account_key)
                log.info("sheets_client_ready", client_email=self._client.client_email)
        return self._client
