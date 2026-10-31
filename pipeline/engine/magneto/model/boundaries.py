"""Magnetopause + bow-shock surfaces for Digistar, driven by the state object.

Two analytic boundaries, both axisymmetric about the GSM X axis, both emitted
on the SAME fixed mesh TOPOLOGY (N_THETA x N_PHI from magneto.model.__init__ -
47 rings x 37 azimuths + 36 meridians x 48 samples) so consecutive exports of a
layer morph ring-for-ring - but, since 2026-08-13, no longer on the same mesh
PARAMETER:

* Magnetopause - Shue et al. (1998), taken from geopack, parameterized by
  THETA from +X_GSM. The Shue functional form is NEVER re-implemented here:
  geopack's shuetal_mgnp() returns an inside/outside flag for a probe point,
  and magnetopause_radius() bisects along a ray until that flag flips. The
  boundary we draw is therefore the same surface geopack's own magnetopause
  code believes in, bug-for-bug. Shue is star-shaped about Earth, so a ray
  parameterization is exact for it; it needs only a bisection bracket wide
  enough to hold the crossing (200 R_E - see model/__init__).
* Bow shock - Farris & Russell (1994) gas-dynamic standoff ratio anchored to
  the Shue nose, wrapped on the Slavin & Holzer (1981) conic
  r(theta) = L / (1 + e cos theta), e = 1.16, L = (1 + e) * standoff. That
  conic is a HYPERBOLA: its asymptote sits at theta = acos(-1/e) = 149.5 deg,
  and past it the ray from Earth never meets the surface at all. So the drawn
  shock is parameterized by X STATION, not by theta (bow_shock_mesh). In
  (x, rho) the same conic is exact, closed-form and open-ended - substituting
  r = L - e x into r^2 = x^2 + rho^2 gives

      rho^2(x) = L^2 - 2 L e x + (e^2 - 1) x^2

  which flares to rho -> sqrt(e^2-1) |x| = 0.588 |x| downstream instead of
  closing. Before this change a third of the drawn shock (16 of 48 rings at
  quiet time, from theta ~ 112 deg on) was a 60 R_E spherical cap that CLOSED
  where the real shock OPENS.

Tail shape v2 (2026-09-05): both fits are extrapolations down-tail, so past a
blend station each drawn surface follows the OBSERVED distant tail instead -
the magnetopause (rays to THETA_BLEND_RAD) relaxes to a MP_TAIL_RADIUS_FACTOR x
standoff near-cylinder, the bow shock (conic to BS_BLEND_X_RE) bends into a
BS_TAIL_SLOPE Mach cone; both are C1 at the blend (_tail_blend) and both run to
X_TAIL_RE. Constants in model/__init__. The shock now encloses the tail all
the way down instead of stopping, 86 R_E wide, at x = -60.

The two layers are separate files under separate holders and are never morphed
against each other, so their parameters do not have to agree - only each
layer's own ring index has to mean the same thing from epoch to epoch, and it
does. The exported header comments say which parameter each file used.

Solar-wind pressure is ALWAYS handed to geopack as (xn_pd=pdyn_npa, vel=-1.0)
so geopack uses the state's proton-only Pdyn instead of recomputing it from
n and v with its built-in 4% alpha assumption (see STATE_SCHEMA).

Physics is GSM in R_E; exports go out through magneto.geo's export-frame
helper, in LIGHT YEARS - 'j2000' by default (sun-fixed geometry in the dome's
own equatorial axes), or 'gsm' (identity rotation) or 'ecef', the rotating
ones baked at the state's epoch through the model.recalc_for_state() gateway.
"""
from __future__ import annotations

import logging
import math
import time
from pathlib import Path

import numpy as np

from . import lin2010
from . import (BISECT_TOL_RE, BS_BLEND_LAMBDA_RE, BS_BLEND_X_RE, BS_TAIL_SLOPE,
               MP_TAIL_RADIUS_FACTOR, N_HEAD, N_PHI, N_THETA, R_BISECT_HI_RE,
               R_BISECT_LO_RE, THETA_BLEND_RAD, THETA_MAX_RAD, X_TAIL_RE,
               recalc_for_state, state_comments)
from .. import geo
from dsexport import validate, write_lines_vla
from dsexport.vla import coord_formatter, val_formatter

log = logging.getLogger(__name__)

# Farris & Russell (1994) gas-dynamic standoff ratio.
GAMMA = 5.0 / 3.0                 # ratio of specific heats
MMS_MIN, MMS_MAX = 1.5, 15.0      # magnetosonic Mach clamp
# The Farris & Russell ratio diverges as M -> 1, and a storm drives the magnetosonic Mach number
# down exactly when the magnetopause is most compressed. Clamped only at M = 1.5 the ratio reached
# 2.16 at the 2003-11-20 peak (magnetopause 7.5 R_E, drawn shock 16.2 R_E): at the moment the
# storm should SQUEEZE the system the shock jumped outward and the sheath ballooned. Observed
# standoff ratios sit near 1.2-1.5, so the drawn ratio is bounded there and the clamp is logged.
BS_RATIO_MAX = 1.5
BS_ECCENTRICITY = 1.16            # Slavin & Holzer (1981) conic eccentricity

# Downstream end of the DRAWN surfaces, in GSM x (R_E) - tail shape v2 (see
# model/__init__): both the magnetopause and the bow shock are drawn to
# X_TAIL_RE. Kept under its old name for the tests / dome notes that quote it.
X_BS_TAIL_RE = X_TAIL_RE
# Shells (phase 4) whose tail end is closed with a rounded cap. The distant
# magnetotail does pinch off beyond the distant X-line; the shock only weakens -
# but on the dome an open tube end reads as a cut pipe with a rim (measured
# 2026-09-05 from the dusk / top-down views), so BOTH drawn shells are closed
# where the drawing stops. The .vla rings stay open. Visual convention, not physics.
SHELL_CAPPED = ("Magnetopause", "BowShock")

# Mesh opacity ramp, nose -> tail edge. Keyed on the RING INDEX, which is the
# morph key both meshes share; on the magnetopause's uniform theta grid that is
# the same linear ramp in theta it has always been (asserted in tests).
OPACITY_NOSE = 1.0
OPACITY_TAIL = 0.15

# Fixed mesh angles. Shared by every export so parametric rows pair up.
THETA_RAD = np.linspace(0.0, THETA_MAX_RAD, N_THETA)          # 48, nose first
PHI_RAD = np.linspace(0.0, 2.0 * math.pi, N_PHI, endpoint=False)   # 36
# Ring index as a 0..1 fraction: the fixed morph key of BOTH meshes.
STATION_FRAC = np.linspace(0.0, 1.0, N_THETA)                 # 48, nose first
# Tail shape v2: the magnetopause's ray stations (nose .. blend), uniform.
THETA_HEAD_RAD = np.linspace(0.0, THETA_BLEND_RAD, N_HEAD)     # 24, nose first


def _tail_blend(x_b: float, rho_b: float, slope_b: float, slope_inf: float,
                lam: float, x) -> np.ndarray:
    """rho(x) down-tail of a blend station (x <= x_b), C1 with the head.

    The flare d rho / d(-x) relaxes exponentially from slope_b (the head's own
    slope at the blend) to slope_inf over the scale lam:

        rho = rho_b + slope_inf d + (slope_b - slope_inf) lam (1 - exp(-d/lam)),
        d = x_b - x

    slope_inf = 0 gives an asymptotic cylinder of radius rho_b + slope_b lam;
    slope_inf > 0 a cone.
    """
    d = np.maximum(x_b - np.asarray(x, dtype=float), 0.0)
    return rho_b + slope_inf * d + (slope_b - slope_inf) * lam * (1.0 - np.exp(-d / lam))


# --- radii --------------------------------------------------------------------

# The lowest dynamic pressure the boundary models are ever evaluated at. Shue's standoff is
# r0 ~ pd**-0.15: a pd of exactly 0 raises ZeroDivisionError (a look-back rebuild died on a
# density-0 sample, 2026-09-22) and anything near 0 sends the nose to the bisection bracket.
# 0.05 nPa is far below any real solar wind (quiet is ~0.5-2 nPa) and gives r0 ~ 16 R_E, so
# the guard changes nothing for measured data and keeps a bad sample from killing a render.
# The data layers (magneto.events._physical, livecore.state._valid_rows) already refuse such
# samples; this is the last line, not the first.
PDYN_FLOOR_NPA = 0.05


