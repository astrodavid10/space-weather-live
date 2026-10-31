r"""normalize_model_layout.py - put every event render on the packaging layout.

THE TWO LAYOUTS
    flat        Models\bastille_FieldLines_0000.vlc
    convention  Models\gannon_FieldLines_0000\gannon_FieldLines_0000.vlc

Digistar's own exporter writes the CONVENTION form (Models\<name>\<name>.<ext>), so a
package laid out that way at source makes the .dsz round trip a no-op instead of a
rewrite. Gannon was re-rendered after that was understood and is already convention;
the other five event packages predate it and are flat. Same show, two layouts, which
is what this fixes (David, 2026-09-20: "make sure that file structure is the same").

THE LIVE FEED IS DELIBERATELY NOT TOUCHED. Its Models\c000042\<Layer>.<ext> per-CYCLE
directories are not a layout choice, they are the mechanism: publish_cycle() writes a
whole new cycle directory and the loader turns layers over by polling which cycle each
is on. Flattening those into per-model folders would break the turnover, not tidy it.
Pass --include-live only if you know why you want that.

    python tools/normalize_model_layout.py --dry-run
    python tools/normalize_model_layout.py

Safe to re-run: a package already on the convention is reported and skipped. Files are
moved with os.replace inside the same Models directory, so a move cannot cross volumes
or half-copy. The manifest flag is flipped only AFTER every file has moved, so an
interrupted run leaves the manifest describing the layout that is actually on disk.

Re-emit afterwards - tools/retime_event.py rebuilds the players from the manifest.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODEL_EXT = {".vla", ".vlc", ".obj", ".glb", ".vl", ".dsg", ".x"}


def classify(root: Path) -> str:
    models = root / "Models"
    if not models.is_dir():
        return "no-models"
    flat = [p for p in models.iterdir() if p.is_file() and p.suffix.lower() in MODEL_EXT]
    subs = [p for p in models.iterdir() if p.is_dir()]
    if flat and not subs:
        return "flat"
    if subs and not flat:
        return "convention"
    if flat and subs:
        return "mixed"
    return "empty"


def normalize(root: Path, dry_run=False) -> dict:
    models = root / "Models"
    moved = skipped = 0
    for p in sorted(models.iterdir()):
        if not p.is_file() or p.suffix.lower() not in MODEL_EXT:
            continue
        d = models / p.stem
        dest = d / p.name
        if dest.exists():
            skipped += 1
            continue
        if not dry_run:
            d.mkdir(exist_ok=True)
            os.replace(p, dest)
        moved += 1

    if not dry_run and moved:
        man = models / "event_frames.json"
        if man.is_file():
            j = json.loads(man.read_text(encoding="ascii", errors="replace"))
            j["model_convention_dirs"] = True
            man.write_text(json.dumps(j, indent=1), encoding="ascii")
    return {"moved": moved, "skipped": skipped}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--include-live", action="store_true",
                    help="also flatten the live feed's per-cycle dirs - see the docstring")
    a = ap.parse_args(argv)

    if a.root:
        roots = [Path(r) for r in a.root]
    else:
        base = Path(__file__).resolve().parents[2] / "Renders"
        roots = [d for d in sorted(base.glob("*")) if (d / "Models").is_dir()]
        if not a.include_live:
            roots = [d for d in roots if d.name != "live"]

    total = 0
    for r in roots:
        kind = classify(r)
        if kind == "convention":
            print(f"  {r.name:32s} already on the convention layout")
            continue
        if kind in ("no-models", "empty"):
            print(f"  {r.name:32s} {kind}")
            continue
        n = normalize(r, a.dry_run)
        total += n["moved"]
        note = " (mixed - had both forms)" if kind == "mixed" else ""
        print(f"  {r.name:32s} {n['moved']:6d} files -> Models\\<name>\\{note}"
              f"{'  [dry run]' if a.dry_run else ''}")
    print()
    print(f"{'would move' if a.dry_run else 'moved'} {total} files")
    if total and not a.dry_run:
        print("Next: tools/retime_event.py --root <each> --step-seconds <its value>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
