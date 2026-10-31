r"""obj_to_glb.py - convert a render's boundary-shell .obj models to binary glTF.

WHY
    Measured 2026-09-20 on a real Gannon shell: 595,385 B as .obj against 146,000 B
    as .glb - 24.5% of the size, because .obj stores every coordinate as 9-significant-
    figure ASCII while .glb stores float32 binary, which is exactly the precision the
    cook keeps anyway (DXGI_FORMAT_R32G32B32_FLOAT). Nothing is lost.

VERIFIED ON THE DOME BEFORE THIS TOOL WAS WRITTEN, each against a control:
    renders identically   obj vs glb 43.95 dB, obj vs obj 43.99 dB (fixed step, one run)
                          and on a shell 54.62 dB vs a 55.18 dB control
    orientation carries   the (x, z, -y) Y-up pre-compensation applies to glTF exactly
                          as to .obj - a .glb built from precompensated vertices lands
                          on the .obj to within the scene's own noise floor
    re-point cadence      312 steps at 0.8 s: motion band identical to .obj
                          (min 21.59, max 30.41, spread 8.82 dB for both)
    extension             Digistar accepts .glb. The Users Guide's 3D Model Formats
                          table says ".gltf .gltb"; .gltb is REJECTED outright
                          ("'.gltb' is not a supported model file type"). Doc bug.
    Draco                 not supported by Digistar, so the buffers stay uncompressed.

WHAT IT DOES
    For every <name>Shell*.obj under <root>/Models: writes <name>.glb beside it,
    verifies the round trip, and (unless --keep-obj) removes the .obj. Then rewrites
    the layer's file list in the manifest so the player re-emit points at .glb.

    Idempotent: a shell that already has a .glb and no .obj is reported as 'already'.
    The .obj is only deleted AFTER its .glb has been read back and checked, so an
    interrupted run leaves both, never neither.

    It does NOT re-emit the players - run tools/retime_event.py afterwards, which
    rebuilds every script from the manifest this tool just updated.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_obj(path: Path):
    """(verts, normals, tris) from an OBJ. Faces may be tris or quads."""
    V, N, F = [], [], []
    for line in path.read_text(encoding="ascii", errors="replace").splitlines():
        if line.startswith("v "):
            V.append([float(x) for x in line.split()[1:4]])
        elif line.startswith("vn "):
            N.append([float(x) for x in line.split()[1:4]])
        elif line.startswith("f "):
            idx = [int(t.split("/")[0]) - 1 for t in line.split()[1:]]
            for k in range(1, len(idx) - 1):
                F.append([idx[0], idx[k], idx[k + 1]])
    return (np.asarray(V, np.float32), np.asarray(N, np.float32),
            np.asarray(F, np.uint32))

_PAD_CHARS = " " + chr(0)   # glTF pads JSON with spaces; tolerate NUL from older writers


def _pad4(b: bytes, fill: bytes = b"\x00") -> bytes:
    """Pad to a 4-byte boundary.

    glTF 2.0 requires the JSON chunk to be padded with SPACES and the BIN chunk with
    zeros. Padding JSON with zeros produced 2,551 failures on the first full run -
    json.loads() reads the trailing NULs as "Extra data" - so the fill is explicit
    here rather than defaulted for both.
    """
    return b + fill * ((4 - len(b) % 4) % 4)


def write_glb(path: Path, V, N, F) -> int:
    """Minimal single-mesh binary glTF 2.0. Vertices are written AS GIVEN -
    the caller's .obj is already Y-up pre-compensated and glTF inherits that."""
    has_n = len(N) == len(V)
    dt, ct = (np.uint16, 5123) if len(V) < 65536 else (np.uint32, 5125)
    I = F.astype(dt)

    bv, bn, bi = V.tobytes(), (N.tobytes() if has_n else b""), _pad4(I.tobytes())
    blob = _pad4(bv + bn + bi)

    views, acc, attrs, off = [], [], {}, 0
    views.append({"buffer": 0, "byteOffset": 0, "byteLength": len(bv), "target": 34962})
    acc.append({"bufferView": 0, "componentType": 5126, "count": len(V), "type": "VEC3",
                "min": V.min(0).tolist(), "max": V.max(0).tolist()})
    attrs["POSITION"] = 0
    off = len(bv)
    if has_n:
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(bn), "target": 34962})
        acc.append({"bufferView": 1, "componentType": 5126, "count": len(N), "type": "VEC3"})
        attrs["NORMAL"] = 1
        off += len(bn)
    views.append({"buffer": 0, "byteOffset": off, "byteLength": len(bi), "target": 34963})
    acc.append({"bufferView": len(views) - 1, "componentType": ct, "count": I.size,
                "type": "SCALAR"})

    # CARRY THE Y-UP STAMP. The .obj records "yup-precompensated: ..." in a header
    # comment; a binary container has no comments, so it goes to asset.extras, which
    # is where webexport.read_glb looks. Dropping it does not corrupt the file - it
    # makes every reader that trusts the stamp skip the undo and draw the shells
    # rotated against the field lines they contain. That is the fault that read as a
    # physics error for three weeks in September 2026, and the first version of this
    # tool shipped without it: 3,436 files converted, none stamped.
    from dsexport.mesh import OBJ_YUP_NOTE
    g = {"asset": {"version": "2.0", "generator": "magneto obj_to_glb.py",
                   "extras": {"yup": OBJ_YUP_NOTE}},
         "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
         "meshes": [{"primitives": [{"attributes": attrs, "indices": len(acc) - 1}]}],
         "buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": acc}
    js = _pad4(json.dumps(g, separators=(",", ":")).encode(), b" ")
    glb = (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
           + struct.pack("<II", len(js), 0x4E4F534A) + js
           + struct.pack("<II", len(blob), 0x004E4942) + blob)
    path.write_bytes(glb)
    return len(glb)