def _sw(state: dict):
    """(pdyn_npa, bz_gsm) - the only two numbers the Shue boundary needs."""
    pdyn = float(state["sw"]["pdyn_npa"])
    if not (pdyn >= PDYN_FLOOR_NPA):          # also catches NaN
        pdyn = PDYN_FLOOR_NPA
    return pdyn, float(state["sw"]["bz_gsm"])


def _bisect_radius(inside, theta_rad: float) -> float:
    """Radius where inside(r) flips false, along the ray theta from +X_GSM.

    Bisection in the x-rho plane on the model's own inside/outside flag, in
    the bracket from model/__init__. Returns the bracket end when the whole
    ray is inside (far tail) or outside (extreme compression) - clamping is
    by design, not an error.
    """
    lo, hi = R_BISECT_LO_RE, R_BISECT_HI_RE
    if not inside(lo, theta_rad):
        return lo
    if inside(hi, theta_rad):
        return hi
    while hi - lo > BISECT_TOL_RE:
        mid = 0.5 * (lo + hi)
        if inside(mid, theta_rad):
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _ray_point(r: float, theta_rad: float):
    """Probe point on the ray: x = r cos(theta), rho along +Z_GSM.

    Both magnetopause models are axisymmetric about X (their inside/outside
    flags depend on x/r only), so the azimuth of the probe ray is irrelevant.
    """
    return r * math.cos(theta_rad), 0.0, r * math.sin(theta_rad)


def _shue_inside(pdyn: float, bz: float):
    from geopack import geopack as gp

    def inside(r: float, theta_rad: float) -> bool:
        x, y, z = _ray_point(r, theta_rad)
        return gp.shuetal_mgnp(pdyn, -1.0, bz, x, y, z)[4] > 0

    return inside


def _t96_inside(pdyn: float):
    from geopack import geopack as gp

    def inside(r: float, theta_rad: float) -> bool:
        x, y, z = _ray_point(r, theta_rad)
        return gp.t96_mgnp(pdyn, -1.0, x, y, z)[4] > 0

    return inside


def magnetopause_radius(theta, state: dict):
    """Shue (1998) magnetopause radius (R_E) at theta from +X_GSM.

    theta is radians, scalar or array-like; returns the same shape. Requires
    nothing of geopack's epoch state - the Shue boundary depends only on
    Pdyn and Bz - but callers normally have recalc'd anyway.
    """
    inside = _shue_inside(*_sw(state))
    th = np.asarray(theta, dtype=float)
    out = np.array([_bisect_radius(inside, float(t)) for t in th.ravel()])
    return out.reshape(th.shape) if th.shape else float(out[0])


def magnetosonic_mach(state: dict) -> float:
    """Fast-magnetosonic Mach number of the solar wind, clamped to [1.5, 15].

    V_A = 21.8 B / sqrt(n) km/s (B in nT, n in cm^-3), c_s = 0.12 sqrt(T) km/s
    (T in K) - the standard brief-formula pair. The clamp keeps the
    Farris & Russell ratio finite when a data glitch puts the wind at or
    below Mach 1.
    """
    sw = state["sw"]
    density = max(float(sw["density"]), 1e-3)
    v_a = 21.8 * float(sw["bt"]) / math.sqrt(density)
    c_s = 0.12 * math.sqrt(max(float(sw["temperature"]), 0.0))
    v_ms = math.hypot(v_a, c_s)
    mach = float(sw["speed"]) / max(v_ms, 1e-6)
    return min(max(mach, MMS_MIN), MMS_MAX)


def shock_compression(state: dict) -> float:
    """Rankine-Hugoniot density ratio n2/n1 across the bow shock, 1 < r <= 4.

    r(M) = (g+1) M^2 / ((g-1) M^2 + 2), the gas-dynamic strong-shock jump at the
    state's fast-magnetosonic Mach number - the same M, and the same
    ((g-1)M^2+2) group, that bow_shock_standoff already uses for Farris &
    Russell's standoff ratio. Bounded above by (g+1)/(g-1) = 4 for g = 5/3.

    r = 1.71 at the M = 1.5 clamp, 3.82 at M = 8, 4 in the strong-shock limit.
    """
    m2 = magnetosonic_mach(state) ** 2
    return float((GAMMA + 1.0) * m2 / ((GAMMA - 1.0) * m2 + 2.0))


def bow_shock_standoff(state: dict) -> float:
    """Bow-shock subsolar distance (R_E): Farris & Russell (1994).

    r_bs = r_mp * (1 + 1.1 * ((g-1) M^2 + 2) / ((g+1)(M^2 - 1))), anchored to
    the Shue nose so the shock always leads the magnetopause it is standing
    off from - both move together when the wind changes.
    """
    r_mp = float(magnetopause_radius(0.0, state))
    m = magnetosonic_mach(state)
    m2 = m * m
    ratio = 1.0 + 1.1 * ((GAMMA - 1.0) * m2 + 2.0) / ((GAMMA + 1.0) * (m2 - 1.0))
    if ratio > BS_RATIO_MAX:
        log.debug("bow shock standoff ratio %.3f clamped to %.2f (Mach %.2f, r_mp %.2f R_E)",
                  ratio, BS_RATIO_MAX, m, r_mp)
        ratio = BS_RATIO_MAX
    return r_mp * ratio


def bow_shock_semi_latus(state: dict) -> float:
    """L = (1 + e) * standoff - the conic's semi-latus rectum (R_E)."""
    return bow_shock_standoff(state) * (1.0 + BS_ECCENTRICITY)


def bow_shock_rho(x, semi_latus: float):
    """Cylindrical radius (R_E) of the bow-shock conic at GSM x. Exact.

    THE closed form the drawn shock is built on. Substituting r = L - e x
    (which is what r = L / (1 + e cos theta) says once x = r cos theta) into
    r^2 = x^2 + rho^2 gives

        rho^2(x) = L^2 - 2 L e x + (e^2 - 1) x^2

    with no singularity anywhere: e > 1 makes the x^2 term positive, so rho
    grows without bound downstream (rho -> sqrt(e^2-1) |x|) instead of the
    ray form's asymptote at theta = 149.5 deg. Valid for x <= L / e, the conic's
    own nose; beyond that there is no surface and the radicand goes negative,
    which is clamped to 0 rather than returned as a nan.
    """
    xx = np.asarray(x, dtype=float)
    e = BS_ECCENTRICITY
    rho2 = (semi_latus * semi_latus - 2.0 * semi_latus * e * xx
            + (e * e - 1.0) * xx * xx)
    rho = np.sqrt(np.maximum(rho2, 0.0))
    return rho if xx.shape else float(rho)


def bow_shock_radius(theta, state: dict):
    """Bow-shock radius (R_E) at theta from +X_GSM - THE WIND'S OBSTACLE TABLE.

    This is no longer what the drawn shock is built from (see bow_shock_mesh):
    it survives because the wind's magnetosheath steering interpolates a
    radius-vs-theta lookup (radius_lut -> wind.Sheath.bs_radius) over tens of
    thousands of parcel positions per substep, and that lookup is keyed on
    theta. The DRAWN shock and the WIND'S obstacle table are therefore two
    different parameterizations of the same Slavin & Holzer conic, and they
    agree exactly wherever the theta form is valid - which is everything from
    the nose out past the terminator to the asymptote at theta = 149.5 deg,
    i.e. the whole dayside and flank region where the sheath steering does its
    work. Past the asymptote the ray never meets the conic and the value is
    clamped to the bracket top, as it always was; parcels that get that far
    behind Earth are already leaving by wind.X_EXIT_RE / RHO_EXIT_RE and are
    not being steered by the shock any more.
    """
    semi_latus = bow_shock_semi_latus(state)
    th = np.asarray(theta, dtype=float)
    denom = 1.0 + BS_ECCENTRICITY * np.cos(th)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(denom > 0.0, semi_latus / np.where(denom > 0.0, denom, 1.0),
                     R_BISECT_HI_RE)
    r = np.clip(r, R_BISECT_LO_RE, R_BISECT_HI_RE)
    return r if th.shape else float(r)


