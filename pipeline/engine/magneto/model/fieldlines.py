"""Tsyganenko field-line tracing from a FIXED seed table, exported as VLC.

The seed table is module-level, built once at import, and never re-randomised
or re-sized: line i is the same seed in every frame, so consecutive exports
morph line-for-line instead of jumping. Seeds are defined in SM (dipole-fixed)
coordinates and rotated into GSM per epoch, so the ionospheric families stay
nailed to the magnetic poles and the tail families stay nailed to the dipole
as the Earth turns under them.

Four families, 200 seeds:
  closed  48  maglat 55/60/65/68 N x 12 MLT, r = 1.02 R_E   rlim 15
  open    72  maglat 75/80/85 x 12 MLT x both hemispheres    rlim 40
  cusp    40  maglat 76-79 x MLT 10-14 x both hemispheres    rlim 20
  tail    40  x -10/-15/-20/-30, y -8..8, z +-3 R_E (SM)     rlim 60
MLT 12 h is +X_SM, MLT increases duskward (+Y_SM).

Every line is arc-length resampled to exactly N_VERTS = 64 vertices, and a
seed whose trace failed becomes 64 copies of itself at opacity 0 - the dummy
line trick, so the polyline count and the per-line vertex count are identical
in every frame no matter what the tracer did.

WHY THE TRACE IS SPLIT INTO HOPS
--------------------------------
geopack.trace() picks its step size from the radius ONLY while the line is
moving inward; on an outbound leg it takes the `pass` branch and keeps
whatever ds it already had. A seed at r = 1.02 therefore locks in
ds = 0.05*(r - r0 + 0.2) = 0.011 R_E and crawls at that step for the entire
line: 3000 iterations (the maxloop cap) to cover 33 R_E, ~2.2 s per branch,
and the line is truncated mid-flight so it never reaches rlim at all.

So each branch is traced as a chain of gp.trace() calls at the radial gates
in GATES_RE, each call resuming where the last one stopped. Every restart
re-derives ds from the current radius, and a restart between 3 and 5 R_E even
gets geopack's ds = 1.0 branch, which is why the last gate sits at 4 R_E and
the whole outer leg is then one hop. Same tracer, same field, same field
line - 15x faster and no maxloop truncation.

COST
----
Even so, 200 lines cost ~18 s per epoch on the reference box (Ryzen laptop,
run-to-run spread ~15%), and essentially all of it is geopack evaluating
IGRF + T89 in pure Python: ~90k field evaluations at ~155 us each. That is
comfortably inside a 60 s poll but it is NOT the 15 s the Build A brief hoped
for, and nothing in the tracer's own bookkeeping is worth optimising - the
field evaluations are the floor.

Two measured knobs are left in place for whoever wants to buy the difference:

  internal='hybrid'  IGRF inside R_IGRF_RE, geopack's centred dipole (same
                     epoch, same moment) outside, where the non-dipole terms
                     are already ~2% of an internal field that is itself no
                     longer dominant.  ~15.7 s, but it moves vertices by
                     0.46 R_E on average and can send a line that sits on the
                     cusp separatrix somewhere else entirely.
  internal='dipole'  no IGRF at all. Fast, and footpoints land on the wrong
                     continent.

The default is 'igrf': the honest field, and the 3 extra seconds are cheaper
than explaining why the lines miss the magnetic poles.

Single-threaded, like everything else that touches geopack's epoch globals.

CROSSFADE ON A TOPOLOGY FLIP
----------------------------
Row i is the same SEED in every frame, but during a storm it is not always the
same LINE: reconnection turns a closed line open (and back), and a seed that
traced cleanly at one epoch can fail at the next. A parametric export pairs the
rows positionally, so Digistar would linearly interpolate a closed dipole loop
into a 40 R_E open lobe - a shape that is in no field anywhere - and slide its
colour from gold to blue on the way.

So an identity break is CROSSFADED instead of morphed, using exactly the wind
layer's anti-streak idiom (a recycled parcel is opacity 0 on BOTH rows of its
pair). In the file that morphs trace j -> trace j+1, a broken row is frozen at
trace j's shape and colour on both rows, visible on the D row and opacity 0 on
the V row: it fades out in place. In the NEXT file it is frozen at trace j+1's
shape and colour, opacity 0 on the D row and visible on the V row: it fades in
in place. The position therefore snaps only at a file boundary, while the line
is invisible, and no file ever blends two topologies' colours. A row that
breaks in two consecutive pairs is simply hidden for the whole middle file.

break_mask() is the single source of truth for "did row i break", and it is a
pure function of two traces - which is what lets the multiprocess executor
precompute every pair's mask from the trace cache and hand each item its own
(the serial backend carries the previous pair's mask frame to frame, and
recovers it from the receipt when --resume skips an item).
"""
from __future__ import annotations

import logging
import math
import time
from pathlib import Path

import numpy as np

from . import recalc_for_state, state_comments
from .. import geo
from dsexport import validate, write_lines_vlc
from dsexport.vla import coord_formatter, val_formatter

log = logging.getLogger(__name__)

N_VERTS = 64                 # vertices per exported line, always
MAXLOOP = 3000               # gp.trace iteration cap per hop
R_SEED_RE = 1.02             # ionospheric seed radius
R_INNER_RE = 1.0             # footpoint sphere (gp.trace r0)
R_IGRF_RE = 4.0              # IGRF inside this radius, centred dipole outside
# Trace-restart radii, up to R_IGRF_RE. Spaced so each hop is a couple of
# geopack steps: closer together buys nothing (geopack's own ds ramp takes
# over), further apart lets the frozen ds get stale. Above 4 R_E the whole
# outer leg is one hop - a restart between 3 and 5 R_E leaves geopack on its
# ds = 1.0 branch, which is the coarsest step it will ever choose by itself.
GATES_RE = (1.05, 1.15, 1.3, 1.5, 1.75, 2.05, 2.4, 2.8, 3.3, 4.0)
MAX_HOPS = 32
MIN_ARC_RE = 0.1             # shorter than this counts as a failed trace
INTERNAL = "igrf"            # 'igrf' | 'hybrid' | 'dipole' - see docstring

