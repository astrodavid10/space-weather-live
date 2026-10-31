#!/usr/bin/env python
r"""check_physics.py - is every rendered package built by the SAME physics?

David's constraint on the 2026-09-16 realism work was "be diligent that new
physics being added does not break older versions and make them inaccurate - we
want everything to be agreeable when you are finished". This is the tool that
answers that with a fact rather than an opinion: it walks every render under
Renders\, reads the `physics` block each manifest carries, and exits non-zero if
the set is mixed.

    python -m tools.check_physics                # every render under Renders\
    python -m tools.check_physics --root <dir>   # one tree
    python -m tools.check_physics --json         # machine-readable

A package rendered BEFORE the stamp existed has no `physics` block at all. That
is reported as `unstamped`, not as agreement - a missing stamp means the physics
is unknown, which is exactly the thing being checked for.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from magneto import paths                              # noqa: E402
from magneto.model import PHYSICS_VERSION, physics_stamp   # noqa: E402

EVENT_MANIFEST = "event_frames.json"
LIVE_MANIFEST = "live_manifest.json"


def find_manifests(root: Path) -> list[Path]:
    """Every render manifest under `root`, newest publication first.

    Only Models/ is searched: .staging and ModelsPrevious hold half-built and
    superseded trees whose physics is deliberately not the question.
    """
    out = []
    for name in (EVENT_MANIFEST, LIVE_MANIFEST):
        for p in sorted(root.glob(f"*/Models/{name}")) + sorted(root.glob(f"Models/{name}")):
            if "ModelsPrevious" in p.parts or ".staging" in str(p):
                continue
            out.append(p)
    return out


def read_physics(path: Path) -> dict:
    """The one package's physics record. Never raises - an unreadable manifest is
    a finding, not a crash."""
    rec = {"path": str(path), "package": path.parents[1].name,
           "kind": "live" if path.name == LIVE_MANIFEST else "event"}
    try:
        m = json.loads(path.read_text(encoding="ascii", errors="replace"))
    except (OSError, ValueError) as exc:
        return dict(rec, error=f"{type(exc).__name__}: {exc}", physics=None)
    rec["event_id"] = m.get("event_id")
    rec["model"] = m.get("model")
    rec["n_frames"] = m.get("n_frames")
    rec["plan_digest"] = m.get("plan_digest")
    rec["physics"] = m.get("physics")
    return rec


def summarise(records: list[dict]) -> dict:
    """Group the packages by physics version. `None` == rendered before the stamp."""
    by_version: dict = {}
    for r in records:
        ph = r.get("physics") or {}
        v = ph.get("physics_version")
        by_version.setdefault(v, []).append(r)
    versions = sorted(v for v in by_version if v is not None)
    unstamped = by_version.get(None, [])
    current = PHYSICS_VERSION
    ok = not unstamped and len(versions) <= 1 and (not versions or versions[0] == current)
    return {"ok": ok, "current": current, "versions": versions,
            "n_unstamped": len(unstamped), "by_version": by_version,
            "n_packages": len(records)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=None,
                    help="tree to walk (default: the package's Renders dir)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)

    root = a.root or paths.renders_root()
    mans = find_manifests(root)
    records = [read_physics(p) for p in mans]
    summ = summarise(records)

    if a.json:
        print(json.dumps({"root": str(root), "summary":
                          {k: v for k, v in summ.items() if k != "by_version"},
                          "packages": records}, indent=1))
        return 0 if summ["ok"] else 1

    print(f"physics consistency under {root}")
    print(f"  engine is at physics v{summ['current']} ({physics_stamp()['tag']})")
    if not records:
        print("  no rendered packages found")
        return 0
    width = max(len(r["package"]) for r in records)
    for r in sorted(records, key=lambda r: r["package"]):
        ph = r.get("physics") or {}
        v = ph.get("physics_version")
        if r.get("error"):
            tag = f"UNREADABLE  {r['error']}"
        elif v is None:
            tag = "UNSTAMPED   rendered before the physics stamp - physics unknown"
        elif v != summ["current"]:
            tag = f"v{v} STALE   {ph.get('tag')} (engine is v{summ['current']})"
        else:
            tag = f"v{v}          {ph.get('tag')}"
        print(f"  {r['package']:<{width}}  {r['kind']:<5}  {tag}")
    print()
    if summ["ok"]:
        print(f"OK: all {summ['n_packages']} packages on physics v{summ['current']}")
        return 0
    bits = []
    if summ["n_unstamped"]:
        bits.append(f"{summ['n_unstamped']} unstamped")
    stale = [v for v in summ["versions"] if v != summ["current"]]
    if stale:
        bits.append("stale versions " + ", ".join(f"v{v}" for v in stale))
    print("MIXED: " + "; ".join(bits))
    print("Re-render the packages listed above on the current engine, or accept the")
    print("mixture deliberately - but do not ship a panel that plays both.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
