"""Read and validate `profiles.yaml` at boot (§7.2).

Loading happens once, when the process starts, so a malformed registry stops
the server rather than surfacing as a confusing tool error hours later on a
phone. The error messages are written for someone editing YAML, because that
is who will read them.
"""

from __future__ import annotations

import os
import re
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

# `${NAME}` anywhere in the registry is replaced by that environment variable.
#
# This is the mechanism that keeps spreadsheet ids out of a public repository
# without taking the rest of the registry with them. PROFILES_YAML can do that
# too, by supplying the whole file from the environment — but then the file and
# the deployment hold separate copies of the *structure*, and they drift. That
# is not hypothetical: within an hour of adding it, a corrected profile
# description was live in git and stale in production, because only the ids had
# ever needed to differ and the whole registry had been moved anyway.
#
# So: structure in the file, versioned and reviewed and singular. Only the
# values that genuinely vary per deployment come from the environment.
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


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


def _expand(parsed: object, *, source: str) -> object:
    """Substitute `${NAME}` from the environment across the parsed registry.

    After parsing rather than over the raw text. Substituting into the text
    would reach into comments and into any description that happened to mention
    a placeholder — which is not a hypothetical: the first version of this
    rewrote the comment in `profiles.yaml` that documents the feature, and every
    test failed asking for a variable named NAME.

    Unset variables are an error rather than an empty string. Substituting
    nothing would produce a registry that loads, validates, and then asks Google
    for a spreadsheet whose id is empty — a 404 four layers from the variable
    that was never set.
    """
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        value = os.environ.get(match.group(1), "").strip()
        if not value:
            missing.append(match.group(1))
            return match.group(0)
        return value

    def walk(node: object) -> object:
        if isinstance(node, str):
            return _PLACEHOLDER.sub(replace, node)
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    expanded = walk(parsed)
    if missing:
        listed = ", ".join(sorted(set(missing)))
        raise ProfileConfigError(
            f"{source} refers to environment variable(s) that are unset or empty: {listed}. "
            "Set them, or write literal values in place of the placeholders."
        )
    return expanded


def load_registry_text(raw: str, *, source: str) -> ProfileRegistry:
    """Validate a registry already in hand, naming `source` in any error.

    Used by `PROFILES_YAML` and by the demo server, both of which have the YAML
    without having a file it came from.
    """
    return _validate(raw, source=source)


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
        return ProfileRegistry.model_validate(_expand(parsed, source=source))
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
