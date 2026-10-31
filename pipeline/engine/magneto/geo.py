"""Coordinate helpers shared by Build A modules.

Three jobs:
1. WGS84 geodetic -> ECEF meters (aurora, surface overlays). Ellipsoidal, not
   spherical - a sphere puts high-latitude points tens of meters off the globe.
2. Frame rotation matrices built from geopack basis transforms, so bulk point
   sets (field lines, particles, meshes) convert GSM->GEO(ECEF) or SM->GSM with
   one matrix multiply instead of one scalar geopack call per vertex.
3. THE EXPORT FRAME. Everything Build A exports goes out through
   ``gsm_re_to_frame`` / ``ecef_m_to_frame`` in one of three frames, and always
   in LIGHT YEARS (see the units note below):

   * ``'j2000'`` (the DEFAULT, live and event) - geocentric equatorial J2000,
     which is exactly the axis triad Digistar's own World Coordinate System
     uses (+Z north celestial pole, +X vernal equinox of J2000). The structure
     is still SUN-FIXED in the physical sense - each frame is baked at its own
     epoch, so the magnetopause nose points at where the Sun really was - but
     the dome side needs NO orientation tokens at all: parent the holder to the
     Earth for position and set ``standardSpace on`` so the Earth's rotation
     and scale are not inherited. That is the whole loader contract.
   * ``'gsm'`` - the raw physics frame, Earth-centred and sun-fixed with +X
     always exactly sunward. Kept for diagnostics and for a holder whose
     attitude is driven externally; a GSM export dropped into a WCS-aligned
     holder points its nose at the vernal equinox, not at the Sun.
   * ``'ecef'`` - the Earth-fixed bake. Correct for a holder parented to the
     rotating Earth with no standardSpace, which is what ``EarthFrameCheck``
     wants; in an event replay the whole magnetosphere would spin with the
     planet, which is what the J2000 default exists to fix.

   The three are NOT interchangeable on the dome. Every exported file states
   its frame, and the required holder setup, in its own header comments.

UNITS. Digistar reads ``set coordsys RIGHT`` coordinates as LIGHT YEARS (Users
Guide, "Creating VLA and VL Model Files"; ``coordsys LEFT`` would be metres but
also flips handedness). So every export-frame helper here returns light years,
not metres - ``RE_LY`` is the one conversion constant. This mirrors the verified
solar players, which write ``xyz_rs * RS_TO_LY`` under ``coordsys RIGHT``.
Coordinates that small must never go out through ``%.6f``, which writes a file
of zeros. The science layers write ``dsexport.vla.fmt_coord_sig`` - 9
significant figures, rounded THROUGH float32, which is exactly what Digistar
cooks a position to - and ``dsexport.vla.fmt_coord`` (full float64 round-trip)
remains the fallback and the mandatory setting for ``EarthFrameCheck.vla``.
Either way a numpy comparison on these numbers needs ``atol=0``.

geopack caveat: every function here that touches geopack requires
`geopack.recalc(ut)` to have been called for the epoch first, and geopack keeps
that state in module-level globals - single render thread only. The 'gsm'
export frame is the one path that needs NO recalc, since its rotation is the
identity.
"""
from __future__ import annotations

import math

import numpy as np

RE_M = 6_371_200.0  # geopack Earth radius (6371.2 km) in meters

# --- export units -------------------------------------------------------------
# 'set coordsys RIGHT' means the file's numbers ARE light years. Nothing on the
# dome side scales them, so the conversion happens here, once.
LY_M = 9.460_730_472_580_800e15   # exact IAU light year, meters
M_TO_LY = 1.0 / LY_M
RE_LY = RE_M * M_TO_LY            # 6.73436e-10 ly per Earth radius

# How many significant digits a science layer writes its light-year
# coordinates to. 9 is the default everywhere (live and event): Digistar cooks
# positions to DXGI_FORMAT_R32G32B32_FLOAT, so 9 digits round-trips binary32
# EXACTLY (dsexport.vla.fmt_coord_sig rounds through float32 first) while
# cutting roughly a third off every file. 17 - dsexport.vla.fmt_coord, full
# float64 round-trip - stays available through --coord-digits, and remains
# MANDATORY for EarthFrameCheck.vla, whose whole job is metric truth.
DEFAULT_COORD_DIGITS = 9

# --- export frames ------------------------------------------------------------

