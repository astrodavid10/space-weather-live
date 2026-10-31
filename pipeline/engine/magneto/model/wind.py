"""Lagrangian solar-wind parcels that FLOW AROUND the magnetosphere.

A FIXED pool of N_PARCELS parcels is advected through three kinematic flow
regimes, all driven by the state object and by the REAL boundary surfaces from
boundaries.py (injected, never imported here):

  1. UPSTREAM   - outside the bow shock: uniform -X GSM at the parcel's own
                  launch speed, exactly as before.
  2. MAGNETOSHEATH - between the bow shock and the magnetopause: the velocity
                  is steered TANGENT to the magnetopause, so parcels deflect
                  around the obstacle instead of dying on it, speed up along
                  the flanks, and stream on down the tail. They leave the scene
                  downtail (x < X_EXIT_RE) or far out on the flank.
  3. CUSP FUNNEL - a tunable fraction (CUSP_CAPTURE_FRAC, default 12%) of the
                  parcels that pass through the dayside high-latitude funnel
                  region are captured onto a cusp GUIDE PATH and slide down
                  toward polar latitudes to ~1.5-2.5 R_E, decelerating and
                  fading out, then recycle. Northern and southern cusps both.

HONESTY (read before putting this on a dome)
--------------------------------------------
This is KINEMATIC DOME ART GROUNDED IN THE REAL GEOMETRY, not MHD. What is
real: the magnetopause and bow shock surfaces are the same Shue (1998) /
Farris & Russell (1994) surfaces the .vla meshes draw, evaluated for THIS
state, so the obstacle the parcels flow around is the obstacle the audience
sees, and it compresses when the storm does. The dipole tilt that orients the
cusp funnels is geopack's, for this epoch. What is NOT real: the streamline
shape, the sheath speed profile, the capture fraction and the descent rate are
constructed to read correctly at dome scale. No continuity equation, no
pressure gradient, no reconnection, no magnetic field along the flow. The
exported files say so in their own header comments.

Anti-streak rule (the CLAUDE.md gotcha): the frame where a parcel recycles
must be opacity 0 on BOTH its D row and its V row, or the morph drags it
backwards across the scene. build_frames() enforces it on frames k and k+1 for
every parcel recycled between them - which now includes parcels that finished
a cusp descent and parcels that left downtail.

Fixed topology: row i is the same parcel in every frame, always N_PARCELS rows,
in every regime. That is what makes the parametric DOTS morph safe.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import recalc_for_state
from .. import geo
from dsexport import validate, write_dots_vlc
from dsexport.vla import fmt_coord

log = logging.getLogger(__name__)

N_PARCELS = 10_000
X_LAUNCH_RE = 240.0        # launch plane, upstream of L1 (~235 R_E)
# THE EXITS MUST SIT OUTSIDE EVERY DRAWN SURFACE, or parcels wink out inside
# the magnetosphere the audience is looking at. Since the boundary meshes were
# un-truncated (2026-08-13) the drawn magnetopause reaches x ~ -110 R_E behind
# Earth at quiet time and ~ -120 in a storm, and the drawn bow shock flares to
# rho ~ 78 R_E at its tail edge - so the old -80 / 65 R_E pair would have
# recycled parcels in full view. Tail shape v2 (2026-09-05) draws BOTH
# surfaces to X_TAIL_RE = -200 R_E (shock rho ~ 76 there) and closes the
# drawn SHELLS with rounded caps whose apexes sit up to ~46 R_E further back
# (0.6 x the shock's 76 R_E end radius), so the tail exit moved to -260. These
# clear everything, with margin.
X_EXIT_RE = -260.0         # downtail exit: past this the parcel has left the show
X_FLOOR_RE = X_EXIT_RE     # legacy alias
RHO_EXIT_RE = 100.0        # flank exit, comfortably outside the drawn shock
RHO_MAX_RE = 35.0          # transverse impact-parameter disk radius
RE_KM = 6371.2             # geopack Earth radius, km
SPEED_JITTER = 0.03        # 1-sigma fractional jitter on launch speed
DEFAULT_SEED = 20260811

# Speed -> colour ramp: dim indigo at 300 km/s to near-white at 800 km/s.
SPEED_LO_KMS, SPEED_HI_KMS = 300.0, 800.0
_C_SLOW = np.array([0.24, 0.16, 0.55])
_C_FAST = np.array([0.96, 0.96, 1.0])

# --- opacity / density modulation ---------------------------------------------
# The state's density drives TWO things at launch, both frozen for a parcel's
# whole life so nothing ever flickers:
#
#   brightness      opacity from OPACITY_FLOOR at a vacuum to 1 at
#                   OPACITY_FULL_N, and
#   apparent count  the fraction of launched parcels that are drawn at all.
#
# Opacity alone is not enough: it saturates at 1 and the storms this build
# exists for run at 15-70 cm^-3, so the whole Gannon main phase would have
# arrived at a flat maximum and the density spike - the thing that slams the
# magnetopause inward - would not read. Dimming AND thinning the quiet wind
# gives the spike somewhere to come from. The pool size never changes: a
# parcel that loses the draw is simply opacity 0 for that life, which is the
# same mechanism the anti-streak rule already uses, so the row count and the
# morph pairing are untouched.
OPACITY_FULL_N = 25.0      # cm^-3 at which the stream is full brightness/count
OPACITY_FLOOR = 0.15
VISIBLE_FRAC_FLOOR = 0.45  # never thinner than this, even in a vacuum
OPACITY_REF_N = OPACITY_FULL_N     # legacy alias
OPACITY_BASE = 1.0

# --- regime 2: magnetosheath steering -----------------------------------------
SHEATH_STEER_P = 1.4       # steering weight = (1 - s)^p, s = 0 at MP, 1 at shock
SHEATH_FAC_NOSE = 0.30     # speed factor at the stagnation point
SHEATH_FLANK_BOOST = 0.35  # extra speed through the flanks (sin^2 theta)
SHEATH_FAC_MIN = 0.12      # nothing ever stalls completely
SHEATH_FAC_MAX = 1.25
MP_MARGIN_RE = 0.06        # parcels are held this far outside the magnetopause
MP_PUSH = 0.55             # outward steering blended in inside the margin
SUBSTEP_MAX_RE = 0.25      # advection substep cap (the no-penetration knob)
MAX_SUBSTEPS = 48
# THE MAGNETOPAUSE MOVES. Between two event steps the storm can expand the
# boundary by several R_E, and it then engulfs parcels that were legitimately
# in the sheath a moment earlier. That is not an integration failure and it
# must not be "fixed" by teleporting the parcel back out to the new surface -
# a jump of several R_E is exactly the streak the anti-streak rule exists to
# prevent. A parcel the boundary sweeps over by more than this is RECYCLED
# instead (opacity 0 on both morph rows, relaunched upstream), which is both
# invisible and honest: that plasma was swallowed by the magnetosphere.
SWEEP_RECYCLE_RE = 0.5

# --- regime 3: cusp funnel ------------------------------------------------------
CUSP_CAPTURE_FRAC = 0.12   # P(capture) for a parcel entering the funnel region
# The funnel THROAT on the magnetopause is a broad, MLT-elongated patch, not a
# circular cone: roughly +-20 deg of magnetic colatitude around 27 deg from the
# dipole axis (the observed exterior-cusp direction, ~63 deg from the Sun line)
# and +-3 h of magnetic local time about noon.
CUSP_ENTRY_ALPHA_DEG = 27.0
CUSP_ALPHA_HALF_DEG = 20.0     # throat half-width in colatitude
CUSP_PHI_HALF_DEG = 45.0       # throat half-width in MLT (+-3 h about noon)
CUSP_CAPTURE_DR_RE = 3.5       # ... and within this height above the magnetopause
CUSP_FOOT_ALPHA_DEG = 13.0     # descent ends at ~77 deg magnetic latitude
CUSP_FOOT_ALPHA_JITTER_DEG = 2.5
CUSP_FOOT_R_LO_RE, CUSP_FOOT_R_HI_RE = 1.5, 2.5
CUSP_V0, CUSP_V1 = 0.28, 0.05  # descent speed factors: entry -> foot (decelerate)
CUSP_PATH_FUDGE = 1.2          # arc length / radial drop
CUSP_FADE_P = 0.8              # opacity ramp exponent along the descent


# --- boundary defaults (production injects the real ones) ----------------------

def default_recycle_radius(theta_rad):
    """Nominal BOW SHOCK conic r = L/(1 + e cos theta), L=25, e=0.8.

    Nose r(0) = 13.89 R_E, terminator r(pi/2) = 25 R_E. Smooth, convex,
    vectorized. Tests and the demo use this; production injects the fit from
    boundaries.py.
    """
    return 25.0 / (1.0 + 0.8 * np.cos(theta_rad))


def default_magnetopause_radius(theta_rad):
    """Nominal MAGNETOPAUSE conic r = 16/(1 + 0.6 cos theta).

    Nose 10.0 R_E, terminator 16 R_E - inside default_recycle_radius at every
    theta, so the default pair is a usable sheath without geopack. Production
    injects boundaries.magnetopause_radius bound to the state.
    """
    return 16.0 / (1.0 + 0.6 * np.cos(theta_rad))


# --- the per-state flow geometry ------------------------------------------------

@dataclass
class Sheath:
    """Everything the flow needs to know about ONE state's magnetosphere.

    Radius lookups are TABLES, not model calls: steering 10 000 parcels through
    tens of advection substeps per frame cannot afford a geopack bisection per
    parcel, so the injected radius functions are sampled once per state on a
    fixed theta grid and interpolated. `tilt_rad` is geopack's dipole tilt for
    the epoch and orients the two cusp funnels.
    """
    theta: np.ndarray
    mp_r: np.ndarray
    mp_dr: np.ndarray
    bs_r: np.ndarray
    tilt_rad: float = 0.0
    # d r_bs / d theta and the Rankine-Hugoniot density ratio. Both default to
    # "no shock jump", so every existing caller and test keeps its old behaviour
    # and only a Sheath built with them set decelerates its parcels.
    bs_dr: "np.ndarray | None" = None
    compression: float = 1.0
    # Beyond the Slavin & Holzer conic's asymptote the theta-parameterized shock
    # radius is CLAMPED to the search bracket, not computed - the obstacle table
    # ends in a balloon (see CLAUDE.md). A parcel out there reads as "inside the
    # shock" only because the lookup saturated, and its apparent shock normal is
    # meaningless. pi = no gating, which is what every pre-2026-09-16 Sheath gets.
    bs_theta_max_rad: float = math.pi

    def mp_radius(self, theta):
        return np.interp(theta, self.theta, self.mp_r)

    def mp_dradius(self, theta):
        return np.interp(theta, self.theta, self.mp_dr)

    def bs_dradius(self, theta):
        """d r_bs / d theta, for the shock's own outward normal. 0 if unknown."""
        if getattr(self, "bs_dr", None) is None:
            return np.zeros_like(np.asarray(theta, dtype=float))
        return np.interp(theta, self.theta, self.bs_dr)

    def bs_radius(self, theta):
        return np.interp(theta, self.theta, self.bs_r)

    @property
    def mp_nose_re(self) -> float:
        return float(self.mp_r[0])

    @property
    def bs_nose_re(self) -> float:
        return float(self.bs_r[0])

    def cusp_basis(self, side: int):
        """(axis, e_noon, e_dawn) for the north (+1) or south (-1) cusp, GSM.

        `axis` is the (signed) dipole axis, `e_noon` the sunward direction made
        perpendicular to it, `e_dawn` completing the right-handed set - so a
        direction at colatitude alpha and azimuth phi about the axis is
        cos(alpha) axis + sin(alpha) (cos(phi) e_noon + sin(phi) e_dawn), with
        phi = 0 the noon meridian. Anchored to the real dipole tilt, so both
        funnels wobble with the epoch exactly as the field lines do.
        """
        psi = float(self.tilt_rad)
        axis = np.array([math.sin(psi), 0.0, math.cos(psi)]) * float(side)
        sun = np.array([1.0, 0.0, 0.0])
        e_noon = sun - axis * float(sun @ axis)
        e_noon /= np.linalg.norm(e_noon)
        return axis, e_noon, np.cross(axis, e_noon)