CLOSED_END_RE = 1.05         # an end at or below this is a footpoint
OPEN_END_FRAC = 0.95         # an end at or beyond frac*rlim is a far end
GEOPACK_X_MAX = 20.0         # gp.trace also stops at x >= 20 ...
GEOPACK_RHO_MAX = 40.0       # ... and at sqrt(y^2+z^2) >= 40

KIND_CLOSED, KIND_OPEN_N, KIND_OPEN_S, KIND_FAILED = (
    "closed", "open_n", "open_s", "failed")

COLOURS = {
    KIND_CLOSED: (1.0, 0.85, 0.2),    # gold - both feet on the ground
    KIND_OPEN_N: (0.3, 0.55, 1.0),    # blue - northern lobe, open to the wind
    KIND_OPEN_S: (1.0, 0.4, 0.1),     # orange - southern lobe
    KIND_FAILED: (0.5, 0.5, 0.5),     # never visible (opacity 0)
}
OPACITY_FOOT = 1.0
OPACITY_FAR = 0.35

# Brightness along a line follows |B|, on a log scale, because that is how the
# quantity actually behaves: ~30,000 nT at a 2 R_E footpoint, ~1-10 nT at the
# apex of a stretched tail line - four decades, which a linear ramp would render
# as "bright at the feet, black everywhere else".
#
# Hue is left alone: it still encodes TOPOLOGY, which is the thing a presenter
# names out loud ("the gold ones are closed"). Mixing the two into one channel
# would make both unreadable.
B_LO_NT = 2.0                # tail-lobe faint end of the ramp
B_HI_NT = 20000.0            # footpoint bright end
BRIGHT_MIN = 0.30            # never fully black - the line's shape is the point
BRIGHT_MAX = 1.0

# Vertex displacement (R_E) above which two same-kind lines are treated as
# different lines rather than one line that moved - the case a `kind`
# comparison cannot see, because a seed can hop to a different branch of the
# separatrix without changing class. Measured on Gannon 2024-05-10 at 10-minute
# steps: the median row moves ~0.2 R_E and the 99th percentile ~3-15 R_E, while
# a genuine hop lands at 20-60. 15 R_E sits in that gap - on the 16:00-16:20
# window it fires on exactly one row (a cusp seed that moved 56.9 R_E) and on
# none of the 199 others.
SHAPE_BREAK_RE = 15.0

# Mutually exclusive, in priority order - see break_mask().
BREAK_REASONS = ("dead_transition", "kind_flip", "shape_jump")


# --- fixed seed table (SM coordinates, built once) ----------------------------

def _sm_point(maglat_deg: float, mlt_h: float, r: float = R_SEED_RE):
    """SM cartesian for a magnetic latitude / magnetic local time seed."""
    lat = math.radians(maglat_deg)
    phi = math.radians((mlt_h - 12.0) * 15.0)      # MLT 12 -> +X_SM
    cl = r * math.cos(lat)
    return (cl * math.cos(phi), cl * math.sin(phi), r * math.sin(lat))


# Outer tracing limit per family (R_E): how far a line of that kind is
# allowed to run before we stop caring where it went.
FAMILY_RLIM = {"closed": 15.0, "polar": 40.0, "cusp": 20.0, "tail": 60.0}

# These gates were tuned against a QUIET magnetopause. A storm drives the subsolar standoff from
# ~10.3 R_E down to 5-7 R_E, and a fixed 15 R_E gate then lets closed lines run far outside the
# boundary the show draws: audited 2026-09-15 on Bastille step 121 (standoff 5.32 R_E), 17.0% of
# exported vertices sat outside the drawn magnetopause and the worst reached 3.5x its radius on the
# DAYSIDE - a closed field line outside the magnetopause is not physical, and it is what "the
# shells do not match the shape of the field" looks like.
#
# The dayside families are therefore gated on the live standoff. Scaling is capped at 1.0 so quiet
# conditions are bit-for-bit unchanged (the look-back and the live feed never shrink), and only the
# families that belong INSIDE the magnetosphere are scaled: 'polar' and 'tail' lines legitimately
# run down the tail, where the boundary flares far wider than the subsolar standoff.
# The DRAWN standoff at pdyn 2 nPa, Bz 0, zero tilt - what the gates above assume.
# It moved from Shue's 10.252 to 10.681 (+4.2%) when the drawn magnetopause became
# the Lin hybrid on 2026-09-16. This number is a DENOMINATOR: leaving it at the
# Shue value would have loosened every dayside gate by 4% without a single test
# failing, because k would read 1.042 at quiet conditions and then be capped at
# 1.0 - silently, and only at quiet times.
R_MP_REF_RE = 10.681
RLIM_SCALED_FAMILIES = ("closed", "cusp")
RLIM_SCALE_FLOOR = 0.35       # never gate tighter than this, whatever the wind does


