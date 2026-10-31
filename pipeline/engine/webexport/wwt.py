"""CSV emitters for WorldWide Telescope spreadsheet layers.

WHAT I AM CONFIDENT ABOUT, and what you should verify on your own machine:

CONFIDENT. WWT's data-layer primitive is the spreadsheet layer: a table plus a mapping from
columns to geometry. It can take CARTESIAN coordinates (rather than RA/Dec) and it can animate
a table as a time series from a date column. pywwt exposes this as
`wwt.layers.add_table_layer(...)`, and the web engine as `LayerManager` / `SpreadSheetLayer`.
So a per-vertex, per-keyframe table with x/y/z/time columns is the natural WWT-native form for
every DOT layer here - wind parcels, ring current, plasmasphere, aurora oval, field-line beads.

NOT CONFIDENT, VERIFY BEFORE BUILDING ON IT. The exact property names and units for the
rectangular coordinate mode, which reference frame to attach an Earth-centred layer to, the
altitude unit, and whether the build you are using renders POLYLINES from a spreadsheet layer
at all. Field lines are 72 polylines of 32 vertices; if your build cannot draw connected lines
from a table, the options are (a) draw them as dense points, which reads acceptably at phone
size, or (b) draw them in a Three.js overlay canvas over the WWT view and share the camera.
I would not design around (b) until (a) is proven inadequate.

FRAME. Everything is Earth-centred and in EARTH RADII, axes J2000 geocentric equatorial:
+Z north celestial pole, +X vernal equinox - the same axes as WWT's own sky frame, which is
why this was the render frame in the first place. Multiply by 6371 for km if a layer wants
metric altitude units.

THE TABLES ARE BULKY as text (a field-line table is ~8 MB of CSV for 60 keyframes). Gzip them,
or use the binary bundle for the big layers and CSV only for the ones you want WWT to own.
"""
from __future__ import annotations

import csv
import pathlib

import numpy as np

RE_KM = 6371.0


def write_layer_csv(path, layer: dict, epochs: list[str], km: bool = False) -> int:
    """One row per vertex per keyframe.

    Columns: time, x, y, z, alpha, r, g, b, vid[, line]
    `time` is the ISO epoch of that keyframe, which is what a WWT time-series layer keys on.
    """
    path = pathlib.Path(path)
    pos = np.asarray(layer["pos"], dtype=np.float64)          # (K, N, 3) Earth radii
    alpha = np.asarray(layer["alpha"], dtype=np.float64)      # (K, N)
    col = np.asarray(layer["color0"], dtype=np.float64)       # (N, 3)
    kmap = list(layer["keyframe_map"])
    scale = RE_KM if km else 1.0

    line_of = None
    if layer.get("starts") is not None:
        line_of = np.zeros(pos.shape[1], dtype=np.int32)
        for li, (s, ln) in enumerate(zip(layer["starts"], layer["lengths"])):
            line_of[int(s):int(s) + int(ln)] = li

    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        head = ["time", "x", "y", "z", "alpha", "r", "g", "b", "vid"]
        if line_of is not None:
            head.append("line")
        w.writerow(head)
        for k, gk in enumerate(kmap):
            t = epochs[gk]
            for i in range(pos.shape[1]):
                row = [t,
                       f"{pos[k, i, 0] * scale:.6g}",
                       f"{pos[k, i, 1] * scale:.6g}",
                       f"{pos[k, i, 2] * scale:.6g}",
                       f"{alpha[k, i]:.3f}",
                       f"{col[i, 0]:.3f}", f"{col[i, 1]:.3f}", f"{col[i, 2]:.3f}",
                       i]
                if line_of is not None:
                    row.append(int(line_of[i]))
                w.writerow(row)
                n += 1
    return n


def write_shell_obj(path, verts: np.ndarray, faces: np.ndarray, note: str = "") -> None:
    """A decimated shell as a plain .obj in the EXPORT frame - the Y-up precompensation has
    already been undone by read_obj, so this file is what every non-Digistar tool expects.
    Do NOT feed this back to Digistar without re-compensating."""
    path = pathlib.Path(path)
    with path.open("w", encoding="ascii") as fh:
        fh.write("# Live Magnetosphere boundary shell, decimated for the web\n")
        fh.write("# frame J2000 geocentric equatorial, units EARTH RADII (1 R_E = 6371 km)\n")
        fh.write("# NOT y-up precompensated - this is the export frame, for Three.js/WWT/glTF.\n")
        if note:
            fh.write("# " + note.strip().replace("\n", "\n# ") + "\n")
        for v in verts:
            fh.write("v %.6g %.6g %.6g\n" % (v[0], v[1], v[2]))
        for f in faces:
            fh.write("f %d %d %d\n" % (f[0] + 1, f[1] + 1, f[2] + 1))
