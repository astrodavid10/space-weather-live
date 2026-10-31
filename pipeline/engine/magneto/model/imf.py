"""Interplanetary field lines draping over the magnetosphere, and reconnection.

    Kan, J. R., & Lee, L. C. (1979), Energy coupling function and solar
    wind-magnetosphere dynamo, Geophys. Res. Lett. 6(7), 577-580,
    doi:10.1029/GL006i007p00577        [DOI verified against Crossref]

        clock angle  theta_c = atan2(By, Bz)         (0 = due north)
        merging rate E_KL    = v * B_T * sin^2(theta_c / 2)

THE POINT OF THIS LAYER
-----------------------
Everything else in the show reacts to southward Bz; nothing shows WHY it
matters. This does. The merging rate carries sin^2(theta_c/2), which is exactly
zero for a northward field and one for a due-southward one, so the same wind
speed and the same field strength produce either a clean drape that slides past
the magnetosphere or a continuous connection at the nose - and the difference is
driven entirely by the measured field direction, not by a switch.

HOW THE DRAPE IS COMPUTED, AND WHAT THAT DOES AND DOES NOT CLAIM
----------------------------------------------------------------
A frozen-in field line is a MATERIAL line: it moves with the plasma. So the
drape is built the honest, literal way - seed a straight line along the measured
IMF direction far upstream, then convect every one of its points through the
wind's own flow field (`wind.flow_velocity`, the same field the 10,000 parcels
use) for the same elapsed time. Points that pass through the sheath slow down
and get steered around the obstacle while points out on the flank run ahead, and
the drape falls out of that difference. Nothing about the shape is drawn by hand.

What it does NOT claim: there is no induction equation here, and no time history.
Each step is the quasi-steady drape for THAT epoch's flow and field direction.
That is the right picture for a steady wind - the field PATTERN is steady while
plasma streams along it - and it is why the lines hold still while the parcels
move through them. Do not describe them as individual field lines being watched
convect downstream: that is a different picture and this is not it.

RECONNECTION, DRAWN AS A CONNECTION RATHER THAN A CUT
-----------------------------------------------------
Where the merging rate is non-zero and a line reaches the subsolar magnetopause,
the part of the line inside the merging region is recoloured to the same blue the
traced OPEN field lines already use: the audience watches an interplanetary field
line become one of Earth's own. The geometry is unchanged by the recolouring -
the tailward sweep is already in the drape, because the flow put it there - so
this layer needs no crossfade and cannot draw a shape that exists in no field.
The fraction of the line that changes colour grows with E_KL, so northward IMF
recolours nothing at all.
"""
from __future__ import annotations

import math

import numpy as np

DOI_KAN_LEE = "10.1029/GL006i007p00577"

# Fixed topology - the morph contract. Never make these data-dependent.
N_IMF = 18                # = N_STAGE * N_SHEET, see below
N_IMF_VERTS = 48

# Seeding geometry, in R_E.
#
# The lines are spaced ALONG THE FLOW, not across one upstream plane, and that is
# the whole construction. Seeding them all on one plane and convecting them the
# same distance just parks the entire bundle downstream together - every line at
# the same stage, none of them draped (measured: closest approach 36 R_E, nothing
# near the boundary). Spacing them along the flow instead means that at any
# instant one line is still straight and far upstream, the next is meeting the
# shock, the next is hung up on the nose while its flanks sweep past, and the
# last is swept well down the tail. That progression IS the draping picture, and
# in a steady flow it is also literally what successive field lines look like.
# Tuned on the dome 2026-09-16: 12 stages x 2 sheets of 84 R_E lines read as a
# regular GRID laid over the scene rather than as a field - too many, too long,
# too evenly spaced. Fewer and shorter reads as field lines.
N_STAGE = 9               # lines spaced along the flow
N_SHEET = 2               # ... times two offset sheets, so it reads as 3-D
X_SEED_MIN_RE = -30.0     # the most-convected line ends up down the tail
X_SEED_MAX_RE = 60.0      # the least-convected is still undisturbed
SHEET_OFFSET_RE = 9.0     # perpendicular separation of the two sheets
LINE_HALF_LEN_RE = 30.0   # half-length of each straight line before draping

# How far each line is carried through the flow. Modest, because the stage
# spacing above already supplies the progression - this is what bends each line
# around the obstacle it happens to be passing.
CONVECT_RE = 42.0
N_SUBSTEP = 140