def sheath_for_state(state: dict, mp_radius_fn=None, bs_radius_fn=None,
                     tilt_rad: float | None = None, n_theta: int = 181) -> Sheath:
    """Sample the injected boundary functions into a Sheath for one state.

    mp_radius_fn / bs_radius_fn take (theta_rad_array) and return radii in R_E;
    boundaries.magnetopause_radius / bow_shock_radius bound to the state are
    exactly that shape (see render._wind_sheath). Defaults are the analytic
    conics above, so this module still runs standalone with no geopack.
    """
    mp_fn = mp_radius_fn or default_magnetopause_radius
    bs_fn = bs_radius_fn or default_recycle_radius
    th = np.linspace(0.0, math.pi, int(n_theta))
    mp = np.asarray(mp_fn(th), dtype=float)
    bs = np.maximum(np.asarray(bs_fn(th), dtype=float), mp + 0.25)
    if tilt_rad is None:
        tilt_rad = 0.0
    return Sheath(theta=th, mp_r=mp, mp_dr=np.gradient(mp, th), bs_r=bs,
                  tilt_rad=float(tilt_rad))


def _as_sheath(sheath) -> Sheath:
    """Accept a Sheath, a bare bow-shock radius callable, or None."""
    if isinstance(sheath, Sheath):
        return sheath
    if sheath is None:
        return sheath_for_state({}, None, None)
    return sheath_for_state({}, None, sheath)          # legacy: a recycle radius