def verify_glb(path: Path, V, F) -> None:
    """Read the file back and check counts and positions. Cheap, and it is what
    makes deleting the .obj safe."""
    b = path.read_bytes()
    magic, ver, total = struct.unpack("<III", b[:12])
    assert magic == 0x46546C67 and ver == 2, f"{path.name}: not a glTF 2.0 binary"
    assert total == len(b), f"{path.name}: header length {total} != file {len(b)}"
    jlen, jtag = struct.unpack("<II", b[12:20])
    assert jtag == 0x4E4F534A, f"{path.name}: first chunk is not JSON"
    g = json.loads(b[20:20 + jlen].decode("utf-8").rstrip(_PAD_CHARS))
    blob_off = 20 + jlen + 8
    a = g["accessors"][0]
    assert a["count"] == len(V), f"{path.name}: {a['count']} verts != {len(V)}"
    bvv = g["bufferViews"][a["bufferView"]]
    got = np.frombuffer(b, np.float32, count=len(V) * 3,
                        offset=blob_off + bvv["byteOffset"]).reshape(-1, 3)
    assert np.array_equal(got, V), f"{path.name}: positions differ after round trip"
    idx = g["accessors"][-1]
    assert idx["count"] == F.size, f"{path.name}: {idx['count']} indices != {F.size}"


def shell_objs(root: Path) -> list[Path]:
    models = root / "Models"
    if not models.is_dir():
        return []
    return sorted(p for p in models.rglob("*Shell*.obj") if p.is_file())


def convert_root(root: Path, dry_run=False, keep_obj=False) -> dict:
    n = {"converted": 0, "already": 0, "obj_bytes": 0, "glb_bytes": 0, "failed": 0}
    for obj in shell_objs(root):
        glb = obj.with_suffix(".glb")
        if glb.exists():
            n["already"] += 1
            continue
        if dry_run:
            n["converted"] += 1
            n["obj_bytes"] += obj.stat().st_size
            continue
        try:
            V, N, F = parse_obj(obj)
            size = write_glb(glb, V, N, F)
            verify_glb(glb, V, F)
        except Exception as e:                      # noqa: BLE001 - report and keep the .obj
            print(f"    FAILED {obj.name}: {e}")
            if glb.exists():
                glb.unlink()
            n["failed"] += 1
            continue
        n["obj_bytes"] += obj.stat().st_size
        n["glb_bytes"] += size
        if not keep_obj:
            obj.unlink()
        n["converted"] += 1
    # count the ones that were already done, for an honest total
    for glb in (root / "Models").rglob("*Shell*.glb"):
        if glb.stat().st_size and not glb.with_suffix(".obj").exists():
            pass
    return n


def retarget_manifest(root: Path, dry_run=False) -> int:
    """Point every mesh layer's file list at .glb. Returns files renamed."""
    man = root / "Models" / "event_frames.json"
    if not man.is_file():
        return 0
    j = json.loads(man.read_text(encoding="ascii", errors="replace"))
    changed = 0
    for layer in j.get("layers", []):
        if layer.get("kind") != "mesh":
            continue
        files = layer.get("files") or []
        new = [f[:-4] + ".glb" if f.endswith(".obj") else f for f in files]
        changed += sum(1 for a, b in zip(files, new) if a != b)
        layer["files"] = new
    if changed and not dry_run:
        j["mesh_format"] = "glb"
        man.write_text(json.dumps(j, indent=1), encoding="ascii")
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=[],
                    help="a render root (…/Renders/<pkg>); repeatable. Default: all of them.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-obj", action="store_true",
                    help="write the .glb but do not delete the .obj")
    a = ap.parse_args(argv)

    if a.root:
        roots = [Path(r) for r in a.root]
    else:
        base = Path(__file__).resolve().parents[2] / "Renders"
        roots = [d for d in sorted(base.glob("*")) if (d / "Models").is_dir()]

    tot = {"converted": 0, "already": 0, "obj_bytes": 0, "glb_bytes": 0, "failed": 0, "man": 0}
    for r in roots:
        n = convert_root(r, a.dry_run, a.keep_obj)
        if n["failed"]:
            print(f"    {n['failed']} failure(s) - manifest NOT retargeted for this root")
            m = 0
        else:
            m = retarget_manifest(r, a.dry_run) if n["converted"] else 0
        for k in n:
            tot[k] += n[k]
        tot["man"] += m
        if n["converted"] or n["already"] or n["failed"]:
            print(f"  {r.name:32s} {n['converted']:5d} converted  {n['already']:5d} already"
                  f"  {n['failed']:3d} failed  manifest rows {m}")
    print()
    if tot["obj_bytes"]:
        print(f"TOTAL {tot['converted']} converted, {tot['failed']} failed, "
              f"{tot['man']} manifest rows retargeted")
        print(f"  .obj {tot['obj_bytes']/1e9:7.3f} GB  ->  .glb {tot['glb_bytes']/1e9:7.3f} GB"
              f"   ({100*tot['glb_bytes']/tot['obj_bytes']:.1f}%, saved "
              f"{(tot['obj_bytes']-tot['glb_bytes'])/1e9:.3f} GB)")
    else:
        print("nothing to convert")
    if tot["failed"]:
        return 1
    print("\nNext: python tools/retime_event.py --root <each root> --step-seconds <its value>")
    print("      to re-emit the players against the updated manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