def family_rlim(state: dict) -> "np.ndarray":
    """Per-seed outer tracing limit (R_E) for this epoch's magnetopause."""
    from .boundaries import drawn_nose_re
    # The gates follow the DRAWN boundary, not the Shue reference: a closed field
    # line outside the surface the audience can see is the artefact this exists to
    # prevent, and it is the drawn one they compare it against.
    r_mp = drawn_nose_re(state)
    k = min(1.0, max(RLIM_SCALE_FLOOR, r_mp / R_MP_REF_RE))
    return np.array([FAMILY_RLIM[f] * (k if f in RLIM_SCALED_FAMILIES else 1.0)
                     for f in SEED_FAMILY], dtype=float)


def field_magnitude_nt(xyz_gsm, state: dict) -> "np.ndarray":
    """|B| in nT at each (..., 3) GSM point, internal + external, this epoch.

    The same field the tracer integrated, sampled at the vertices it produced, so
    the colour cannot disagree with the shape. geopack's model routines are
    scalar, so this is a Python loop - measured at 0.06 ms/point on T89, i.e.
    ~0.8 s for a full 200 x 64 trace and about 10 s spread across a whole
    312-step render. TS05 is dearer but still small beside its own trace cost.

    Requires the epoch to be recalc'd already (trace_all has just done it).
    """
    import importlib
    from geopack import geopack as gp

    parmod, exname = _model_args(state)
    ext = getattr(importlib.import_module("geopack." + exname), exname) if exname else None
    pts = np.asarray(xyz_gsm, dtype=float)
    flat = pts.reshape(-1, 3)
    out = np.empty(flat.shape[0])
    ps = float(getattr(gp, "psi", 0.0))
    for i, (x, y, z) in enumerate(flat):
        try:
            bx, by, bz = gp.igrf_gsm(x, y, z)
            if ext is not None:
                ex, ey, ez = ext(parmod, ps, x, y, z)
                bx += ex
                by += ey
                bz += ez
            out[i] = math.sqrt(bx * bx + by * by + bz * bz)
        except Exception:
            # A vertex the model will not evaluate (inside the inner boundary, or
            # a numerical edge) gets the faint end rather than killing the render.
            out[i] = B_LO_NT
    return out.reshape(pts.shape[:-1])


def brightness_from_b(bmag_nt) -> "np.ndarray":
    """|B| (nT) -> a BRIGHT_MIN..BRIGHT_MAX multiplier, log-scaled and clipped."""
    b = np.clip(np.asarray(bmag_nt, dtype=float), B_LO_NT, B_HI_NT)
    f = (np.log10(b) - math.log10(B_LO_NT)) / (math.log10(B_HI_NT) - math.log10(B_LO_NT))
    return BRIGHT_MIN + (BRIGHT_MAX - BRIGHT_MIN) * f


def _build_seeds():
    seeds, families = [], []

    def add(family, point):
        seeds.append(point)
        families.append(family)

    mlt12 = [float(h) for h in range(0, 24, 2)]    # 12 MLT, every 2 hours

    for maglat in (55.0, 60.0, 65.0, 68.0):        # closed dipole shells, north
        for mlt in mlt12:
            add("closed", _sm_point(maglat, mlt))

    for maglat in (75.0, 80.0, 85.0):              # open polar caps, both hemis
        for mlt in mlt12:
            for sign in (1.0, -1.0):
                add("polar", _sm_point(sign * maglat, mlt))

    for maglat in (76.0, 77.0, 78.0, 79.0):        # cusp fans, noon +- 2 h
        for mlt in (10.0, 11.0, 12.0, 13.0, 14.0):
            for sign in (1.0, -1.0):
                add("cusp", _sm_point(sign * maglat, mlt))

    for x in (-10.0, -15.0, -20.0, -30.0):         # tail lobes / plasma sheet
        for y in (-8.0, -4.0, 0.0, 4.0, 8.0):
            for z in (3.0, -3.0):
                add("tail", (x, y, z))

    return (np.array(seeds, dtype=float), tuple(families),
            np.array([FAMILY_RLIM[f] for f in families], dtype=float))


SEEDS_SM, SEED_FAMILY, SEED_RLIM = _build_seeds()
N_SEEDS = len(SEEDS_SM)                            # 200


# --- tracing ------------------------------------------------------------------

def _model_args(state: dict):
    """(parmod, exname) for gp.trace from state['model'] (see STATE_SCHEMA)."""
    model = state["model"]
    ext = str(model["external"]).lower()
    # geopack names: t96 -> t96, TS05 (Tsyganenko & Sitnov 2005) -> t04, T01 -> t01
    exname = {"t96": "t96", "ts05": "t04", "t04": "t04", "t01": "t01"}.get(ext)
    if exname is not None:
        parmod = np.array(model["parmod"], dtype=float)
        if parmod.size != 10:
            raise ValueError(f"{ext} needs a 10-element parmod")
        return parmod, exname
    return int(model["iopt"]), "t89"


def _hop_plan(r: float, rlim: float, internal: str):
    """(inname, r0, gate) for the next gp.trace call from radius r."""
    if internal == "hybrid" and r >= R_IGRF_RE - 1e-9:
        return "dipole", R_IGRF_RE, rlim
    inname = "dipole" if internal == "dipole" else "igrf"
    gate = rlim
    for g in GATES_RE:
        if g > r + 1e-9:
            gate = min(g, rlim)
            break
    if internal == "hybrid":
        gate = min(gate, R_IGRF_RE)
    return inname, R_INNER_RE, gate