# --- the parcel pool -------------------------------------------------------------

@dataclass
class WindField:
    """Fixed-size parcel pool. Row i is the same parcel forever.

    pos_re     (N, 3) GSM R_E
    speed_kms  (N,)   LAUNCH speed - changes ONLY on recycle (the Lagrangian
                      rule and the colour source). Regime speed factors
                      multiply it, they never overwrite it.
    op_base    (N,)   launch opacity, set from the state density at launch
    mode       (N,)   0 = flowing (upstream or sheath), 1 = cusp descent
    in_zone    (N,)   already offered a cusp capture on this pass
    cusp_*     (N,)   guide-path parameters, meaningful when mode == 1
    """
    pos_re: np.ndarray
    speed_kms: np.ndarray
    rng: np.random.Generator
    op_base: np.ndarray | None = None
    mode: np.ndarray | None = None
    in_zone: np.ndarray | None = None
    cusp_u: np.ndarray | None = None
    cusp_side: np.ndarray | None = None
    cusp_phi: np.ndarray | None = None
    cusp_a0: np.ndarray | None = None
    cusp_a1: np.ndarray | None = None
    cusp_r0: np.ndarray | None = None
    cusp_r1: np.ndarray | None = None
    diag: dict = field(default_factory=dict)

    def __post_init__(self):
        n = len(self.speed_kms)
        if self.op_base is None:
            self.op_base = np.full(n, OPACITY_BASE)
        for name, dtype, fill in (("mode", np.int8, 0), ("in_zone", bool, False),
                                  ("cusp_side", np.int8, 1)):
            if getattr(self, name) is None:
                setattr(self, name, np.full(n, fill, dtype=dtype))
        for name in ("cusp_u", "cusp_phi", "cusp_a0", "cusp_a1",
                     "cusp_r0", "cusp_r1"):
            if getattr(self, name) is None:
                setattr(self, name, np.zeros(n))
        self.diag.setdefault("cusp_draws", 0)
        self.diag.setdefault("cusp_captures", 0)
        self.diag.setdefault("exits_tail", 0)
        self.diag.setdefault("exits_cusp", 0)
        self.diag.setdefault("swept", 0)
        self.diag.setdefault("max_penetration_re", 0.0)
        self.diag.setdefault("min_gap_re", float("inf"))
        self.diag.setdefault("substeps", 0)


def _launch_speeds(rng: np.random.Generator, n: int, bulk_kms: float) -> np.ndarray:
    """Bulk speed with ~3% gaussian jitter, floored at 50 km/s."""
    return np.maximum(50.0, bulk_kms * (1.0 + SPEED_JITTER * rng.standard_normal(n)))


def _launch_transverse(rng: np.random.Generator, n: int):
    """(y, z) GSM R_E: uniform over the disk rho <= RHO_MAX_RE (rho = R*sqrt(u))."""
    rho = RHO_MAX_RE * np.sqrt(rng.random(n))
    phi = 2.0 * np.pi * rng.random(n)
    return rho * np.cos(phi), rho * np.sin(phi)


def launch_opacity(state: dict) -> float:
    """Per-parcel opacity for the CURRENT density (the brightness half)."""
    n = float(state.get("sw", {}).get("density", OPACITY_FULL_N))
    return float(np.clip(OPACITY_FLOOR + (1.0 - OPACITY_FLOOR) * n / OPACITY_FULL_N,
                         OPACITY_FLOOR, 1.0))


def visible_fraction(state: dict) -> float:
    """Fraction of launched parcels drawn at all (the apparent-count half)."""
    n = float(state.get("sw", {}).get("density", OPACITY_FULL_N))
    return float(np.clip(
        VISIBLE_FRAC_FLOOR + (1.0 - VISIBLE_FRAC_FLOOR) * n / OPACITY_FULL_N,
        VISIBLE_FRAC_FLOOR, 1.0))


def _launch_opacities(rng: np.random.Generator, n: int, state: dict) -> np.ndarray:
    """Per-parcel launch opacity: the brightness, thinned by the visible fraction."""
    op = launch_opacity(state)
    return np.where(rng.random(n) < visible_fraction(state), op, 0.0)


def _bulk_speed(state: dict) -> float:
    return float(state.get("sw", {}).get("speed", 400.0))


def new_pool(state: dict, sheath=None, n: int = N_PARCELS,
             seed: int = DEFAULT_SEED) -> WindField:
    """Seeded pool, pre-filled along the flow so frame 0 is already a stream.

    Each parcel gets a jittered copy of the current bulk speed, a uniform-disk
    transverse position and an x drawn uniformly over the whole visible run
    (launch plane -> downtail exit). Draws that land INSIDE the magnetopause are
    re-drawn - a parcel can only be inside the magnetopause by being captured
    into a cusp, never by being born there.
    """
    sh = _as_sheath(sheath)
    rng = np.random.default_rng(seed)
    speed = _launch_speeds(rng, n, _bulk_speed(state))
    y, z = _launch_transverse(rng, n)
    x = X_EXIT_RE + (X_LAUNCH_RE - X_EXIT_RE) * rng.random(n)
    for _ in range(16):
        rho = np.hypot(y, z)
        r = np.hypot(x, rho)
        bad = r < sh.mp_radius(np.arctan2(rho, x)) + MP_MARGIN_RE
        k = int(bad.sum())
        if not k:
            break
        x[bad] = X_EXIT_RE + (X_LAUNCH_RE - X_EXIT_RE) * rng.random(k)
    else:                                                  # pragma: no cover
        x[bad] = X_LAUNCH_RE
    return WindField(pos_re=np.column_stack([x, y, z]), speed_kms=speed, rng=rng,
                     op_base=_launch_opacities(rng, n, state))


# --- regime kinematics -----------------------------------------------------------