def radius_lut(radius_fn, state: dict, n_theta: int = 181):
    """(theta_grid, r_grid) over the full 0..pi sweep, for cheap np.interp.

    The wind's magnetosheath steering needs the magnetopause radius at tens of
    thousands of parcel positions per advection substep; bisecting geopack's
    inside/outside flag per parcel is out of the question. This samples the
    real radius_fn ONCE per state on a fixed theta grid, and the caller
    interpolates. The grid runs to pi (the mesh only goes to THETA_MAX_RAD)
    because parcels stream well past the tail edge of the drawn surface.

    Note this is the WIND'S view of the boundaries, and for the bow shock it is
    deliberately not the same parameterization the drawn shock uses - see
    bow_shock_radius. Both are the same conic; the theta form is exact out to
    the asymptote at 149.5 deg (the whole region the sheath steering acts in)
    and clamped past it, where the wind's own exit thresholds have taken over.
    """
    th = np.linspace(0.0, math.pi, int(n_theta))
    return th, np.asarray(radius_fn(th, state), dtype=float)


# --- mesh ---------------------------------------------------------------------

def drawn_radius_lut(state: dict, n_theta: int = 181):
    """(theta_grid, r_grid) for the DRAWN magnetopause, averaged over azimuth.

    Same contract as radius_lut, and the same use: the wind's sheath steering
    interpolates it for 10,000 parcels per substep. The difference is what it
    samples - the Lin hybrid surface the dome actually shows, instead of the
    Shue reference. Shue sat about 4% inside it at the nose, which put parcels
    slightly inside the visible shell.

    ONE-DIMENSIONAL BY DECISION. wind.Sheath is keyed on theta alone, so the
    azimuthal structure - the cusp indentations above all - is averaged away
    here. Parcels therefore do not dip into the cusp funnels the shell shows;
    the cusp CAPTURE they do have is wind.py's own analytic funnel, which is
    anchored on the nose and the dipole tilt and is unaffected. Making the
    steering genuinely 2-D is a larger change than this one.

    Clamped to R_BISECT_HI_RE like the Shue table: Lin's flaring term diverges
    as theta approaches pi, the same way Shue's does, and parcels that far
    behind Earth are leaving by X_EXIT_RE / RHO_EXIT_RE anyway.
    """
    th = np.linspace(0.0, math.pi, int(n_theta))
    r = magnetopause_radius_3d(th[:, None], PHI_RAD[None, :], state)
    r = np.clip(np.asarray(r, dtype=float), 0.0, R_BISECT_HI_RE).mean(axis=1)
    return th, r


def drawn_profile_lut(state: dict):
    """(theta, r) of the magnetopause profile that is actually DRAWN.

    WHY THIS EXISTS, measured on Gannon step 150 (2026-09-20):

        solar zenith   drawn shell   magnetopause_radius_3d   ratio
           0-120 deg      6.8-14.8            6.0-13.0         0.88
         150-175 deg        126.4               284.2          2.07
         175-181 deg        209.6              1575.6          7.49

    The analytic radius FLARES WITHOUT BOUND as theta approaches pi - Shue's form and
    Lin's alike - while boundary_mesh deliberately relaxes the drawn tail to a
    ~25-30 R_E near-cylinder out to X_TAIL_RE, because that is what the observed
    distant magnetotail does. Past the flanks the two therefore disagree by a factor
    of 2 to 7, and anything using the analytic radius as a stand-in for the drawn
    surface is wrong by exactly that factor where the tail is.

    That is what let field lines run outside the drawn magnetopause down the tail:
    5.7% of vertices at the storm peak, 41.5% of those beyond 40 R_E. The nose is fine
    (6.00 analytic against 6.10 drawn, 1.6%), which is why it read as a tail-only
    "misalignment" rather than a frame error - the shells themselves sit 0.01 deg from
    true anti-sunward at every epoch tested.

    Returns the SAME profile boundary_mesh builds, as a monotone theta -> r table, so
    a caller can interpolate the drawn boundary without rebuilding the lattice.
    """
    m = boundary_mesh(magnetopause_radius, state)
    th = np.asarray(m["theta"], dtype=float).ravel()
    r = np.asarray(m["r"], dtype=float).ravel()
    o = np.argsort(th)
    return th[o], r[o]


def drawn_surface_lut(state: dict):
    r"""The DRAWN magnetopause as a 2-D (theta, phi) -> radius table, azimuth resolved.

    WHY A THETA-ONLY PROFILE IS NOT ENOUGH, measured on Jan 2026 step 140 (2026-09-21):
    at polar angle 107 deg the drawn shell's radius runs from **7.0 to 95.5 R_E across
    azimuth** - a 13-fold spread. Any single number for that theta is wrong for almost
    every azimuth, and clipping against it left 259 open-line vertices sitting at
    36.6 R_E where the boundary in their own direction is 13.1 R_E: up to 4.45x outside
    the shell the audience can see, in a five-step window of the replay.

    That was the SAME mistake as the original bug one level down. The first version
    clipped against the analytic surface instead of the drawn one; the second clipped
    against the drawn one AVERAGED OVER AZIMUTH. Both are "a boundary that is not the
    boundary on screen", and both show up as field lines that do not match the shells.

    Returns (theta_grid, r_grid, phi_grid) straight off `magnetopause_lattice` - the
    very lattice the .glb is written from - so the clip and the drawn surface cannot
    disagree by construction. theta increases monotonically down each azimuth column
    (nose to tail edge), which is what the per-column interpolation needs.
    """
    m = magnetopause_lattice(state)
    x = np.asarray(m["x"], dtype=float)                 # (N_THETA, N_PHI)
    rho = np.asarray(m["rho"], dtype=float)
    r = np.hypot(x, rho)
    th = np.arccos(np.clip(x / np.maximum(r, 1e-12), -1.0, 1.0))
    return th, r, PHI_RAD


def drawn_radius_clip(theta, phi, state: dict, tilt_rad=None, alpha_rad=None,
                      r0_re=None, lut=None):
    """Radius of the DRAWN magnetopause in the direction (theta, phi).

    Bilinear on the export lattice: interpolate in theta down the two azimuth columns
    that bracket phi, then between them. Exact to the mesh's own facets, which is the
    most any clip can be - the shell IS that mesh.

    The analytic radius is deliberately NOT part of this any more. It was previously
    min()-ed in because the theta profile lacked Lin's azimuthal asymmetry and the
    analytic term put some of it back; with the real 2-D surface the asymmetry is
    already here, and keeping the analytic could only cut lines that are genuinely
    inside the drawn shell - a false statement in the other direction.
    """
    th = np.asarray(theta, dtype=float)
    ph = np.mod(np.asarray(phi, dtype=float), 2.0 * math.pi)
    tg, rg, phg = lut if lut is not None else drawn_surface_lut(state)

    n_phi = len(phg)
    dphi = 2.0 * math.pi / n_phi
    j0 = np.floor(ph / dphi).astype(int) % n_phi
    j1 = (j0 + 1) % n_phi
    w = (ph - j0 * dphi) / dphi

    t, a, b, wt = th.ravel(), j0.ravel(), j1.ravel(), w.ravel()
    ra = np.empty(t.shape, dtype=float)
    rb = np.empty(t.shape, dtype=float)
    # np.interp takes one xp per call, so walk the 36 columns and do the points that
    # bracket each. Each point contributes to exactly two columns, so no double count.
    for j in range(n_phi):
        tj, rj = tg[:, j], rg[:, j]
        sa = a == j
        if sa.any():
            ra[sa] = np.interp(t[sa], tj, rj, left=rj[0], right=rj[-1])
        sb = b == j
        if sb.any():
            rb[sb] = np.interp(t[sb], tj, rj, left=rj[0], right=rj[-1])
    return (ra * (1.0 - wt) + rb * wt).reshape(th.shape)


def _ramp_opacity(frac) -> np.ndarray:
    """OPACITY_NOSE at frac 0 -> OPACITY_TAIL at frac 1, linear."""
    f = np.asarray(frac, dtype=float)
    return OPACITY_NOSE + (OPACITY_TAIL - OPACITY_NOSE) * f


def _theta_opacity(theta_rad: np.ndarray) -> np.ndarray:
    """The magnetopause ramp, keyed on theta (its own mesh parameter)."""
    return _ramp_opacity(np.asarray(theta_rad, dtype=float) / THETA_MAX_RAD)


def _station_opacity() -> np.ndarray:
    """The bow shock's ramp, keyed on STATION INDEX instead of theta.

    Identical numbers to _theta_opacity(THETA_RAD) - the magnetopause's theta
    grid is uniform, so its ramp was already linear in ring index - which is
    what makes the two layers still fade together on the dome even though they
    no longer share a mesh parameter. A test pins the two arrays to 1e-12.
    """
    return _ramp_opacity(STATION_FRAC)


