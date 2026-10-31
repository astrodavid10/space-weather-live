"""Read a boundary-shell .obj and UNDO Digistar's Y-up precompensation.

READ THIS BEFORE USING THE SHELLS ANYWHERE OUTSIDE DIGISTAR.

Digistar imports .obj through a Y-up pipeline: a file vertex (x, y, z) is DRAWN at
(x, -z, y). `.vla`/`.vlc` are taken literally. So dsexport/mesh.py PRE-COMPENSATES - it
writes each export-frame vertex (x, y, z) to the file as (x, z, -y), which makes
Digistar's import a no-op and puts the shell back on the same frame as every other layer.

Three.js, glTF, WWT and everything else do NOT apply Digistar's import transform. A web
loader reading the shipped file therefore gets a shell rotated against the field lines it
is supposed to contain - up to 66 degrees, depending on the season. That fault read as a
physics error for three weeks (progress log 2026-09-17) and every file-level check passed
while it was happening, because each file was correct on its own terms.

The inverse is: given file vertex f, the export-frame vertex is (f.x, -f.z, f.y).

Files carrying the header line `# yup-precompensated:` are compensated. Files without it
are raw export-frame and must NOT be transformed - so this reader keys off the stamp
rather than assuming, and says which branch it took.
"""
from __future__ import annotations

import pathlib

import numpy as np

RE_LY = 6.73436e-10
YUP_STAMP = "yup-precompensated"


class ObjMesh:
    """faces are 0-based int32 (V, 3); verts are EARTH RADII in the export frame."""

    __slots__ = ("verts", "faces", "header", "was_precompensated", "lattice", "wrap")

    def __init__(self, verts, faces, header, was_precompensated, lattice=None, wrap=None):
        self.verts = verts
        self.faces = faces
        self.header = header
        self.was_precompensated = was_precompensated
        # (n_u, n_v) when the writer stamped the file as a structured lattice (the ring
        # current and the plasmasphere, dsexport/mesh.py::write_lattice_surface; vertex
        # index = u * n_v + v), with (wrap_u, wrap_v) saying which directions close. None
        # for the two boundary shells, whose apex-and-rings grid has no such stamp and is
        # recognised by its vertex count instead (decimate.shell_grid).
        self.lattice = lattice
        self.wrap = wrap


def parse_lattice(extras: dict):
    """(lattice, wrap) from a writer's extras dict, or (None, None) when unstamped."""
    lat = extras.get("lattice")
    if not lat:
        return None, None
    parts = [t.strip() for t in str(lat).lower().split("x")]
    if len(parts) != 2 or not all(t.isdigit() for t in parts):
        raise ValueError(f"unreadable lattice stamp {lat!r}")
    n_u, n_v = int(parts[0]), int(parts[1])

    def flag(v, default):
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "on")
        return default if v is None else bool(v)

    return (n_u, n_v), (flag(extras.get("wrap_u"), True), flag(extras.get("wrap_v"), False))


def undo_yup(v: np.ndarray) -> np.ndarray:
    """(fx, fy, fz) -> (fx, -fz, fy). Inverse of mesh.obj_precompensate's (x, z, -y)."""
    v = np.asarray(v, dtype=np.float64)
    return np.stack([v[..., 0], -v[..., 2], v[..., 1]], axis=-1)