def _spherical(pos: np.ndarray):
    """(r, rho, theta, r_hat, theta_hat) for GSM positions, theta from +X."""
    x = pos[:, 0]
    rho = np.hypot(pos[:, 1], pos[:, 2])
    r = np.maximum(np.hypot(x, rho), 1e-6)
    theta = np.arctan2(rho, x)
    safe = np.maximum(rho, 1e-9)
    rho_hat = np.zeros_like(pos)
    rho_hat[:, 1] = pos[:, 1] / safe
    rho_hat[:, 2] = pos[:, 2] / safe
    flat = rho < 1e-9
    if flat.any():                       # on-axis: any transverse direction will do
        rho_hat[flat, 1] = 1.0
        rho_hat[flat, 2] = 0.0
    r_hat = pos / r[:, None]
    ct, st = np.cos(theta)[:, None], np.sin(theta)[:, None]
    x_hat = np.zeros_like(pos)
    x_hat[:, 0] = 1.0
    theta_hat = -st * x_hat + ct * rho_hat
    return r, rho, theta, r_hat, theta_hat


def _normalize(v: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1)
    bad = n < 1e-3
    out = np.where(bad[:, None], fallback, v)
    n = np.maximum(np.linalg.norm(out, axis=1), 1e-12)
    return out / n[:, None]


def _shock_jump(sheath: Sheath, r, theta, r_hat, theta_hat, u):
    """v2/v1 at each parcel, obliquity-weighted. 1.0 when the sheath has no ratio.

    cos^2 of the angle between the upstream flow and the shock's own outward
    normal weights the jump: 1 at the nose (a normal shock, full compression),
    0 on the asymptote (a Mach cone, no jump). The shock normal is built the same
    way the magnetopause's is, from grad(r - R(theta)).
    """
    ratio = float(getattr(sheath, "compression", 1.0) or 1.0)
    if ratio <= 1.0:
        return np.ones(len(r))
    dbs = sheath.bs_dradius(theta) if hasattr(sheath, "bs_dradius") else np.zeros_like(theta)
    n_bs = _normalize(r_hat - (dbs / np.maximum(r, 1e-9))[:, None] * theta_hat, r_hat)
    cos2 = np.einsum("ij,ij->i", u, n_bs) ** 2

    # Fade the jump out approaching the conic's asymptote and switch it off past
    # it. Two reasons, and the second is the one that bites: physically the shock
    # relaxes into a Mach cone with no jump across it, and numerically the radius
    # lookup is clamped out there, which made every parcel 100+ R_E downtail read
    # as freshly shocked with cos2 ~ 0.97 and cost them 13% of their transit time
    # (caught by wind.selftest's ballistic far-flank check, 2026-09-16).
    th_max = float(getattr(sheath, "bs_theta_max_rad", math.pi))
    if th_max < math.pi:
        taper = np.clip((th_max - np.asarray(theta)) / SHOCK_TAPER_RAD, 0.0, 1.0)
        cos2 = cos2 * taper

    return 1.0 - (1.0 - 1.0 / ratio) * np.power(cos2, SHOCK_OBLIQUITY_P / 2.0)


# --- the shock jump -------------------------------------------------------------
# Only the flow component NORMAL to the shock is compressed. Without that
# weighting a parcel crossing the flaring shock 90 R_E out on the flank would
# lose ~74% of its speed, which is wrong by construction: far down the flank the
# shock relaxes into a Mach cone across which there is no jump at all.
SHOCK_OBLIQUITY_P = 2.0
# Cap on the colour gain. The bunching is already physical and free (parcels
# slow, so they crowd); this is the separate HEATING, and it is capped so the
# sheath cannot bloom out of the ramp the rest of the layer uses.
SHOCK_BRIGHTEN_MAX = 1.6
# Angular width of the fade to no-jump before the conic's asymptote.
SHOCK_TAPER_RAD = math.radians(25.0)


def flow_velocity(pos: np.ndarray, speed_kms: np.ndarray, sheath: Sheath):
    """Velocity (R_E/s) for flowing parcels, plus (gap_re, r, theta) diagnostics.

    Upstream of the bow shock the answer is uniform -X. Inside it the velocity
    is steered off the magnetopause: take -X, remove the component driving the
    parcel INTO the surface (weighted by how deep into the sheath it is), and
    scale the speed by a stagnation-at-the-nose / faster-through-the-flanks
    profile. At the stagnation point the tangential projection collapses, so
    the fallback is theta_hat - the direction that carries a nose parcel around
    toward the flank instead of letting it pile up.
    """
    r, _rho, theta, r_hat, theta_hat = _spherical(pos)
    r_mp = sheath.mp_radius(theta)
    r_bs = sheath.bs_radius(theta)
    gap = r - r_mp

    # outward normal of the magnetopause surface: grad(r - R(theta))
    n_vec = r_hat - (sheath.mp_dradius(theta) / r)[:, None] * theta_hat
    n_hat = _normalize(n_vec, r_hat)

    u = np.zeros_like(pos)
    u[:, 0] = -1.0
    s = np.clip(gap / np.maximum(r_bs - r_mp, 1e-6), 0.0, 1.0)
    inside_shock = r < r_bs
    w = np.where(inside_shock, np.power(1.0 - s, SHEATH_STEER_P), 0.0)

    dot = np.einsum("ij,ij->i", u, n_hat)
    v_dir = u - (w * np.minimum(dot, 0.0))[:, None] * n_hat
    # inside the safety margin, add an explicit outward push
    push = np.clip((MP_MARGIN_RE - gap) / MP_MARGIN_RE, 0.0, 1.0) * MP_PUSH
    v_dir = v_dir + push[:, None] * n_hat
    v_hat = _normalize(v_dir, theta_hat)

    base = SHEATH_FAC_NOSE + (1.0 - SHEATH_FAC_NOSE) * 0.5 * (1.0 - np.cos(theta))
    fac = np.clip(base * (1.0 + SHEATH_FLANK_BOOST * np.sin(theta) ** 2),
                  SHEATH_FAC_MIN, SHEATH_FAC_MAX)
    # AT the shock (s = 1) the speed is now the post-shock value, not the
    # upstream one: that discontinuity IS the shock. At the magnetopause (s = 0)
    # the existing stagnation profile is untouched.
    jump = _shock_jump(sheath, r, theta, r_hat, theta_hat, u)
    fac = np.where(inside_shock, fac + (jump - fac) * s, 1.0)

    v = v_hat * (speed_kms * fac / RE_KM)[:, None]
    return v, gap, r, theta, inside_shock