def _surface_of_revolution(x: np.ndarray, rho: np.ndarray,
                           opacity: np.ndarray) -> dict:
    """Rings + meridians for ANY axisymmetric (x, rho) profile about +X_GSM.

    The one place the export topology is built, shared by both boundary meshes
    so a re-parameterization can never change a row count by accident:
      rings      - N_THETA-1 closed polylines of N_PHI+1 vertices (station 0
                   is the degenerate nose point and is dropped)
      meridians  - N_PHI polylines of N_THETA vertices, nose -> tail edge
      polylines  - rings + meridians, each an (n, 4) array of
                   (x, y, z, opacity) in GSM R_E; this is the export order and
                   it depends only on the topology constants, never on data.
    """
    for name, arr in (("x", x), ("rho", rho), ("opacity", opacity)):
        if np.shape(arr) != (N_THETA,):
            raise ValueError(f"{name} has shape {np.shape(arr)}, want ({N_THETA},)")
    y = rho[:, None] * np.cos(PHI_RAD)[None, :]           # (N_THETA, N_PHI)
    z = rho[:, None] * np.sin(PHI_RAD)[None, :]

    rings = []
    for i in range(1, N_THETA):                           # skip degenerate nose
        ring = np.empty((N_PHI + 1, 4))
        ring[:N_PHI, 0] = x[i]
        ring[:N_PHI, 1] = y[i]
        ring[:N_PHI, 2] = z[i]
        ring[:N_PHI, 3] = opacity[i]
        ring[N_PHI] = ring[0]                             # close the ring
        rings.append(ring)

    meridians = []
    for j in range(N_PHI):
        mer = np.empty((N_THETA, 4))
        mer[:, 0] = x
        mer[:, 1] = y[:, j]
        mer[:, 2] = z[:, j]
        mer[:, 3] = opacity
        meridians.append(mer)

    return {"x": x, "rho": rho, "frac": STATION_FRAC, "rings": rings,
            "meridians": meridians, "polylines": rings + meridians}


def boundary_mesh(radius_fn, state: dict) -> dict:
    """Mesh for an axisymmetric radius_fn(theta, state) with the v2 tail.

    Stations 0..N_HEAD-1 are RAYS: theta uniform from the nose to
    THETA_BLEND_RAD, radius from radius_fn (the Shue surface geopack's own
    inside/outside flag believes in, bisected). Stations N_HEAD..N_THETA-1 are
    X STATIONS from the blend to X_TAIL_RE: the tail radius relaxes from the
    head's flare slope to the asymptote MP_TAIL_RADIUS_FACTOR x standoff (C1
    at the blend), because Shue's own form flares without bound past the
    flanks while the observed distant magnetotail is a ~25-30 R_E
    near-cylinder. Adds 'theta', 'r' and 'param' to the shared surface dict.
    """
    r_head = np.asarray(radius_fn(THETA_HEAD_RAD, state), dtype=float)
    if r_head.shape != THETA_HEAD_RAD.shape:
        raise ValueError(f"radius_fn returned {r_head.shape}, want {THETA_HEAD_RAD.shape}")
    x_h = r_head * np.cos(THETA_HEAD_RAD)
    rho_h = r_head * np.sin(THETA_HEAD_RAD)
    # head flare d rho / d(-x) at the blend, from the last two ray stations
    slope_b = float((rho_h[-1] - rho_h[-2]) / max(x_h[-2] - x_h[-1], 1e-9))
    slope_b = max(slope_b, 1e-6)
    rho_inf = max(MP_TAIL_RADIUS_FACTOR * r_head[0], 1.05 * rho_h[-1])
    # slope_inf = 0, so the relaxation scale that lands on rho_inf exactly is
    lam = float(np.clip((rho_inf - rho_h[-1]) / slope_b, 5.0, 200.0))
    x_t = np.linspace(x_h[-1], X_TAIL_RE, N_THETA - N_HEAD + 1)[1:]
    rho_t = _tail_blend(float(x_h[-1]), float(rho_h[-1]), slope_b, 0.0, lam, x_t)
    x = np.concatenate([x_h, x_t])
    rho = np.concatenate([rho_h, rho_t])

    mesh = _surface_of_revolution(x, rho, _station_opacity())
    mesh["theta"] = np.arctan2(rho, x)
    mesh["r"] = np.hypot(x, rho)
    mesh["blend_re"] = (float(x_h[-1]), float(rho_h[-1]))
    mesh["tail_radius_re"] = float(rho_inf)
    mesh["param"] = [
        f"tail shape v2 - HEAD parameterized by THETA: {N_HEAD} uniform rays, 0 (nose) to "
        f"{math.degrees(THETA_BLEND_RAD):g} deg from +X_GSM, radii bisected in "
        f"[{R_BISECT_LO_RE:g}, {R_BISECT_HI_RE:g}] R_E tol {BISECT_TOL_RE:g} (Shue 1998 via geopack)",
        f"TAIL parameterized by X STATION: {N_THETA - N_HEAD} uniform x from the blend "
        f"(x {x_h[-1]:.2f}, rho {rho_h[-1]:.2f}) to {X_TAIL_RE:g} R_E, flare relaxing from "
        f"{slope_b:.3f} to 0 over {lam:.1f} R_E toward a {rho_inf:.2f} R_E cylinder "
        f"({MP_TAIL_RADIUS_FACTOR:g} x standoff - the observed distant tail, not Shue's extrapolation)",
        f"nose {r_head[0]:.3f} R_E, tail-edge ring x {x[-1]:.3f} rho {rho[-1]:.3f} R_E; "
        f"opacity {OPACITY_NOSE:g} nose -> {OPACITY_TAIL:g} tail edge (linear in ring index)",
    ]
    return mesh



# --- Lin et al. (2010) hybrid magnetopause -------------------------------------
#
# The drawn magnetopause is Lin's 3-D SHAPE (cusp indentations, dipole-tilt
# response, dawn-dusk and north-south asymmetry) carried on a SUBSOLAR DISTANCE
# that blends to Shue outside Lin's fitted box. Two reasons for the blend, and
# neither is "so the nose keeps responding" - both models saturate in Bz and
# neither stops responding:
#
#   1. CALIBRATION CONTINUITY. geopack's Shue is the reference every other number
#      in this tree is measured against: gate 1's 10-11 R_E band, gate 2's
#      Shue-vs-T96 crosscheck, gate 5's last-closed-field-line comparison, and
#      fieldlines.R_MP_REF_RE, which the storm-following trace gates divide by.
#   2. LIN'S FITTED BOX EXCLUDES THE EVENTS THIS SHOW EXISTS FOR. Bastille,
#      Nov 2003 and Gannon all leave Bz [-15, +5] nT and Pd [1, 16] nPa. A fit
#      used outside its range is an extrapolation, and this project's honesty
#      law says extrapolations get named, not hidden.
#
# Every Lin INPUT is clamped to the box (lin2010.clamp_inputs); only r0 is
# blended on top. beta0 is the coefficient that extrapolates worst - about -1.37
# at the box edge, nearly doubling to -2.20 on naive extrapolation to Bz -50 -
# and clamping it is what keeps the tail from deforming at a storm peak.

# How far outside the fitted box the blend takes to reach pure Shue.
LIN_BZ_MARGIN_NT = 10.0           # full Shue at Bz <= -25 nT
LIN_PD_MARGIN_NPA = 10.0          # full Shue at Pd >= 26 nPa


