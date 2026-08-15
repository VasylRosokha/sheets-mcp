"""Read and validate `profiles.yaml` at boot (§7.2).

Loading happens once, when the process starts, so a malformed registry stops
the server rather than surfacing as a confusing tool error hours later on a
phone. The error messages are written for someone editing YAML, because that
is who will read them.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from sheets_mcp.profiles.models import ProfileRegistry

DEFAULT_FILENAME = "profiles.yaml"


class ProfileConfigError(RuntimeError):
    """Raised at boot when `profiles.yaml` is missing, malformed, or invalid."""


class ProfileNotFoundError(LookupError):
    """Raised when a tool is asked for a profile name that resolves to nothing.

    Carries the available names, because the model that guessed wrong is the
    one that needs the list, and a second round trip to `list_profiles` is a
    poor way to deliver it.
    """

    def __init__(self, token: str, available: list[str]) -> None:
        self.token = token
        self.available = available
        super().__init__(f"No profile named {token!r}. Available profiles: {', '.join(available)}.")


def registry_path(explicit: str | Path | None = None) -> Path:
    """Locate `profiles.yaml`.

    `PROFILES_PATH` exists so the deployed service can point at a file mounted
    outside the repository; the default keeps it beside `pyproject.toml`, which
    is where someone editing the project would look for it.
    """
    if explicit is not None:
        return Path(explicit)
    from_env = os.environ.get("PROFILES_PATH", "").strip()
    if from_env:
        return Path(from_env)
    return Path(__file__).resolve().parents[3] / DEFAULT_FILENAME


def load_registry(path: str | Path | None = None) -> ProfileRegistry:
    """Read, parse, and validate the profile registry.

    Every failure mode is converted to `ProfileConfigError` with the file path
    in the message. A `yaml` or `pydantic` traceback would name a line number
    in a library the reader did not write.
    """
    resolved = registry_path(path)

    try:
        raw = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProfileConfigError(
            f"No profile registry at {resolved}. Copy profiles.example.yaml to "
            f"{DEFAULT_FILENAME} and fill in the spreadsheet IDs and tab names."
        ) from exc
    except OSError as exc:
        raise ProfileConfigError(f"Could not read {resolved}: {exc}") from exc

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ProfileConfigError(f"{resolved} is not valid YAML: {exc}") from exc

    if parsed is None:
        raise ProfileConfigError(f"{resolved} is empty; it needs a top-level `profiles:` list")
    if not isinstance(parsed, dict):
        raise ProfileConfigError(f"{resolved} must be a mapping with a top-level `profiles:` key")

    try:
        return ProfileRegistry.model_validate(parsed)
    except ValidationError as exc:
        raise ProfileConfigError(f"{resolved} is invalid:\n{_render(exc)}") from exc


def load_registry_if_present(path: str | Path | None = None) -> ProfileRegistry | None:
    """Load the registry, treating an absent file as "not configured yet".

    The distinction is deliberate. A registry that exists and is wrong is a
    mistake, and the process refuses to start on it. A registry that does not
    exist yet is a stage of the build — the server ran with no profiles at all
    through Phase 1 — and taking the service down for it would trade a working
    `ping` for nothing.

    The tools report the empty case in their own output, so a caller is told
    "no profiles are configured" rather than being handed an empty list and
    left to infer it.
    """
    resolved = registry_path(path)
    if not resolved.exists():
        return None
    return load_registry(resolved)


def _render(error: ValidationError) -> str:
    """Turn pydantic's error list into lines an editor of the YAML can act on.

    pydantic's own rendering leads with the model class name and the union
    discriminator, neither of which appears in the file being edited.
    """
    lines: list[str] = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"] if part != "function-after")
        lines.append(f"  {location or '(root)'}: {detail['msg']}")
    return "\n".join(lines)
