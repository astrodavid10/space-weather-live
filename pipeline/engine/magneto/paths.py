"""Where everything lives - the ONE module that knows the package layout.

The Live Magnetosphere ships as a Digistar content package:

    <package>\\                      $Content\\User\\Packages\\INTUITIVE\\LiveMagnetosphere
        ControlPanel\\               the .dscp + Media\\
        Scripts\\                    panel-fired .ds/.js + GENERATED shims
        Engine\\                     this Python tree (optional on a playback-only dome)
        Renders\\lookback\\          the 48 h look-back render (Models\\ + Scripts\\)
        Renders\\storm_<id>\\        one root per curated storm
        Renders\\live\\              the live daemon's root
        Renders\\_kickoffs\\         kickoff logs, RUNNING.lock, progress.json
        Config\\magneto.local.json  machine-local, installer-written, never shipped

Nothing else in the engine may spell a drive letter. Every location is derived
from this file's own position (``Path(__file__)``) plus the optional local
config, so the same tree works under the package, under a bare checkout
(``PACKAGE is ENGINE`` then, and renders land in ``<checkout>\\output``), and
on another Digistar system after a plain copy.

``ds_token(path)`` turns a filesystem path under ``$Content`` into the
``$Content\\...`` form Digistar scripts use, so generated ``.ds`` shims never
carry a drive letter either.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
PACKAGE = ENGINE.parent if ENGINE.name.lower() == "engine" else ENGINE
IN_PACKAGE = PACKAGE != ENGINE

CONFIG_NAME = "magneto.local.json"
CONFIG_DIR = PACKAGE / "Config"
SCRIPTS_DIR = PACKAGE / "Scripts"          # panel-fired scripts + generated shims
CONTROLPANEL_DIR = PACKAGE / "ControlPanel"

WORKERS_CAP = 24                           # the pool is memory/IO-bound past this (CLAUDE.md)
LOOKBACK_DIRNAME = "lookback"
LIVE_DIRNAME = "live"
KICKOFF_DIRNAME = "_kickoffs"
STORM_PREFIX = "storm_"


def config_path() -> Path:
    return CONFIG_DIR / CONFIG_NAME


def load_local_config() -> dict:
    """The machine-local config, or {} when none has been written yet."""
    try:
        # utf-8-sig: PowerShell 5.1's Out-File -Encoding utf8 writes a BOM, which json.load rejects
        with open(config_path(), encoding="utf-8-sig") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def renders_root() -> Path:
    """Where rendered packages land: config override, else <package>\\Renders
    (or <checkout>\\output on a bare engine checkout)."""
    cfg = load_local_config().get("renders_root")
    if cfg:
        return Path(str(cfg))
    return PACKAGE / "Renders" if IN_PACKAGE else ENGINE / "output"


def render_root(kind: str, event_id: str | None = None) -> Path:
    """'lookback' -> Renders\\lookback, ('storm', id) -> Renders\\storm_<id>, 'live' -> Renders\\live."""
    kind = str(kind).lower()
    if kind == "lookback":
        return renders_root() / LOOKBACK_DIRNAME
    if kind == "live":
        return renders_root() / LIVE_DIRNAME
    if kind == "storm":
        if not event_id:
            raise ValueError("render_root('storm') needs an event id")
        return renders_root() / (STORM_PREFIX + str(event_id))
    raise ValueError(f"unknown render kind {kind!r} (lookback | storm | live)")


def kickoff_log_dir() -> Path:
    return renders_root() / KICKOFF_DIRNAME


def venv_python() -> Path:
    return ENGINE / ".venv" / "Scripts" / "python.exe"


def python_exe() -> Path:
    """The interpreter that runs the engine on THIS machine.

    Order: local config -> the engine's own .venv -> whatever is running now.
    The last rule keeps a developer's conda env working without a config.
    """
    cfg = load_local_config().get("python_exe")
    if cfg and Path(str(cfg)).exists():
        return Path(str(cfg))
    if venv_python().exists():
        return venv_python()
    return Path(sys.executable)


def workers() -> int:
    cfg = load_local_config().get("workers")
    try:
        n = int(cfg) if cfg else 0
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        n = min(WORKERS_CAP, max(1, (os.cpu_count() or 2)))
    return max(1, n)


def ds_token(path) -> str:
    r"""Filesystem path -> the form a Digistar script should use.

    Anything under <content root>\User\Packages\... or \User\Downloads\... comes
    back as '$Content\User\...' (portable across machines - that is how the
    package addresses itself). Anything else is returned as an absolute
    Windows path, which is only ever right on the machine that wrote it.
    """
    p = Path(path)
    parts = [str(x) for x in p.parts]
    for i in range(len(parts) - 1):
        if parts[i].lower() == "user" and parts[i + 1].lower() in ("packages", "downloads"):
            return "$Content\\" + "\\".join(parts[i:])
    return str(p.resolve()) if not p.is_absolute() else str(p)


def package_token() -> str:
    """'$Content\\User\\Packages\\INTUITIVE\\LiveMagnetosphere' (or the absolute
    path on a bare checkout)."""
    return ds_token(PACKAGE)


def describe() -> dict:
    """Everything a human or the installer wants to see in one place."""
    return {
        "engine": str(ENGINE), "package": str(PACKAGE), "in_package": IN_PACKAGE,
        "package_token": package_token(), "config": str(config_path()),
        "config_present": config_path().exists(), "renders_root": str(renders_root()),
        "python_exe": str(python_exe()), "workers": workers(),
    }


if __name__ == "__main__":                                 # pragma: no cover
    print(json.dumps(describe(), indent=1))