def _smootherstep(t):
    """Ken Perlin's C2 smootherstep, 6t^5 - 15t^4 + 10t^3 on [0, 1].

    C2, not the usual C1 smoothstep, because the blend weight multiplies a
    DISTANCE that is then morphed between render steps: a discontinuous second
    derivative shows up as a visible tick in the nose as a storm crosses the box
    edge, which is exactly the frame the audience is watching.
    """
    t = np.clip(np.asarray(t, dtype=float), 0.0, 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def hybrid_r0(state: dict) -> dict:
    """Subsolar standoff: Lin inside its fitted box, Shue outside, C2 between.

    Returns the blended r0 AND the whole derivation - both models' values, the
    weight, and how far outside the box the drivers are - because this is the
    number the credits page has to describe honestly and the manifest has to
    record per step.
    """
    pdyn, bz = _sw(state)
    bt = float(((state or {}).get("sw") or {}).get("bt") or 0.0)
    box = lin2010.clamp_inputs(pdyn, bz)

    # Distance outside the box, per driver, as a fraction of its margin. The
    # WORST driver governs: one extreme parameter is enough to make the whole
    # evaluation an extrapolation.
    u_bz = abs(box["bz_excess_nt"]) / LIN_BZ_MARGIN_NT
    u_pd = abs(box["pd_excess_npa"]) / LIN_PD_MARGIN_NPA
    u = float(min(1.0, max(u_bz, u_pd)))
    w_lin = float(1.0 - _smootherstep(u))

    r_lin = lin2010.r0_re(box["pd_npa"], box["bz_nt"], bt)       # clamped inputs
    r_shue = float(_bisect_radius(_shue_inside(pdyn, bz), 0.0))  # raw inputs
    r0 = w_lin * r_lin + (1.0 - w_lin) * r_shue
    return {"r0_re": float(r0), "w_lin": w_lin, "lin_r0_re": float(r_lin),
            "shue_r0_re": r_shue, "outside_box_frac": u,
            "bt_nt": bt, **box}


def magnetopause_radius_3d(theta, phi, state: dict, tilt_rad=None, alpha_rad=None,
                           r0_re=None):
    """The DRAWN magnetopause radius (R_E) at GSM direction(s) (theta, phi).

    theta is from +X_GSM, phi is atan2(z, y) - this project's mesh convention.
    Both broadcast. The aberration is applied HERE and only here: the GSM
    direction is rotated into cGSM, Lin is evaluated at that direction, and the
    radius is returned against the original GSM direction. Nothing downstream has
    anything to undo, and there is no second site where it could be applied twice.

    `tilt_rad` / `alpha_rad` default to the recalc'd epoch's dipole tilt and the
    state's own aberration angle; pass them to pin a test.
    """
    if tilt_rad is None:
        tilt_rad = geo.dipole_tilt_rad()
    if alpha_rad is None:
        alpha_rad = geo.aberration_angle_for_state(state)
    r0 = float(hybrid_r0(state)["r0_re"]) if r0_re is None else float(r0_re)

    th, ph = np.broadcast_arrays(np.asarray(theta, dtype=float),
                                 np.asarray(phi, dtype=float))
    # GSM unit direction -> cGSM unit direction -> (theta', phi') for Lin
    d = np.stack([np.cos(th), np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph)], axis=-1)
    dc = geo.gsm_to_cgsm(d, alpha_rad)
    th_c = np.arccos(np.clip(dc[..., 0], -1.0, 1.0))
    ph_c = np.arctan2(dc[..., 2], dc[..., 1])

    box = lin2010.clamp_inputs(*_sw(state))
    bt = float(((state or {}).get("sw") or {}).get("bt") or 0.0)
    return lin2010.radius_re(th_c, ph_c, box["pd_npa"], box["bz_nt"], bt,
                             tilt_rad, r0_override=r0)


def drawn_nose_re(state: dict) -> float:
    """Subsolar distance of the DRAWN boundary, cusp term included.

    Not the same as hybrid_r0()['r0_re']: Lin's cusp term Q is non-zero even at
    the nose (both cusps are ~63 deg away and their tails reach it), pulling the
    nose in by ~0.3 R_E. The field-line trace gates follow THIS, because it is
    the surface the audience sees the lines against.
    """
    return float(magnetopause_radius_3d(0.0, 0.0, state))


def magnetopause_lattice(state: dict) -> dict:
    """The drawn magnetopause as an (N_THETA, N_PHI) lattice, tail shape v2.

    Same construction as boundary_mesh's axisymmetric path and the same station
    counts, so the OBJ topology, every count test and the player are unchanged:

      stations 0..N_HEAD-1   RAYS at the fixed THETA_HEAD_RAD angles, radius from
                             magnetopause_radius_3d - now varying with azimuth.
      stations N_HEAD..      X STATIONS relaxing each azimuth's flare to a
                             cylinder, PER AZIMUTH, each starting at its own blend
                             point and all ending at X_TAIL_RE.

    The per-azimuth tail grid is deliberate. A single x grid shared by every
    azimuth would have to start at some average blend x, leaving each meridian a
    step to take at the blend - about 1 R_E of kink with Lin's flank asymmetry.
    Per-azimuth grids are continuous at the blend by construction and converge to
    a common, planar tail-end ring.
    """
    tilt = geo.dipole_tilt_rad()
    alpha = geo.aberration_angle_for_state(state)
    hy = hybrid_r0(state)

    th = THETA_HEAD_RAD[:, None]                       # (N_HEAD, 1)
    ph = PHI_RAD[None, :]                              # (1, N_PHI)
    r_head = np.asarray(magnetopause_radius_3d(th, ph, state, tilt, alpha,
                                               r0_re=hy["r0_re"]), dtype=float)
    x_h = r_head * np.cos(th)
    rho_h = r_head * np.sin(th)

    n_tail = N_THETA - N_HEAD
    x_t = np.empty((n_tail, N_PHI))
    rho_t = np.empty((n_tail, N_PHI))
    rho_inf_all = np.empty(N_PHI)
    for j in range(N_PHI):
        slope_b = float((rho_h[-1, j] - rho_h[-2, j]) / max(x_h[-2, j] - x_h[-1, j], 1e-9))
        slope_b = max(slope_b, 1e-6)
        rho_inf = max(MP_TAIL_RADIUS_FACTOR * float(r_head[0, j]), 1.05 * float(rho_h[-1, j]))
        lam = float(np.clip((rho_inf - float(rho_h[-1, j])) / slope_b, 5.0, 200.0))
        xs = np.linspace(float(x_h[-1, j]), X_TAIL_RE, n_tail + 1)[1:]
        x_t[:, j] = xs
        rho_t[:, j] = _tail_blend(float(x_h[-1, j]), float(rho_h[-1, j]),
                                  slope_b, 0.0, lam, xs)
        rho_inf_all[j] = rho_inf

    x = np.concatenate([x_h, x_t], axis=0)
    rho = np.concatenate([rho_h, rho_t], axis=0)
    return {"x": x, "rho": rho, "r_head": r_head, "hybrid": hy,
            "tilt_rad": float(tilt), "alpha_rad": float(alpha),
            "tail_radius_re": float(np.mean(rho_inf_all))}


def bow_shock_stations(standoff_re: float) -> np.ndarray:
    """The N_THETA GSM x values the drawn bow shock is sampled at (R_E).

    HEAD: N_HEAD stations cosine-spaced from the exact Farris & Russell nose to
    BS_BLEND_X_RE (the terminator plane) - x_i = x_nose + (x_b - x_nose) *
    (1 - cos(pi/2 * i/(N_HEAD-1))) clusters them at the NOSE, where the
    curvature is. TAIL: the remaining stations uniform from the blend to
    X_TAIL_RE, where the surface is nearly straight. Station 0 is the nose
    exactly, station N_HEAD-1 the blend exactly, station N_THETA-1 the tail
    edge exactly.
    """
    frac = 1.0 - np.cos(0.5 * math.pi * np.linspace(0.0, 1.0, N_HEAD))
    frac[0], frac[-1] = 0.0, 1.0            # both ends exact, not cos-rounded
    x_head = float(standoff_re) + (BS_BLEND_X_RE - float(standoff_re)) * frac
    x_tail = np.linspace(BS_BLEND_X_RE, X_TAIL_RE, N_THETA - N_HEAD + 1)[1:]
    return np.concatenate([x_head, x_tail])



def bow_shock_mesh(state: dict) -> dict:
    """X-STATION parameterized mesh of the Slavin & Holzer conic.

    Same topology as boundary_mesh (47 rings x 37 + 36 meridians x 48, ring
    index is still the morph key), different parameter: rho comes from the
    exact closed form bow_shock_rho(x), so the tail OPENS instead of closing on
    a bisection bracket. The nose station is the Farris & Russell standoff
    itself, so the layer's nose radius is unchanged from before this
    re-parameterization.
    """
    return bow_shock_mesh_at(bow_shock_standoff(state))