def _trace_branch(p0, direction: int, rlim: float, parmod, exname: str,
                  internal: str) -> np.ndarray:
    """One direction of one field line, as an (n, 3) GSM R_E point array.

    Chains gp.trace() calls across the radial gates (see module docstring) and
    lands the last point exactly on the r = R_INNER_RE sphere when the branch
    reaches the ionosphere - geopack's own footpoint interpolation is a no-op
    (it interpolates a point against itself), so it returns the first point
    INSIDE r0 instead, which for a seed-radius start can be r = 0.77.
    """
    from geopack import geopack as gp

    x, y, z = (float(p0[0]), float(p0[1]), float(p0[2]))
    xs, ys, zs = [x], [y], [z]
    for _ in range(MAX_HOPS):
        r = math.sqrt(x * x + y * y + z * z)
        if r >= rlim or x >= GEOPACK_X_MAX or (y * y + z * z) >= GEOPACK_RHO_MAX ** 2:
            break
        inname, r0, gate = _hop_plan(r, rlim, internal)
        if r <= r0:
            break
        xf, yf, zf, xx, yy, zz = gp.trace(
            x, y, z, direction, rlim=gate, r0=r0, parmod=parmod,
            exname=exname, inname=inname, maxloop=MAXLOOP)
        if len(xx) <= 1:
            break
        xs.extend(xx[1:]); ys.extend(yy[1:]); zs.extend(zz[1:])
        x, y, z = float(xf), float(yf), float(zf)
        rf = math.sqrt(x * x + y * y + z * z)
        hit_maxloop = len(xx) > MAXLOOP
        if rf <= r0 + 1e-9 and r0 <= R_INNER_RE + 1e-9:
            break                                   # reached the ionosphere
        if rf < gate - 1e-9 and not hit_maxloop and rf > r0 + 1e-9:
            break                                   # x>=20 / rho>=40 domain exit
        if gate >= rlim - 1e-9 and rf >= rlim - 1e-9:
            break                                   # reached the outer limit

    pts = np.column_stack([xs, ys, zs])
    return _land_on_inner_sphere(pts)


def _land_on_inner_sphere(pts: np.ndarray) -> np.ndarray:
    """Move a below-surface last point back onto r = R_INNER_RE exactly."""
    if len(pts) < 2:
        return pts
    r_end = float(np.linalg.norm(pts[-1]))
    if r_end >= R_INNER_RE:
        return pts
    r_prev = float(np.linalg.norm(pts[-2]))
    if r_prev <= R_INNER_RE:
        return pts[:-1]
    frac = (r_prev - R_INNER_RE) / (r_prev - r_end)
    p = pts[-2] + frac * (pts[-1] - pts[-2])
    pts = pts.copy()
    pts[-1] = p * (R_INNER_RE / np.linalg.norm(p))
    return pts


# Drawn lines stop at the drawn magnetopause (David 2026-09-16). The tolerance lets a
# vertex graze the surface without being cut; the cap ignores the analytic radius where it
# diverges down the tail, which is where a line SHOULD be allowed to run.
CLIP_TO_BOUNDARY = True
CLIP_TOL = 0.02               # 2% slack on the boundary radius
CLIP_R_MP_MAX_RE = 400.0      # beyond this the analytic radius is not meaningful


def _clip_to_drawn_boundary(pts, seed_idx, state, tilt_rad, alpha_rad, r0_re, lut=None):
    """The contiguous run through the seed that stays inside the DRAWN magnetopause.

    Returns `pts` unchanged when nothing needs cutting, so a quiet epoch - where every
    vertex is already inside - is bit-for-bit what it was before this existed.
    """
    from .boundaries import drawn_radius_clip
    p = np.asarray(pts, dtype=float)
    if not CLIP_TO_BOUNDARY or len(p) < 3:
        return p
    r = np.linalg.norm(p, axis=1)
    safe = np.maximum(r, 1e-9)
    th = np.arccos(np.clip(p[:, 0] / safe, -1.0, 1.0))
    ph = np.arctan2(p[:, 2], p[:, 1])
    # The DRAWN boundary, not the analytic one. magnetopause_radius_3d flares without
    # bound as theta -> pi while the drawn tail relaxes to a near-cylinder out to
    # X_TAIL_RE, so testing against the analytic radius let lines run to 2-7x the
    # visible shell down the tail - 5.7% of vertices at the Gannon peak, 41.5% of
    # those beyond 40 R_E (measured 2026-09-20). drawn_radius_clip takes the TIGHTER
    # of the two per direction, so Lin's dayside asymmetry still governs the nose.
    r_mp = np.asarray(drawn_radius_clip(th, ph, state, tilt_rad=tilt_rad,
                                        alpha_rad=alpha_rad, r0_re=r0_re, lut=lut),
                      dtype=float)
    usable = np.isfinite(r_mp) & (r_mp > 0.0) & (r_mp < CLIP_R_MP_MAX_RE)
    inside = ~usable | (r <= r_mp * (1.0 + CLIP_TOL))
    if inside.all():
        return p
    i0 = int(max(0, min(seed_idx, len(p) - 1)))
    if inside[i0]:
        a = b = i0
        while a - 1 >= 0 and inside[a - 1]:
            a -= 1
        while b + 1 < len(p) and inside[b + 1]:
            b += 1
    else:
        # THE SEED ITSELF IS OUTSIDE. Until 2026-09-21 this returned the line whole -
        # "nothing to anchor a run to, so leave the line alone rather than invent one" -
        # and that reasoning is wrong in the case that actually occurs. TAIL seeds sit
        # at ~31 R_E where the drawn boundary is ~30, so on a crushed storm a seed can
        # be 4% outside, essentially ON the surface. Abandoning the clip for that whole
        # line then let it run to 48 R_E against a 13.7 R_E boundary - 3.5x outside, and
        # the single worst escape left on Gannon after the 2-D clip landed.
        #
        # A marginal seed must not disable the clip. Keep the LONGEST run that is
        # inside; the line stays attached to the magnetosphere it belongs to and simply
        # loses the part that is outside the shell.
        a = b = -1
        best = 0
        k = 0
        while k < len(p):
            if not inside[k]:
                k += 1
                continue
            j = k
            while j + 1 < len(p) and inside[j + 1]:
                j += 1
            if j - k + 1 > best:
                best, a, b = j - k + 1, k, j
            k = j + 1
        if best < 2:
            # nothing inside at all: there is no honest cut, so leave it whole and let
            # the escape be counted rather than silently half-drawn. Rare by
            # construction - past the lattice's theta range r_mp is not usable, which
            # already reads as inside.
            return p
    return p if b - a + 1 < 2 else p[a:b + 1]