def read_obj(path, to_earth_radii: bool = True) -> ObjMesh:
    path = pathlib.Path(path)
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    header: list[str] = []
    stamped = False
    extras: dict = {}

    with path.open("r", encoding="ascii", errors="replace") as fh:
        for raw in fh:
            if raw.startswith("#"):
                line = raw[1:].strip()
                header.append(line)
                if YUP_STAMP in raw:
                    stamped = True
                # "# lattice: 120 x 50" / "# wrap_u: True" - the .obj twin of the glb extras
                k, sep, v = line.partition(":")
                if sep and k.strip() in ("lattice", "wrap_u", "wrap_v"):
                    extras[k.strip()] = v.strip()
                continue
            if raw.startswith("v "):
                t = raw.split()
                verts.append((float(t[1]), float(t[2]), float(t[3])))
            elif raw.startswith("f "):
                t = raw.split()
                # "f a b c" or "f a/vt/vn ..." - only the vertex index is wanted, 1-based
                idx = [int(p.split("/")[0]) - 1 for p in t[1:4]]
                faces.append((idx[0], idx[1], idx[2]))

    v = np.asarray(verts, dtype=np.float64)
    if stamped:
        v = undo_yup(v)
    if to_earth_radii and v.size:
        v = v / RE_LY
    lattice, wrap = parse_lattice(extras)
    return ObjMesh(
        verts=v.astype(np.float32).reshape(-1, 3),
        faces=np.asarray(faces, dtype=np.int32).reshape(-1, 3),
        header=header,
        was_precompensated=stamped,
        lattice=lattice, wrap=wrap,
    )


def read_glb(path, to_earth_radii: bool = True) -> ObjMesh:
    """Binary glTF, returned as the same ObjMesh the rest of the exporter expects.

    The mesh layers moved from .obj to .glb on 2026-09-20 (a quarter the bytes for a
    render Digistar cannot tell apart). The web exporter has to read what the render
    actually writes, so this is the counterpart of read_obj - same frame handling,
    same units, same y-up undo, driven by the same stamp.

    That stamp now lives in asset.extras.yup rather than a "#" header comment, because
    a binary container has nowhere to put a comment. Everything else is identical.
    """
    import json
    import struct

    path = pathlib.Path(path)
    b = path.read_bytes()
    magic, ver, total = struct.unpack("<III", b[:12])
    if magic != 0x46546C67 or ver != 2:
        raise ValueError(f"{path.name}: not a glTF 2.0 binary")
    if total != len(b):
        raise ValueError(f"{path.name}: header says {total} bytes, file is {len(b)}")
    jlen, jtag = struct.unpack("<II", b[12:20])
    if jtag != 0x4E4F534A:
        raise ValueError(f"{path.name}: first chunk is not JSON")
    g = json.loads(b[20:20 + jlen].decode("utf-8"))
    blob = 20 + jlen + 8

    extras = (g.get("asset") or {}).get("extras") or {}
    stamped = YUP_STAMP in str(extras.get("yup", ""))
    header = [f"{k}: {extras[k]}" for k in sorted(extras) if k != "notes"]
    header += [str(x) for x in (extras.get("notes") or [])]

    accs, views = g["accessors"], g["bufferViews"]
    pos, idx = accs[0], accs[-1]
    pv = views[pos["bufferView"]]
    v = np.frombuffer(b, np.float32, count=int(pos["count"]) * 3,
                      offset=blob + int(pv["byteOffset"])).reshape(-1, 3).astype(np.float64)
    iv = views[idx["bufferView"]]
    dt = np.uint16 if idx["componentType"] == 5123 else np.uint32
    f = np.frombuffer(b, dt, count=int(idx["count"]),
                      offset=blob + int(iv["byteOffset"])).reshape(-1, 3)

    if stamped:
        v = undo_yup(v)
    if to_earth_radii and v.size:
        v = v / RE_LY
    lattice, wrap = parse_lattice(extras)
    if lattice is not None and lattice[0] * lattice[1] != v.shape[0]:
        raise ValueError(f"{path.name}: lattice stamp {lattice} does not match "
                         f"{v.shape[0]} vertices")
    return ObjMesh(
        verts=v.astype(np.float32).reshape(-1, 3),
        faces=f.astype(np.int32).reshape(-1, 3),
        header=header,
        was_precompensated=stamped,
        lattice=lattice, wrap=wrap,
    )


def read_mesh(path, to_earth_radii: bool = True) -> ObjMesh:
    """Read a surface in whichever format it is on disk."""
    path = pathlib.Path(path)
    if path.suffix.lower() == ".glb":
        return read_glb(path, to_earth_radii)
    return read_obj(path, to_earth_radii)