def bow_shock_mesh_at(standoff: float) -> dict:
    """bow_shock_mesh for a GIVEN subsolar standoff (R_E) - the shape library's entry point
    (magneto.model.shell_library builds its one reference shock at R_REF_RE)."""
    standoff = float(standoff)
    semi_latus = standoff * (1.0 + BS_ECCENTRICITY)
    x = bow_shock_stations(standoff)
    e = BS_ECCENTRICITY
    rho = np.empty(N_THETA)
    rho[:N_HEAD] = bow_shock_rho(x[:N_HEAD], semi_latus)
    rho[0] = 0.0                                      # the nose is exact
    # Tail shape v2: past the blend the conic's own flare (analytic from
    # rho^2(x): d rho / d(-x) = (L e - (e^2-1) x) / rho) relaxes into the
    # BS_TAIL_SLOPE Mach cone - the conic alone is 86 R_E wide by x = -60.
    x_b, rho_b = float(x[N_HEAD - 1]), float(rho[N_HEAD - 1])
    slope_b = (semi_latus * e - (e * e - 1.0) * x_b) / rho_b
    rho[N_HEAD:] = _tail_blend(x_b, rho_b, slope_b, BS_TAIL_SLOPE,
                               BS_BLEND_LAMBDA_RE, x[N_HEAD:])

    mesh = _surface_of_revolution(x, rho, _station_opacity())
    # r = L - e x is the conic in radius form on the head; the tail is not the
    # conic any more, so the radius is simply the geometric one everywhere.
    mesh["r"] = np.hypot(x, rho)
    mesh["theta"] = np.arctan2(rho, x)
    mesh["blend_re"] = (x_b, rho_b)
    mesh["standoff_re"] = standoff
    mesh["semi_latus_re"] = semi_latus
    mesh["param"] = [
        f"tail shape v2 - parameterized by X STATION, not by theta: {N_HEAD} cosine-spaced "
        f"GSM x from the nose to {BS_BLEND_X_RE:g} R_E (clustered at the nose), then "
        f"{N_THETA - N_HEAD} uniform x to {X_TAIL_RE:g} R_E",
        f"head rho(x) = sqrt(L^2 - 2 L e x + (e^2-1) x^2), e = {BS_ECCENTRICITY:g}, "
        f"L = (1+e) * standoff = {semi_latus:.3f} R_E - the Slavin & Holzer conic in closed "
        f"form; tail flare relaxing from the conic's {slope_b:.3f} at the blend (rho {rho_b:.2f}) "
        f"to {BS_TAIL_SLOPE:.3f} (a {math.degrees(math.atan(BS_TAIL_SLOPE)):g} deg Mach cone) over "
        f"{BS_BLEND_LAMBDA_RE:g} R_E - the observed distant shock, not the conic's 30.5 deg asymptote",
        f"nose {standoff:.3f} R_E, tail-edge ring x {x[-1]:.3f} rho "
        f"{rho[-1]:.3f} (r {mesh['r'][-1]:.3f}) R_E; opacity {OPACITY_NOSE:g} "
        f"nose -> {OPACITY_TAIL:g} tail edge",
        "NOTE the magnetopause file is parameterized by THETA and this one by "
        "X: the two layers no longer share a mesh grid. They are separate "
        "files under separate holders and never morph against each other, so "
        "only each layer's own ring index has to pair across epochs, and it "
        "does. This is informational, not a warning",
    ]
    return mesh


def _mesh_to_frame(mesh: dict, frame=geo.DEFAULT_FRAME) -> list:
    """(x,y,z,opacity) GSM R_E polylines -> export-frame light years.

    The boundaries are already GSM physics, so 'gsm' is a pure scale and 'ecef'
    is the historical rotation at the recalc'd epoch - both go through the one
    frame helper in magneto.geo.
    """
    out = []
    for poly in mesh["polylines"]:
        rows = np.empty_like(poly)
        rows[:, :3] = geo.gsm_re_to_frame(poly[:, :3], frame)
        rows[:, 3] = poly[:, 3]
        out.append(rows)
    return out


# --- cross-check --------------------------------------------------------------

def mgnp_crosscheck(state: dict) -> dict:
    """Subsolar standoff from geopack's two magnetopause models, side by side.

    t96_mgnp knows nothing about Bz, so the two agree closely for northward /
    weakly southward IMF and diverge as Bz turns strongly southward - that
    divergence IS the Shue Bz dependence, and seeing it is the point.
    """
    pdyn, bz = _sw(state)
    shue = _bisect_radius(_shue_inside(pdyn, bz), 0.0)
    t96 = _bisect_radius(_t96_inside(pdyn), 0.0)
    return {"pdyn_npa": pdyn, "bz_gsm": bz, "shue_nose_re": shue,
            "t96_nose_re": t96, "diff_re": abs(shue - t96)}


# The last-closed-field-line cross-check. VALIDATION ONLY - nothing that is
# drawn depends on these numbers, and no render calls this (a run costs ~4 s at
# T89 and ~27 s at T96, which is per-frame unaffordable). Gate 5 owns it.
LCFL_RLIM_RE = 40.0             # a trace that gets this far counts as open
LCFL_TRUNCATED_FRAC = 0.95      # ... and an apex this close to it is unusable
LCFL_LAT_LO_DEG = 50.0          # bisection bracket in seed magnetic latitude
LCFL_LAT_HI_DEG = 89.0
LCFL_LAT_ITERS = 9              # 39 deg -> 0.08 deg
LCFL_AXIS_LO_RE = 3.0           # bisection bracket on the subsolar +X axis
LCFL_AXIS_HI_RE = 30.0
LCFL_AXIS_ITERS = 12            # 27 R_E -> 0.007 R_E


def _lcfl_tracer(state: dict):
    """(is_closed(seed_gsm) -> (bool, line), n_traces_so_far) for one state.

    Reuses the field-line layer's tracer wholesale - the hop-chained
    gp.trace(), the arc-length resample and the same open/closed
    classification - so the cross-check is measuring the field the dome is
    actually drawing, not a second opinion about it.
    """
    from . import fieldlines as fl

    recalc_for_state(state)
    sm_to_gsm = geo.sm_to_gsm_matrix()
    gsm_to_sm = sm_to_gsm.T
    parmod, exname = fl._model_args(state)
    count = [0]

    def closed(seed_gsm, seed_sm=None):
        count[0] += 1
        back = fl._trace_branch(seed_gsm, -1, LCFL_RLIM_RE, parmod, exname,
                               fl.INTERNAL)
        fwd = fl._trace_branch(seed_gsm, 1, LCFL_RLIM_RE, parmod, exname,
                              fl.INTERNAL)
        stitched = (np.vstack([back[::-1], fwd[1:]]) if len(fwd) > 1
                    else back[::-1])
        line = fl._resample(stitched)
        if line is None:
            return False, None
        kind = fl._classify(line, LCFL_RLIM_RE, gsm_to_sm,
                            np.asarray(seed_gsm if seed_sm is None else seed_sm))
        return kind == fl.KIND_CLOSED, line

    return closed, sm_to_gsm, count


def _lcfl_subsolar(closed, count) -> float | None:
    """Largest radius on the +X_GSM axis whose field line is still closed.

    THE number gate 5 asserts on. Bisecting the subsolar line rather than a
    seed latitude gives something directly comparable to the Shue nose: it is
    the model field's own dayside standoff, in the same units, on the same ray.
    """
    lo, hi = LCFL_AXIS_LO_RE, LCFL_AXIS_HI_RE
    if not closed(np.array([lo, 0.0, 0.0]))[0]:
        return None                       # nothing closed even at 3 R_E
    if closed(np.array([hi, 0.0, 0.0]))[0]:
        return float(hi)                  # still closed at the bracket top
    for _ in range(LCFL_AXIS_ITERS):
        mid = 0.5 * (lo + hi)
        if closed(np.array([mid, 0.0, 0.0]))[0]:
            lo = mid
        else:
            hi = mid
    return float(0.5 * (lo + hi))