def _closed_outside_count(xyz, kinds, state, tilt_rad, alpha_rad, r0_re) -> int:
    """Vertices of CLOSED lines that lie outside the drawn magnetopause.

    Not an error and not clipped: it is the size of the disagreement between the drawn
    boundary (Lin blended to Shue) and the external field model about where the closed-field
    region ends. Worth carrying because it is exactly the quantity that made the boundary
    look wrong on the dome.
    """
    # Against the DRAWN boundary, same as the clip: a counter that measured the
    # disagreement with a surface nobody can see would not be the honest number.
    from .boundaries import drawn_radius_clip
    idx = [i for i, k in enumerate(kinds) if k == KIND_CLOSED]
    if not idx:
        return 0
    v = np.asarray(xyz)[idx].reshape(-1, 3)
    r = np.linalg.norm(v, axis=1)
    safe = np.maximum(r, 1e-9)
    th = np.arccos(np.clip(v[:, 0] / safe, -1.0, 1.0))
    ph = np.arctan2(v[:, 2], v[:, 1])
    rmp = np.asarray(drawn_radius_clip(th, ph, state, tilt_rad=tilt_rad,
                                       alpha_rad=alpha_rad, r0_re=r0_re), dtype=float)
    usable = np.isfinite(rmp) & (rmp > 0.0) & (rmp < CLIP_R_MP_MAX_RE)
    return int((usable & (r > rmp * (1.0 + CLIP_TOL))).sum())


def _resample(pts: np.ndarray, n: int = N_VERTS):
    """Arc-length resample to exactly n vertices; None if the line is degenerate."""
    if len(pts) < 2:
        return None
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    keep = np.concatenate([[True], np.diff(s) > 0.0])
    s, pts = s[keep], pts[keep]
    if len(pts) < 2 or s[-1] < MIN_ARC_RE:
        return None
    t = np.linspace(0.0, s[-1], n)
    return np.stack([np.interp(t, s, pts[:, k]) for k in range(3)], axis=1)


def _end_role(p: np.ndarray, rlim: float) -> str:
    """'foot' | 'far' | 'stalled' for one end of a traced line.

    'far' covers every way gp.trace leaves the modelling domain, not just
    r >= rlim: it also stops dead at x = 20 and at rho = 40, and a dayside
    open line hits those long before it hits rlim.
    """
    r = float(np.linalg.norm(p))
    if r <= CLOSED_END_RE:
        return "foot"
    rho = math.hypot(float(p[1]), float(p[2]))
    if (r >= OPEN_END_FRAC * rlim or p[0] >= GEOPACK_X_MAX - 0.05
            or rho >= GEOPACK_RHO_MAX - 0.05):
        return "far"
    return "stalled"


def _classify(line: np.ndarray, rlim: float, gsm_to_sm: np.ndarray,
              seed_sm: np.ndarray) -> str:
    roles = [_end_role(line[0], rlim), _end_role(line[-1], rlim)]
    if roles == ["foot", "foot"]:
        return KIND_CLOSED
    if "far" not in roles:
        return KIND_FAILED
    if roles[0] == "foot":
        z_sm = float((gsm_to_sm @ line[0])[2])
    elif roles[1] == "foot":
        z_sm = float((gsm_to_sm @ line[-1])[2])
    else:
        z_sm = float(seed_sm[2])
    return KIND_OPEN_N if z_sm >= 0.0 else KIND_OPEN_S