# Reconnection geometry. The merging region is the subsolar cone: lines whose
# closest approach falls inside it, on the dayside, can connect.
MERGE_CONE_DEG = 50.0
MERGE_GAP_RE = 3.0        # how close to the magnetopause counts as "reaching" it
# Merging rate (mV/m) at which the recoloured fraction saturates. Kan & Lee's
# E_KL runs ~0-2 mV/m in quiet wind and reaches ~20 in a severe storm.
E_KL_FULL_MV_M = 8.0

# Colours. Pale violet is the unconnected interplanetary field - deliberately
# unlike any existing layer. The connected part takes fieldlines' open-north blue
# so the two read as the same population, which is the whole teaching point.
COLOUR_IMF = (0.72, 0.62, 1.0)
COLOUR_MERGED = (0.30, 0.55, 1.0)
OPACITY = 0.55


def clock_angle_rad(state: dict) -> float:
    """IMF clock angle: 0 = due north, pi = due south. atan2(By, Bz)."""
    sw = (state or {}).get("sw") or {}
    by = float(sw.get("by_gsm") or 0.0)
    bz = float(sw.get("bz_gsm") or 0.0)
    return float(math.atan2(by, bz))


def transverse_field_nt(state: dict) -> float:
    """|B| in the Y-Z plane - the component that can merge."""
    sw = (state or {}).get("sw") or {}
    by = float(sw.get("by_gsm") or 0.0)
    bz = float(sw.get("bz_gsm") or 0.0)
    return float(math.hypot(by, bz))


def merging_rate_mv_m(state: dict) -> float:
    """Kan & Lee (1979) reconnection electric field, mV/m.

    E_KL = v * B_T * sin^2(theta_c/2), the half-wave rectifier: identically zero
    for a northward IMF however fast or strong the wind, and maximal for a due
    southward one. This is the number that decides whether anything merges, and
    it comes entirely from the measurement.
    """
    sw = (state or {}).get("sw") or {}
    v = float(sw.get("speed") or 0.0)
    bt = transverse_field_nt(state)
    half = math.sin(0.5 * clock_angle_rad(state)) ** 2
    # v[km/s] * B[nT] -> mV/m is a factor 1e-3
    return 1e-3 * v * bt * half


def merged_fraction(state: dict) -> float:
    """0..1 - how much of a connected line is drawn as Earth's field."""
    return float(min(1.0, merging_rate_mv_m(state) / E_KL_FULL_MV_M))


def imf_direction(state: dict) -> np.ndarray:
    """Unit vector along the measured IMF in GSM. Falls back to +Y if absent."""
    sw = (state or {}).get("sw") or {}
    b = np.array([float(sw.get("bx_gsm") or 0.0),
                  float(sw.get("by_gsm") or 0.0),
                  float(sw.get("bz_gsm") or 0.0)], dtype=float)
    n = np.linalg.norm(b)
    if not np.isfinite(n) or n < 1e-9:
        return np.array([0.0, 1.0, 0.0])
    return b / n


def seed_lines(state: dict) -> np.ndarray:
    """(N_IMF, N_IMF_VERTS, 3) straight lines along B on the upstream plane.

    The bundle is spread across the impact-parameter disk PERPENDICULAR to the
    field, so the lines are parallel and evenly spaced rather than fanned - which
    is what an undisturbed interplanetary field looks like.
    """
    b_hat = imf_direction(state)
    # A direction perpendicular to both B and the flow, to offset the sheets.
    flow = np.array([-1.0, 0.0, 0.0])
    perp = np.cross(b_hat, flow)
    n = np.linalg.norm(perp)
    perp = perp / n if n > 1e-6 else np.array([0.0, 1.0, 0.0])

    t = np.linspace(-LINE_HALF_LEN_RE, LINE_HALF_LEN_RE, N_IMF_VERTS)
    stages = np.linspace(X_SEED_MAX_RE, X_SEED_MIN_RE, N_STAGE)
    sheets = (np.arange(N_SHEET) - 0.5 * (N_SHEET - 1)) * SHEET_OFFSET_RE

    lines = []
    for sh in sheets:
        for x0 in stages:
            base = np.array([x0, 0.0, 0.0]) + sh * perp
            pts = base[None, :] + t[:, None] * b_hat[None, :]
            # the line lies along B, so subtract whatever sunward component that
            # gave it: each line is meant to SIT at its own stage x0, not to rake
            # across several of them.
            pts[:, 0] = pts[:, 0] - pts[:, 0].mean() + x0
            lines.append(pts)
    return np.stack(lines, axis=0)


