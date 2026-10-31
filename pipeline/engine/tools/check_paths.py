#!/usr/bin/env python
r"""The no-hardcoded-paths gate.

Fails (exit 1, one line per hit) if any shipped file spells a drive letter or a
user profile: G:\, I:\, C:\Users\, C:\Data\, anaconda3, the old I:\08_ / G:\2026
roots. Comments count - a misleading comment is the operator's problem too.
C:\CXSoftware is allowed (the Digistar install itself).

Scanned:  <package>\Scripts\**  (js ds py ps1 vbs bat cmd json)
          <package>\ControlPanel\*.dscp
          <package>\Install\**
          <package>\Config\*.example.json
          <engine>\**\*.py  and  <engine>\tools\**
Skipped:  Renders\, .venv\, docs\, *.md, *.lis, *.bak, *.pre-*, __pycache__,
          Config\magneto.local.json (machine-local by design), tests\fixtures\

    python tools\check_paths.py            # exit 0 = clean
Also run as a test: tests\test_no_hardcoded_paths.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from magneto import paths                                   # noqa: E402

PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9_])[GI]:\\|C:\\Users\\|C:\\Data\\|I:\\08_|G:\\2026|anaconda3")
ALLOW = re.compile(r"(?i)C:\\CXSoftware")
# A per-line opt-out. A human has to type the marker, so it cannot happen by
# accident, and `grep -rn portable-path` lists every exemption for review. It
# exists for one situation: code that SEARCHES the standard places an
# interpreter might live (the installer sweeps $env:USERPROFILE and the
# machine-wide conda roots) rather than naming this box's own path. Loosening
# the regex instead would stop it catching a genuinely hardcoded interpreter.
OPT_OUT = "portable-path"
SKIP_DIR_NAMES = {"Renders", ".venv", "docs", "__pycache__", ".pytest_cache", "fixtures",
                  "_retired", "_retired_eye_views", "output", ".staging",
                  "dev"}          # tools\dev = this-box measurement tools, never shipped
SKIP_SUFFIXES = {".md", ".lis", ".bak", ".pyc", ".png", ".jpg", ".dds", ".vla", ".vlc", ".obj"}
SCRIPT_SUFFIXES = {".js", ".ds", ".py", ".ps1", ".vbs", ".bat", ".cmd", ".json", ".dscp"}


SELF_NAMES = {"check_paths.py", "test_no_hardcoded_paths.py"}   # the gate's own regex


def _skip(p: Path) -> bool:
    if p.name in SELF_NAMES:
        return True
    if any(part in SKIP_DIR_NAMES for part in p.parts):
        return True
    # (no run_kickoff_*.vbs exemption since 2026-10-04: kickoff.js, which wrote them with this
    # machine's interpreter path baked in, is retired, and the .vbs launchers are static)
    if p.suffix.lower() in SKIP_SUFFIXES or ".pre-" in p.name:
        return True
    if p.name == paths.CONFIG_NAME:
        return True
    return False


def scan_roots() -> list[Path]:
    roots = [paths.ENGINE]
    if paths.IN_PACKAGE:
        roots += [paths.PACKAGE / "Scripts", paths.PACKAGE / "ControlPanel",
                  paths.PACKAGE / "Install", paths.PACKAGE / "Config"]
    return [r for r in roots if r.exists()]


def find_hits(roots=None) -> list[tuple[Path, int, str]]:
    hits = []
    for root in (roots or scan_roots()):
        for p in root.rglob("*"):
            if not p.is_file() or _skip(p) or p.suffix.lower() not in SCRIPT_SUFFIXES:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if text.startswith("\ufeff"):
                text = text[1:]
            for n, line in enumerate(text.splitlines(), 1):
                if (PATTERN.search(line) and not ALLOW.search(line)
                        and OPT_OUT not in line):
                    hits.append((p, n, line.strip()[:160]))
    return hits


def main() -> int:
    hits = find_hits()
    for p, n, line in hits:
        print(f"{p}:{n}: {line}")
    print(f"{len(hits)} hardcoded-path hit(s) across {len(scan_roots())} root(s)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