FRAME_J2000 = "j2000"
FRAME_GSM = "gsm"
FRAME_ECEF = "ecef"
FRAMES = (FRAME_J2000, FRAME_GSM, FRAME_ECEF)
DEFAULT_FRAME = FRAME_J2000


def check_frame(frame) -> str:
    """Normalise + validate an export frame name ('gsm' | 'ecef')."""
    f = str(frame).strip().lower()
    if f not in FRAMES:
        raise ValueError(f"unknown export frame {frame!r}; known: {list(FRAMES)}")
    return f


def frame_comments(frame=DEFAULT_FRAME) -> list[str]:
    """The provenance lines every exported .vla/.vlc carries about its frame."""
    frame = check_frame(frame)
    units = (f"physics GSM R_E; exported coordinates LIGHT YEARS "
             f"(1 R_E = {RE_LY:.5e} ly) - coordsys RIGHT means light years")
    if frame == FRAME_J2000:
        return [
            "frame J2000: geocentric equatorial, +Z north celestial pole, +X "
            "vernal equinox - the same axes as the Digistar WCS",
            "holder: Earth add <holder> then <holder> standardSpace on, so it "
            "takes Earth position but NOT Earth rotation. No attitude needed.",
            "sun-fixed by construction: each frame is baked at its own epoch, "
            "so the magnetopause nose points where the Sun really was",
            units,
        ]
    if frame == FRAME_GSM:
        return [
            "frame GSM: +X sunward, +Z north-dipole-ish; the holder's attitude "
            "must be driven externally - a WCS-aligned holder aims the nose at "
            "the vernal equinox, not the Sun",
            units,
        ]
    return [
        "frame ECEF: Earth-fixed, +X at (0 N, 0 E), +Z the north pole; parent "
        "the holder to the Earth (no standardSpace) so it rides the rotation",
        units,
    ]


def coord_comments(digits: int = DEFAULT_COORD_DIGITS) -> list[str]:
    """The provenance line about how this file's coordinates were FORMATTED.

    Only the reduced form says anything: full float64 round-trip is what every
    writer here did until 2026-08-13 and is still what EarthFrameCheck.vla
    must use, so its presence is the unremarkable case. Nine significant
    figures is remarkable - it is a deliberate truncation - so it is declared,
    together with the reason it loses nothing the GPU would have kept.
    """
    from dsexport.vla import COORD_SIG_DIGITS

    if int(digits) != COORD_SIG_DIGITS:
        return []
    return [f"coordinates {COORD_SIG_DIGITS} significant figures "
            "(float32-faithful - Digistar cooks positions to "
            "DXGI_FORMAT_R32G32B32_FLOAT)"]

# WGS84 ellipsoid
_A = 6378137.0             # semi-major axis, meters
_F = 1 / 298.257223563     # flattening
_E2 = 2 * _F - _F * _F     # first eccentricity squared


def geodetic_to_ecef(lon_deg, lat_deg, h_m=0.0):
    """Geodetic (lon, lat, height above ellipsoid) -> ECEF meters. Vectorized.

    Returns (x, y, z) arrays (or scalars) in meters. +X -> (0N, 0E),
    +Y -> (0N, 90E), +Z -> North Pole.
    """
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    sin_lat, cos_lat = np.sin(lat), np.cos(lat)
    n = _A / np.sqrt(1 - _E2 * sin_lat * sin_lat)
    x = (n + h_m) * cos_lat * np.cos(lon)
    y = (n + h_m) * cos_lat * np.sin(lon)
    z = (n * (1 - _E2) + h_m) * sin_lat
    return x, y, z


def _basis_matrix(transform) -> np.ndarray:
    """3x3 rotation matrix for a geopack scalar transform fn(x, y, z, j=1).

    Column i is the image of unit vector e_i, so M @ v == transform(v).
    Requires geopack.recalc() to have been called for the epoch.
    """
    cols = [transform(1.0, 0.0, 0.0, 1),
            transform(0.0, 1.0, 0.0, 1),
            transform(0.0, 0.0, 1.0, 1)]
    return np.array(cols, dtype=float).T


def gsm_to_geo_matrix() -> np.ndarray:
    """GSM -> GEO rotation matrix at the recalc'd epoch (GEO == ECEF axes)."""
    from geopack import geopack as gp
    # geogsm(j=1) maps GEO->GSM; invert (transpose) for GSM->GEO.
    return _basis_matrix(gp.geogsm).T


