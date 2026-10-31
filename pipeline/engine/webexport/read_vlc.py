"""Read a Digistar .vlc/.vla frame into numpy arrays.

THE ONE THING TO GET RIGHT: a PARAMETRIC .vlc carries TWO poses interleaved.

    DOTS   D V D V D V ...      D = previous epoch, V = this epoch
    LINES  P V (L V)* P V ...   P opens a polyline, L continues it; both carry the
                                PREVIOUS epoch's pose, V carries this one.

So the current frame is the V rows ALONE. Reading every row mixes two epochs and, for
lines, straddles polyline boundaries - it produces plausible but wrong output, which is
exactly what happened on 2026-09-16 before this was pinned down.

BUT THE LAST FRAME OF EVERY SERIES IS DIFFERENT. It has no next epoch to morph toward, so
the writer emits it `NON_PARAMETRIC` with a SINGLE pose carried on the D (dots) or P/L
(lines) rows and NO V rows at all. A reader that takes V rows unconditionally gets an empty
final frame - which is how this was found, as an IndexError on keyframe 49 of 49. So both
streams are collected and the V stream is used only when it is actually there.

The P/L sequence is still needed: it is where polyline membership lives. The i-th V row
belongs to the polyline opened by the most recent P row.

Row grammar (dsexport/vla.py::_row8) - 8 whitespace-separated tokens:

    <cmd> x y z opacity r g b

Coordinates are LIGHT YEARS (`set coordsys RIGHT` means light years to Digistar).
Opacity and colour are 0..1.
"""
from __future__ import annotations

import pathlib

import numpy as np

RE_LY = 6.73436e-10          # magneto/geo.py - light years per Earth radius


class VlcFrame:
    """One parsed .vlc frame, in EARTH RADII.

    pos      (N, 3) float32   current-epoch vertex positions
    opacity  (N,)   float32   0..1, already carrying the crossfade mask - a field line
                              whose identity broke sits at 0 on both poses
    color    (N, 3) float32   0..1 RGB
    starts   (M,)   int32     index into pos of each polyline's first vertex (LINES only;
                              empty for DOTS)
    """

    __slots__ = ("pos", "opacity", "color", "starts", "kind", "parametric", "comments")

    def __init__(self, pos, opacity, color, starts, kind, parametric, comments):
        self.pos = pos
        self.opacity = opacity
        self.color = color
        self.starts = starts
        self.kind = kind
        self.parametric = parametric
        self.comments = comments

    @property
    def n(self) -> int:
        return int(self.pos.shape[0])

    def line_lengths(self) -> np.ndarray:
        """Vertices per polyline. Constant by contract (64 for field lines) but measured."""
        if self.starts.size == 0:
            return np.empty(0, dtype=np.int32)
        ends = np.append(self.starts[1:], self.n)
        return (ends - self.starts).astype(np.int32)


def read_vlc(path, to_earth_radii: bool = True) -> VlcFrame:
    path = pathlib.Path(path)
    kind = "DOTS"
    parametric = False
    comments: list[str] = []

    # two parallel streams: [0] is the D/P/L pose, [1] is the V pose
    pos: list[list] = [[], []]
    opa: list[list] = [[], []]
    col: list[list] = [[], []]
    starts: list[list] = [[], []]
    pending = False

    with path.open("r", encoding="ascii", errors="replace") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            if raw.startswith("set "):
                t = raw.split()
                if len(t) >= 3 and t[1] == "filecontent":
                    kind = t[2]
                elif len(t) >= 3 and t[1] == "parametric":
                    parametric = t[2] == "PARAMETRIC"
                elif len(t) >= 3 and t[1] == "comment":
                    comments.append(" ".join(t[2:]))
                continue
            cmd = raw[0]
            if cmd not in ("P", "L", "D", "V"):
                continue
            t = raw.split()
            if len(t) < 8:
                continue
            k = 1 if cmd == "V" else 0
            if cmd == "P":
                starts[0].append(len(pos[0]))
                pending = True
            elif cmd == "V" and pending:
                starts[1].append(len(pos[1]))
                pending = False
            pos[k].append((float(t[1]), float(t[2]), float(t[3])))
            opa[k].append(float(t[4]))
            col[k].append((float(t[5]), float(t[6]), float(t[7])))

    k = 1 if pos[1] else 0          # V stream when present, else the single NON_PARAMETRIC pose
    pos, opa, col, starts = pos[k], opa[k], col[k], starts[k]
    p = np.asarray(pos, dtype=np.float64)
    if to_earth_radii and p.size:
        # Light years are ~1e-10 and break every web renderer's near/far plane, raycasting
        # and bounding-sphere maths. Earth radii keep the numbers in a sane range and make
        # float32 comfortable. RE_LY is the ONLY conversion constant in this project.
        p = p / RE_LY
    return VlcFrame(
        pos=p.astype(np.float32).reshape(-1, 3),
        opacity=np.asarray(opa, dtype=np.float32),
        color=np.asarray(col, dtype=np.float32).reshape(-1, 3),
        starts=np.asarray(starts, dtype=np.int32),
        kind=kind,
        parametric=parametric,
        comments=comments,
    )
