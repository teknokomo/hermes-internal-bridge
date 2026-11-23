"""Hermes home/root resolution and private-directory handling.

The root Hermes home is the host-wide home that holds ``profiles/``. Named
profiles live at ``<root>/profiles/<name>``; the default profile home *is* the
root. ``get_hermes_home()`` returns the *profile* home, which is not enough for
shared state (spec decision: the bus lives on the root home so every profile of
one host sees the same database).

Resolution order:

1. ``HERMES_HOME`` env override, if set — root derived from the profile layout.
2. ``hermes_constants.get_hermes_home()`` / ``get_default_hermes_root()`` (both
   exist since v0.23.1) when the plugin runs inside Hermes.
3. Platform default: ``~/.hermes`` on POSIX, ``%LOCALAPPDATA%\\hermes`` on Windows.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("hib.paths")

PROFILES_DIR_NAME = "profiles"

# Files that prove a directory is a Hermes home (mirrors hermes_constants._HERMES_HOME_MARKERS).
_HOME_MARKERS = ("config.yaml", ".env", "state.db")


def platform_default_home(env: dict[str, str] | None = None) -> Path:
    """The Hermes home Hermes itself would pick without any configuration."""
    env = os.environ if env is None else env
    if sys.platform == "win32":
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "hermes"
    return Path.home() / ".hermes"


def hermes_home(env: dict[str, str] | None = None) -> Path:
    """The *profile* Hermes home this process runs under."""
    env = os.environ if env is None else env
    override = (env.get("HERMES_HOME") or "").strip()
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    try:
        from hermes_constants import get_hermes_home  # allowlisted since 0.23.1

        return Path(get_hermes_home())
    except Exception:
        return platform_default_home(env)


def hermes_root_of(home: Path) -> Path:
    """The root home that owns *home*: a named profile points at ``<root>/profiles/<name>``.

    The default profile home is the root itself, so it passes through unchanged.
    """
    home = Path(home)
    if home.parent.name == PROFILES_DIR_NAME:
        return home.parent.parent
    return home


def resolve_hermes_root(env: dict[str, str] | None = None) -> Path:
    """The root Hermes home shared by every profile of this host."""
    env = os.environ if env is None else env
    override = (env.get("HERMES_HOME") or "").strip()
    if override:
        return hermes_root_of(hermes_home(env))
    try:
        from hermes_constants import get_default_hermes_root  # allowlisted since 0.23.1

        return Path(get_default_hermes_root())
    except Exception:
        return hermes_root_of(platform_default_home(env))


def profile_name_of_home(home: Path | str) -> str:
    """Profile id that owns *home*: ``profiles/<name>`` → name, the root home → ``default``."""
    home = Path(home)
    if home.parent.name == PROFILES_DIR_NAME:
        return home.name
    return "default"


def profile_name(home: Path | None = None, env: dict[str, str] | None = None,
                 module_path: Path | None = None) -> str:
    """The profile this plugin copy serves.

    A multiplexed gateway runs every profile's plugin copy inside one process, with the
    same ``HERMES_HOME``, so the environment cannot tell them apart. The plugin file's own
    location can: ``<root>/profiles/<name>/plugins/...`` versus ``<root>/plugins/...``.
    """
    if module_path is None:
        module_path = Path(__file__).resolve()
    parts = Path(module_path).parts
    if PROFILES_DIR_NAME in parts:
        index = parts.index(PROFILES_DIR_NAME)
        if index + 1 < len(parts):
            candidate = parts[index + 1]
            if candidate and candidate != PROFILES_DIR_NAME:
                return candidate
    home = home or hermes_home(env)
    if home.parent.name == PROFILES_DIR_NAME:
        return home.name
    return "default"


def expand_path(value: str, env: dict[str, str] | None = None) -> Path:
    """Expand ``${HERMES_ROOT}``, ``${HERMES_HOME}``, ``~`` and environment variables in *value*."""
    env = os.environ if env is None else env
    text = str(value)
    if "${HERMES_ROOT}" in text or "%HERMES_ROOT%" in text:
        text = text.replace("${HERMES_ROOT}", str(resolve_hermes_root(env))).replace(
            "%HERMES_ROOT%", str(resolve_hermes_root(env))
        )
    if "${HERMES_HOME}" in text or "%HERMES_HOME%" in text:
        text = text.replace("${HERMES_HOME}", str(hermes_home(env))).replace(
            "%HERMES_HOME%", str(hermes_home(env))
        )
    text = os.path.expandvars(text)
    return Path(text).expanduser()


def default_bus_state_path(env: dict[str, str] | None = None) -> Path:
    """Default shared-bus database path on the root home's plugin-data area."""
    return (
        resolve_hermes_root(env)
        / "plugin-data"
        / "hermes-internal-bridge"
        / "hib.sqlite3"
    )


def ensure_private_dir(path: Path) -> Path:
    """Create *path* and restrict it to the current user (0700 on POSIX, ACL on Windows).

    On Windows the ACL call is best-effort: icacls may be unavailable in exotic
    environments, so failures are logged and do not break the plugin.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        user = os.environ.get("USERNAME", "")
        if user:
            try:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(OI)(CI)F"],
                    check=True,
                    capture_output=True,
                )
            except Exception as exc:  # pragma: no cover - depends on host tooling
                logger.warning("could not tighten ACLs on %s: %s", path, exc)
    else:
        try:
            os.chmod(path, 0o700)
        except OSError as exc:  # pragma: no cover - unusual filesystems
            logger.warning("could not chmod 0700 on %s: %s", path, exc)
    return path
