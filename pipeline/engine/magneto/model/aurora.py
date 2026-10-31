"""OVATION aurora layers for Digistar: probability oval dots + 3D curtains.

Consumes the SWPC OVATION Prime product
(https://services.swpc.noaa.gov/json/ovation_aurora_latest.json): a 1x1 degree
integer grid of aurora probability 0-100 on lon 0..359, lat -90..90.

Two visual layers, both on FIXED cell topologies so consecutive exports morph
row-for-row (parametric VLC requires identical row count/order every frame):

* oval_layer   - one dot per grid cell with |lat| >= 45 at 110 km altitude.
                 92 latitude rows x 360 longitudes = N_OVAL_CELLS = 33120 dots,
                 always, regardless of grid content (invisible cells get
                 opacity 0, they are never dropped).
* curtain_layer - one vertical 8-vertex polyline per 2x2 degree cell with
                 |lat| in [55, 79]: 26 latitude rows x 180 longitudes =
                 N_CURTAIN_CELLS = 4680 polylines, 100 km -> 250 km altitude.

Dayside honesty: cells whose solar zenith angle is < 100 deg (sunlit or civil
twilight) are dimmed x0.2 - the model still predicts aurora there but nobody
would see it, and pretending otherwise reads as a bug on the dome.

Positions are WGS84 geodetic -> export-frame LIGHT YEARS via magneto.geo
(ellipsoidal, never a sphere; light years because Digistar reads a
'coordsys RIGHT' file's numbers as light years).

THE EPOCH MATTERS TWICE, and which of the two depends on the export frame:

* frame='ecef' (the historical bake): the cells are Earth-fixed, so their
  positions never move and the epoch only sets the terminator dimming.
* frame='j2000' (the DEFAULT) and 'gsm': the magnetosphere is exported
  sun-fixed, so the
  aurora has to be carried geodetic -> ECEF -> GEO -> GSM at ITS OWN epoch
  (geo.geodetic_to_frame, the inverse of gsm_to_geo_matrix). The cell
  topology is unchanged - row i is the same geodetic cell in every frame - but
  its coordinates now rotate ~15 deg/hour, which is precisely the aurora
  sweeping underneath a stationary magnetosphere as the Earth turns. That
  conversion needs geopack recalc'd for the epoch, which this module does
  through the model gateway.

Because the positions move with the epoch in the GSM frame, a parametric
export must build its PREVIOUS rows at the PREVIOUS epoch - passing one epoch
for both would freeze the sweep. export_aurora() takes prev_epoch for exactly
that reason.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from . import recalc_for_epoch
from .. import geo
from dsexport import validate, write_dots_vlc, write_lines_vlc
from dsexport.vla import coord_formatter, val_formatter

log = logging.getLogger(__name__)

# --- fixed cell topology (module-level, never varies between frames) --------

# Oval: every integer (lon, lat) cell with |lat| >= 45.
# 46 lats in -90..-45 plus 46 in 45..90 = 92 rows x 360 lons = 33120 cells.
_OVAL_LAT_VALUES = np.concatenate([np.arange(-90, -44), np.arange(45, 91)])
_g = np.meshgrid(np.arange(360), _OVAL_LAT_VALUES, indexing="ij")
OVAL_LON = _g[0].ravel()
OVAL_LAT = _g[1].ravel()
N_OVAL_CELLS = OVAL_LON.size          # 33120
OVAL_ALT_M = 110_000.0                # nominal auroral emission altitude

# Curtains: every 2 deg lon x 2 deg lat cell with |lat| in [55, 79].
# 13 lats per hemisphere (55, 57, .. 79) x 180 lons = 4680 polylines.
_CURTAIN_LAT_VALUES = np.concatenate([np.arange(-79, -54, 2), np.arange(55, 80, 2)])
_g = np.meshgrid(np.arange(0, 360, 2), _CURTAIN_LAT_VALUES, indexing="ij")
CURTAIN_LON = _g[0].ravel()
CURTAIN_LAT = _g[1].ravel()
N_CURTAIN_CELLS = CURTAIN_LON.size    # 4680
CURTAIN_VERTS = 8
_CURTAIN_ALT_M = np.linspace(100_000.0, 250_000.0, CURTAIN_VERTS)
del _g

# --- colours -----------------------------------------------------------------

_GREEN = np.array([0.1, 1.0, 0.35])   # auroral green (557.7 nm-ish)
_YELLOW_GREEN = np.array([0.6, 1.0, 0.3])
_RED = np.array([1.0, 0.3, 0.25])     # high-altitude red (630 nm-ish)

# Piecewise-linear probability -> colour ramp for the oval:
# green below 40, blending to yellow-green across 40-60, to red-topped >= 60.
_RAMP_PROB = np.array([0.0, 40.0, 60.0, 100.0])
_RAMP_RGB = np.vstack([_GREEN, _GREEN, _YELLOW_GREEN, _RED])

TERMINATOR_ZENITH_DEG = 100.0         # sunlit-or-twilight threshold
DAYSIDE_DIM = 0.2                     # opacity multiplier on the dayside


def parse_ovation(payload: dict) -> dict:
    """SWPC OVATION JSON object -> {obs_time, forecast_time, grid}.

    grid is a (360, 181) int array indexed [lon, lat_idx] with
    lat_idx = lat + 90. Raises on a malformed / truncated payload.
    """
    coords = np.asarray(payload["coordinates"], dtype=int)
    if coords.shape != (360 * 181, 3):
        raise ValueError(f"expected 65160 [lon, lat, val] rows, got {coords.shape}")
    lon, lat, val = coords.T
    if lon.min() < 0 or lon.max() > 359 or lat.min() < -90 or lat.max() > 90:
        raise ValueError("OVATION lon/lat outside 0..359 / -90..90")
    grid = np.zeros((360, 181), dtype=int)
    grid[lon, lat + 90] = val
    return {
        "obs_time": payload["Observation Time"],
        "forecast_time": payload["Forecast Time"],
        "grid": grid,
    }


# --- terminator ---------------------------------------------------------------

def _as_time(epoch_utc):
    """ISO string (trailing Z ok) or datetime -> astropy Time (UTC)."""
    from .. import offline
    offline.quiet_astropy()                    # no IERS / leap-second downloads (magneto/offline.py)
    from astropy.time import Time
    if isinstance(epoch_utc, str):
        return Time(epoch_utc.replace("Z", ""), format="isot", scale="utc")
    return Time(epoch_utc, scale="utc")


def _gmst_deg(t) -> float:
    """Greenwich mean sidereal time, degrees, from UTC as a UT1 proxy.

    |UT1-UTC| < 0.9 s -> < 0.004 deg error; keeps the module offline-safe
    (no IERS table download, which astropy's ITRS path would want for 2026).
    """
    d = t.jd - 2451545.0
    tc = d / 36525.0
    return (280.46061837 + 360.98564736629 * d + 3.87933e-4 * tc * tc) % 360.0


def solar_zenith_deg(lons, lats, epoch_utc) -> np.ndarray:
    """Solar zenith angle (deg) at geodetic (lon, lat) cells, vectorized.

    Subsolar point from astropy get_sun (one Time object): subsolar lat = GCRS
    dec, subsolar lon = GCRS RA - GMST(UTC~UT1). GCRS vs of-date equinox and
    the spherical-Earth zenith formula each cost < 0.5 deg - ample for a
    terminator dimming threshold, and it needs no IERS/network access.
    """
    from astropy.coordinates import get_sun
    t = _as_time(epoch_utc)
    sun = get_sun(t)
    lat_s = np.radians(float(sun.dec.deg))
    lon_s = np.radians((float(sun.ra.deg) - _gmst_deg(t) + 180.0) % 360.0 - 180.0)
    lat = np.radians(np.asarray(lats, dtype=float))
    lon = np.radians(np.asarray(lons, dtype=float))
    cos_z = (np.sin(lat) * np.sin(lat_s)
             + np.cos(lat) * np.cos(lat_s) * np.cos(lon - lon_s))
    return np.degrees(np.arccos(np.clip(cos_z, -1.0, 1.0)))


def _dayside_factor(lons, lats, epoch_utc) -> np.ndarray:
    """Opacity multiplier: DAYSIDE_DIM where the Sun is up (zenith < 100 deg)."""
    zen = solar_zenith_deg(lons, lats, epoch_utc)
    return np.where(zen < TERMINATOR_ZENITH_DEG, DAYSIDE_DIM, 1.0)


# --- layers -------------------------------------------------------------------

def _prob_rgb(prob: np.ndarray) -> np.ndarray:
    """Probability (0-100) -> (N, 3) colour via the piecewise-linear ramp."""
    return np.stack(
        [np.interp(prob, _RAMP_PROB, _RAMP_RGB[:, c]) for c in range(3)], axis=-1)


def _frame_positions(lons, lats, alt_m, epoch_utc, frame):
    """Geodetic cells -> export-frame light years, recalc'ing when needed.

    Both rotating frames ('j2000', 'gsm') need geopack's GEO rotation for THIS
    epoch - and 'j2000' additionally needs the epoch itself, for the precession
    term, which the recalc gateway records. 'ecef' needs nothing at all. Kept
    in one place so both layers rotate identically and neither can silently
    skip the recalc.
    """
    frame = geo.check_frame(frame)
    if frame != geo.FRAME_ECEF:
        recalc_for_epoch(str(epoch_utc))
    return geo.geodetic_to_frame(lons, lats, alt_m, frame)


def oval_layer(grid: np.ndarray, epoch_utc, frame=geo.DEFAULT_FRAME) -> np.ndarray:
    """Aurora probability oval as (N_OVAL_CELLS, 7) dot rows, export-frame m.

    Row = (x, y, z, opacity, r, g, b). Opacity 0 below prob 8, else
    0.15 + 0.85*prob/100, times the dayside dimming. Topology is fixed:
    always exactly N_OVAL_CELLS rows in the same order - only the coordinates
    move, and only in the 'gsm' frame (see the module docstring).
    """
    prob = grid[OVAL_LON, OVAL_LAT + 90].astype(float)
    x, y, z = _frame_positions(OVAL_LON, OVAL_LAT, OVAL_ALT_M, epoch_utc, frame)
    op = np.where(prob < 8.0, 0.0, 0.15 + 0.85 * prob / 100.0)
    op *= _dayside_factor(OVAL_LON, OVAL_LAT, epoch_utc)
    return np.column_stack([x, y, z, op, _prob_rgb(prob)])


def curtain_layer(grid: np.ndarray, epoch_utc, frame=geo.DEFAULT_FRAME) -> list:
    """Aurora curtains: N_CURTAIN_CELLS vertical polylines, each (8, 7).

    One polyline per fixed 2x2 deg cell, 100 km -> 250 km altitude. Colour
    green at the base blending to red at the top. Opacity proportional to
    probability (0 below prob 25), tapering to 0.3x at the top vertex, times
    the dayside dimming. Topology is fixed cell-for-cell and vertex-for-vertex.
    """
    prob = grid[CURTAIN_LON, CURTAIN_LAT + 90].astype(float)
    base_op = np.where(prob < 25.0, 0.0, prob / 100.0)
    base_op *= _dayside_factor(CURTAIN_LON, CURTAIN_LAT, epoch_utc)

    frac = np.linspace(0.0, 1.0, CURTAIN_VERTS)          # 0 base -> 1 top
    x, y, z = _frame_positions(CURTAIN_LON[:, None], CURTAIN_LAT[:, None],
                               _CURTAIN_ALT_M[None, :], epoch_utc, frame)
    out = np.empty((N_CURTAIN_CELLS, CURTAIN_VERTS, 7))
    out[..., 0], out[..., 1], out[..., 2] = x, y, z
    out[..., 3] = base_op[:, None] * (1.0 - 0.7 * frac)[None, :]
    out[..., 4:7] = (_GREEN + frac[:, None] * (_RED - _GREEN))[None, :, :]
    return list(out)


# --- export -------------------------------------------------------------------

def _grid_and_times(g):
    """Accept a parse_ovation() dict or a bare (360, 181) grid array."""
    if isinstance(g, dict):
        return g["grid"], g.get("obs_time"), g.get("forecast_time")
    return np.asarray(g), None, None


def export_aurora(cur_grids, prev_grids, epoch, out_dir, prev_epoch=None,
                  frame=geo.DEFAULT_FRAME, with_curtains: bool = True,
                  coord_digits: int = geo.DEFAULT_COORD_DIGITS) -> dict:
    """Write AuroraOval.vlc (+ AuroraCurtains.vlc when with_curtains), prev -> cur.

    D/P/L rows carry the previous grid at the PREVIOUS epoch, V rows the
    current grid at the current one, so the dome morphs from the last OVATION
    frame into this one - and, in the sun-fixed 'gsm' frame, sweeps the oval
    under the magnetosphere by the Earth rotation between the two epochs.
    prev_epoch defaults to epoch (a static previous frame, honest on a restart).
    cur_grids / prev_grids are parse_ovation() dicts (preferred - their
    obs/forecast times land in the header comments) or bare (360, 181) arrays.
    Returns paths + counts.
    """
    out_dir = Path(out_dir)
    frame = geo.check_frame(frame)
    cfmt, vfmt = coord_formatter(coord_digits), val_formatter(coord_digits)
    prev_epoch = epoch if prev_epoch is None else prev_epoch
    cur_grid, cur_obs, cur_fc = _grid_and_times(cur_grids)
    prev_grid, prev_obs, prev_fc = _grid_and_times(prev_grids)

    comments = [
        "OVATION Prime aurora probability grid (SWPC ovation_aurora_latest)",
        "oval = modeled probability of aurora, dimmed side is in daylight",
        f"terminator dimming x{DAYSIDE_DIM} where solar zenith < "
        f"{TERMINATOR_ZENITH_DEG:g} deg at epoch {epoch}",
        "morph: D/P/L rows = previous grid, V rows = current grid",
        f"previous epoch {prev_epoch} -> current epoch {epoch}",
    ] + geo.frame_comments(frame) + geo.coord_comments(coord_digits)
    if cur_obs:
        comments.append(f"current obs {cur_obs} forecast {cur_fc}")
    if prev_obs:
        comments.append(f"previous obs {prev_obs} forecast {prev_fc}")

    # Previous rows FIRST, so geopack is left sitting on the current epoch.
    prev_oval = oval_layer(prev_grid, prev_epoch, frame)

    oval_path = out_dir / "AuroraOval.vlc"
    n_oval = write_dots_vlc(oval_path, prev_oval,
                            next_points=oval_layer(cur_grid, epoch, frame),
                            comments=comments, coord_fmt=cfmt, val_fmt=vfmt)

    # Curtains are OFF by default since the 2026-08-12 show-look decision -
    # the curtain look comes from Digistar's native planet aurora instead
    # (dsexport.ds.write_native_aurora_ds). The writer stays for opt-in.
    curtain_path = None
    n_curtain_rows = 0
    if with_curtains:
        prev_curtains = curtain_layer(prev_grid, prev_epoch, frame)
        curtain_path = out_dir / "AuroraCurtains.vlc"
        n_curtain_rows = write_lines_vlc(
            curtain_path, prev_curtains,
            next_polylines=curtain_layer(cur_grid, epoch, frame),
            comments=comments, coord_fmt=cfmt, val_fmt=vfmt)
    log.info("aurora export: %d oval dots, %d curtain polylines -> %s",
             n_oval, N_CURTAIN_CELLS if with_curtains else 0, out_dir)
    return {
        "oval": oval_path, "curtains": curtain_path,
        "n_oval": n_oval,
        "n_curtains": N_CURTAIN_CELLS if with_curtains else 0,
        "n_curtain_rows": n_curtain_rows,
    }


# --- demo ---------------------------------------------------------------------

def _demo() -> None:
    """Write demo aurora VLCs from cached-or-fixture OVATION + nominal state."""
    repo = Path(__file__).resolve().parents[2]
    fixture = repo / "tests" / "fixtures" / "payloads" / "ovation.json"
    cached = repo / "cache" / "ovation" / "latest.json"
    cur_path = cached if cached.exists() else fixture
    snaps = sorted((repo / "cache" / "ovation").glob("2*.json"))
    prev_path = snaps[0] if snaps else cur_path

    cur = parse_ovation(json.loads(cur_path.read_text()))
    prev = parse_ovation(json.loads(prev_path.read_text()))
    state = json.loads(
        (repo / "tests" / "fixtures" / "states" / "nominal.json").read_text())

    res = export_aurora(cur, prev, state["epoch"], repo / "output" / "magneto" / "demo")
    for key in ("oval", "curtains"):
        ok, errors = validate(res[key], quiet=True)
        size = res[key].stat().st_size
        log.info("%s: %s, %.1f KB, valid=%s %s",
                 res[key].name, "OK" if ok else "INVALID", size / 1024, ok,
                 errors[:3] if errors else "")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _demo()