def cusp_positions(wind: WindField, sheath: Sheath, idx=None) -> np.ndarray:
    """Guide-path position (GSM R_E) of the cusp-captured parcels in `idx`.

    The path is a documented analytic curve, NOT a traced field line: from the
    capture point (its own colatitude about the dipole axis and its own radius)
    down to CUSP_FOOT_ALPHA_DEG at 1.5-2.5 R_E, in the parcel's own magnetic
    meridian. It is anchored to geopack's dipole tilt for the epoch and to the
    magnetopause radius at capture time, so the funnel leans and moves inward
    with the storm - which is why it is cheap enough to evaluate every substep,
    unlike re-tracing the cusp field-line family.
    """
    if idx is None:
        idx = np.flatnonzero(wind.mode == 1)
    out = np.zeros((len(idx), 3))
    if not len(idx):
        return out
    u = np.clip(wind.cusp_u[idx], 0.0, 1.0)
    alpha = wind.cusp_a0[idx] + (wind.cusp_a1[idx] - wind.cusp_a0[idx]) * u
    rad = wind.cusp_r0[idx] + (wind.cusp_r1[idx] - wind.cusp_r0[idx]) * u
    phi = wind.cusp_phi[idx]
    for side in (1, -1):
        m = wind.cusp_side[idx] == side
        if not m.any():
            continue
        axis, e_noon, e_dawn = sheath.cusp_basis(side)
        ca, sa = np.cos(alpha[m])[:, None], np.sin(alpha[m])[:, None]
        cp, sp = np.cos(phi[m])[:, None], np.sin(phi[m])[:, None]
        d = ca * axis[None, :] + sa * (cp * e_noon[None, :] + sp * e_dawn[None, :])
        out[m] = d * rad[m][:, None]
    return out


def _try_cusp_capture(wind: WindField, sheath: Sheath, r, theta, gap,
                      inside_shock) -> None:
    """Offer a capture to every parcel entering the funnel region, once per pass."""
    flowing = wind.mode == 0
    pos = wind.pos_re
    norm = np.maximum(np.linalg.norm(pos, axis=1), 1e-9)
    alpha_entry = math.radians(CUSP_ENTRY_ALPHA_DEG)
    d_alpha = math.radians(CUSP_ALPHA_HALF_DEG)
    d_phi = math.radians(CUSP_PHI_HALF_DEG)
    radial = flowing & inside_shock & (gap >= -MP_MARGIN_RE) \
        & (gap <= CUSP_CAPTURE_DR_RE)

    in_zone = np.zeros(len(pos), dtype=bool)
    side_of = np.zeros(len(pos), dtype=np.int8)
    for side in (1, -1):
        axis, e_noon, e_dawn = sheath.cusp_basis(side)
        alpha = np.arccos(np.clip((pos @ axis) / norm, -1.0, 1.0))
        phi = np.arctan2(pos @ e_dawn, pos @ e_noon)
        hit = radial & (np.abs(alpha - alpha_entry) <= d_alpha) \
            & (np.abs(phi) <= d_phi)
        side_of[hit & ~in_zone] = side
        in_zone |= hit

    fresh = np.flatnonzero(in_zone & ~wind.in_zone)
    wind.in_zone = in_zone
    if not len(fresh):
        return
    draw = wind.rng.random(len(fresh)) < CUSP_CAPTURE_FRAC
    wind.diag["cusp_draws"] += int(len(fresh))
    taken = fresh[draw]
    if not len(taken):
        return
    wind.diag["cusp_captures"] += int(len(taken))

    side = side_of[taken]
    a0 = np.empty(len(taken))
    phi = np.empty(len(taken))
    for s in (1, -1):
        m = side == s
        if not m.any():
            continue
        axis, e_noon, e_dawn = sheath.cusp_basis(s)
        p = wind.pos_re[taken[m]]
        rr = np.maximum(np.linalg.norm(p, axis=1), 1e-9)
        a0[m] = np.arccos(np.clip((p @ axis) / rr, -1.0, 1.0))
        phi[m] = np.arctan2(p @ e_dawn, p @ e_noon)
    jitter = wind.rng.uniform(-1.0, 1.0, len(taken)) * CUSP_FOOT_ALPHA_JITTER_DEG
    wind.mode[taken] = 1
    wind.cusp_side[taken] = side
    wind.cusp_u[taken] = 0.0
    wind.cusp_a0[taken] = a0
    wind.cusp_phi[taken] = phi
    wind.cusp_a1[taken] = np.radians(CUSP_FOOT_ALPHA_DEG + jitter)
    wind.cusp_r0[taken] = np.linalg.norm(wind.pos_re[taken], axis=1)
    wind.cusp_r1[taken] = wind.rng.uniform(CUSP_FOOT_R_LO_RE, CUSP_FOOT_R_HI_RE,
                                           len(taken))


def _relaunch(wind: WindField, state: dict, mask: np.ndarray) -> None:
    """Send recycled parcels back to the launch plane with CURRENT conditions."""
    k = int(mask.sum())
    if not k:
        return
    wind.speed_kms[mask] = _launch_speeds(wind.rng, k, _bulk_speed(state))
    wind.op_base[mask] = _launch_opacities(wind.rng, k, state)
    y, z = _launch_transverse(wind.rng, k)
    wind.pos_re[mask, 0] = X_LAUNCH_RE
    wind.pos_re[mask, 1] = y
    wind.pos_re[mask, 2] = z
    wind.mode[mask] = 0
    wind.in_zone[mask] = False
    wind.cusp_u[mask] = 0.0