def lcfl_crosscheck(state: dict, n_mlt: int = 8) -> dict:
    """Where does the TRACED field stop being closed, vs where Shue puts the wall?

    An honesty cross-check between the two halves of this build that have no
    business disagreeing quietly: the magnetopause we DRAW is an empirical
    boundary model (Shue 1998, from the solar wind), while the field lines we
    draw come from a completely separate empirical field model (T89/T96, from
    Kp or from parmod). Nothing couples them. If the last closed field line
    sits far outside the drawn magnetopause, the dome is showing closed flux
    outside its own boundary and one of the two is wrong for that state.

    Two measurements, both by bisection on the tracer's own open/closed
    classification:

      * 'lcfl_nose_re' - the largest radius on the +X_GSM axis whose line is
        still closed. Directly comparable to the Shue nose.
      * one row per magnetic local time - the polar-cap boundary latitude
        (last closed seed latitude at r = 1.02 R_E) and that line's apex
        radius, against the Shue radius in the apex's own direction.

    HONEST LIMITS, and they matter for reading the table:
      * a trace that reaches LCFL_RLIM_RE counts as OPEN, so any row whose apex
        lands near it is rlim-truncated, not physics: the nightside last closed
        line really does stretch far past 40 R_E. Those rows carry
        'truncated': True and must not be asserted on.
      * T89 has NO dynamic-pressure term at all. Its magnetopause is fixed per
        iopt, so at storm pressures its last closed field line CANNOT follow a
        Shue nose driven inward by Pdyn, and the two are expected to part
        company. That divergence is the point of printing it.

    Costs ~4 s at T89 and ~27 s at T96 for n_mlt=8. Validation only.
    """
    from . import fieldlines as fl

    t0 = time.time()
    closed, sm_to_gsm, count = _lcfl_tracer(state)
    shue_nose = float(magnetopause_radius(0.0, state))
    lcfl_nose = _lcfl_subsolar(closed, count)

    rows = []
    for k in range(int(n_mlt)):
        mlt = 24.0 * k / int(n_mlt)

        def closed_at(lat):
            seed_sm = np.array(fl._sm_point(lat, mlt))
            return closed(seed_sm @ sm_to_gsm.T, seed_sm)

        ok_lo, line = closed_at(LCFL_LAT_LO_DEG)
        row = {"mlt_h": mlt, "polar_cap_lat_deg": None,
               "last_closed_eq_re": None, "apex_theta_deg": None,
               "shue_re": None, "pct_diff": None, "truncated": True,
               "shue_clamped": False}
        if not ok_lo:
            rows.append(row)
            continue
        lo, hi = LCFL_LAT_LO_DEG, LCFL_LAT_HI_DEG
        if closed_at(LCFL_LAT_HI_DEG)[0]:
            lo = LCFL_LAT_HI_DEG
        else:
            for _ in range(LCFL_LAT_ITERS):
                mid = 0.5 * (lo + hi)
                ok, ln = closed_at(mid)
                if ok:
                    lo, line = mid, ln
                else:
                    hi = mid
        r = np.linalg.norm(line, axis=1)
        apex = line[int(np.argmax(r))]
        eq_re = float(r.max())
        theta = math.atan2(math.hypot(float(apex[1]), float(apex[2])),
                           float(apex[0]))
        shue = float(magnetopause_radius(theta, state))
        row.update(polar_cap_lat_deg=float(lo), last_closed_eq_re=eq_re,
                   apex_theta_deg=math.degrees(theta), shue_re=shue,
                   pct_diff=100.0 * (eq_re - shue) / shue,
                   truncated=bool(eq_re >= LCFL_TRUNCATED_FRAC * LCFL_RLIM_RE),
                   # theta -> 180 deg is where the Shue radius diverges and the
                   # bisection returns its own search bound: a number to label,
                   # not to compare against.
                   shue_clamped=bool(shue >= R_BISECT_HI_RE - 1e-3))
        rows.append(row)

    return {"epoch": state["epoch"], "model": state["model"]["external"],
            "n_mlt": int(n_mlt), "rlim_re": LCFL_RLIM_RE,
            "shue_nose_re": shue_nose, "lcfl_nose_re": lcfl_nose,
            "nose_pct_diff": (None if lcfl_nose is None else
                              100.0 * (lcfl_nose - shue_nose) / shue_nose),
            "rows": rows, "n_traces": int(count[0]),
            "elapsed_s": time.time() - t0}


def magnetopause_mesh(state: dict) -> dict:
    """The drawn magnetopause, as the mesh dict every exporter already expects.

    Drop-in for `boundary_mesh(magnetopause_radius, state)`: same keys, same
    station counts, same morph key. 'x', 'rho' and 'r' are now (N_THETA, N_PHI)
    rather than (N_THETA,), which dsexport.mesh.surface_vertices dispatches on.
    """
    lat = magnetopause_lattice(state)
    x, rho = lat["x"], lat["rho"]
    hy = lat["hybrid"]
    nose = float(np.asarray(np.hypot(x, rho))[0].ravel()[0])
    box = ("inside Lin's fitted box" if hy["in_fitted_box"] else
           f"OUTSIDE Lin's fitted box by {abs(hy['bz_excess_nt']):.1f} nT Bz / "
           f"{abs(hy['pd_excess_npa']):.1f} nPa Pd")
    mesh = {
        "x": x, "rho": rho, "r": np.hypot(x, rho), "frac": STATION_FRAC,
        "theta": np.arctan2(rho, x), "lattice": lat,
        "tail_radius_re": lat["tail_radius_re"],
        "param": [
            f"magnetopause HEAD from Lin et al. 2010 (doi:{lin2010.DOI}) 3-D surface: "
            f"{N_HEAD} rays x {N_PHI} azimuths, dipole tilt "
            f"{math.degrees(lat['tilt_rad']):+.1f} deg, evaluated in aberrated GSM "
            f"({math.degrees(lat['alpha_rad']):.2f} deg about +Z)",
            f"subsolar distance {hy['r0_re']:.3f} R_E = {hy['w_lin']:.3f} x Lin "
            f"{hy['lin_r0_re']:.3f} + {1.0 - hy['w_lin']:.3f} x Shue 1998 "
            f"{hy['shue_r0_re']:.3f} - drivers are {box}, Lin inputs clamped to it",
            f"TAIL parameterized by X STATION per azimuth: {N_THETA - N_HEAD} stations "
            f"to {X_TAIL_RE:g} R_E, each meridian relaxing its own flare toward a "
            f"{lat['tail_radius_re']:.1f} R_E cylinder (observed distant tail, not an "
            f"extrapolation of the head)",
            f"nose {nose:.3f} R_E, opacity {OPACITY_NOSE:g} nose -> {OPACITY_TAIL:g} "
            f"tail edge (linear in ring index)",
        ],
    }
    return mesh


# The sheath must stay a sheath. With an axisymmetric pause and an axisymmetric
# conic that was automatic; with Lin's asymmetric flanks and cusp indentations
# against a symmetric conic, the shock CAN cut inside the pause. So the shock is
# not rebuilt from scratch - it inherits the pause's azimuthal shape.
#
# Along each ray direction (theta, phi), let
#
#   g(theta, phi) = r_mp(theta, phi) / mean_phi r_mp(theta)
#
# be the pause's RELATIVE shape about the axis - 1 where the pause is round, less
# than 1 in a cusp, more on a bulging flank. The shock's position vector at each
# of its stations is then scaled by g at that station's own angle:
#
#   (x, rho)_bs(theta, phi) = g(theta, phi) * (x, rho)_bs_conic(theta)
#
# Scaling the whole vector leaves the direction alone, so the station is still at
# angle theta, and the gas-dynamic conic still sets the sheath THICKNESS at every
# angle (nose standoff from Farris & Russell, flaring from Slavin & Holzer). All
# the pause contributes is its shape around the axis. That gives the invariant:
#
#   r_bs(theta, phi) / r_mp(theta, phi) = r_bs_conic(theta) / mean_phi r_mp(theta)
#
# which is INDEPENDENT OF PHI. So if the conic clears the mean pause at some
# angle, the 3-D shock clears the 3-D pause at every azimuth at that angle - by
# construction, not by luck, and the test checks the ratio itself rather than
# sampling and hoping.
#
# Keying on x instead was the obvious first try and it is wrong: r_mp is zero at
# the nose and undefined sunward of it, which is exactly where the shock is.
BS_SHAPE_FACTOR_MIN = 0.55        # a cusp cannot pinch the shock to nothing
BS_SHAPE_FACTOR_MAX = 1.80
# Floor on the sheath thickness, as a fraction of the pause radius. The conic is
# fitted against SHUE, and the drawn pause is now Lin-hybrid (about 3% further out
# at the nose), so the conic is no longer guaranteed to clear it. Rather than let
# the surfaces touch, the shock is pushed out to this minimum and the event is
# logged and reported - a visible, named correction instead of a silent crossing.
BS_MIN_SHEATH_FRAC = 0.04