def drape(state: dict, sheath, convect_re: float = CONVECT_RE,
          n_substep: int = N_SUBSTEP) -> np.ndarray:
    """Convect the seeded lines through the flow. (N_IMF, N_IMF_VERTS, 3) GSM R_E.

    Every point is carried for the SAME elapsed time, so points that enter the
    sheath and slow fall behind the ones on the flank - that difference IS the
    drape. `sheath` is a wind.Sheath (injected, like everywhere else in this
    tree, so this module never imports the boundary code).
    """
    from . import wind as wind_mod

    pts = seed_lines(state).reshape(-1, 3).copy()
    speed = float(((state or {}).get("sw") or {}).get("speed") or 400.0)
    speed_arr = np.full(len(pts), speed)
    # elapsed time to convect `convect_re` at the undisturbed speed
    dt = (convect_re / max(n_substep, 1)) * wind_mod.RE_KM / max(speed, 1e-6)

    for _ in range(int(n_substep)):
        v, _gap, _r, _th, _inside = wind_mod.flow_velocity(pts, speed_arr, sheath)
        pts = pts + v * dt
    return pts.reshape(N_IMF, N_IMF_VERTS, 3)


def merged_mask(lines: np.ndarray, sheath, state: dict) -> np.ndarray:
    """(N_IMF, N_IMF_VERTS) bool - vertices drawn as connected to Earth's field.

    A vertex counts when the line reaches the magnetopause inside the subsolar
    merging cone AND the merging rate is non-zero. The recoloured RUN grows from
    the contact point with E_KL, so a weakly southward field connects a short
    stretch and a strongly southward one connects most of the line.
    """
    frac = merged_fraction(state)
    out = np.zeros(lines.shape[:2], dtype=bool)
    if frac <= 0.0:
        return out                                   # northward IMF: nothing merges

    r = np.linalg.norm(lines, axis=2)
    r_mp = sheath.mp_radius(np.arccos(np.clip(lines[..., 0] / np.maximum(r, 1e-9),
                                              -1.0, 1.0)))
    gap = r - r_mp
    cone = np.degrees(np.arccos(np.clip(lines[..., 0] / np.maximum(r, 1e-9), -1.0, 1.0)))
    touching = (gap < MERGE_GAP_RE) & (cone < MERGE_CONE_DEG)

    n_run = max(1, int(round(frac * N_IMF_VERTS)))
    for i in range(lines.shape[0]):
        hit = np.flatnonzero(touching[i])
        if hit.size == 0:
            continue
        c = int(hit[hit.size // 2])                  # middle of the contact run
        lo = max(0, c - n_run // 2)
        out[i, lo:lo + n_run] = True
    return out


def line_rows(state: dict, sheath, frame, coord_digits: int = 9):
    """List of (N_IMF_VERTS, 7) polylines in the export frame: xyz, opacity, rgb."""
    from .. import geo as geo_mod

    lines = drape(state, sheath)
    merged = merged_mask(lines, sheath, state)
    xyz = geo_mod.gsm_re_to_frame(lines.reshape(-1, 3), frame).reshape(lines.shape)

    rows = []
    for i in range(lines.shape[0]):
        block = np.empty((N_IMF_VERTS, 7))
        block[:, :3] = xyz[i]
        block[:, 3] = OPACITY
        block[:, 4:7] = np.where(merged[i][:, None],
                                 np.array(COLOUR_MERGED)[None, :],
                                 np.array(COLOUR_IMF)[None, :])
        rows.append(block)
    return rows


def comments(state: dict) -> list:
    """File-header provenance. ASCII, no semicolons."""
    e = merging_rate_mv_m(state)
    th = math.degrees(clock_angle_rad(state))
    return [
        f"IMF field lines draping over the magnetosphere - {N_IMF} lines x "
        f"{N_IMF_VERTS} vertices, fixed every frame (the morph contract)",
        f"clock angle {th:+.1f} deg (0 = due north), transverse field "
        f"{transverse_field_nt(state):.1f} nT, merging rate {e:.3f} mV/m from "
        f"Kan & Lee 1979 E_KL = v Bt sin^2(theta_c/2), doi:{DOI_KAN_LEE}",
        f"{merged_fraction(state) * 100:.0f} percent of a connected line is drawn in the "
        f"open-field colour - a northward IMF merges nothing at all, whatever the "
        f"wind speed, which is the point of the sin^2 factor",
        "DRAPE: each line is a material line convected through the wind's own "
        "kinematic flow field (the same field the parcels use) for a fixed time - "
        "the shape is not drawn by hand",
        "NOT claimed: there is no induction equation and no time history here. Each "
        "frame is the quasi-steady drape for that epoch, which is why the pattern "
        "holds still while the parcels stream through it",
    ]


def selftest() -> dict:
    """The half-wave rectifier, and the fixed topology."""
    out = {}
    north = {"sw": {"speed": 600.0, "by_gsm": 0.0, "bz_gsm": 12.0,
                    "bx_gsm": 0.0, "bt": 12.0}}
    south = {"sw": {"speed": 600.0, "by_gsm": 0.0, "bz_gsm": -12.0,
                    "bx_gsm": 0.0, "bt": 12.0}}
    dawn = {"sw": {"speed": 600.0, "by_gsm": 12.0, "bz_gsm": 0.0,
                   "bx_gsm": 0.0, "bt": 12.0}}
    out["E_KL_north"] = round(merging_rate_mv_m(north), 4)
    out["E_KL_south"] = round(merging_rate_mv_m(south), 4)
    out["E_KL_dawn"] = round(merging_rate_mv_m(dawn), 4)
    assert merging_rate_mv_m(north) == 0.0, "a northward IMF must merge nothing"
    assert merging_rate_mv_m(south) > merging_rate_mv_m(dawn) > 0.0
    # due south is the maximum: sin^2(pi/2) = 1
    assert abs(merging_rate_mv_m(south) - 1e-3 * 600.0 * 12.0) < 1e-9

    out["clock_north_deg"] = round(math.degrees(clock_angle_rad(north)), 1)
    out["clock_south_deg"] = round(math.degrees(clock_angle_rad(south)), 1)

    assert N_IMF == N_STAGE * N_SHEET, "the bundle must fill the fixed row count"
    pts = seed_lines(south)
    out["seed_shape"] = list(pts.shape)
    assert pts.shape == (N_IMF, N_IMF_VERTS, 3)
    # the bundle spans the flow, from undisturbed upstream to down the tail
    out["stage_x_range"] = [round(float(pts[..., 0].min()), 1),
                            round(float(pts[..., 0].max()), 1)]
    assert pts[..., 0].max() >= X_SEED_MAX_RE - 1e-6
    assert pts[..., 0].min() <= X_SEED_MIN_RE + 1e-6
    # the lines are parallel: every one has the same direction
    d = pts[:, -1, :] - pts[:, 0, :]
    d = d / np.linalg.norm(d, axis=1, keepdims=True)
    assert np.allclose(d, d[0], atol=1e-9), "the seeded bundle must be parallel"
    return out


# --- export ---------------------------------------------------------------------

def export_imf_lines(state: dict, prev_state, sheath, prev_sheath, out_path,
                     frame=None, coord_digits: int = 9):
    """Write IMFLines.vlc (VLC LINES), morphing prev -> cur.

    No crossfade machinery. The recolouring is what marks a reconnected line, and
    colour morphs continuously, so this layer can never blend two topologies'
    SHAPES the way the traced field lines can - which is exactly why the merging
    was drawn as a connection rather than as a cut.

    `sheath` is injected (a wind.Sheath) so this module never imports the boundary
    code, the same rule wind.py follows.
    """
    from pathlib import Path

    from dsexport import write_lines_vlc
    from dsexport.vla import coord_formatter, val_formatter
    from .. import geo as geo_mod
    from . import recalc_for_state, state_comments

    frame = geo_mod.check_frame(frame or geo_mod.DEFAULT_FRAME)
    out_path = Path(out_path)
    cfmt, vfmt = coord_formatter(coord_digits), val_formatter(coord_digits)

    recalc_for_state(state)
    cur = line_rows(state, sheath, frame, coord_digits)
    prev = cur
    if prev_state is not None and prev_sheath is not None:
        recalc_for_state(prev_state)
        prev = line_rows(prev_state, prev_sheath, frame, coord_digits)
        recalc_for_state(state)             # leave geopack on THIS epoch

    lines = comments(state)
    lines.append("morph: D/P/L rows = previous epoch, V rows = current epoch")
    n = write_lines_vlc(out_path, prev, cur,
                        comments=state_comments(state, lines, frame=frame),
                        coord_fmt=cfmt, val_fmt=vfmt)
    return {"path": out_path, "vertices": n, "frame": frame,
            "merging_mv_m": merging_rate_mv_m(state),
            "clock_deg": math.degrees(clock_angle_rad(state)),
            "merged_fraction": merged_fraction(state)}


if __name__ == "__main__":
    import json
    print(json.dumps(selftest(), indent=1))
