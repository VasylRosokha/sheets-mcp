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

# The registry inline, as YAML, rather than as a path to a file. It exists so a
# public repository does not have to carry the spreadsheet ids of private
# sheets: they are not credentials, but they are permanent pointers at personal
# data, and there is no reason for them to be in a clone.
#
# Raw YAML rather than base64, unlike GOOGLE_SERVICE_ACCOUNT_KEY. The key is
# opaque and is only ever pasted; this is line-oriented and gets edited in a
# dashboard text box, which base64 would make impossible.
ENV_INLINE = "PROFILES_YAML"


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


def inline_registry() -> str | None:
    """The registry supplied directly in the environment, if any."""
    return os.environ.get(ENV_INLINE, "").strip() or None


def load_registry(path: str | Path | None = None) -> ProfileRegistry:
    """Read, parse, and validate the profile registry.

    An explicit `path` always wins, so a test names its own fixture and cannot
    be affected by whatever the developer has exported. Otherwise `PROFILES_YAML`
    is used if set, then `PROFILES_PATH`, then the file beside `pyproject.toml`.

    Every failure mode is converted to `ProfileConfigError` naming its source. A
    `yaml` or `pydantic` traceback would name a line number in a library the
    reader did not write.
    """
    if path is None:
        inline = inline_registry()
        if inline is not None:
            return _validate(inline, source=f"${ENV_INLINE}")

    resolved = registry_path(path)

    try:
        raw = resolved.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ProfileConfigError(
            f"No profile registry at {resolved}. The repository ships a "
            f"{DEFAULT_FILENAME} with placeholder spreadsheet ids — restore it and fill "
            f"those in, point PROFILES_PATH at your own file, or set {ENV_INLINE} to the "
            "registry itself."
        ) from exc
    except OSError as exc:
        raise ProfileConfigError(f"Could not read {resolved}: {exc}") from exc

    return _validate(raw, source=str(resolved))


def _validate(raw: str, *, source: str) -> ProfileRegistry:
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ProfileConfigError(f"{source} is not valid YAML: {exc}") from exc

    if parsed is None:
        raise ProfileConfigError(f"{source} is empty; it needs a top-level `profiles:` list")
    if not isinstance(parsed, dict):
        raise ProfileConfigError(f"{source} must be a mapping with a top-level `profiles:` key")

    try:
        return ProfileRegistry.model_validate(parsed)
    except ValidationError as exc:
        raise ProfileConfigError(f"{source} is invalid:\n{_render(exc)}") from exc


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
    if path is None and inline_registry() is not None:
        return load_registry()

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