def trace_all(state: dict, internal: str = INTERNAL,
              frame=geo.DEFAULT_FRAME) -> dict:
    """Trace the whole fixed seed table at one epoch.

    Returns a dict in stable seed order:
      'xyz'    (N_SEEDS, N_VERTS, 3) GSM R_E
      'export' (N_SEEDS, N_VERTS, 3) export-frame light years, this epoch
      'ecef'   (N_SEEDS, N_VERTS, 3) ECEF meters (always, for cross-checks)
      'kind'   list of 'closed' | 'open_n' | 'open_s' | 'failed'
      'frame', 'seed_gsm', 'seed_sm', 'epoch', 'counts', 'n_failed', 'elapsed_s'

    The lines are GSM physics already, so 'gsm' export is a pure scale and the
    tail/lobe geometry is stationary between epochs - only the dipole tilt and
    the storm move it, which is exactly the motion the frame is meant to show.
    """
    frame = geo.check_frame(frame)
    t0 = time.time()
    recalc_for_state(state)
    sm_to_gsm = geo.sm_to_gsm_matrix()
    gsm_to_sm = sm_to_gsm.T
    seeds_gsm = SEEDS_SM @ sm_to_gsm.T
    parmod, exname = _model_args(state)
    rlims = family_rlim(state)
    # Hoisted once per epoch: magnetopause_radius_3d would otherwise re-derive the dipole
    # tilt, the aberration angle and the hybrid r0 (which runs a bisection) on every one of
    # the 200 lines.
    clip_tilt = geo.dipole_tilt_rad()
    clip_alpha = geo.aberration_angle_for_state(state)
    from .boundaries import hybrid_r0 as _hybrid_r0, drawn_surface_lut
    clip_r0 = float(_hybrid_r0(state)["r0_re"])
    # The drawn surface as a 2-D (theta, phi) table, hoisted for the same reason: it
    # costs one lattice build and would otherwise be rebuilt for every clipped line.
    # 2-D, not a theta profile: at the flank the shell's radius varies THIRTEENFOLD
    # with azimuth, so a single radius per theta clips most directions against a
    # boundary that is not there (see boundaries.drawn_surface_lut).
    clip_lut = drawn_surface_lut(state)
    n_clipped = 0

    xyz = np.empty((N_SEEDS, N_VERTS, 3))
    kinds = []
    for i in range(N_SEEDS):
        rlim = float(rlims[i])
        seed = seeds_gsm[i]
        back = _trace_branch(seed, -1, rlim, parmod, exname, internal)
        fwd = _trace_branch(seed, 1, rlim, parmod, exname, internal)
        stitched = np.vstack([back[::-1], fwd[1:]]) if len(fwd) > 1 else back[::-1]
        full = _resample(stitched)
        if full is None:
            xyz[i] = np.repeat(seed[None, :], N_VERTS, axis=0)
            kinds.append(KIND_FAILED)
            continue
        # Topology from the FULL trace - a clipped end sits at the magnetopause, not at
        # rlim, and _classify reads the end roles. Geometry from the clipped run.
        kind = _classify(full, rlim, gsm_to_sm, SEEDS_SM[i])
        kinds.append(kind)
        # OPEN lines only. A closed line is defined by having both feet on the Earth, so
        # cutting its apex would draw a gold "closed" line ending in mid-air; and since the
        # magnetopause IS the boundary of the closed-field region, a closed line outside it
        # is a disagreement between the drawn surface and the field model - reported below,
        # not hidden here. Closed lines stay governed by family_rlim, which already shrinks
        # the dayside gate with the standoff.
        if kind in (KIND_OPEN_N, KIND_OPEN_S):
            cut = _clip_to_drawn_boundary(stitched, len(back) - 1, state,
                                          clip_tilt, clip_alpha, clip_r0, clip_lut)
            if cut is not stitched:
                n_clipped += 1
            line = _resample(cut)
            xyz[i] = full if line is None else line
        else:
            xyz[i] = full

    # How much CLOSED field still sits outside the drawn magnetopause. Not clipped - a
    # closed line must keep both feet - so it is counted and carried instead, because it is
    # the honest measure of where Lin/Shue and the Tsyganenko field disagree.
    n_closed_outside = _closed_outside_count(xyz, kinds, state, clip_tilt, clip_alpha, clip_r0)

    ecef = geo.gsm_re_to_ecef_m(xyz)
    export = ecef if frame == geo.FRAME_ECEF else geo.gsm_re_to_frame(xyz, frame)
    # |B| rides WITH the trace, like the export-frame bake above: each epoch
    # carries its own, so a morph can never colour one epoch's shape with
    # another's field, and the trace cache and the parallel executor get it for
    # free instead of each recomputing it against whatever epoch geopack is on.
    bmag = field_magnitude_nt(xyz, state)
    counts = {k: kinds.count(k) for k in
              (KIND_CLOSED, KIND_OPEN_N, KIND_OPEN_S, KIND_FAILED)}
    elapsed = time.time() - t0
    log.info("traced %d lines at %s in %.2f s: %s (%d open lines clipped at the "
             "magnetopause, %d closed vertices still outside it)",
             N_SEEDS, state["epoch"], elapsed, counts, n_clipped, n_closed_outside)
    return {"xyz": xyz, "ecef": ecef, "export": export, "frame": frame,
            "bmag_nt": bmag, "kind": kinds, "seed_gsm": seeds_gsm,
            "seed_sm": SEEDS_SM, "epoch": state["epoch"], "counts": counts,
            "n_failed": counts[KIND_FAILED], "elapsed_s": elapsed,
            "n_clipped": n_clipped, "n_closed_outside": n_closed_outside,
            "internal": internal}


# --- identity breaks ------------------------------------------------------------

def break_mask(prev_trace: dict, cur_trace: dict,
               shape_break_re: float = SHAPE_BREAK_RE) -> dict:
    """Rows whose line IDENTITY broke between two traces. Pure, no I/O.

    A break means "row i is not the same line in both traces", so a positional
    morph between them would draw shapes that exist in no field. Three ways it
    happens, checked in this order and reported as mutually exclusive reasons:

      dead_transition  the trace failed in exactly one of the two epochs (the
                       other side is 64 copies of the seed - morphing into or
                       out of that blob sweeps the line across the scene).
      kind_flip        both traced, but the classification changed
                       (closed <-> open_n <-> open_s): reconnection. The colour
                       would slide as well as the shape.
      shape_jump       same kind, both traced, but some vertex moved more than
                       shape_break_re - the seed hopped to a different branch
                       of the separatrix without changing class.

    Two FAILED epochs in a row are not a break: the row is already opacity 0 in
    both files (the `dead` mask), so there is nothing to fade.

    Returns four (N_SEEDS,) boolean arrays: the three reasons plus their union
    under 'broke'. Both traces must share the seed table's shape.
    """
    pk = np.asarray(prev_trace["kind"], dtype="<U8")
    ck = np.asarray(cur_trace["kind"], dtype="<U8")
    prev_xyz = np.asarray(prev_trace["xyz"], dtype=float)
    cur_xyz = np.asarray(cur_trace["xyz"], dtype=float)
    if pk.shape != ck.shape or prev_xyz.shape != cur_xyz.shape:
        raise ValueError(
            f"break_mask topology mismatch: kinds {pk.shape} vs {ck.shape}, "
            f"xyz {prev_xyz.shape} vs {cur_xyz.shape}")

    pf, cf = (pk == KIND_FAILED), (ck == KIND_FAILED)
    dead_transition = pf ^ cf
    kind_flip = (pk != ck) & ~dead_transition
    step = np.linalg.norm(cur_xyz - prev_xyz, axis=2).max(axis=1)
    shape_jump = (~pf & ~cf & (pk == ck) & (step > float(shape_break_re)))
    return {"dead_transition": dead_transition, "kind_flip": kind_flip,
            "shape_jump": shape_jump,
            "broke": dead_transition | kind_flip | shape_jump}


