"""Field-line PULSES - beads gliding along the traced field lines in the direction of B.

Phase 5 of the competition upgrade (2026-09-05). The field lines themselves morph
between epochs, which shows the magnetosphere BREATHING but not that it is a
magnetic field: nothing says which way B points or that the lines are one
connected system. This layer puts N_BEADS beads on every traced line and
advances them along +B by SEGMENTS_PER_STEP line segments per field step, so a
full traverse of a 63-segment line takes ~42 steps (~34 s of dome time at
0.8 s/step). Earth's field emerges from the southern hemisphere and enters in
the north, so on a closed line the beads run south foot -> north foot.

Geometry: the beads are placed on the SAME trace_all() lines FieldLines.vlc is
written from (the arc-length resampled `export` array, light years in the
export frame), so they sit exactly on the drawn lines and, written as a
parametric DOTS file with D rows = previous epoch / V rows = current epoch like
the lines, they morph in sync with them.

Direction convention: trace_all() stitches back[::-1] + fwd, where geopack's
trace direction +1 follows -B (antiparallel) and -1 follows +B - so the vertex
index runs ALONG -B: index 0 is the +B end (the northern footpoint of a closed
line), index N_VERTS-1 the -B end. A bead moving along +B moves toward index 0;
PULSE_INDEX_DIRECTION records that (verified by test on a nominal trace).

Anti-streak rule (same as the wind parcels): a bead that wraps from the end of
its line back to the start between two epochs, a bead on a line that failed to
trace in either epoch, and a bead on a line whose topology broke between the
epochs (fieldlines.break_mask - reconnection, the lines crossfade there) get
opacity 0 on BOTH rows, so nothing is drawn shooting across the magnetosphere.

The layer is a plain DOTS .vlc: dotModelClass on the dome, one holder
(MagnetoEventFieldPulsesHolder), toggled by the panel's Field Pulses buttons.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from dsexport.vla import coord_formatter, val_formatter, write_dots_vlc

from .. import geo
from . import fieldlines as fl

log = logging.getLogger(__name__)

N_BEADS = 6                       # beads per line, equally spaced in line fraction
SEGMENTS_PER_STEP = 1.5           # bead advance per field step, in line segments
PHASE_STEP = SEGMENTS_PER_STEP / (fl.N_VERTS - 1)   # ... as a line fraction
PULSE_INDEX_DIRECTION = -1        # +B runs toward DEcreasing vertex index (see docstring)
BEAD_OPACITY = 0.95
BRIGHTEN = 1.3                    # family colour x this, clamped - beads outshine their line
STEPS_PER_TRAVERSE = 1.0 / PHASE_STEP


def bead_phases(step_index: int) -> np.ndarray:
    """UNWRAPPED phase of the N_BEADS beads at state index k (line fractions along +B).

    Bead b starts at b/N_BEADS and advances PHASE_STEP per state. Unwrapped so
    two consecutive states can be compared for a wrap (integer crossing).
    """
    return np.arange(N_BEADS) / N_BEADS + int(step_index) * PHASE_STEP


def positions_along(lines: np.ndarray, frac) -> np.ndarray:
    """(N_LINES, len(frac), 3) points at line fractions `frac` measured along +B.

    `lines` is (N_LINES, N_VERTS, 3), arc-length resampled (uniform index ==
    uniform arc length, which trace_all guarantees). frac is wrapped into [0, 1).
    """
    lines = np.asarray(lines, dtype=float)
    n = lines.shape[1]
    f = np.mod(np.asarray(frac, dtype=float), 1.0)
    u = (n - 1) * (1.0 - f) if PULSE_INDEX_DIRECTION < 0 else (n - 1) * f
    i0 = np.clip(np.floor(u).astype(int), 0, n - 2)
    t = (u - i0)[None, :, None]
    return lines[:, i0, :] * (1.0 - t) + lines[:, i0 + 1, :] * t


def bead_rows(trace: dict, step_index: int, hide=None) -> np.ndarray:
    """(N_SEEDS * N_BEADS, 7) rows (x y z opacity r g b), export-frame light years.

    hide: optional (N_SEEDS, N_BEADS) bool - those beads get opacity 0. Beads on
    lines that failed to trace are always hidden.
    """
    pts = positions_along(trace["export"], bead_phases(step_index))
    n_lines = pts.shape[0]
    rows = np.zeros((n_lines, N_BEADS, 7))
    rows[:, :, :3] = pts
    rows[:, :, 3] = BEAD_OPACITY
    for i, kind in enumerate(trace["kind"]):
        rows[i, :, 4:7] = np.clip(np.asarray(fl.COLOURS[kind], dtype=float) * BRIGHTEN, 0.0, 1.0)
        if kind == fl.KIND_FAILED:
            rows[i, :, 3] = 0.0
    if hide is not None:
        rows[np.asarray(hide, dtype=bool), 3] = 0.0
    return rows.reshape(-1, 7)


def wrapped_between(k_prev: int, k_cur: int) -> np.ndarray:
    """(N_BEADS,) True where a bead's unwrapped phase crosses an integer prev -> cur."""
    return np.floor(bead_phases(k_prev)) != np.floor(bead_phases(k_cur))


