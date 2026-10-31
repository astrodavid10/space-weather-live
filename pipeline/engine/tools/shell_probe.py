r"""shell_probe.py - dome-side evidence for "are the shells drawn where the field lines are?"

The file-level checks (tools\check_alignment.py, the yup stamp, the holder setup) all pass, so
the remaining questions can only be answered on the dome, by drawing the SAME numbers two ways:

  1. POSITION: write a shipped shell's own vertices as two .vla wireframes.
       verbatim.vla  - the file coordinates exactly as the .glb stores them
       undone.vla    - the file coordinates with the Y-up pre-compensation undone,
                       (x, y, z) -> (x, -z, y): the pose we MEAN, and where a Y-up importer
                       draws the .glb. Digistar takes .vla literally.
     Load the .glb and both wireframes under one holder. The surface must coincide with
     undone.vla (BLUE). If it sits on verbatim.vla (RED) instead, Digistar is NOT rotating
     this .glb and dsexport.mesh.OBJ_YUP_PRECOMPENSATE has to become format-aware.
     The matching FieldLines_NNNN.vlc rides along so the "inside the shell" question is
     answered in the same frame.

  2. NORMALS: the shell's whole look (effectMode angleBasedIntensity, abiMode fadeCenter) is
     computed from the NORMAL accessor. glTF keeps it separate from POSITION, so a loader can
     rotate one and not the other. Two probe .glb files of one sphere: n_match.glb (normals
     consistent with the positions) and n_rot.glb (normals rotated 90 deg about X). Loaded in
     turn on the same object from the same eye: identical captures mean Digistar ignores or
     recomputes NORMAL; different captures mean the shipped normals decide the look and must
     be checked against an .obj twin.

Usage (from Engine, with the venv python):
    python tools\shell_probe.py --glb ..\Renders\storm_2024-05-10_gannon\Models\gannon_MagnetopauseShell_0150\gannon_MagnetopauseShell_0150.glb ^
        --vlc ..\Renders\storm_2024-05-10_gannon\Models\gannon_FieldLines_0150\gannon_FieldLines_0150.vlc ^
        --out ..\Renders\_clips\shellprobe

Then over the bridge:  script play <out>\shellprobe.ds      (scene, stays up)
                       script play <out>\normprobe.ds        (two captures, 3 s apart)
                       script play <out>\shellprobe-off.ds   (cleanup)
Rulers are LINES with `useWidth on` - a DOTS probe in the wrong class cooks to nothing.
Coordinates go out through fmt_coord_sig; the default formatter writes `P 0 0 0` for
light-year values and the probe draws nothing (2026-09-17 lesson).
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np

ENGINE = Path(__file__).resolve().parents[1]
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from dsexport import mesh as mesh_mod                       # noqa: E402
from dsexport.vla import fmt_coord_sig, write_lines_vla     # noqa: E402
from magneto import paths                                    # noqa: E402

RE_LY = 6.73436e-10


def read_glb_arrays(path: Path):
    """POSITION (float32 xyz), NORMAL (or None), triangle indices, and asset.extras."""
    raw = path.read_bytes()
    magic, _ver, _len = struct.unpack_from("<4sII", raw, 0)
    if magic != b"glTF":
        raise ValueError(f"{path} is not a binary glTF")
    off = 12
    js_len, js_type = struct.unpack_from("<II", raw, off)
    off += 8
    g = json.loads(raw[off:off + js_len])
    off += js_len
    bin_len, bin_type = struct.unpack_from("<II", raw, off)
    off += 8
    blob = raw[off:off + bin_len]

    def accessor(idx):
        a = g["accessors"][idx]
        bv = g["bufferViews"][a["bufferView"]]
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        comp = {5126: np.float32, 5123: np.uint16, 5125: np.uint32}[a["componentType"]]
        n = {"VEC3": 3, "SCALAR": 1}[a["type"]]
        arr = np.frombuffer(blob, dtype=comp, count=a["count"] * n, offset=start)
        return arr.reshape(a["count"], n) if n > 1 else arr

    prim = g["meshes"][0]["primitives"][0]
    pos = accessor(prim["attributes"]["POSITION"]).astype(float)
    nrm = accessor(prim["attributes"]["NORMAL"]).astype(float) if "NORMAL" in prim["attributes"] else None
    idx = accessor(prim["indices"]).astype(np.int64).reshape(-1, 3)
    return pos, nrm, idx, g.get("asset", {}).get("extras", {})


def undo_yup(v: np.ndarray) -> np.ndarray:
    """The importer's rotation: file (x, y, z) is drawn at (x, -z, y)."""
    return np.stack([v[..., 0], -v[..., 2], v[..., 1]], axis=-1)


def edges_of(tris: np.ndarray) -> np.ndarray:
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e = np.sort(e, axis=1)
    return np.unique(e, axis=0)