def step(wind: WindField, dt_s: float, state: dict, sheath=None,
         substep_max_re: float = SUBSTEP_MAX_RE) -> np.ndarray:
    """Advance dt_s seconds through the three regimes. Returns the recycled mask.

    The step is split into internal substeps small enough that a sheath parcel
    moves at most `substep_max_re` per substep - that is the knob the
    no-penetration assertion in selftest() rides on, because the steering is
    evaluated at the start of each substep and a long step would let a parcel
    cut the corner straight through the magnetopause.
    """
    sh = _as_sheath(sheath)
    n = len(wind.speed_kms)
    recycled = np.zeros(n, dtype=bool)

    v_max = float(wind.speed_kms.max()) * SHEATH_FAC_MAX / RE_KM
    n_sub = int(min(MAX_SUBSTEPS,
                    max(1, math.ceil(v_max * float(dt_s) / max(substep_max_re, 1e-3)))))
    h = float(dt_s) / n_sub
    wind.diag["substeps"] = n_sub

    for _ in range(n_sub):
        # (0) The boundary may have MOVED since the last substep (a new state's
        # magnetopause). Parcels it engulfed are recycled, not dragged out.
        flowing = wind.mode == 0
        r_s, _rho_s, th_s, _rh, _th = _spherical(wind.pos_re)
        swept = flowing & ((r_s - sh.mp_radius(th_s)) < -SWEEP_RECYCLE_RE)
        if swept.any():
            wind.diag["swept"] += int(swept.sum())
            _relaunch(wind, state, swept)
            recycled |= swept

        # (1) Flow. `gap` is measured BEFORE the move, so the penetration
        # statistic below is about the integration - a parcel that was outside
        # and ended up inside - and never about a boundary that moved.
        flowing = wind.mode == 0
        v, gap, r, theta, inside_shock = flow_velocity(
            wind.pos_re, wind.speed_kms, sh)
        if flowing.any():
            wind.pos_re[flowing] += v[flowing] * h
            pos_f = wind.pos_re[flowing]
            rho = np.hypot(pos_f[:, 1], pos_f[:, 2])
            rf = np.maximum(np.hypot(pos_f[:, 0], rho), 1e-6)
            th = np.arctan2(rho, pos_f[:, 0])
            mp = sh.mp_radius(th)
            depth = mp - rf
            was_out = gap[flowing] >= 0.0
            if was_out.any():
                wind.diag["max_penetration_re"] = max(
                    wind.diag["max_penetration_re"],
                    float(depth[was_out].max()))
                wind.diag["min_gap_re"] = min(
                    wind.diag["min_gap_re"], float((rf - mp)[was_out].min()))
            over = depth > 0.0
            if over.any():                      # safety net, should be rare
                scale = (mp[over] + MP_MARGIN_RE) / rf[over]
                pos_f[over] *= scale[:, None]
                wind.pos_re[flowing] = pos_f

        cusped = wind.mode == 1
        if cusped.any():
            idx = np.flatnonzero(cusped)
            u = wind.cusp_u[idx]
            vfac = CUSP_V0 + (CUSP_V1 - CUSP_V0) * u
            length = np.maximum(
                np.abs(wind.cusp_r0[idx] - wind.cusp_r1[idx]) * CUSP_PATH_FUDGE, 0.25)
            wind.cusp_u[idx] = u + (wind.speed_kms[idx] * vfac / RE_KM) * h / length
            wind.pos_re[idx] = cusp_positions(wind, sh, idx)

        _try_cusp_capture(wind, sh, r, theta, gap, inside_shock)

        x = wind.pos_re[:, 0]
        rho = np.hypot(wind.pos_re[:, 1], wind.pos_re[:, 2])
        gone_tail = (wind.mode == 0) & ((x < X_EXIT_RE)
                                        | ((x < 20.0) & (rho > RHO_EXIT_RE)))
        gone_cusp = (wind.mode == 1) & (wind.cusp_u >= 1.0)
        gone = gone_tail | gone_cusp
        if gone.any():
            wind.diag["exits_tail"] += int(gone_tail.sum())
            wind.diag["exits_cusp"] += int(gone_cusp.sum())
            _relaunch(wind, state, gone)
            recycled |= gone
    return recycled


def _speed_rgb(speed_kms: np.ndarray) -> np.ndarray:
    """Per-parcel colour: dim indigo (300 km/s) -> near-white (800 km/s)."""
    f = np.clip((speed_kms - SPEED_LO_KMS) / (SPEED_HI_KMS - SPEED_LO_KMS), 0.0, 1.0)
    return _C_SLOW + f[:, None] * (_C_FAST - _C_SLOW)


def snapshot(wind: WindField) -> np.ndarray:
    """(N, 7) rows: x, y, z GSM R_E, opacity, r, g, b - one frame of the stream.

    Opacity is the parcel's launch opacity (density-modulated), ramped down
    along a cusp descent so a funnelled parcel dims out at the foot instead of
    vanishing mid-air.
    """
    n = len(wind.speed_kms)
    rows = np.empty((n, 7))
    rows[:, :3] = wind.pos_re
    fade = np.where(wind.mode == 1,
                    np.power(np.clip(1.0 - wind.cusp_u, 0.0, 1.0), CUSP_FADE_P), 1.0)
    rows[:, 3] = np.clip(wind.op_base * fade, 0.0, 1.0)
    rows[:, 4:7] = _speed_rgb(wind.speed_kms)
    return rows


def build_frames(state: dict, sheath=None, n_frames: int = 30,
                 frame_dt_s: float = 10.0, pool: WindField | None = None):
    """Bake n_frames of the stream. Returns (frames, recycled, speeds).

    frames   : list of (N, 7) arrays (x, y, z GSM R_E, opacity, r, g, b)
    recycled : list of n_frames-1 bool masks - parcels recycled between
               frame k and k+1
    speeds   : (n_frames, N) per-parcel LAUNCH speed history (for selftest)

    The anti-streak rule: a parcel recycled between k and k+1 is opacity 0 in
    BOTH frame k and frame k+1, hiding both halves of its D/V morph pair.
    """
    sh = _as_sheath(sheath)
    if pool is None:
        pool = new_pool(state, sh)
    n = len(pool.speed_kms)

    frames, recycled, speeds = [], [], np.empty((n_frames, n))
    for k in range(n_frames):
        speeds[k] = pool.speed_kms
        frames.append(snapshot(pool))
        if k < n_frames - 1:
            recycled.append(step(pool, frame_dt_s, state, sh))
    for k, mask in enumerate(recycled):
        frames[k][mask, 3] = 0.0        # D side of the morph pair
        frames[k + 1][mask, 3] = 0.0    # V side of the morph pair
    return frames, recycled, speeds


# --- morph-clipping check --------------------------------------------------------

