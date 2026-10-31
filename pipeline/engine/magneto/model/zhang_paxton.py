"""Kp-driven statistical auroral oval for curated-event playback.

Why this module exists
----------------------
The live path uses SWPC's OVATION Prime grid (``magneto/model/aurora.py``).
OVATION is **not archived** - there is no public record of the grid SWPC
published during the May 2024 or January 2026 storms - so an event replay has
nothing to feed the aurora layer. What *is* archived, for every storm ever, is
Kp. This module turns Kp into a grid in exactly OVATION's layout so the same
aurora layers run unchanged over archived inputs.

Provenance - read this before putting it on a dome
--------------------------------------------------
``MODEL_PROVENANCE`` is the honest one-liner and ``label(kp)`` is the caption
the dome should carry. In short:

  * This is **not** OVATION and it is **not** the Zhang & Paxton (2008)
    coefficient set. Zhang & Paxton (J. Atmos. Sol.-Terr. Phys. 70, 1231)
    fit TIMED/GUVI FUV auroral energy flux with Epstein functions whose
    parameters are Fourier series in magnetic local time, tabulated per Kp
    bin. Those tables run to hundreds of coefficients; they could not be
    reproduced here from primary sources with any confidence, and inventing
    numbers and calling them Zhang & Paxton would be worse than useless.
  * What this is instead: a **clean parametric oval** with the same physical
    ingredients - a band in magnetic latitude, bounded by an equatorward and a
    poleward boundary that both depend on Kp and magnetic local time, with
    soft edges - whose boundary positions are pinned to published auroral-oval
    anchors (``EQUATORWARD_ANCHORS`` below) and interpolated monotonically in
    between. Every number in it is visible at the top of this file.
  * The Kp driving it is real: for events it is the observed OMNI Kp for that
    epoch. So the *timing and extent* of the oval track the actual storm; the
    *shape* is climatology. Caption it as a modeled oval, never as an
    observation.

Calibration anchors (midnight, equatorward edge of the auroral oval, magnetic
latitude): Kp 2 -> 66 deg, Kp 5 -> 60 deg, Kp 7 -> 56 deg, Kp 9 -> 49 deg,
with Kp 0 -> 68 deg completing the quiet end. These are the boundaries the
build brief specifies and they agree with the usual statistical oval pictures
(Feldstein-Starkov, OVATION climatology) to about a degree.

Coordinates
-----------
The oval is built in centred-dipole geomagnetic coordinates and rotated into
geodetic longitude/latitude using a **fixed** dipole north pole at
80.7 N, 72.7 W. Fixed means: no IGRF epoch handling, no secular variation, no
network. The dipole pole moves ~0.05 deg/yr, so over the 2024-2026 span of the
event library the error from freezing it is well under a degree; the centred
dipole approximation itself costs 1-2 deg against a proper AACGM conversion.
That is acceptable for a 1 deg probability grid that is going to be drawn as
soft dots at 110 km.

Magnetic local time needs the Sun, so it needs an epoch:

  * ``oval_probability_grid(kp, epoch_utc)`` places the oval correctly - the
    wide, bright sector sits at magnetic midnight.
  * ``oval_probability_grid(kp)`` with no epoch returns the MLT-averaged ring
    (a valid, if less interesting, grid) rather than guessing an orientation.

API
---
``oval_probability_grid`` returns a ``(360, 181)`` int array of probability
0-100 indexed ``[lon, lat + 90]`` - the identical convention
``aurora.parse_ovation`` produces, so it is a drop-in for ``aurora.oval_layer``
and ``aurora.curtain_layer``.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

MODEL_PROVENANCE = (
    "Modeled auroral oval: Kp-parameterised statistical oval (not OVATION, not "
    "the Zhang & Paxton 2008 coefficient set), boundaries calibrated to "
    "published midnight anchors; driven by observed Kp.")

# --- calibration ----------------------------------------------------------------

# Equatorward boundary of the oval at magnetic midnight, magnetic latitude (deg).
# Monotone-decreasing anchor table; linear interpolation between anchors, and
# the end anchors extend flat beyond Kp 0 / Kp 9.
EQUATORWARD_ANCHORS = ((0.0, 68.0), (2.0, 66.0), (5.0, 60.0),
                       (7.0, 56.0), (9.0, 49.0))
_ANCH_KP = np.array([a[0] for a in EQUATORWARD_ANCHORS])
_ANCH_LAT = np.array([a[1] for a in EQUATORWARD_ANCHORS])

# Oval width (poleward minus equatorward boundary) at magnetic midnight, deg:
# 5 deg quiet growing to 8 deg at Kp 9, per the build brief.
WIDTH_MIDNIGHT_KP0 = 5.0
WIDTH_MIDNIGHT_KP9 = 8.0
# The dayside oval is narrower than the nightside one; this is the noon/midnight
# width ratio, applied smoothly through the flanks.
WIDTH_NOON_RATIO = 0.6

# Noon-midnight offset of the equatorward boundary (deg): the oval is a ring
# centred on the pole but pushed toward the nightside. ~8.5 deg quiet, growing
# slightly with activity (Kp 9: midnight 49 -> noon ~59).
NOON_OFFSET_KP0 = 8.5
NOON_OFFSET_PER_KP = 0.15

# The most equatorward point of the real oval sits just pre-midnight (Harang /
# substorm sector), not exactly at 00 MLT.
MLT_OF_MINIMUM = 23.0

# Peak probability inside the band, and how much of it survives on the dayside
# (magnetic-local-time occurrence, independent of the sunlight dimming that
# aurora.py applies separately - the two are not the same effect).
PEAK_PROB_KP0 = 25.0
PEAK_PROB_PER_KP = 8.0
PEAK_PROB_MAX = 95.0
DAYSIDE_PROB_RATIO = 0.6

# Soft-edge scale heights (deg). The equatorward edge of an auroral oval is
# noticeably sharper than the poleward one.
EDGE_SCALE_EQUATORWARD = 1.1
EDGE_SCALE_POLEWARD = 1.8

# Fixed centred-dipole north pole, geodetic (deg). IGRF-13/14 epoch ~2020-2025.
DIPOLE_POLE_LAT = 80.7
DIPOLE_POLE_LON = -72.7

GRID_SHAPE = (360, 181)          # [lon 0..359, lat+90 0..180] - OVATION's layout
_MLT_AVERAGE_SAMPLES = 24        # used when no epoch is supplied


# --- boundaries -----------------------------------------------------------------

def _kp(kp) -> np.ndarray:
    return np.clip(np.asarray(kp, dtype=float), 0.0, 9.0)


def _mlt_phase(mlt):
    """0 at the most-equatorward MLT, 1 at the opposite (dayside) meridian.

    A raised cosine in magnetic local time, shifted so its minimum lands at
    MLT_OF_MINIMUM rather than exactly at midnight.
    """
    ang = 2.0 * np.pi * (np.asarray(mlt, dtype=float) - MLT_OF_MINIMUM) / 24.0
    return 0.5 * (1.0 - np.cos(ang))


def midnight_equatorward_boundary(kp) -> np.ndarray:
    """Equatorward oval boundary at magnetic midnight, magnetic latitude (deg).

    Monotone-decreasing piecewise-linear interpolation of EQUATORWARD_ANCHORS.
    This is the value at the oval's *most equatorward* meridian, which sits at
    MLT_OF_MINIMUM (23 MLT, just pre-midnight); ``equatorward_boundary(kp, 0)``
    is therefore ~0.2 deg poleward of this.
    """
    return np.interp(_kp(kp), _ANCH_KP, _ANCH_LAT)


def equatorward_boundary(kp, mlt) -> np.ndarray:
    """Equatorward oval boundary, magnetic latitude (deg), vs Kp and MLT."""
    k = _kp(kp)
    offset = NOON_OFFSET_KP0 + NOON_OFFSET_PER_KP * k
    return midnight_equatorward_boundary(k) + offset * _mlt_phase(mlt)


def oval_width(kp, mlt) -> np.ndarray:
    """Poleward minus equatorward boundary (deg): grows with Kp, wider at night."""
    k = _kp(kp)
    w_mid = WIDTH_MIDNIGHT_KP0 + (WIDTH_MIDNIGHT_KP9 - WIDTH_MIDNIGHT_KP0) * k / 9.0
    return w_mid * (1.0 - (1.0 - WIDTH_NOON_RATIO) * _mlt_phase(mlt))


def poleward_boundary(kp, mlt) -> np.ndarray:
    """Poleward oval boundary, magnetic latitude (deg)."""
    return equatorward_boundary(kp, mlt) + oval_width(kp, mlt)


def peak_probability(kp, mlt) -> np.ndarray:
    """Probability (0-100) at the centre of the band, vs Kp and MLT."""
    k = _kp(kp)
    p = np.minimum(PEAK_PROB_KP0 + PEAK_PROB_PER_KP * k, PEAK_PROB_MAX)
    return p * (1.0 - (1.0 - DAYSIDE_PROB_RATIO) * _mlt_phase(mlt))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))


def oval_profile(abs_mlat, mlt, kp) -> np.ndarray:
    """Probability 0-100 at |magnetic latitude| for a given MLT and Kp.

    A band between the two boundaries with soft edges: the product of a
    logistic rising at the equatorward boundary and one falling at the
    poleward boundary. Same role as the Epstein function in Zhang & Paxton -
    a single-humped latitude profile - but with the boundaries, not fitted
    coefficients, as its parameters.
    """
    lat = np.asarray(abs_mlat, dtype=float)
    eq = equatorward_boundary(kp, mlt)
    pol = poleward_boundary(kp, mlt)
    band = (_sigmoid((lat - eq) / EDGE_SCALE_EQUATORWARD)
            * _sigmoid((pol - lat) / EDGE_SCALE_POLEWARD))
    # Renormalise so the mid-band value is peak_probability regardless of how
    # much the two soft edges overlap for a narrow oval.
    mid = 0.5 * (eq + pol)
    norm = (_sigmoid((mid - eq) / EDGE_SCALE_EQUATORWARD)
            * _sigmoid((pol - mid) / EDGE_SCALE_POLEWARD))
    return peak_probability(kp, mlt) * band / norm


# --- geomagnetic <-> geodetic ----------------------------------------------------

def _mag_basis():
    """Orthonormal centred-dipole (MAG) basis expressed in Earth-fixed axes.

    Standard convention (Russell 1971, as used by geopack): Z_MAG is the dipole
    north pole; Y_MAG = Z_GEO x Z_MAG normalised, so the geomagnetic prime
    meridian is the great circle through both the dipole and the geographic
    poles; X_MAG = Y_MAG x Z_MAG completes the right-handed set.
    """
    lat = np.radians(DIPOLE_POLE_LAT)
    lon = np.radians(DIPOLE_POLE_LON)
    z = np.array([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    y = np.cross([0.0, 0.0, 1.0], z)
    y /= np.linalg.norm(y)
    x = np.cross(y, z)
    return x, y, z


def geodetic_to_magnetic(lon_deg, lat_deg):
    """Geodetic (lon east, lat) deg -> centred-dipole (mlat, mlon) deg.

    Spherical geometry - the ~0.2 deg geodetic/geocentric latitude difference is
    far below this model's own uncertainty.
    """
    phi, lam = np.broadcast_arrays(np.radians(np.asarray(lon_deg, dtype=float)),
                                   np.radians(np.asarray(lat_deg, dtype=float)))
    r = np.stack([np.cos(lam) * np.cos(phi), np.cos(lam) * np.sin(phi),
                  np.sin(lam)], axis=-1)
    xh, yh, zh = _mag_basis()
    mlat = np.degrees(np.arcsin(np.clip(r @ zh, -1.0, 1.0)))
    mlon = np.degrees(np.arctan2(r @ yh, r @ xh)) % 360.0
    return mlat, mlon


def subsolar_geodetic(epoch_utc):
    """Subsolar point (lon east, lat) in degrees at a UTC epoch.

    Reuses the exact solar-position path aurora.solar_zenith_deg uses (astropy
    get_sun in GCRS, GMST from UTC as a UT1 proxy) rather than restating it:
    same numbers, same offline behaviour, no IERS download.
    """
    from astropy.coordinates import get_sun

    from .aurora import _as_time, _gmst_deg

    t = _as_time(epoch_utc)
    sun = get_sun(t)
    lat = float(sun.dec.deg)
    lon = (float(sun.ra.deg) - _gmst_deg(t) + 180.0) % 360.0 - 180.0
    return lon, lat


def magnetic_local_time(mlon_deg, epoch_utc):
    """Magnetic longitude -> MLT hours, with 12 h at the subsolar magnetic meridian."""
    slon, slat = subsolar_geodetic(epoch_utc)
    _, sun_mlon = geodetic_to_magnetic(slon, slat)
    return ((np.asarray(mlon_deg, dtype=float) - float(sun_mlon)) / 15.0 + 12.0) % 24.0


# --- the grid ---------------------------------------------------------------------

_GRID_LON, _GRID_LAT = np.meshgrid(np.arange(360.0), np.arange(-90.0, 91.0),
                                   indexing="ij")
_GRID_MLAT, _GRID_MLON = geodetic_to_magnetic(_GRID_LON, _GRID_LAT)


def oval_probability_grid(kp, epoch_utc=None) -> np.ndarray:
    """Kp (+ optional epoch) -> (360, 181) int probability grid, 0-100.

    Indexed ``[lon, lat + 90]`` on lon 0..359 / lat -90..90 - byte-identical in
    layout to ``aurora.parse_ovation``'s grid, so every consumer there works
    unchanged. Both hemispheres are filled: the oval is symmetric in magnetic
    latitude, which is what a Kp-only model can honestly claim.

    With ``epoch_utc`` the oval is oriented by magnetic local time (nightside
    wide and bright). Without it, the MLT-averaged ring is returned - see the
    module docstring.
    """
    abs_mlat = np.abs(_GRID_MLAT)
    if epoch_utc is None:
        mlts = np.linspace(0.0, 24.0, _MLT_AVERAGE_SAMPLES, endpoint=False)
        prob = np.mean([oval_profile(abs_mlat, m, kp) for m in mlts], axis=0)
    else:
        prob = oval_profile(abs_mlat, magnetic_local_time(_GRID_MLON, epoch_utc), kp)
    return np.clip(np.rint(prob), 0, 100).astype(int)


def grid_for_state(state: dict) -> np.ndarray:
    """Convenience: the oval for a schema-1 state (its Kp, at its epoch)."""
    return oval_probability_grid(state["kp"]["value"], state["epoch"])


def label(kp) -> str:
    """Dome caption. Says 'modeled' out loud, every time."""
    return (f"Modeled auroral oval - Kp {float(kp):g} "
            f"(statistical Kp model, not an observation)")


def comments(kp, epoch_utc=None) -> list[str]:
    """Provenance lines for an exported .vlc built on this grid."""
    lines = [MODEL_PROVENANCE, label(kp),
             f"midnight equatorward boundary "
             f"{float(midnight_equatorward_boundary(kp)):.1f} deg magnetic latitude",
             f"anchors (Kp, mlat): "
             + " ".join(f"{k:g}/{v:g}" for k, v in EQUATORWARD_ANCHORS),
             f"centred dipole pole fixed at {DIPOLE_POLE_LAT} N "
             f"{abs(DIPOLE_POLE_LON)} W (1-2 deg magnetic->geodetic error)"]
    lines.append(f"oriented by magnetic local time at {epoch_utc}" if epoch_utc
                 else "MLT-averaged (no epoch supplied)")
    return lines


# --- demo ---------------------------------------------------------------------------

def _demo() -> None:                                   # pragma: no cover
    for k in (0, 2, 5, 7, 9):
        g = oval_probability_grid(k, "2024-05-11T02:00:00Z")
        print(f"Kp {k}: midnight eq boundary "
              f"{float(midnight_equatorward_boundary(k)):5.1f} mlat, "
              f"width {float(oval_width(k, 0.0)):.1f} deg, "
              f"grid max {g.max():3d}, cells>=20 {int((g >= 20).sum()):6d}")


if __name__ == "__main__":                             # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _demo()