def geo_to_gsm_matrix() -> np.ndarray:
    """GEO(ECEF) -> GSM rotation matrix at the recalc'd epoch.

    The exact inverse of gsm_to_geo_matrix(); this is the direction the aurora
    needs, because its cells are born geodetic and have to be carried into the
    sun-fixed frame per epoch (that is what makes the oval sweep underneath a
    stationary magnetosphere instead of the magnetosphere spinning with it).
    """
    from geopack import geopack as gp
    return _basis_matrix(gp.geogsm)


# --- the J2000 export frame ---------------------------------------------------
# geopack's "GEI" (geigeo) is a pure rotation about Z by Greenwich MEAN sidereal
# time, and its `sun` routine uses the MEAN obliquity - so that frame is the
# mean equator and equinox OF DATE. Digistar's WCS is J2000. The difference is
# precession: ~0.34 deg of equinox by 2026, which would rotate the whole
# structure (and slide the aurora ~40 km against the Earth texture). So the
# export applies the IAU 1976 precession explicitly. Nutation is deliberately
# left out on both sides - it is <=17 arcsec, and geopack never had it either.

_ARCSEC_RAD = math.pi / (180.0 * 3600.0)
_JD_J2000 = 2451545.0
_UNIX_JD0 = 2440587.5          # Julian date of the unix epoch

# The epoch recalc_for_epoch() last prepared geopack for, in unix seconds.
# Precession needs the date, which geopack does not expose; magneto.model's
# single recalc gateway records it here so the two can never drift apart.
_epoch_ut: float | None = None


def note_epoch_ut(ut: float) -> None:
    """Record the epoch geopack was just recalc'd for (unix seconds, UTC)."""
    global _epoch_ut
    _epoch_ut = None if ut is None else float(ut)


def epoch_ut() -> float:
    """The recorded recalc epoch, or raise - never silently precess to J2000.0."""
    if _epoch_ut is None:
        raise RuntimeError(
            "no epoch recorded: call magneto.model.recalc_for_epoch()/"
            "recalc_for_state() before any J2000 frame conversion")
    return _epoch_ut


def julian_centuries_j2000(ut: float) -> float:
    """Unix UTC seconds -> Julian centuries since J2000.0.

    TT-vs-UTC (~69 s) is ignored: it moves the precession angles by 4e-5 arcsec.
    """
    return ((ut / 86400.0 + _UNIX_JD0) - _JD_J2000) / 36525.0


def precession_of_date_to_j2000_matrix(t_cy: float) -> np.ndarray:
    """IAU 1976 (Lieske) precession: mean equatorial OF DATE -> J2000.

    Built from active (vector-rotating) elementary rotations, which is why the
    familiar Rz(-z) Ry(theta) Rz(-zeta) product comes out in the of-date ->
    J2000 direction rather than the other way round. Verified end-to-end
    against astropy's Sun position in tests/test_aurora_wind.py: getting this
    transpose backwards leaves the whole structure 0.74 deg off instead of
    0.002 deg. Accurate to well under an arcsecond over the few decades this
    show cares about.
    """
    zeta = (2306.2181 * t_cy + 0.30188 * t_cy**2 + 0.017998 * t_cy**3) * _ARCSEC_RAD
    z = (2306.2181 * t_cy + 1.09468 * t_cy**2 + 0.018203 * t_cy**3) * _ARCSEC_RAD
    theta = (2004.3109 * t_cy - 0.42665 * t_cy**2 - 0.041833 * t_cy**3) * _ARCSEC_RAD

    def rz(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])

    def ry(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])

    return rz(-z) @ ry(theta) @ rz(-zeta)


def geo_to_gei_of_date_matrix() -> np.ndarray:
    """GEO(ECEF) -> GEI mean-of-date at the recalc'd epoch (rotation by -GMST)."""
    from geopack import geopack as gp
    # geigeo(j=1) maps GEI->GEO; invert for GEO->GEI.
    return _basis_matrix(gp.geigeo).T


def geo_to_j2000_matrix(ut=None) -> np.ndarray:
    """GEO(ECEF) -> geocentric equatorial J2000 (the Digistar WCS axes)."""
    t_cy = julian_centuries_j2000(epoch_ut() if ut is None else ut)
    return precession_of_date_to_j2000_matrix(t_cy) @ geo_to_gei_of_date_matrix()


def gsm_to_j2000_matrix(ut=None) -> np.ndarray:
    """GSM -> geocentric equatorial J2000 at the recalc'd epoch."""
    return geo_to_j2000_matrix(ut) @ gsm_to_geo_matrix()