def break_record(mask: dict) -> dict:
    """JSON-safe summary of one pair's mask: the rows, and a count per reason.

    Small enough to live in an item receipt, which is how a --resume recovers
    the previous pair's mask without re-tracing the epochs it skipped.
    """
    return {"rows": [int(i) for i in np.nonzero(mask["broke"])[0]],
            "n": int(mask["broke"].sum()),
            **{r: int(mask[r].sum()) for r in BREAK_REASONS}}


def rows_to_mask(rows) -> np.ndarray:
    """break_record()['rows'] -> the (N_SEEDS,) boolean mask it came from."""
    out = np.zeros(N_SEEDS, dtype=bool)
    if rows:
        out[np.asarray(list(rows), dtype=int)] = True
    return out


def churn_stats(records) -> dict:
    """The frames manifest's 'fieldline_churn' block, from per-pair records.

    `records` is one break_record() per MORPH PAIR in emission order, with
    None (or a falsy entry) for anything that is not a pair - the last,
    non-parametric field-line frame has no successor to break against.
    """
    recs = [r for r in records if r]
    counts = [int(r["n"]) for r in recs]
    return {"n_pairs": len(recs), "n_breaks": int(sum(counts)),
            "max_breaks_per_pair": int(max(counts, default=0)),
            "pairs_with_breaks": int(sum(1 for c in counts if c)),
            "by_reason": {r: int(sum(int(rec[r]) for rec in recs))
                          for r in BREAK_REASONS}}


# --- export -------------------------------------------------------------------

def _line_rows(traced: dict, dead: np.ndarray) -> list:
    """(N_SEEDS,) list of (N_VERTS, 7) VLC rows: export-frame m, opacity, r,g,b.

    Opacity ramps OPACITY_FOOT at the line's closest approach to Earth down to
    OPACITY_FAR at its farthest point, so both feet of a closed line are bright
    and the apex fades; `dead` forces opacity 0 for lines that failed in EITHER
    frame of a morph (a line that exists in one frame only would sweep across
    the scene instead of appearing).
    """
    xyz, kinds = traced["xyz"], traced["kind"]
    ecef = traced.get("export", traced["ecef"])
    # Older cached traces predate bmag_nt; they simply get flat topology colour.
    bmag = traced.get("bmag_nt")
    bright = None if bmag is None else brightness_from_b(bmag)
    r = np.linalg.norm(xyz, axis=2)                       # (N, V) R_E
    r_lo = r.min(axis=1, keepdims=True)
    r_hi = r.max(axis=1, keepdims=True)
    span = np.where(r_hi - r_lo > 1e-9, r_hi - r_lo, 1.0)
    op = OPACITY_FOOT + (OPACITY_FAR - OPACITY_FOOT) * (r - r_lo) / span
    op[dead] = 0.0
    rows = []
    for i in range(len(kinds)):
        block = np.empty((xyz.shape[1], 7))
        block[:, :3] = ecef[i]
        block[:, 3] = op[i]
        rgb = np.asarray(COLOURS[kinds[i]], dtype=float)
        if bright is None or kinds[i] == KIND_FAILED:
            block[:, 4:7] = rgb
        else:
            # hue from topology, brightness from |B| along this line
            block[:, 4:7] = rgb[None, :] * bright[i][:, None]
        rows.append(block)
    return rows