def export_fieldpulses(cur: dict, prev, k_cur: int, k_prev, out_path,
                       shape_break_re: float = fl.SHAPE_BREAK_RE,
                       coord_digits: int = geo.DEFAULT_COORD_DIGITS) -> dict:
    """Write FieldPulses.vlc (VLC DOTS, EXPLICIT), morphing prev -> cur like the lines.

    cur / prev are trace_all() results (prev None -> non-parametric, the first
    frame of a series); k_cur / k_prev their STATE indices, which set the bead
    phases - so beads advance PHASE_STEP per state whatever stride the field
    lines run on. D rows = beads on prev at phase(k_prev), V rows = beads on
    cur at phase(k_cur).
    """
    out_path = Path(out_path)
    cfmt, vfmt = coord_formatter(coord_digits), val_formatter(coord_digits)
    if cur["export"].shape != (fl.N_SEEDS, fl.N_VERTS, 3):
        raise ValueError(f"current trace has shape {cur['export'].shape}")
    dead = np.array([k == fl.KIND_FAILED for k in cur["kind"]])
    comments = [
        f"field-line pulses: {N_BEADS} beads per line advancing along +B by "
        f"{SEGMENTS_PER_STEP:g} of {fl.N_VERTS - 1} segments per field step "
        f"(full traverse {STEPS_PER_TRAVERSE:.0f} steps)",
        "beads sit on the same traced lines as FieldLines.vlc and morph in sync with them",
        "wrapped / failed / reconnected beads are opacity 0 on BOTH rows (anti-streak)",
        f"epoch {cur['epoch']} state index {int(k_cur)} frame {cur.get('frame')}",
    ]
    if prev is None:
        rows = bead_rows(cur, k_cur)
        n = write_dots_vlc(out_path, rows, comments=comments, coord_fmt=cfmt, val_fmt=vfmt)
        return {"path": out_path, "dots": n, "parametric": False, "n_hidden": int((rows[:, 3] == 0).sum()),
                "n_wrapped": 0, "n_dead": int(dead.sum()), "n_broke": 0}
    if prev["export"].shape != cur["export"].shape:
        raise ValueError(f"morph topology mismatch: {prev['export'].shape} vs {cur['export'].shape}")
    if prev.get("frame", cur.get("frame")) != cur.get("frame"):
        raise ValueError(f"morph frame mismatch: {prev.get('frame')} vs {cur.get('frame')}")
    if k_prev is None:
        raise ValueError("k_prev is required when prev is given")
    dead = dead | np.array([k == fl.KIND_FAILED for k in prev["kind"]])
    broke = np.asarray(fl.break_mask(prev, cur, shape_break_re)["broke"], dtype=bool)
    wrap = wrapped_between(k_prev, k_cur)
    hide = (dead | broke)[:, None] | wrap[None, :]
    rows_d = bead_rows(prev, k_prev, hide)
    rows_v = bead_rows(cur, k_cur, hide)
    comments.append(f"morph: D rows = previous epoch {prev['epoch']} (state {int(k_prev)}), V rows = current epoch")
    n = write_dots_vlc(out_path, rows_d, next_points=rows_v, comments=comments,
                       coord_fmt=cfmt, val_fmt=vfmt)
    log.info("FieldPulses: %d beads, %d hidden (%d wrapped bead phase(s), %d dead line(s), "
             "%d broke) -> %s", n, int(hide.sum()), int(wrap.sum()), int(dead.sum()),
             int(broke.sum()), out_path)
    return {"path": out_path, "dots": n, "parametric": True, "n_hidden": int(hide.sum()),
            "n_wrapped": int(wrap.sum()), "n_dead": int(dead.sum()), "n_broke": int(broke.sum())}