def sm_to_gsm_matrix() -> np.ndarray:
    """SM (dipole-fixed) -> GSM rotation matrix at the recalc'd epoch."""
    from geopack import geopack as gp
    return _basis_matrix(gp.smgsm)


def gse_to_gsm_matrix() -> np.ndarray:
    """GSE -> GSM rotation matrix at the recalc'd epoch."""
    from geopack import geopack as gp
    # gsmgse(j=1) maps GSM->GSE; invert for GSE->GSM.
    return _basis_matrix(gp.gsmgse).T


# --- aberration (GSM -> cGSM) -------------------------------------------------
#
# Earth runs along its orbit at ~29.8 km/s while the solar wind comes radially
# off the Sun, so in Earth's frame the wind arrives from a few degrees off the
# Sun line - the same reason rain slants on a moving car's windscreen. Boundary
# models that are FITTED in the aberrated frame have to be EVALUATED in it.
#
# In GSE, +X is sunward, +Z is ecliptic north, and Y completes the set, which
# puts Earth's orbital velocity along -Y. The wind's apparent velocity is then
# (-v_sw, +v_E, 0), so the direction the flow comes FROM is rotated from +X
# toward -Y by alpha = atan(v_E / v_sw): about 4.3 deg at a 400 km/s wind.
#
# SCOPE - this is the part that is easy to get wrong, and I did get it wrong
# once. Aberration is applied ONLY to:
#   * the direction at which a Lin/Shue magnetopause is evaluated, and
#   * the wind's upstream flow direction.
# It is NOT applied to the Tsyganenko field-line output. T89/T96/TS05 are fitted
# in plain, un-aberrated GSM with the aberration already absorbed into the fit,
# so rotating their result would introduce an error rather than remove one.
# Applying it in exactly one place means nothing downstream has anything to undo.
#
# It is ~4 deg, about 0.7 R_E at the nose, and effectively invisible on the dome.
# It is done because Lin is DEFINED that way, not to change the picture.

V_EARTH_ORBIT_KMS = 29.78          # mean orbital speed, IAU 2015 / JPL constants
ABERRATION_MAX_DEG = 12.0          # guard: a nonsense wind speed must not spin the mesh


def aberration_angle_rad(speed_kms) -> float:
    """Aberration angle alpha = atan(v_Earth / v_sw), radians, always >= 0.

    A missing or absurd wind speed falls back to zero rather than to a wild
    rotation: the show's law is degrade honestly, and an un-aberrated boundary is
    a 4-degree error while a boundary spun by a garbage speed is a broken image.
    """
    try:
        v = float(speed_kms)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v) or v <= 0.0:
        return 0.0
    alpha = math.atan2(V_EARTH_ORBIT_KMS, v)
    return min(alpha, math.radians(ABERRATION_MAX_DEG))


def aberration_angle_for_state(state: dict) -> float:
    """The state's aberration angle, from its measured wind speed."""
    return aberration_angle_rad(((state or {}).get("sw") or {}).get("speed"))


def aberration_matrix(alpha_rad: float) -> np.ndarray:
    """GSM -> cGSM rotation: a right-handed turn of +alpha about Z.

    The aberrated sunward axis is X' = (cos a, -sin a, 0) in GSM - tilted toward
    DUSK, because Earth's orbital motion is along -Y_GSE. A basis rotated by
    R_z(-a) transforms components by its transpose, R_z(+a), which is what this
    returns. Identity at alpha = 0, and its own inverse under negation.

    The sign is the whole content of this function and it is easy to get
    backwards, so `test_aberration_tilts_the_sun_line_toward_dusk` checks the
    axis direction itself rather than a round trip - a round trip passes just as
    happily with the rotation the wrong way.
    """
    c, s = math.cos(float(alpha_rad)), math.sin(float(alpha_rad))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=float)


def gsm_to_cgsm(points, alpha_rad: float):
    """(..., 3) GSM -> aberrated GSM. Pure rotation, so units are preserved."""
    return np.asarray(points, dtype=float) @ aberration_matrix(alpha_rad).T


def cgsm_to_gsm(points, alpha_rad: float):
    """(..., 3) aberrated GSM -> GSM. The exact inverse of gsm_to_cgsm."""
    return np.asarray(points, dtype=float) @ aberration_matrix(-alpha_rad).T