def morph_clip_stats(pos_a: np.ndarray, pos_b: np.ndarray, sheath: Sheath,
                     mask=None, near_re: float = 30.0,
                     tol_re: float = 0.5) -> dict:
    """How far a LINEAR morph between two wind frames cuts the magnetopause.

    Digistar interpolates a parcel's D row straight to its V row. The real
    trajectory bends around the obstacle, so the chord can pass inside it if the
    frames are too far apart in time. This samples the chord MIDPOINT of every
    near-Earth parcel and measures how deep inside the magnetopause it lands -
    the number that decides the wind sub-frame cadence.

    `mask` should exclude cusp-captured parcels: they are legitimately inside
    the magnetopause and their descent is not a clipping error. Parcels that
    are inside the boundary at EITHER endpoint are dropped here too, for the
    same reason plus one more: a parcel the moving boundary engulfed is
    recycled and therefore drawn at opacity 0 through that whole morph, so it
    cannot clip anything the audience can see.
    """
    a = np.asarray(pos_a, dtype=float)[:, :3]
    b = np.asarray(pos_b, dtype=float)[:, :3]
    near = (np.linalg.norm(a, axis=1) < near_re) | (np.linalg.norm(b, axis=1) < near_re)
    if mask is not None:
        near &= np.asarray(mask, dtype=bool)
    for p in (a, b):
        rho = np.hypot(p[:, 1], p[:, 2])
        r = np.maximum(np.hypot(p[:, 0], rho), 1e-6)
        near &= (r - sheath.mp_radius(np.arctan2(rho, p[:, 0]))) >= -0.05
    if not near.any():
        return {"n_near": 0, "n_clipped": 0, "max_depth_re": 0.0,
                "p99_depth_re": 0.0, "tol_re": tol_re, "ok": True}
    mid = 0.5 * (a[near] + b[near])
    rho = np.hypot(mid[:, 1], mid[:, 2])
    r = np.maximum(np.hypot(mid[:, 0], rho), 1e-6)
    depth = sheath.mp_radius(np.arctan2(rho, mid[:, 0])) - r
    depth = np.maximum(depth, 0.0)
    return {"n_near": int(near.sum()), "n_clipped": int((depth > tol_re).sum()),
            "max_depth_re": float(depth.max()),
            "p99_depth_re": float(np.percentile(depth, 99.0)),
            "tol_re": tol_re, "ok": bool(depth.max() <= tol_re)}


# --- geopack-dependent pieces ---------------------------------------------------

def _epoch_unix(state: dict) -> float:
    return datetime.fromisoformat(
        state["epoch"].replace("Z", "+00:00")).timestamp()


def _recalc(state: dict) -> float:
    """geopack.recalc at the state epoch, through the ONE gateway.

    Must go through model.recalc_for_state rather than calling gp.recalc
    directly: the gateway is also what records the epoch for the J2000 export
    frame's precession term, and a direct call would leave the frame code
    unable to tell which date it is baking.
    """
    return recalc_for_state(state)


def l1_marker(state: dict, frame=geo.DEFAULT_FRAME) -> np.ndarray:
    """(1, 7) bright white dot at the L1 spacecraft, export-frame light years.

    GSE -> GSM via geo.gse_to_gsm_matrix (recalc'd here for the state epoch),
    then GSM R_E -> the export frame.
    """
    _recalc(state)
    gsm = geo.gse_to_gsm_matrix() @ np.asarray(
        state["propagation"]["l1_gse_re"], dtype=float)
    x, y, z = geo.gsm_re_to_frame(gsm, frame)
    return np.array([[x, y, z, 1.0, 1.0, 1.0, 1.0]])


def stream_comments(state: dict, sheath: Sheath, n_parcels: int,
                    frame_dt_s: float) -> list[str]:
    """The honest description of what the parcel layer is and is not."""
    return [
        "solar wind parcel stream, three kinematic regimes: uniform -X upstream "
        "of the bow shock, steered tangent to the magnetopause inside the "
        "magnetosheath, and a cusp funnel that carries a fraction to polar "
        "latitudes",
        f"{n_parcels} parcels, frame step {frame_dt_s:g} s of real flow",
        f"obstacle = THIS state's Shue magnetopause (nose {sheath.mp_nose_re:.2f} "
        f"R_E) inside THIS state's Farris-Russell bow shock (nose "
        f"{sheath.bs_nose_re:.2f} R_E), both injected from boundaries.py",
        f"cusp funnels anchored to the dipole tilt "
        f"{math.degrees(sheath.tilt_rad):+.2f} deg, throat at "
        f"{CUSP_ENTRY_ALPHA_DEG:g}+-{CUSP_ALPHA_HALF_DEG:g} deg colatitude x "
        f"+-{CUSP_PHI_HALF_DEG / 15:g} h MLT, capture fraction "
        f"{CUSP_CAPTURE_FRAC:g} of parcels entering it, descent "
        f"to {CUSP_FOOT_R_LO_RE:g}-{CUSP_FOOT_R_HI_RE:g} R_E with an opacity ramp",
        "HONEST LABEL: kinematic dome art on real boundary geometry - the "
        "surfaces, their storm response and the dipole tilt are the model's, "
        "the streamline shape and speeds are constructed, there is no MHD here",
        f"density modulation: launch opacity {launch_opacity(state):.3f} and "
        f"{visible_fraction(state) * 100:.0f}% of parcels drawn, from the state "
        f"density {float(state['sw']['density']):g} per cc (full brightness and "
        f"full count at {OPACITY_FULL_N:g} per cc). Parcel COUNT is fixed at "
        f"{n_parcels} in every frame - the thinning is opacity 0, not a "
        f"different row count",
        "recycled parcels are opacity 0 on both morph rows (anti-streak rule)",
    ]


def export_wind(state: dict, sheath=None, out_dir="output/magneto/demo",
                n_frames: int = 30, frame_dt_s: float = 10.0,
                frame=geo.DEFAULT_FRAME) -> list[Path]:
    """Write WindParticles_0000..NNNN.vlc + L1Marker.vlc. Returns the paths.

    Frame k is PARAMETRIC with V rows = frame k+1; the LAST frame is
    NON_PARAMETRIC (no successor - loop closure is the .ds player's job).
    All frames share the single state epoch: geopack is recalc'd once and one
    GSM->export-frame conversion handles the whole (F, N, 3) stack.
    """
    sh = _as_sheath(sheath)
    out_dir = Path(out_dir)
    _recalc(state)

    frames, _, _ = build_frames(state, sh, n_frames=n_frames, frame_dt_s=frame_dt_s)
    pts = geo.gsm_re_to_frame(np.stack([f[:, :3] for f in frames]), frame)
    rows = [np.column_stack([pts[k], frames[k][:, 3:7]]) for k in range(n_frames)]

    base_comments = [
        f"epoch {state['epoch']} speed {state['sw']['speed']:g} km/s "
        f"density {state['sw']['density']:g} per cc",
    ] + stream_comments(state, sh, len(rows[0]), frame_dt_s) \
      + geo.frame_comments(frame)
    paths = []
    for k in range(n_frames):
        nxt = rows[k + 1] if k < n_frames - 1 else None
        tail = (f"frame {k:04d} of {n_frames}, V rows = frame {k + 1:04d}"
                if nxt is not None else
                f"frame {k:04d} of {n_frames}, final frame, "
                "loop closure handled by the .ds player")
        path = out_dir / f"WindParticles_{k:04d}.vlc"
        write_dots_vlc(path, rows[k], next_points=nxt,
                       comments=base_comments + [tail], coord_fmt=fmt_coord)
        paths.append(path)

    l1_path = out_dir / "L1Marker.vlc"
    write_dots_vlc(l1_path, l1_marker(state, frame), coord_fmt=fmt_coord,
                   comments=[f"L1 monitor {state['source']} at GSE "
                             f"{state['propagation']['l1_gse_re']} R_E, "
                             f"epoch {state['epoch']}"] + geo.frame_comments(frame))
    paths.append(l1_path)
    log.info("wind export: %d frame files + L1 marker -> %s", n_frames, out_dir)
    return paths