def export_fieldlines(cur: dict, prev, state: dict, out_path,
                      broke_prev=None,
                      shape_break_re: float = SHAPE_BREAK_RE,
                      coord_digits: int = geo.DEFAULT_COORD_DIGITS) -> dict:
    """Write FieldLines.vlc (VLC LINES, EXPLICIT), morphing prev -> cur.

    cur/prev are trace_all() results; each already carries its own export-frame
    bake at its own epoch, so no frame math happens here and the two epochs can
    never be crossed. Topology is asserted identical: same line count, same
    vertex count, same seed table - and the two traces must agree on the export
    FRAME, or the morph would interpolate between two different coordinate
    systems.

    broke_prev  the (N_SEEDS,) mask of rows that broke in the PREVIOUS pair
                (break_mask(trace j-1, trace j)), i.e. the rows this file owes
                a fade-IN. None means "no previous pair" - the first frame of
                the series, or a caller that does not track continuity. The
                break in THIS pair is computed here, from the two traces, so
                the decision is always a pure function of the item's own inputs
                and a parallel executor cannot disagree with a serial one.

    Returns the usual counts plus 'break' (a break_record() for this pair, or
    None when there is no pair) and 'n_crossfade' (rows drawn frozen instead of
    morphed in THIS file - the fade-outs and the fade-ins together).
    """
    out_path = Path(out_path)
    cfmt, vfmt = coord_formatter(coord_digits), val_formatter(coord_digits)
    if cur["xyz"].shape != (N_SEEDS, N_VERTS, 3):
        raise ValueError(f"current trace has shape {cur['xyz'].shape}")
    dead = np.array([k == KIND_FAILED for k in cur["kind"]])
    prev_rows = None
    extra = []
    breaks = None
    freeze = np.zeros(N_SEEDS, dtype=bool)
    broke_here = np.zeros(N_SEEDS, dtype=bool)
    fade_in = np.zeros(N_SEEDS, dtype=bool)
    if prev is not None:
        if prev["xyz"].shape != cur["xyz"].shape:
            raise ValueError(
                f"morph topology mismatch: {prev['xyz'].shape} vs {cur['xyz'].shape}")
        if len(prev["kind"]) != len(cur["kind"]):
            raise ValueError("morph topology mismatch: line count")
        if prev.get("frame", cur.get("frame")) != cur.get("frame"):
            raise ValueError(
                f"morph frame mismatch: {prev.get('frame')} vs {cur.get('frame')}")
        dead = dead | np.array([k == KIND_FAILED for k in prev["kind"]])
        breaks = break_mask(prev, cur, shape_break_re)
        broke_here = breaks["broke"]
        if broke_prev is not None:
            fade_in = np.asarray(broke_prev, dtype=bool)
            if fade_in.shape != (N_SEEDS,):
                raise ValueError(
                    f"broke_prev has shape {fade_in.shape}, want ({N_SEEDS},)")
        freeze = broke_here | fade_in
        extra = ["morph: D/P/L rows = previous epoch, V rows = current epoch",
                 f"previous epoch {prev['epoch']} {prev['counts']}"]
        if freeze.any():
            extra.append(
                f"{int(freeze.sum())} line(s) crossfade rather than morph here "
                "- their magnetic topology changed between these epochs "
                "(reconnection), and a straight-line morph between different "
                "topologies would draw non-physical shapes")

    cur_rows = _line_rows(cur, dead)
    comments = state_comments(state, [
        f"{N_SEEDS} fixed seeds (SM) x {N_VERTS} arc-length resampled vertices: "
        "closed 48, polar 72, cusp 40, tail 40",
        f"colour: hue = topology (closed gold, open-north blue, open-south orange), "
        f"brightness = |B| along the line, log-scaled {B_LO_NT:g}-{B_HI_NT:g} nT",
        f"internal field {cur['internal']} (IGRF inside {R_IGRF_RE:g} R_E); "
        f"rlim closed 15 polar 40 cusp 20 tail 60 R_E; maxloop {MAXLOOP}",
        f"colours closed {COLOURS[KIND_CLOSED]} open_n {COLOURS[KIND_OPEN_N]} "
        f"open_s {COLOURS[KIND_OPEN_S]}; opacity {OPACITY_FOOT:g} footpoint -> "
        f"{OPACITY_FAR:g} far end, failed lines opacity 0",
        f"classification {cur['counts']}",
    ] + geo.coord_comments(coord_digits) + extra,
        frame=cur.get("frame", geo.DEFAULT_FRAME))

    if prev is not None:
        prev_rows = _line_rows(prev, dead)
        # Frozen rows take the PREVIOUS trace's geometry AND colour on both
        # sides of the pair, so nothing moves and nothing blends; the fade is
        # entirely in the opacity column (index 3).
        for i in np.nonzero(freeze)[0]:
            cur_rows[i] = prev_rows[i].copy()
        for i in np.nonzero(fade_in)[0]:
            prev_rows[i] = prev_rows[i].copy()
            prev_rows[i][:, 3] = 0.0
        for i in np.nonzero(broke_here)[0]:
            cur_rows[i][:, 3] = 0.0
        n = write_lines_vlc(out_path, prev_rows, next_polylines=cur_rows,
                            comments=comments, coord_fmt=cfmt, val_fmt=vfmt)
    else:
        # The resting frame: no successor, so no morph and nothing to crossfade
        # into. It draws the true final pose, which is what the series is
        # supposed to come to rest on.
        n = write_lines_vlc(out_path, cur_rows, comments=comments,
                            coord_fmt=cfmt, val_fmt=vfmt)
    log.info("field lines: %d lines, %d vertex rows, %d crossfaded -> %s",
             N_SEEDS, n, int(freeze.sum()), out_path)
    return {"path": out_path, "lines": N_SEEDS, "rows": n,
            "counts": cur["counts"], "n_dead": int(dead.sum()),
            "parametric": prev is not None,
            "break": None if breaks is None else break_record(breaks),
            "n_crossfade": int(freeze.sum())}


# --- demo ---------------------------------------------------------------------

def _demo() -> None:
    """Write a demo FieldLines.vlc: nominal state morphing from the storm state."""
    import json

    repo = Path(__file__).resolve().parents[2]
    states = repo / "tests" / "fixtures" / "states"
    cur_state = json.loads((states / "nominal.json").read_text())
    prev_state = json.loads((states / "storm.json").read_text())
    prev = trace_all(prev_state)
    cur = trace_all(cur_state)
    res = export_fieldlines(cur, prev, cur_state,
                            repo / "output" / "magneto" / "demo" / "FieldLines.vlc")
    ok, errors = validate(res["path"], quiet=True)
    log.info("%s: %s %.1f KB %s prev=%s cur=%s", res["path"].name,
             "OK" if ok else "INVALID", res["path"].stat().st_size / 1024,
             errors[:3] if errors else "", prev["counts"], cur["counts"])
    log.info("trace time prev %.2f s cur %.2f s", prev["elapsed_s"],
             cur["elapsed_s"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _demo()
