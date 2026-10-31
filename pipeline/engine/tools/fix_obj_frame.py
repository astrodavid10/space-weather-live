r"""Rotate already-rendered boundary .obj files into Digistar's Y-up import frame.

WHY THIS EXISTS
---------------
Digistar imports .obj through a Y-up pipeline: a vertex written at file (0, d, 0) is DRAWN
where file (0, 0, d) is, i.e. drawn = (x, -z, y). The format carries no axis metadata to
prevent it. Every other layer this project exports is .vla/.vlc, which Digistar takes
literally, so the boundary shells were the ONLY layer drawn rotated - against the field lines
they are supposed to contain.

Because the rotation is fixed in J2000 while the magnetotail points anti-sunward, the visible
error follows the season rather than the physics:

    48-hour look-back (Sept 2026)   10.7 deg   - reads as fine
    Gannon            (May 2024)    66.3 deg   - reads as badly wrong

which is why it was reported for weeks as a storm-specific physics fault. The exported
geometry was correct the whole time (the tail axis measures 0.13 deg from true anti-Sun in the
file); only the rendering was rotated.

`dsexport/mesh.py` now pre-compensates at write time, so any NEW render is correct. This tool
brings already-rendered packages into line without re-rendering: it is a pure coordinate
rotation of the .obj files, seconds per package, and it touches nothing else.

IDEMPOTENCE
-----------
Applying the rotation twice would leave the shells wrong in a new way, so every converted file
is stamped with the same marker the writer emits, and a stamped file is skipped. That makes
this safe to re-run over a tree that is half converted - which is the realistic case when a
render lands while the tool is being used.

    python -m tools.fix_obj_frame --root <package>      # one render root
    python -m tools.fix_obj_frame --all                 # every render under Renders/
    python -m tools.fix_obj_frame --all --dry-run       # report only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dsexport.mesh import OBJ_YUP_NOTE, obj_precompensate   # noqa: E402
from magneto import paths                                   # noqa: E402

MARKER = "yup-precompensated"


def convert_file(path: Path, dry_run: bool = False) -> str:
    """Returns 'converted', 'already', or 'empty'."""
    text = path.read_text(encoding="ascii", errors="replace")
    if MARKER in text:
        return "already"
    lines = text.splitlines()
    out, n_v, n_n = [], 0, 0
    header_done = False
    for ln in lines:
        if ln.startswith("v "):
            if not header_done:
                out.append("# " + OBJ_YUP_NOTE)
                header_done = True
            p = obj_precompensate(np.array([float(x) for x in ln.split()[1:4]]))
            out.append(f"v {p[0]:.9g} {p[1]:.9g} {p[2]:.9g}")
            n_v += 1
        elif ln.startswith("vn "):
            p = obj_precompensate(np.array([float(x) for x in ln.split()[1:4]]))
            out.append(f"vn {p[0]:.5f} {p[1]:.5f} {p[2]:.5f}")
            n_n += 1
        else:
            out.append(ln)
    if not n_v:
        return "empty"
    if not dry_run:
        # write beside, then replace, so a kill mid-write cannot leave a half-rotated shell
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(out) + "\n", encoding="ascii")
        tmp.replace(path)
    return "converted"


def shell_objs(root: Path) -> list[Path]:
    """Every boundary .obj under a render root, whatever the layout.

    Two layouts exist and the first version of this tool only handled one of them:

      * EVENT renders      Models/MagnetopauseShell_0217.obj      (flat, numbered)
      * the LIVE feed      Models/c000382/MagnetopauseShell.obj   (one dir per cycle, UNnumbered)

    A non-recursive `*Shell_*.obj` misses the live feed on both counts - the nested cycle
    directory AND the missing `_NNNN`. It reported "0 converted" for the live root without
    saying anything was odd, which is the same silent-default shape that hid the shells_hidden
    bug and the write_shims bug. Hence rglob, a looser stem, and a caller that prints the file
    count per root so an empty one is visible.
    """
    models = root / "Models"
    if not models.is_dir():
        return []
    return sorted(p for p in models.rglob("*Shell*.obj") if p.is_file())


def convert_root(root: Path, dry_run: bool = False) -> dict:
    tally = {"converted": 0, "already": 0, "empty": 0}
    for o in shell_objs(root):
        tally[convert_file(o, dry_run)] += 1
    return tally


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.fix_obj_frame",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", help="one render root (its Models/ holds the .obj files)")
    ap.add_argument("--all", action="store_true", help="every render under Renders/")
    ap.add_argument("--dry-run", action="store_true", help="report without writing")
    args = ap.parse_args(argv)

    if not args.root and not args.all:
        ap.error("give --root or --all")

    roots = []
    if args.root:
        roots.append(Path(args.root))
    if args.all:
        base = paths.renders_root()
        roots += [d for d in sorted(base.glob("*")) if (d / "Models").is_dir()]

    total = {"converted": 0, "already": 0, "empty": 0}
    for r in roots:
        t = convert_root(r, args.dry_run)
        n = sum(t.values())
        for k in total:
            total[k] += t[k]
        note = "" if n else "   <- NO shell .obj found; check the layout"
        print(f"{r.name:34s} files {n:5d}  converted {t['converted']:5d}  "
              f"already {t['already']:5d}{note}")
    verb = "would convert" if args.dry_run else "converted"
    print(f"\n{verb} {total['converted']} shell .obj file(s); "
          f"{total['already']} already carried the stamp")
    if total["empty"]:
        print(f"WARNING: {total['empty']} file(s) had no vertices and were left alone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