def aberration_comments(alpha_rad: float, speed_kms=None) -> list[str]:
    """The provenance line every aberrated export carries. ASCII, no semicolons."""
    deg = math.degrees(float(alpha_rad))
    if deg <= 0.0:
        return ["aberration: none applied - boundary evaluated in plain GSM"]
    sw = f" at {float(speed_kms):.0f} km/s" if speed_kms else ""
    return [f"aberration: {deg:.2f} deg about GSM +Z{sw} - boundary evaluated in "
            f"aberrated GSM (cGSM), the frame Lin et al. 2010 is fitted in",
            "aberration is NOT applied to the Tsyganenko field lines - those are "
            "fitted in plain GSM and already absorb it"]


def dipole_tilt_rad() -> float:
    """Dipole tilt angle (radians) at the recalc'd epoch - geopack's `psi`.

    Positive when the north magnetic pole leans sunward. It sweeps +-34 deg over
    a year and about +-11 deg over a day, and it is what makes Lin's cusp
    indentations move: theta_n = a19 + a20*tilt, theta_s = a19 - a20*tilt.

    Reads the module global geopack's recalc() sets, so it obeys the same
    single-thread rule as every other geopack call and must be asked AFTER a
    recalc_for_state/recalc_for_epoch. Returns 0.0 if geopack has never been
    recalc'd, which draws an untilted boundary rather than raising - the wrong
    boundary by a few degrees beats a black dome.
    """
    from geopack import geopack as gp
    psi = getattr(gp, "psi", None)
    try:
        return float(psi) if psi is not None and math.isfinite(float(psi)) else 0.0
    except (TypeError, ValueError):
        return 0.0


def gsm_re_to_ecef_m(points_re: np.ndarray) -> np.ndarray:
    """Bulk-convert (..., 3) GSM points in R_E to ECEF meters at the epoch."""
    m = gsm_to_geo_matrix()
    pts = np.asarray(points_re, dtype=float)
    return (pts @ m.T) * RE_M


def gsm_re_rotation(frame=DEFAULT_FRAME) -> np.ndarray:
    """The 3x3 GSM -> export-frame rotation (identity for 'gsm')."""
    frame = check_frame(frame)
    if frame == FRAME_GSM:
        return np.eye(3)
    if frame == FRAME_ECEF:
        return gsm_to_geo_matrix()
    return gsm_to_j2000_matrix()


def gsm_re_to_frame(points_re, frame=DEFAULT_FRAME) -> np.ndarray:
    """(..., 3) GSM points in R_E -> export-frame LIGHT YEARS.

    'j2000' rotate GSM->GEO->GEI(of date)->J2000, then scale. This is the
            default and the only frame the dome loader needs no attitude for.
    'gsm'   identity rotation, pure scale - needs no geopack epoch.
    'ecef'  rotate GSM->GEO at the recalc'd epoch, then scale.
    Both rotating frames REQUIRE geopack.recalc() for the epoch first (and
    'j2000' also requires the epoch to have been recorded - see note_epoch_ut).
    """
    pts = np.asarray(points_re, dtype=float)
    frame = check_frame(frame)
    if frame == FRAME_GSM:
        return pts * RE_LY
    return (pts @ gsm_re_rotation(frame).T) * RE_LY


def ecef_m_to_frame(points_m, frame=DEFAULT_FRAME) -> np.ndarray:
    """(..., 3) ECEF meters -> export-frame LIGHT YEARS (the aurora's direction).

    A geodetic cell is born Earth-fixed and has to be carried into the export
    frame per epoch - that is what makes the oval sweep underneath a structure
    that is not itself spinning. 'ecef' is a pure scale; the other two rotate
    and REQUIRE geopack.recalc() for the epoch first.
    """
    pts = np.asarray(points_m, dtype=float)
    frame = check_frame(frame)
    if frame == FRAME_ECEF:
        return pts * M_TO_LY
    m = geo_to_gsm_matrix() if frame == FRAME_GSM else geo_to_j2000_matrix()
    return (pts @ m.T) * M_TO_LY


def geodetic_to_frame(lon_deg, lat_deg, h_m=0.0, frame=DEFAULT_FRAME):
    """WGS84 geodetic -> export-frame light years. Returns (x, y, z) arrays.

    Composition of geodetic_to_ecef and ecef_m_to_frame, kept as one call so
    aurora-style bulk layers never hand-roll the two-step conversion.
    """
    x, y, z = geodetic_to_ecef(lon_deg, lat_deg, h_m)
    pts = np.stack(np.broadcast_arrays(x, y, z), axis=-1)
    out = ecef_m_to_frame(pts, frame)
    return out[..., 0], out[..., 1], out[..., 2]
