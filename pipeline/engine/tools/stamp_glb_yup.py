r"""stamp_glb_yup.py - add the missing y-up stamp to .glb meshes written by the
first version of obj_to_glb.py.

WHY THIS EXISTS
    dsexport/mesh.py writes shell vertices as (x, z, -y) to cancel Digistar's Y-up
    .obj import, and records that in a header comment. A .glb has no comments, so
    the note belongs in asset.extras.yup - which is where webexport.read_glb looks
    to decide whether to undo the rotation.

    obj_to_glb.py's first version dropped it. 3,436 converted shells carry correct
    geometry and no stamp, so every reader that trusts the stamp skips the undo and
    draws the shells rotated against the field lines they contain. That is precisely
    the fault that read as a physics error for three weeks in September 2026.

WHAT DECIDES WHETHER A FILE GETS STAMPED: PROVENANCE, NOT GEOMETRY
    Only files whose asset.generator is "magneto obj_to_glb.py" are stamped. That
    tool read nothing but .obj files written by dsexport/mesh.py with
    OBJ_YUP_PRECOMPENSATE on, and fix_obj_frame.py had already converted every
    older .obj on 2026-09-17 - so everything it produced is precompensated, and the
    stamp records a fact about how the file was made.

    A GEOMETRIC test was tried first and rejected: "the long axis lands in the
    ecliptic plane only after the undo" is season-dependent, because the magnetotail's
    direction in J2000 moves through the year. It flagged all 576 Oct-2024 shells as
    "not precompensated" when they are simply from a different month than the Gannon
    frames it was calibrated on. It is kept below as an ADVISORY only - a mismatch is
    printed, never acted on - because a wrong stamp is worse than a missing one.

    Files that already carry a stamp, or that came from any other generator, are left
    untouched.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dsexport.mesh import OBJ_YUP_NOTE                      # noqa: E402
from webexport.read_obj import undo_yup                     # noqa: E402


def _read(path: Path):
    b = path.read_bytes()
    magic, ver, total = struct.unpack("<III", b[:12])
    if magic != 0x46546C67 or ver != 2 or total != len(b):
        raise ValueError("not a well-formed glTF 2.0 binary")
    jlen, jtag = struct.unpack("<II", b[12:20])
    if jtag != 0x4E4F534A:
        raise ValueError("first chunk is not JSON")
    g = json.loads(b[20:20 + jlen].decode("utf-8"))
    blen, btag = struct.unpack("<II", b[20 + jlen:28 + jlen])
    blob = b[28 + jlen:28 + jlen + blen]
    return g, blob


def _positions(g, blob):
    a = g["accessors"][0]
    v = g["bufferViews"][a["bufferView"]]
    return np.frombuffer(blob, np.float32, count=int(a["count"]) * 3,
                         offset=int(v["byteOffset"])).reshape(-1, 3).astype(np.float64)


def looks_precompensated(P: np.ndarray) -> bool:
    """A shell's long axis lands in the ecliptic-ish plane only after the undo."""
    if P.size == 0:
        return False
    as_stored = int((P.max(0) - P.min(0)).argmax())
    undone = undo_yup(P)
    after = int((undone.max(0) - undone.min(0)).argmax())
    return as_stored == 2 and after in (0, 1)


#: the one generator whose output is known to be precompensated and unstamped
CONVERTER = "magneto obj_to_glb.py"


def stamp(path: Path, dry_run=False) -> str:
    try:
        g, blob = _read(path)
    except Exception as e:                                   # noqa: BLE001
        return f"unreadable ({e})"
    asset = g.get("asset") or {}
    extras = asset.get("extras") or {}
    if "yup" in extras:
        return "already"
    if asset.get("generator") != CONVERTER:
        return f"other generator ({asset.get('generator')!r}) - left alone"
    if not looks_precompensated(_positions(g, blob)):
        # advisory only: the check is season-dependent (see the module docstring)
        print(f"    note: {path.name} does not match the ecliptic-axis heuristic "
              "(expected for some epochs) - stamping on provenance")
    if dry_run:
        return "would stamp"
    extras["yup"] = OBJ_YUP_NOTE
    extras["stamped_by"] = "tools/stamp_glb_yup.py"
    g.setdefault("asset", {})["extras"] = extras
    js = json.dumps(g, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)                     # glTF pads JSON with SPACES
    blob += bytes(((4 - len(blob) % 4) % 4))
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
                     + struct.pack("<II", len(js), 0x4E4F534A) + js
                     + struct.pack("<II", len(blob), 0x004E4942) + blob)
    return "stamped"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    roots = ([Path(r) for r in a.root] if a.root else
             [d for d in sorted((Path(__file__).resolve().parents[2] / "Renders").glob("*"))
              if (d / "Models").is_dir()])
    from collections import Counter
    grand = Counter()
    for r in roots:
        c = Counter()
        for f in sorted((r / "Models").rglob("*.glb")):
            c[stamp(f, a.dry_run)] += 1
        if c:
            print("  %-30s %s" % (r.name, dict(c)))
            grand.update(c)
    print("\nTOTAL", dict(grand))
    bad = sum(v for k, v in grand.items() if k.startswith("unreadable"))
    if bad:
        print(f"{bad} file(s) were unreadable - inspect them before shipping")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