# --- selftest -------------------------------------------------------------------

def selftest(state: dict, sheath=None, n_steps: int = 400,
             dt_s: float = 20.0) -> dict:
    """Physics/topology assertions; returns the measured numbers.

    1. Row count constant across all baked frames (fixed pool).
    2. A parcel's LAUNCH speed changes ONLY on recycle frames.
    3. Recycled parcels are opacity 0 on BOTH sides of their morph pair.
    4. NO PENETRATION: no parcel ever gets more than 0.3 R_E inside the
       magnetopause during the advection (measured before the safety clamp).
    5. CUSP FRACTION: the realized capture rate among parcels entering the
       funnel region matches CUSP_CAPTURE_FRAC within binomial tolerance, and
       some parcels really do finish a descent.
    6. TAILWARD EXIT: the stream drains downtail - most recycles are tail
       exits, and the far-flank parcels that never meet the shock transit
       launch -> exit at exactly their own speed (within 1%).
    """
    sh = _as_sheath(sheath)

    frames, recycled, speeds = build_frames(state, sh)
    n = frames[0].shape[0]
    assert all(f.shape == (n, 7) for f in frames), "row count varies"
    for k, mask in enumerate(recycled):
        changed = speeds[k + 1] != speeds[k]
        assert np.array_equal(changed, mask), \
            f"frame {k}: launch speed changed off-recycle (or a recycle kept it)"
        assert (frames[k][mask, 3] == 0.0).all(), f"frame {k}: D-side not hidden"
        assert (frames[k + 1][mask, 3] == 0.0).all(), f"frame {k + 1}: V-side not hidden"

    # A long run on a fresh pool: enough transits to exercise all three regimes.
    pool = new_pool(state, sh, seed=DEFAULT_SEED + 1)
    launch_t = np.full(n, np.nan)
    launch_v = pool.speed_kms.copy()
    launch_rho = np.hypot(pool.pos_re[:, 1], pool.pos_re[:, 2])
    measured, expected = [], []
    cusp_now = []
    t = 0.0
    for _ in range(n_steps):
        mask = step(pool, dt_s, state, sh)
        t += dt_s
        cusp_now.append(int((pool.mode == 1).sum()))
        # far-flank parcels never meet the shock: a pure ballistic check
        done = mask & np.isfinite(launch_t) & (launch_rho > RHO_MAX_RE * 0.9)
        measured.append(t - launch_t[done])
        expected.append((X_LAUNCH_RE - X_EXIT_RE) * RE_KM / launch_v[done])
        launch_t[mask] = t
        launch_v[mask] = pool.speed_kms[mask]
        launch_rho[mask] = np.hypot(pool.pos_re[mask, 1], pool.pos_re[mask, 2])
    assert len(pool.speed_kms) == n, "parcel count not preserved"

    d = pool.diag
    draws, caps = int(d["cusp_draws"]), int(d["cusp_captures"])
    frac = caps / draws if draws else 0.0
    measured = np.concatenate(measured)
    expected = np.concatenate(expected)
    result = {
        "n_parcels": n,
        "n_frames": len(frames),
        "recycled_per_frame_mean": float(np.mean([m.sum() for m in recycled])),
        "mp_nose_re": sh.mp_nose_re,
        "bs_nose_re": sh.bs_nose_re,
        "substeps_per_step": int(d["substeps"]),
        "max_penetration_re": float(d["max_penetration_re"]),
        "min_gap_re": float(d["min_gap_re"]),
        "cusp_draws": draws,
        "cusp_captures": caps,
        "cusp_fraction": frac,
        "cusp_resident_mean": float(np.mean(cusp_now)),
        "cusp_resident_max": int(max(cusp_now)),
        "exits_tail": int(d["exits_tail"]),
        "exits_cusp": int(d["exits_cusp"]),
        "swept_by_boundary": int(d["swept"]),
        "n_tracked": int(measured.size),
        "transit_expected_s": float(expected.mean()) if measured.size else 0.0,
        "transit_measured_s": float(measured.mean()) if measured.size else 0.0,
    }
    result["transit_ratio"] = (result["transit_measured_s"]
                               / result["transit_expected_s"]
                               if result["transit_expected_s"] else 1.0)

    assert result["max_penetration_re"] < 0.3, \
        f"parcels cut {result['max_penetration_re']:.3f} R_E into the magnetopause"
    assert draws > 200, f"too few funnel encounters to judge the fraction ({draws})"
    tol = max(0.03, 4.0 * math.sqrt(CUSP_CAPTURE_FRAC * (1 - CUSP_CAPTURE_FRAC)
                                    / draws))
    assert abs(frac - CUSP_CAPTURE_FRAC) < tol, \
        f"cusp capture fraction {frac:.4f} off {CUSP_CAPTURE_FRAC} by > {tol:.4f}"
    assert result["exits_cusp"] > 0, "no parcel ever completed a cusp descent"
    assert result["exits_tail"] > result["exits_cusp"], \
        "the stream must drain downtail, not into the cusps"
    assert measured.size > 50, f"too few tracked transits ({measured.size})"
    assert abs(result["transit_ratio"] - 1.0) < 0.01, \
        f"far-flank transit ratio {result['transit_ratio']:.4f} off by > 1%"
    log.info("wind selftest: %s", result)
    return result


# --- demo -------------------------------------------------------------------------

def _demo() -> None:
    """Bake the 30-frame demo stream + L1 marker from the nominal state."""
    repo = Path(__file__).resolve().parents[2]
    state = json.loads(
        (repo / "tests" / "fixtures" / "states" / "nominal.json").read_text())
    numbers = selftest(state)
    log.info("selftest: penetration %.4f R_E, cusp fraction %.4f (%d/%d), "
             "tail exits %d, transit ratio %.4f",
             numbers["max_penetration_re"], numbers["cusp_fraction"],
             numbers["cusp_captures"], numbers["cusp_draws"],
             numbers["exits_tail"], numbers["transit_ratio"])
    paths = export_wind(state, out_dir=repo / "output" / "magneto" / "demo")
    for p in paths:
        ok, errors = validate(p, quiet=True)
        log.info("%s: %.1f KB, valid=%s %s", p.name, p.stat().st_size / 1024,
                 ok, errors[:3] if errors else "")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _demo()