def _mp_radius_at_theta(mp_lattice: dict, theta_query: np.ndarray) -> np.ndarray:
    """Drawn magnetopause radius at arbitrary polar angles, per azimuth.

    Returns (len(theta_query), N_PHI). Interpolated along each meridian of the
    DRAWN lattice - not re-evaluated from Lin - so the shock follows the surface
    the audience actually sees, tail relaxation included. Theta increases
    monotonically along every meridian (nose 0 -> tail near pi), which is what
    np.interp needs.
    """
    xm, rm = np.asarray(mp_lattice["x"]), np.asarray(mp_lattice["rho"])
    th_m = np.arctan2(rm, xm)
    r_m = np.hypot(xm, rm)
    tq = np.asarray(theta_query, dtype=float).ravel()
    out = np.empty((tq.size, xm.shape[1]))
    for j in range(xm.shape[1]):
        out[:, j] = np.interp(tq, th_m[:, j], r_m[:, j])
    return out


def bow_shock_lattice(state: dict, mp_lattice: dict | None = None) -> dict:
    """The drawn bow shock as an (N_THETA, N_PHI) lattice that follows the pause."""
    if mp_lattice is None:
        mp_lattice = magnetopause_lattice(state)
    bs = bow_shock_mesh(state)                       # the axisymmetric conic
    x1 = np.asarray(bs["x"], dtype=float)
    rho1 = np.asarray(bs["rho"], dtype=float)
    th1 = np.arctan2(rho1, x1)
    r1 = np.hypot(x1, rho1)

    r_mp = _mp_radius_at_theta(mp_lattice, th1)      # (N_THETA, N_PHI)
    mean_mp = r_mp.mean(axis=1, keepdims=True)
    shape = np.clip(r_mp / mean_mp, BS_SHAPE_FACTOR_MIN, BS_SHAPE_FACTOR_MAX)

    # Sheath floor, applied to the axisymmetric ratio so it stays phi-independent
    # and the invariant survives.
    need = (1.0 + BS_MIN_SHEATH_FRAC) * mean_mp[:, 0]
    push = np.maximum(need / np.maximum(r1, 1e-9), 1.0)
    n_pushed = int((push > 1.0 + 1e-12).sum())
    if n_pushed:
        log.debug("bow shock pushed out at %d/%d stations to keep a %.0f%% sheath "
                  "(conic is fitted against Shue - the drawn pause is Lin-hybrid)",
                  n_pushed, r1.size, 100.0 * BS_MIN_SHEATH_FRAC)

    scale = shape * push[:, None]
    x = x1[:, None] * scale
    rho = rho1[:, None] * scale
    return {"x": x, "rho": rho, "shape_factor": shape, "push": push,
            "n_pushed": n_pushed, "axisymmetric": bs,
            "mp_radius_at_theta": r_mp, "theta": th1}


def bow_shock_mesh_following(state: dict, mp_lattice: dict | None = None) -> dict:
    """bow_shock_mesh, but 3-D and following the pause. Same dict shape."""
    lat = bow_shock_lattice(state, mp_lattice)
    bs = lat["axisymmetric"]
    x, rho = lat["x"], lat["rho"]
    mesh = dict(bs)
    mesh.update({"x": x, "rho": rho, "r": np.hypot(x, rho),
                 "theta": np.arctan2(rho, x), "lattice": lat})
    sf = lat["shape_factor"]
    note = (f" - pushed out at {lat['n_pushed']} of {len(lat['push'])} stations to hold a "
            f"{100.0 * BS_MIN_SHEATH_FRAC:.0f}% minimum sheath" if lat["n_pushed"] else "")
    mesh["param"] = list(bs.get("param", [])) + [
        f"shock FOLLOWS the magnetopause: at each polar angle the whole position is "
        f"scaled by that azimuth's pause radius over the ring mean (range "
        f"{sf.min():.3f}-{sf.max():.3f}), so the sheath thickness ratio is identical at "
        f"every azimuth and the shock cannot cut inside the pause anywhere{note}",
    ]
    return mesh


# --- export -------------------------------------------------------------------

def _surface_comments(state: dict, name: str, mesh: dict, extra=(),
                      frame=geo.DEFAULT_FRAME,
                      coord_digits: int = geo.DEFAULT_COORD_DIGITS) -> list[str]:
    lines = [
        f"{name} surface, {N_THETA - 1} rings x {N_PHI} azimuths + "
        f"{N_PHI} meridians x {N_THETA} stations (ring index is the morph key)",
    ]
    lines.extend(mesh["param"])
    lines.extend(geo.coord_comments(coord_digits))
    lines.extend(extra)
    return state_comments(state, lines, frame=frame)


def export_boundaries(state: dict, prev_state, out_dir,
                      frame=geo.DEFAULT_FRAME,
                      coord_digits: int = geo.DEFAULT_COORD_DIGITS,
                      shells: bool = False) -> dict:
    """Write MagnetopauseShell.obj + BowShockShell.obj (effectModelClass shells).

    2026-09-14: the boundary layer is the two translucent OBJ shells; the
    .vla ring/meridian wireframes were retired. Meshes cannot morph, so each
    export is the CURRENT state only (the player crossfades consecutive
    frames); `prev_state` and `shells` are accepted for call-site
    compatibility and ignored. The surface is built and frame-baked at the
    state's epoch through the model gateway, so geopack is left sitting on
    THIS epoch when we are done.

    Returns {'MagnetopauseShell': {...}, 'BowShockShell': {...}, 'nose_re':
    {'Magnetopause': r, 'BowShock': r}, 'frame', 'parametric': False}.
    """
    out_dir = Path(out_dir)
    frame = geo.check_frame(frame)
    cfmt, vfmt = coord_formatter(coord_digits), val_formatter(coord_digits)
    # Each layer builds its own mesh in its own parameter (theta for the
    # magnetopause, x station for the bow shock); the topology they emit is
    # identical, which is all the morph pairing needs.
    # The magnetopause is Lin's 3-D surface on a hybrid subsolar distance; the
    # shock is built FROM it so the sheath can never invert. boundary_mesh /
    # bow_shock_mesh remain as the axisymmetric Shue pair - still the calibration
    # reference for validate gates 1, 2 and 5, and still what a Shue-only render
    # would draw.
    from dsexport.mesh import write_surface

    # RECALC FIRST. The Lin surface reads the dipole tilt from geopack's module
    # global, so building a mesh before this line silently bakes whatever epoch
    # geopack was last left on - which depends on render order and made the export
    # non-reproducible run to run (caught by test_tiny_render_is_reproducible,
    # 2026-09-16). The old axisymmetric Shue path had no epoch dependence at all,
    # which is why the recalc could sit below the mesh build before today.
    recalc_for_state(state)
    mp_mesh = magnetopause_mesh(state)
    bs_mesh = bow_shock_mesh_following(state, mp_mesh["lattice"])
    surfaces = (("Magnetopause", mp_mesh), ("BowShock", bs_mesh))
    result = {"parametric": False, "frame": frame, "nose_re": {}}
    for name, mesh in surfaces:
        # The (x, rho) profile as a closed surface of revolution for Digistar's
        # effectModelClass (dsexport/mesh.py), in export-frame light years at
        # the render's coordinate precision. CURRENT state only.
        # write_surface picks the extension from dsexport.mesh.MESH_FORMAT ("glb"
        # since 2026-09-20 - a quarter the bytes for an identical render).
        sres = write_surface(
            out_dir / f"{name}Shell", mesh, frame=frame,
            comments=([f"epoch {state.get('epoch')}"] + list(mesh.get("param", []))[:1]
                      + list(geo.coord_comments(coord_digits))),
            cap=name in SHELL_CAPPED, coord_fmt=cfmt)
        result[f"{name}Shell"] = sres
        result["nose_re"][name] = float(np.asarray(mesh["r"])[0].ravel()[0])
        log.info("%sShell: %d vertices, %d faces, nose %.3f R_E -> %s", name,
                 sres["vertices"], sres["faces"], result["nose_re"][name], sres["path"])
    return result


# --- demo ---------------------------------------------------------------------

def _demo() -> None:
    """Write demo boundary VLAs: nominal state morphing from the storm state."""
    import json

    repo = Path(__file__).resolve().parents[2]
    states = repo / "tests" / "fixtures" / "states"
    cur = json.loads((states / "nominal.json").read_text())
    prev = json.loads((states / "storm.json").read_text())
    res = export_boundaries(cur, prev, repo / "output" / "magneto" / "demo")
    for name in ("Magnetopause", "BowShock"):
        path = res[name]["path"]
        ok, errors = validate(path, quiet=True)
        log.info("%s: %s %.1f KB nose %.3f R_E %s", path.name,
                 "OK" if ok else "INVALID", path.stat().st_size / 1024,
                 res[name]["nose_re"], errors[:3] if errors else "")
    log.info("crosscheck %s", mgnp_crosscheck(cur))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _demo()