def write_wireframe(path: Path, verts_ly: np.ndarray, edges: np.ndarray, note: str) -> int:
    polys = [[tuple(verts_ly[a]), tuple(verts_ly[b])] for a, b in edges]
    return write_lines_vla(path, polys, intensity="FULL", coord_fmt=fmt_coord_sig,
                           comments=[note, "shell_probe.py wireframe - LINES, useWidth on"])


def sphere_glb(path: Path, radius_ly: float, rotate_normals: bool, n_lat=36, n_lon=72) -> None:
    """One sphere as binary glTF, normals either radial or rotated 90 deg about X."""
    lat = np.linspace(-np.pi / 2, np.pi / 2, n_lat)
    lon = np.linspace(0, 2 * np.pi, n_lon, endpoint=False)
    L, M = np.meshgrid(lat, lon, indexing="ij")
    V = np.stack([np.cos(L) * np.cos(M), np.cos(L) * np.sin(M), np.sin(L)], axis=-1).reshape(-1, 3)
    N = V.copy()
    if rotate_normals:
        # 90 deg about X: (x, y, z) -> (x, -z, y)
        N = np.stack([N[:, 0], -N[:, 2], N[:, 1]], axis=-1)
    tris = []
    for i in range(n_lat - 1):
        for j in range(n_lon):
            a, b = i * n_lon + j, i * n_lon + (j + 1) % n_lon
            c, d = a + n_lon, b + n_lon
            tris += [(a, b, c), (b, d, c)]
    Vf = np.ascontiguousarray(V * radius_ly, dtype=np.float32)
    Nf = np.ascontiguousarray(N, dtype=np.float32)
    I = np.ascontiguousarray(np.asarray(tris), dtype=np.uint16)
    bv, bn, bi = Vf.tobytes(), Nf.tobytes(), mesh_mod._pad4(I.tobytes(), mesh_mod.GLB_BIN_PAD)
    blob = mesh_mod._pad4(bv + bn + bi, mesh_mod.GLB_BIN_PAD)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(bv), "target": 34962},
             {"buffer": 0, "byteOffset": len(bv), "byteLength": len(bn), "target": 34962},
             {"buffer": 0, "byteOffset": len(bv) + len(bn), "byteLength": len(bi), "target": 34963}]
    acc = [{"bufferView": 0, "componentType": 5126, "count": int(len(Vf)), "type": "VEC3",
            "min": Vf.min(0).tolist(), "max": Vf.max(0).tolist()},
           {"bufferView": 1, "componentType": 5126, "count": int(len(Nf)), "type": "VEC3"},
           {"bufferView": 2, "componentType": 5123, "count": int(I.size), "type": "SCALAR"}]
    g = {"asset": {"version": "2.0", "generator": "tools/shell_probe.py",
                   "extras": {"probe": "normals rotated 90 deg about X" if rotate_normals else "normals radial (match)"}},
         "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2}]}],
         "buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": acc}
    js = mesh_mod._pad4(json.dumps(g, separators=(",", ":")).encode("utf-8"), mesh_mod.GLB_JSON_PAD)
    glb = (struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
           + struct.pack("<II", len(js), 0x4E4F534A) + js
           + struct.pack("<II", len(blob), 0x004E4942) + blob)
    path.write_bytes(glb)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--glb", required=True, help="a shipped MagnetopauseShell/BowShockShell .glb")
    ap.add_argument("--vlc", default=None, help="the matching FieldLines_NNNN.vlc to overlay")
    ap.add_argument("--out", required=True, help="output folder (under the package, so $Content tokens resolve)")
    ap.add_argument("--sphere-re", type=float, default=30.0, help="normal-probe sphere radius, R_E")
    args = ap.parse_args(argv)

    glb, out = Path(args.glb).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    pos, nrm, tris, extras = read_glb_arrays(glb)
    stamped = "yup-precompensated" in str(extras.get("yup", ""))
    edges = edges_of(tris)
    n_v = write_wireframe(out / "verbatim.vla", pos, edges,
                          f"{glb.name}: file coordinates VERBATIM (RED)")
    n_u = write_wireframe(out / "undone.vla", undo_yup(pos), edges,
                          f"{glb.name}: Y-up pre-compensation UNDONE (x,-z,y) = intended pose (BLUE)")
    sphere_glb(out / "n_match.glb", args.sphere_re * RE_LY, rotate_normals=False)
    sphere_glb(out / "n_rot.glb", args.sphere_re * RE_LY, rotate_normals=True)

    tok = paths.ds_token
    look = mesh_mod.__dict__  # noqa: F841  (P2 colours are below; kept literal for a probe)
    lines = [
        "# shellprobe.ds  -  GENERATED by Engine\\tools\\shell_probe.py",
        f"# {glb.name}: yup stamp {'PRESENT' if stamped else 'ABSENT'}; {len(pos)} vertices, {len(edges)} edges",
        "# The glb surface must coincide with the BLUE wireframe (undone.vla). RED = literal import.",
        "# Field lines in white. Everything under one holder, standardSpace on, like the show.",
        "", "0:00",
        "\tspHolder is emptyClass", "\tEarth add spHolder", "\tspHolder standardSpace on",
        "\tspHolder intensity 100", "",
        "+0.5",
        "\tspShell is effectModelClass", '\tspShell effectMode "angleBasedIntensity"',
        '\tspShell abiMode "fadeCenter"', "\tspShell abiThickness 96", '\tspShell blendMode "add"',
        '\tspShell cullMode "none"', "\tspShell tessellation 0", "\tspShell scale 1 ly",
        f"\tspShell model {tok(glb)}", "\tspShell color 14 34 40", "\tspShell intensity 100",
        "\tspHolder add spShell", "",
        "+0.5",
        "\tspVerbatim is lineModelClass", "\tspVerbatim useWidth on", "\tspVerbatim width 2",
        "\tspVerbatim depthCue off", f"\tspVerbatim model {tok(out / 'verbatim.vla')}",
        "\tspVerbatim color 100 20 20", "\tspVerbatim intensity 60", "\tspHolder add spVerbatim", "",
        "+0.5",
        "\tspUndone is lineModelClass", "\tspUndone useWidth on", "\tspUndone width 2",
        "\tspUndone depthCue off", f"\tspUndone model {tok(out / 'undone.vla')}",
        "\tspUndone color 25 55 100", "\tspUndone intensity 60", "\tspHolder add spUndone", "",
    ]
    if args.vlc:
        vlc = Path(args.vlc).resolve()
        lines += ["+0.5",
                  "\tspLines is lineModelClass", "\tspLines useWidth on", "\tspLines width 1.5",
                  "\tspLines depthCue off", "\tspLines DIIMorph off", "\tspLines morph 100",
                  f"\tspLines model {tok(vlc)}", "\tspLines color 90 90 90", "\tspLines intensity 90",
                  "\tspHolder add spLines", ""]
    lines += ["+1",
              '\tsystem message "shell probe: surface on BLUE = Y-up import confirmed, on RED = literal import"', ""]
    (out / "shellprobe.ds").write_text("\r\n".join(lines) + "\r\n", encoding="ascii")

    norm = [
        "# normprobe.ds  -  GENERATED by Engine\\tools\\shell_probe.py",
        "# One sphere, two glb files: radial normals, then normals rotated 90 deg about X.",
        "# Capture each (surface0 fisheye). Identical = Digistar ignores NORMAL. Different = it decides the look.",
        "", "0:00",
        "\tnpHolder is emptyClass", "\tEarth add npHolder", "\tnpHolder standardSpace on", "\tnpHolder intensity 100",
        "\tnpSphere is effectModelClass", '\tnpSphere effectMode "angleBasedIntensity"', '\tnpSphere abiMode "fadeCenter"',
        "\tnpSphere abiThickness 96", '\tnpSphere blendMode "add"', '\tnpSphere cullMode "none"', "\tnpSphere tessellation 0",
        "\tnpSphere scale 1 ly", f"\tnpSphere model {tok(out / 'n_match.glb')}", "\tnpSphere color 14 34 40",
        "\tnpSphere intensity 100", "\tnpHolder add npSphere", "",
        "\tcapture path C:/CXContent/User/Capture/Magneto/shellprobe", '\tcapture fileType "png"',
        "\tcapture surface surface0", "\tcapture size 1600 1600", "\tcapture pixelUnitScale 1",
        "\tcapture logosAndTimecode off", "\tcapture overwrite on", "",
        "+3", '\tcapture filename "n_match"', "+3.5", "\tcapture frame", "",
        "+1", f"\tnpSphere model {tok(out / 'n_rot.glb')}", "",
        "+3", '\tcapture filename "n_rot"', "+3.5", "\tcapture frame", "",
        "+1", '\tsystem message "normal probe: two frames in Capture/Magneto/shellprobe"', "",
    ]
    (out / "normprobe.ds").write_text("\r\n".join(norm) + "\r\n", encoding="ascii")
    off = ["# shellprobe-off.ds  -  remove every probe object", "", "0:00",
           "\tspLines delete", "\tspUndone delete", "\tspVerbatim delete", "\tspShell delete", "\tspHolder delete",
           "\tnpSphere delete", "\tnpHolder delete", ""]
    (out / "shellprobe-off.ds").write_text("\r\n".join(off) + "\r\n", encoding="ascii")

    print(f"{glb.name}: {len(pos)} vertices, {len(edges)} edges, yup stamp {'present' if stamped else 'ABSENT'}")
    print(f"verbatim.vla {n_v} rows, undone.vla {n_u} rows -> {out}")
    print(f"n_match.glb / n_rot.glb: {args.sphere_re:g} R_E sphere -> {out}")
    print(f"play: script play {tok(out / 'shellprobe.ds')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
