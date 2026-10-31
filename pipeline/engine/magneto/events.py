"""Curated storm-event library: archived OMNI -> schema-1 state timelines.

Stage 6 of the brief. A curated event is a directory under ``events/<id>/``
holding

  raw/           the untouched HAPI responses (CSV + info JSON) - provenance
  timeline.jsonl one schema-1 state object per line, 5-minute cadence
  event.json     manifest: window, sources, coverage, peaks, gap list

The timeline states are **identical in shape to live states** (STATE_SCHEMA.md)
with ``tier: "EVENT"`` and ``event_id`` set - ``magneto/render.py`` must not be
able to tell an event from live data. That is the whole point of the stage:
same renderer, archived inputs.

Data source
-----------
CDAWeb HAPI (https://cdaweb.gsfc.nasa.gov/hapi):

  * ``OMNI_HRO_1MIN``   1-minute IMF + plasma, already time-shifted to the bow
    shock nose, plus 1-minute SYM/H from Kyoto. Verified 2026-08-11: covers
    1981-01-01 .. 2026-07-31, so both curated events are in range.
  * ``OMNI2_H0_MRG1HR``  hourly merged OMNI; ``KP1800`` is **Kp x 10** (27 = 3-)
    and ``DST1800`` is the hourly Dst. Only Kp is used for the state; Dst is
    fetched alongside it purely as a manifest cross-check against SYM/H.

Facts about the feed that shape the code (all verified against /hapi/info):

  * Parameters must be requested in the server's declared order or the request
    is refused (magneto.hapi_csv checks it, as hapiclient did).
  * Fill values are per-parameter sentinels (9999.99 nT, 99999.9 km/s,
    999.99 cm-3, 9999999.0 K, 99999 nT, Kp*10 = 99). They are read out of the
    HAPI metadata, never hard-coded here, and masked to NaN before anything
    else touches the numbers.
  * OMNI carries **BX_GSE only**. GSM is obtained from GSE by a rotation about
    the common X axis, so Bx_GSM == Bx_GSE identically - no conversion needed.
  * ``F`` is the magnitude of the average field vector, i.e. the |B| that is
    consistent with the averaged components, so it is the right ``sw.bt``.

Resampling and gaps
-------------------
1-minute samples are binned to 5 minutes and reduced with a **median**, and
each bin is labelled with its **end** time - exactly the live producer rule
("median of valid samples in the trailing 5 minutes"), so an event state and a
live state mean the same thing at the same epoch.

Gap policy, applied per *block* (mag = Bt/Bx/By/Bz, wind = V/n/T, symh) so a
block is always internally consistent:

  * bin valid  <=> every field of the block has at least one unmasked sample
  * gap <= 15 min, bracketed by valid bins  -> linear interpolation
  * longer gap                              -> step-hold the last valid bin,
                                               recorded in the manifest gap list
  * **leading** invalid bins have nothing to hold, so they are trimmed off the
    front of the timeline and reported as ``coverage.trimmed_leading_min``.
    Nothing is ever invented to fill them.

``sw.stale_s`` / ``age_s`` stay honest through all of this: they measure the
epoch minus the newest genuinely observed 1-minute sample in that block, so a
step-held stretch shows up as a climbing age exactly as it would live.

Model
-----
Events run T96 (the brief: "T89 driven by live Kp for the live mode; T96 for
the curated historic events, where all parameters are known after the fact").
``parmod = [pdyn, SYM-H, By_gsm, Bz_gsm, 0 x 6]``; SYM/H stands in for Dst,
which is standard practice and better resolved than the hourly index.

``propagation.lead_s = 235 R_E / speed`` with ``l1_gse_re = [235, 0, 0]``: a
nominal L1 stand-off, because OMNI does not ship a spacecraft position and its
values are *already* propagated to the bow shock nose. So the event epoch is
arrival-at-Earth, and lead_s exists for the wind/L1 visual layer to have a
consistent transit time - it is not a measured lag. Recorded in the manifest.

Look-back events (recent real solar wind)
-----------------------------------------
OMNI lags by weeks, so "what did the Sun do in the last four days" cannot come
from the curated path. ``fetch_lookback`` builds the identical directory from
the real-time L1 products served by CCMC ISWA HAPI
(https://iswa.gsfc.nasa.gov/hapi) under a DYNAMIC id,
``lookback-<YYYYMMDD>-<YYYYMMDD>`` - no registry entry, resolved by scanning
``events/``. It runs T89 off observed Kp (no reanalysed parameters exist for
yesterday) rather than T96. See the section marker "look-back" below for the
verified facts about that feed, including that its B really is GSM.

CLI
---
    python -m magneto.events list
    python -m magneto.events fetch 2024-05-10_gannon
    python -m magneto.events fetch-all
    python -m magneto.events summary 2024-05-10_gannon
    python -m magneto.events fetch-lookback [--days 4] [--end ISO] [--feed ace]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

# Reused from the live producer so the two can never drift apart: the schema
# version, the Earth radius, the atomic writer and the dome age readout.
from livecore.cache import write_atomic
from livecore.state import RE_KM, SCHEMA_VERSION, data_age_str

log = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[1]
EVENTS_DIR = REPO / "events"

# --- the registry -------------------------------------------------------------

# talk: the presenter's talking points for this storm, posted as system messages by the
# player after its title banner (WATCH / PHYSICS / IMPACT; each <= 255 chars, ASCII, no
# quotes or semicolons). Only facts that are widely documented; a storm with no vetted
# notes gets the data-derived KEY MOMENTS line only (magneto.plan.key_moments).
EventSpec = namedtuple("EventSpec", "id title start stop talk", defaults=[()])

EVENTS: dict[str, EventSpec] = {
    "2000-07-15_bastille": EventSpec(
        "2000-07-15_bastille",
        "Bastille Day Storm - July 2000 (1140 km/s)",
        "2000-07-15T00:00:00Z", "2000-07-17T00:00:00Z",
        talk=(
            "WATCH: the CME shock from the X5.7 flare hits L1 about 14:37 UT on 15 Jul - the "
            "pressure jumps roughly tenfold and the magnetopause and bow shock crush inward within a step",
            "PHYSICS: Bz swings to about -60 nT. Southward IMF reconnects at the nose, dayside field "
            "lines peel back into the tail - watch the tail lines stretch and the oval expand",
            "IMPACT: Kp 9, Dst about -300 nT. Aurora seen as far south as Texas - the ASCA X-ray "
            "satellite lost attitude control and never observed again, radio and GPS were disrupted",
        )),
    "2003-11-20_superstorm": EventSpec(
        "2003-11-20_superstorm",
        "November 2003 Superstorm (SYM-H -490 nT)",
        "2003-11-19T18:00:00Z", "2003-11-21T12:00:00Z",
        talk=(
            "WATCH: not the fastest wind (about 700 km/s) - a magnetic cloud arriving on 20 Nov holds "
            "Bz near -50 nT for hours. Sustained southward field, not speed, drives this storm",
            "PHYSICS: hours of steady southward Bz keep dayside reconnection running - the tail loads "
            "and the ring current deepens to the cycle record SYM-H near -490 nT",
            "IMPACT: Kp 9-, the deepest storm of solar cycle 23 by Dst (-422 nT). Aurora reported from "
            "Texas, Florida and the Mediterranean",
        )),
    "2024-05-10_gannon": EventSpec(
        "2024-05-10_gannon",
        "Gannon Storm - May 2024 (Kp 9)",
        "2024-05-10T00:00:00Z", "2024-05-12T12:00:00Z",
        talk=(
            "WATCH: from about 16:30 UT on 10 May several CMEs from active region 13664 arrive stacked "
            "- repeated pressure jumps and Bz dips as each one hits",
            "PHYSICS: back-to-back CMEs merge, so southward Bz keeps returning - each dip re-opens the "
            "dayside and pumps the tail, and the oval reaches mid-latitudes",
            "IMPACT: G5, Kp 9, Dst about -410 nT - the strongest storm in about 20 years. Aurora photographed "
            "from Mexico, Puerto Rico and southern Europe, farm GPS knocked out at planting time",
        )),
    "2024-10-10_g4": EventSpec(
        "2024-10-10_g4",
        "October 2024 G4 Storm (aurora to the Gulf Coast)",
        "2024-10-10T00:00:00Z", "2024-10-12T00:00:00Z",
        talk=(
            "WATCH: the CME from the X1.8 flare of 8-9 Oct arrives about 15:15 UT on 10 Oct as one sharp "
            "shock - a clean single-hit storm to compare with May's stacked arrivals",
            "PHYSICS: Bz near -40 nT for a shorter spell than in May - watch how quickly the "
            "magnetopause rebounds once Bz turns north again",
            "IMPACT: G4 severe, Kp 9-, Dst about -333 nT - the second strongest storm of cycle 25 so far, "
            "and it arrived in the days after Hurricane Milton",
        )),
    "2026-01-19_kp9": EventSpec(
        "2026-01-19_kp9",
        "January 2026 Superstorm (Kp 9-)",
        "2026-01-19T00:00:00Z", "2026-01-21T00:00:00Z"),
}

DEFAULT_EVENT = "2024-05-10_gannon"

# --- HAPI wiring ---------------------------------------------------------------

HAPI_SERVER = "https://cdaweb.gsfc.nasa.gov/hapi"
SW_DATASET = "OMNI_HRO_1MIN"
KP_DATASET = "OMNI2_H0_MRG1HR"
# Order is the server's declared parameter order - magneto.hapi_csv enforces it.
SW_PARAMS = "F,BX_GSE,BY_GSM,BZ_GSM,flow_speed,Vx,Vy,Vz,proton_density,T,SYM_H"
KP_PARAMS = "KP1800,DST1800"

# state field -> HAPI parameter, grouped into the blocks the gap policy uses.
MAG_MAP = {"bt": "F", "bx_gsm": "BX_GSE", "by_gsm": "BY_GSM", "bz_gsm": "BZ_GSM"}
WIND_MAP = {"speed": "flow_speed", "vx_gse": "Vx", "vy_gse": "Vy",
            "vz_gse": "Vz", "density": "proton_density", "temperature": "T"}
SYMH_MAP = {"symh": "SYM_H"}
BLOCKS = (("mag", MAG_MAP), ("wind", WIND_MAP), ("symh", SYMH_MAP))

CADENCE_S = 300                 # 5-minute timeline cadence
BIN_MINUTES = CADENCE_S // 60
GAP_INTERP_S = 15 * 60          # <= 15 min -> linear interpolate, else hold
KP_LEAD_H = 3                   # hourly Kp lead-in so step-hold has a seed
L1_STANDOFF_RE = 235.0          # nominal L1 distance, R_E (see module docstring)
L1_GSE_RE = [L1_STANDOFF_RE, 0.0, 0.0]

# Rounding applied to the stored driving values. Matched to OMNI's own
# precision so the JSONL stays small; pdyn is deliberately NOT rounded so
# invariant 4 (pdyn == 1.6726e-6 n v^2) holds bit-exactly on the stored values.
_ROUND = {"bt": 2, "bx_gsm": 2, "by_gsm": 2, "bz_gsm": 2,
          "speed": 1, "vx_gse": 1, "vy_gse": 1, "vz_gse": 1,
          "density": 2, "temperature": 0}

PDYN_COEFF = 1.6726e-6          # STATE_SCHEMA.md: pdyn = coeff * n * v^2


# --- small shared helpers ------------------------------------------------------

def _iso(dt: datetime) -> str:
    """UTC datetime -> the schema's 'YYYY-MM-DDTHH:MM:SSZ'."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def pdyn_npa(density: float, speed: float) -> float:
    """STATE_SCHEMA.md invariant 4, same formula livecore.state.build_state uses.

    Kept as a function (livecore inlines it) so events and tests reference one
    expression; the coefficient is proton-only on purpose - never let geopack
    recompute pressure from n, v.
    """
    return PDYN_COEFF * density * speed ** 2


# Strictly positive by physics. A density, speed or temperature of exactly 0 (or below) is
# not a quiet measurement, it is a bad sample the server did not flag - ACE SWEPAM's
# real-time feed served 33 five-minute bins of ProtonDensity 0.0 for 20-22 Sep 2026 with a
# valid BulkSpeed beside them (found 2026-09-22 when the look-back rebuild died on it:
# pdyn 0 -> Shue's r0 = ... * pd**-0.15 -> ZeroDivisionError, on any Python). So these go
# to NaN BEFORE binning, where the gap policy interpolates or step-holds them and lists them
# in the manifest's gaps - honest, and never a zero pressure handed to the model.
POSITIVE_FIELDS = ("density", "speed", "temperature")


def _physical(field: str, values):
    """NaN out non-positive samples of a strictly positive quantity; other fields untouched."""
    if field not in POSITIVE_FIELDS:
        return values
    v = np.asarray(values, dtype=float).copy()
    v[~(v > 0.0)] = np.nan
    return v


def iopt_for_kp(kp: float) -> int:
    """STATE_SCHEMA.md: iopt = clamp(round(Kp) + 1, 1, 7) (the T89 index)."""
    return max(1, min(7, int(round(kp)) + 1))


def event_dir(event_id: str) -> Path:
    return EVENTS_DIR / event_id


def _spec(event_id: str) -> EventSpec:
    try:
        return EVENTS[event_id]
    except KeyError:
        raise KeyError(f"unknown event {event_id!r}; known: {sorted(EVENTS)}") from None


# --- HAPI fetch ----------------------------------------------------------------

def _fill_map(meta: dict) -> dict:
    """Parameter name -> fill value as float (None when the server declares none)."""
    out = {}
    for p in meta.get("parameters", []):
        fill = p.get("fill")
        out[p["name"]] = None if fill in (None, "", "null") else float(fill)
    return out


def _columns(data, meta: dict) -> dict:
    """HAPI structured array -> {parameter: float array with fills as NaN}.

    Fill sentinels come from the server metadata, never from a hard-coded
    table - if CDAWeb ever changes one, this follows automatically.

    Non-numeric parameters (the ISWA real-time sets ship a `source` string and
    extra isotime columns) are skipped rather than force-cast; read them with
    _str_col.
    """
    fills = _fill_map(meta)
    types = {p["name"]: p.get("type") for p in meta.get("parameters", [])}
    cols = {}
    for name in data.dtype.names:
        if name == "Time" or types.get(name) in ("string", "isotime"):
            continue
        if data[name].dtype.kind in "SUOMm":       # bytes/str/object/datetime
            continue
        v = np.asarray(data[name], dtype=float)
        fill = fills.get(name)
        if fill is not None:
            v = np.where(np.isclose(v, fill, rtol=1e-6, atol=0.0), np.nan, v)
        cols[name] = v
    return cols


def _times(data, name: str = "Time") -> np.ndarray:
    """HAPI isotime column -> array of aware UTC datetimes."""
    raw = data[name]
    out = []
    for t in raw:
        s = t.decode() if isinstance(t, (bytes, np.bytes_)) else str(t)
        out.append(_parse_iso(s.replace("Z", "")[:19] + "Z"))
    return np.array(out, dtype=object)


def _str_col(data, name: str) -> list[str]:
    """A HAPI string column as a list of str ('' where absent/empty)."""
    if name not in (data.dtype.names or ()):
        return []
    out = []
    for v in data[name]:
        s = v.decode() if isinstance(v, (bytes, np.bytes_)) else str(v)
        out.append(s.strip())
    return out


def _hapi_fetch(dataset: str, params: str, start: str, stop: str, raw_dir: Path,
                server: str = HAPI_SERVER):
    """One HAPI request; the raw CSV + info JSON land in raw_dir as provenance.

    magneto.hapi_csv keeps the untouched server responses under SHORT names
    (<dataset>.json, <dataset>_<t0>_<t1>.csv). CSV rather than binary: the
    archived response should be readable by anyone who opens the directory in
    five years. (hapiclient did this until 2026-10-15; its cache names reached
    253 characters under an installed package - see magneto/hapi_csv.py.)
    """
    from . import hapi_csv

    raw_dir.mkdir(parents=True, exist_ok=True)
    log.info("HAPI %s %s %s %s..%s", server, dataset, params, start, stop)
    data, meta = hapi_csv.fetch(server, dataset, params, start, stop, raw_dir=raw_dir)
    if data is None:
        raise RuntimeError(f"{dataset}: HAPI returned no data object "
                           f"({start}..{stop})")
    return data, meta


# --- resampling + gap policy ---------------------------------------------------

def _bin_median(times, values, start: datetime, n_bins: int):
    """1-minute series -> (median per 5-min bin, newest observed minute per bin).

    Bins are [start + 5k, start + 5(k+1)); the state built from bin k is
    labelled with the bin's END, matching the live 'trailing 5 minutes' rule.
    Returns (median array with NaN where the bin held nothing, offset array
    giving the last observed minute index inside the bin, -1 when empty).
    """
    n_min = n_bins * BIN_MINUTES
    grid = np.full(n_min, np.nan)
    idx = np.array([int((t - start).total_seconds() // 60) for t in times], dtype=int)
    keep = (idx >= 0) & (idx < n_min) & ~np.isnan(values)
    grid[idx[keep]] = values[keep]
    cells = grid.reshape(n_bins, BIN_MINUTES)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN bins
        med = np.nanmedian(cells, axis=1)
    seen = ~np.isnan(cells)
    last = np.where(seen.any(axis=1),
                    BIN_MINUTES - 1 - np.argmax(seen[:, ::-1], axis=1), -1)
    return med, last


def _fill_gaps(series: dict, valid: np.ndarray, epochs, block: str):
    """Apply the gap policy in place to one block's binned series.

    ``valid`` is the per-bin mask (all fields present). Returns
    ``(first_usable_bin, gaps)`` where gaps is the manifest list. Bins before
    ``first_usable_bin`` have no earlier value to hold and must be trimmed.
    """
    good = np.flatnonzero(valid)
    if good.size == 0:
        raise ValueError(f"{block}: no valid samples anywhere in the window")
    first = int(good[0])
    gaps = []

    # interior runs, each bracketed by a valid bin on both sides
    for a, b in zip(good[:-1], good[1:]):
        n_missing = int(b - a - 1)
        if n_missing == 0:
            continue
        span_s = n_missing * CADENCE_S
        if span_s <= GAP_INTERP_S:
            method = "interp"
            w = np.arange(1, n_missing + 1) / (n_missing + 1)
            for key, arr in series.items():
                arr[a + 1:b] = arr[a] + w * (arr[b] - arr[a])
        else:
            method = "hold"
            for arr in series.values():
                arr[a + 1:b] = arr[a]
        gaps.append({"block": block, "method": method,
                     "start": _iso(epochs[a + 1]), "end": _iso(epochs[b - 1]),
                     "minutes": n_missing * BIN_MINUTES})

    # trailing run: a left bracket exists, so hold (and say so)
    last = int(good[-1])
    n_tail = len(valid) - 1 - last
    if n_tail:
        for arr in series.values():
            arr[last + 1:] = arr[last]
        gaps.append({"block": block, "method": "hold",
                     "start": _iso(epochs[last + 1]), "end": _iso(epochs[-1]),
                     "minutes": n_tail * BIN_MINUTES})
    return first, gaps


def _observed_age_s(last_offset: np.ndarray, valid: np.ndarray, n_bins: int):
    """Per-bin age (s) of the newest genuinely observed sample in the block.

    Bin k covers minutes [5k, 5k+5) and is labelled at 5(k+1); a sample at
    in-bin offset ``o`` therefore sits ``(5 - o) * 60`` seconds before the
    label. Held bins inherit the previous bin's sample time, so the age climbs
    exactly as it would on a live feed that had gone quiet.
    """
    age = np.full(n_bins, np.nan)
    age[valid] = (BIN_MINUTES - last_offset[valid]) * 60.0
    running = np.nan
    for k in range(n_bins):
        if valid[k]:
            running = age[k]
        elif not np.isnan(running):
            running = running + CADENCE_S
            age[k] = running
    return age


def _step_held(times, values, epochs, what: str = "Kp"):
    """Coarse index series -> (value, time_tag) per 5-min epoch, step-held.

    The rule is "the last sample tagged at or before this epoch" - never an
    interpolation and never a look-ahead, so a 3-hourly index reads on the dome
    exactly as it does in the bulletin. ``values`` may be (n,) or (n, m); a row
    counts only when every component of it is valid, which keeps a vector
    (e.g. an L1 position) internally consistent.

    Callers request a KP_LEAD_H lead-in so the first epoch of the window always
    has an earlier sample to hold.
    """
    v = np.asarray(values, dtype=float)
    vv = v if v.ndim == 2 else v[:, None]
    ok = ~np.isnan(vv).any(axis=1)
    kt = [t for t, o in zip(times, ok) if o]
    kv = vv[ok]
    if not kt:
        raise ValueError(f"no valid {what} in the requested window")
    edges = np.array([t.timestamp() for t in kt])
    want = np.array([e.timestamp() for e in epochs])
    pos = np.searchsorted(edges, want, side="right") - 1
    # Epochs before the first sample fall back to the first one; the lead-in
    # normally makes this unreachable, and it is reported if it ever fires.
    n_before = int((pos < 0).sum())
    if n_before:
        log.warning("%d epochs precede the first %s sample; using it anyway",
                    n_before, what)
    pos = np.clip(pos, 0, len(kv) - 1)
    out = kv[pos]
    return (out[:, 0] if v.ndim == 1 else out), [kt[int(p)] for p in pos], n_before


def _step_held_kp(kp_times, kp_values, epochs):
    """Hourly Kp -> (value, time_tag) per 5-min epoch, step-held (last <= epoch).

    OMNI2 reports the 3-hour Kp at hourly midpoints (00:30, 01:30, ...) as
    Kp x 10, so 27 means 2.7 (i.e. 3-).
    """
    return _step_held(kp_times, kp_values, epochs, "Kp")


# --- state assembly ------------------------------------------------------------

def _state(spec: EventSpec, epoch: datetime, generated: datetime, sw_vals: dict,
           dst: float, dst_time: datetime, kp: float, kp_time: datetime,
           stale_mag: float, stale_wind: float, *,
           source: str = "OMNI", dst_source: str = "omni_symh",
           external: str = "t96", l1_gse_re=None, lead_s=None) -> dict:
    """One schema-1 state object. Every invariant in STATE_SCHEMA.md holds here.

    The keyword arguments are what separates a curated OMNI event (the
    defaults: T96 off SYM-H, nominal L1 stand-off) from a look-back off the
    real-time L1 feed (T89 off Kp, Kyoto Dst, measured spacecraft position).
    Everything structural - rounding, the pdyn and iopt formulas, the invariant
    set - is shared, which is the point: one builder, one contract.
    """
    sw = {k: (round(v, _ROUND[k]) if k in _ROUND else v) for k, v in sw_vals.items()}
    sw["temperature"] = float(sw["temperature"])
    pdyn = pdyn_npa(sw["density"], sw["speed"])
    iopt = iopt_for_kp(kp)
    l1 = [float(v) for v in (L1_GSE_RE if l1_gse_re is None else l1_gse_re)]
    # Nominal stand-off / speed when no spacecraft position is known (OMNI);
    # the measured x_l1_km / speed lead when one is (STATE_SCHEMA fallback rule).
    lead_s = round(L1_STANDOFF_RE * RE_KM / sw["speed"] if lead_s is None
                   else lead_s)
    age_s = round(max(stale_mag, stale_wind))
    if external == "t96":
        model = {"external": "t96", "iopt": iopt,
                 "parmod": [pdyn, float(dst), sw["by_gsm"], sw["bz_gsm"],
                            0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}
    elif external == "t89":
        model = {"external": "t89", "iopt": iopt, "parmod": None}
    else:
        raise ValueError(f"model.external must be 't89' or 't96', got {external!r}")
    return {
        "schema": SCHEMA_VERSION,
        "epoch": _iso(epoch),
        "generated": _iso(generated),
        "tier": "EVENT",
        "event_id": spec.id,
        "source": source,
        "age_s": age_s,
        "sw": {
            "bx_gsm": sw["bx_gsm"], "by_gsm": sw["by_gsm"],
            "bz_gsm": sw["bz_gsm"], "bt": sw["bt"],
            "density": sw["density"], "speed": sw["speed"],
            "temperature": sw["temperature"],
            "vx_gse": sw["vx_gse"], "vy_gse": sw["vy_gse"], "vz_gse": sw["vz_gse"],
            "pdyn_npa": pdyn,
            # OMNI ships no per-sample quality flag; fills are already masked
            # out, so every surviving sample is good by construction -> 0.
            "quality": {"mag": 0, "wind": 0},
            "stale_s": {"mag": round(stale_mag), "wind": round(stale_wind)},
        },
        "kp": {"value": round(float(kp), 2), "time_tag": _iso(kp_time), "iopt": iopt},
        # STATE_SCHEMA: dst may be null and consumers must tolerate it (the T89
        # path needs none). A look-back whose Dst feed is down still plays.
        "dst": None if dst is None else {
            "value": float(dst), "time_tag": _iso(dst_time),
            "source": dst_source},
        "propagation": {
            "lead_s": lead_s,
            "arrival": _iso(epoch + timedelta(seconds=lead_s)),
            "l1_gse_re": l1,
        },
        "model": model,
    }


def build_timeline(spec: EventSpec, sw_data, sw_meta, kp_data, kp_meta,
                   generated: datetime | None = None):
    """Resample + gap-fill the raw HAPI arrays into (states, build report).

    Pure function of its inputs - the tests drive it with synthetic
    HAPI-shaped arrays, so the whole gap/invariant surface is offline-testable.
    """
    generated = generated or datetime.now(timezone.utc)
    start, stop = _parse_iso(spec.start), _parse_iso(spec.stop)
    n_bins = int((stop - start).total_seconds() // CADENCE_S)
    if n_bins < 1:
        raise ValueError(f"{spec.id}: window shorter than one {CADENCE_S}s bin")
    epochs = [start + timedelta(seconds=CADENCE_S * (k + 1)) for k in range(n_bins)]

    times = _times(sw_data)
    cols = _columns(sw_data, sw_meta)

    binned, offsets = {}, {}
    for _block, mapping in BLOCKS:
        for field, param in mapping.items():
            if param not in cols:
                raise KeyError(f"{spec.id}: HAPI response lacks {param!r}")
            binned[field], offsets[field] = _bin_median(
                times, _physical(field, cols[param]), start, n_bins)

    first_bin, gaps, stale, observed = 0, [], {}, {}
    for block, mapping in BLOCKS:
        fields = list(mapping)
        valid = np.ones(n_bins, dtype=bool)
        for f in fields:
            valid &= ~np.isnan(binned[f])
        observed[block] = int(valid.sum())
        # min across the block's fields: the newest instant at which the
        # *complete* block is known (live uses the same min-of-newest rule).
        stale[block] = _observed_age_s(
            np.min([offsets[f] for f in fields], axis=0), valid, n_bins)
        f0, block_gaps = _fill_gaps({f: binned[f] for f in fields}, valid,
                                    epochs, block)
        first_bin = max(first_bin, f0)
        gaps.extend(block_gaps)

    kp_times = _times(kp_data)
    kp_cols = _columns(kp_data, kp_meta)
    kp_series, kp_tags, n_kp_before = _step_held_kp(
        kp_times, kp_cols["KP1800"] / 10.0, epochs)   # KP1800 is Kp x 10

    states = []
    for k in range(first_bin, n_bins):
        sw_vals = {f: float(binned[f][k]) for f in list(MAG_MAP) + list(WIND_MAP)}
        symh_age = stale["symh"][k]
        states.append(_state(
            spec, epochs[k], generated, sw_vals,
            dst=round(float(binned["symh"][k]), 1),
            dst_time=epochs[k] - timedelta(seconds=float(symh_age)),
            kp=float(kp_series[k]), kp_time=kp_tags[k],
            stale_mag=float(stale["mag"][k]), stale_wind=float(stale["wind"][k])))

    # Gaps entirely inside the trimmed lead-in are not part of the timeline.
    kept_from = epochs[first_bin]
    gaps = [g for g in gaps if _parse_iso(g["end"]) >= kept_from]
    report = {
        "n_bins": n_bins,
        "first_bin": first_bin,
        "trimmed_leading_min": first_bin * BIN_MINUTES,
        "observed_bins": observed,
        "gaps": sorted(gaps, key=lambda g: (g["start"], g["block"])),
        "kp_epochs_before_first_sample": n_kp_before,
        "raw_peaks": _raw_peaks(cols, kp_cols),
    }
    return states, report


def _nan_ext(v, fn):
    """np.nanmin/nanmax of an array that may be entirely NaN -> float or None."""
    if v is None or np.isnan(v).all():
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return float(fn(v))


def _raw_peaks(cols: dict, kp_cols: dict) -> dict:
    """Extremes of the un-resampled 1-minute data, for cross-checking.

    The timeline stores 5-minute medians, which clip the sharpest 1-minute
    spikes - Gannon's SYM/H bottoms at -518 nT for one minute but -498 nT as a
    5-minute median. Recording both keeps the manifest honest about what the
    playback actually shows versus what the storm actually did.
    """
    dens, spd = cols.get("proton_density"), cols.get("flow_speed")
    pdyn = None if dens is None or spd is None else pdyn_npa(dens, spd)
    return {
        "cadence": "1 min (OMNI_HRO_1MIN), before 5-minute median resampling",
        "min_symh_nt": _nan_ext(cols.get("SYM_H"), np.nanmin),
        "min_bz_nt": _nan_ext(cols.get("BZ_GSM"), np.nanmin),
        "max_bt_nt": _nan_ext(cols.get("F"), np.nanmax),
        "max_speed_kms": _nan_ext(spd, np.nanmax),
        "max_density_cm3": _nan_ext(dens, np.nanmax),
        "max_pdyn_npa": _nan_ext(pdyn, np.nanmax),
        "max_kp": _nan_ext(kp_cols.get("KP1800") / 10.0, np.nanmax),
    }


# --- manifest ------------------------------------------------------------------

def _dst_key(states_or_source) -> str:
    """Which disturbance-index key a peaks block carries.

    Curated OMNI events store 1-minute SYM/H; look-backs store Kyoto Dst. They
    are not the same measurement, so they do not share a key name - a manifest
    that says `min_symh_nt` really is SYM/H.
    """
    source = (states_or_source if isinstance(states_or_source, str)
              else (states_or_source[0].get("dst") or {}).get("source"))
    return "min_symh_nt" if source == "omni_symh" else "min_dst_nt"


def _dst_peak(peaks: dict):
    """(label, value) for whichever disturbance index a peaks block carries."""
    if "min_symh_nt" in peaks:
        return "SYM-H", peaks["min_symh_nt"]
    return "Dst", peaks.get("min_dst_nt")


def _peaks(states: list[dict]) -> dict:
    bz = [s["sw"]["bz_gsm"] for s in states]
    dsts = [s["dst"]["value"] for s in states if s.get("dst")]
    peaks = {
        "max_abs_bz_nt": max(abs(v) for v in bz),
        "min_bz_nt": min(bz),
        "max_bt_nt": max(s["sw"]["bt"] for s in states),
        "max_pdyn_npa": max(s["sw"]["pdyn_npa"] for s in states),
        "max_speed_kms": max(s["sw"]["speed"] for s in states),
        "max_density_cm3": max(s["sw"]["density"] for s in states),
        "max_kp": max(s["kp"]["value"] for s in states),
        "max_age_s": max(s["age_s"] for s in states),
    }
    if dsts:                       # dst is nullable by STATE_SCHEMA
        peaks[_dst_key(states)] = min(dsts)
    return peaks


def _source_block(dataset: str, params: str, start: str, stop: str, meta: dict,
                  raw_dir: Path, server: str = HAPI_SERVER) -> dict:
    return {
        "server": server,
        "dataset": dataset,
        "parameters": params,
        "requested": {"time.min": start, "time.max": stop},
        "dataset_coverage": {"startDate": meta.get("startDate"),
                             "stopDate": meta.get("stopDate")},
        "raw": sorted(p.name for p in raw_dir.glob(f"**/{dataset}*")),
    }


def _manifest(spec: EventSpec, states: list[dict], report: dict, sources: list,
              generated: datetime, extra_dst=None, *, feed: str = "OMNI",
              model_block: dict | None = None, propagation_note: str | None = None,
              b_note: str | None = None, extra: dict | None = None) -> dict:
    """The event.json manifest. Defaults describe a curated OMNI event.

    The keyword arguments carry the handful of statements that are true of a
    look-back but not of OMNI (which feed, which model, whether the lead time
    is measured or nominal) - everything else, including the coverage and gap
    accounting, is identical for both by construction.
    """
    peaks = _peaks(states)
    # 'partial' means the requested window could not be covered end to end.
    # A step-held stretch inside the window is a data-quality note, not a
    # coverage failure - it goes in `notes` so the two never get confused.
    partial, reasons, notes = False, [], []
    if report["trimmed_leading_min"]:
        partial = True
        reasons.append(
            f"no {feed} coverage for the first {report['trimmed_leading_min']} min "
            f"of the requested window; timeline starts at {states[0]['epoch']}")
    held = sum(g["minutes"] for g in report["gaps"] if g["method"] == "hold")
    interp = sum(g["minutes"] for g in report["gaps"] if g["method"] == "interp")
    if held:
        notes.append(f"{held} min step-held across gaps longer than "
                     f"{GAP_INTERP_S // 60} min (see gaps[]); age_s and "
                     f"sw.stale_s climb honestly through them")
    if interp:
        notes.append(f"{interp} min linearly interpolated across gaps of "
                     f"{GAP_INTERP_S // 60} min or less")
    out = {
        "schema": SCHEMA_VERSION,
        "event_id": spec.id,
        "title": spec.title,
        "window": {"start": spec.start, "stop": spec.stop,
                   "note": "HAPI time.max is exclusive; states are labelled at "
                           "the END of each 5-minute bin"},
        "cadence_s": CADENCE_S,
        "n_steps": len(states),
        "coverage": {
            "first_epoch": states[0]["epoch"],
            "last_epoch": states[-1]["epoch"],
            "bins_in_window": report["n_bins"],
            "trimmed_leading_min": report["trimmed_leading_min"],
            "observed_bins": report["observed_bins"],
            "observed_fraction": {b: round(n / report["n_bins"], 4)
                                  for b, n in report["observed_bins"].items()},
        },
        "partial": partial,
        "partial_reason": reasons,
        "notes": notes,
        "peaks": peaks,
        "peaks_raw_1min": report["raw_peaks"],
        "gaps": report["gaps"],
        "gap_policy": {
            "interpolate_max_s": GAP_INTERP_S,
            "longer": "step-hold last valid bin",
            "leading": "trimmed - nothing to hold, nothing invented",
        },
        "sources": sources,
        "hourly_dst_crosscheck": extra_dst,
        "model": model_block or {
            "external": "t96",
            "parmod": "[pdyn, SYM-H, By_gsm, Bz_gsm, 0 x 6]",
            "note": "SYM/H substitutes for Dst (1-min, better resolved than the "
                    "hourly index)",
        },
        "propagation_note": propagation_note or (
            f"OMNI values are already time-shifted to the bow shock nose, so the "
            f"epoch is arrival-at-Earth. lead_s = {L1_STANDOFF_RE:g} R_E / speed "
            f"with l1_gse_re = {L1_GSE_RE} is a nominal L1 stand-off for the wind "
            f"and L1-marker layers, not a measured lag."),
        "bx_note": b_note or (
            "OMNI ships BX_GSE only; GSE->GSM is a rotation about the "
            "shared X axis, so bx_gsm == BX_GSE exactly."),
        "fetched": _iso(generated),
    }
    out.update(extra or {})
    return out


# --- fetch / load / summary ----------------------------------------------------

def build_event(spec: EventSpec, sw_data, sw_meta, kp_data, kp_meta,
                root: Path, generated: datetime | None = None) -> dict:
    """Raw HAPI arrays -> timeline.jsonl + event.json under root. Returns manifest.

    Split out of fetch_event so the whole assembly path - resampling, gap
    policy, manifest, writer - is exercised offline by the tests with
    synthetic HAPI-shaped arrays.
    """
    generated = generated or datetime.now(timezone.utc)
    root = Path(root)
    raw_dir = root / "raw"
    states, report = build_timeline(spec, sw_data, sw_meta, kp_data, kp_meta,
                                    generated)

    dst_hourly = _columns(kp_data, kp_meta).get("DST1800")
    extra_dst = None
    if dst_hourly is not None and not np.isnan(dst_hourly).all():
        extra_dst = {"dataset": KP_DATASET, "parameter": "DST1800",
                     "min_nt": float(np.nanmin(dst_hourly))}

    kp_start = _iso(_parse_iso(spec.start) - timedelta(hours=KP_LEAD_H))
    sources = [
        _source_block(SW_DATASET, SW_PARAMS, spec.start, spec.stop, sw_meta, raw_dir),
        _source_block(KP_DATASET, KP_PARAMS, kp_start, spec.stop, kp_meta, raw_dir),
    ]
    manifest = _manifest(spec, states, report, sources, generated, extra_dst)
    write_timeline(root, states, manifest)
    log.info("%s: %d states %s..%s, %d gaps, min SYM-H %.0f nT, max Kp %.1f",
             spec.id, len(states), states[0]["epoch"], states[-1]["epoch"],
             len(report["gaps"]), manifest["peaks"]["min_symh_nt"],
             manifest["peaks"]["max_kp"])
    return manifest


def fetch_event(event_id: str, out_root: Path | None = None) -> dict:
    """Pull one event from CDAWeb HAPI and write raw/, timeline.jsonl, event.json."""
    spec = _spec(event_id)
    root = Path(out_root or EVENTS_DIR) / spec.id
    raw_dir = root / "raw"
    generated = datetime.now(timezone.utc)

    sw_data, sw_meta = _hapi_fetch(SW_DATASET, SW_PARAMS, spec.start, spec.stop,
                                   raw_dir)
    kp_start = _iso(_parse_iso(spec.start) - timedelta(hours=KP_LEAD_H))
    kp_data, kp_meta = _hapi_fetch(KP_DATASET, KP_PARAMS, kp_start, spec.stop,
                                   raw_dir)
    return build_event(spec, sw_data, sw_meta, kp_data, kp_meta, root, generated)


def write_timeline(root: Path, states: list[dict], manifest: dict) -> Path:
    """Atomically write timeline.jsonl + event.json under root.

    ``allow_nan=False`` is the structural guard on invariant 1: a NaN that
    survived the gap policy raises here instead of reaching the renderer.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(s, allow_nan=False, separators=(",", ":")) + "\n"
                   for s in states)
    path = root / "timeline.jsonl"
    write_atomic(path, body.encode("ascii"))
    write_atomic(root / "event.json",
                 json.dumps(manifest, indent=1, allow_nan=False).encode("ascii"))
    return path


def load_event(event_id: str, root: Path | None = None):
    """Iterate the stored schema-1 states of an event, in time order."""
    path = Path(root or EVENTS_DIR) / event_id / "timeline.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found - run: {_fetch_hint(event_id)}")
    with open(path, "r", encoding="ascii") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_manifest(event_id: str, root: Path | None = None) -> dict:
    path = Path(root or EVENTS_DIR) / event_id / "event.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found - run: {_fetch_hint(event_id)}")
    return json.loads(path.read_text(encoding="ascii"))


def event_summary(event_id: str, root: Path | None = None) -> dict:
    """Manifest plus a cheap re-derivation from the timeline on disk.

    Re-reading the states rather than trusting the manifest means a summary
    can never quietly disagree with what the renderer will actually play.
    """
    manifest = load_manifest(event_id, root)
    states = list(load_event(event_id, root))
    epochs = [s["epoch"] for s in states]
    peak = min(states, key=lambda s: (s.get("dst") or {}).get("value", 0.0))
    return {
        "event_id": event_id,
        "title": manifest["title"],
        "partial": manifest.get("partial", False),
        "partial_reason": manifest.get("partial_reason", []),
        "notes": manifest.get("notes", []),
        "peaks_raw_1min": manifest.get("peaks_raw_1min"),
        "n_steps": len(states),
        "cadence_s": manifest["cadence_s"],
        "first_epoch": epochs[0],
        "last_epoch": epochs[-1],
        "duration_h": round(len(states) * manifest["cadence_s"] / 3600.0, 2),
        "peaks": _peaks(states),
        "peak_epoch": peak["epoch"],
        "coverage_fraction": (manifest.get("coverage") or {}).get(
            "observed_fraction"),
        "stalest_readout": state_age_readout(max(states, key=lambda s: s["age_s"])),
        "n_gaps": len(manifest.get("gaps", [])),
        "gap_minutes_held": sum(g["minutes"] for g in manifest.get("gaps", [])
                                if g["method"] == "hold"),
        "gap_minutes_interp": sum(g["minutes"] for g in manifest.get("gaps", [])
                                  if g["method"] == "interp"),
        "sources": [f"{s['server']} {s['dataset']}" for s in manifest["sources"]],
        "fetched": manifest["fetched"],
    }


def state_age_readout(state: dict) -> str:
    """Dome readout for one playback frame: 'as of HH:MM UT (Xm ago)'.

    The live banner's own helper, evaluated against the state's epoch instead
    of wall-clock now - so a step-held stretch of an event reads exactly as a
    stalling live feed would, which is the point of keeping age_s honest.
    """
    return data_age_str(state["age_s"], now=_parse_iso(state["epoch"]))


def _row(event_id: str, root: Path, spec: EventSpec | None = None) -> dict:
    """One listing row; a dynamic event's window and title come off its manifest."""
    tl = root / event_id / "timeline.jsonl"
    row = {"id": event_id,
           "title": spec.title if spec else event_id,
           "start": spec.start if spec else None,
           "stop": spec.stop if spec else None,
           "fetched": tl.exists(), "n_steps": None, "partial": None,
           "dynamic": spec is None}
    if tl.exists():
        try:
            m = load_manifest(event_id, root)
            row["n_steps"] = m["n_steps"]
            row["partial"] = m.get("partial")
            if spec is None:
                row["title"] = m.get("title", event_id)
                row["start"] = (m.get("window") or {}).get("start")
                row["stop"] = (m.get("window") or {}).get("stop")
        except Exception:                            # pragma: no cover
            log.exception("%s: manifest unreadable", event_id)
    return row


def available(root: Path | None = None) -> list[dict]:
    """Registry entries plus whatever else is on disk (look-backs included).

    A look-back has no registry entry - it is created on demand and identified
    by its directory - so the listing scans events/ too. Registry entries take
    precedence, so a directory that shadows a registered id is reported once,
    as the registry entry.
    """
    root = Path(root or EVENTS_DIR)
    out = [_row(spec.id, root, spec) for spec in EVENTS.values()]
    if root.is_dir():
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            if d.name not in EVENTS and (d / "timeline.jsonl").exists():
                out.append(_row(d.name, root))
    return out


# --- look-back: the last N days of REAL solar wind ------------------------------
#
# OMNI lags by weeks, so a "what did the Sun do this week" replay cannot come
# from the curated path above. It comes from the same real-time L1 products the
# live daemon watches, served as HAPI by CCMC ISWA, and lands in exactly the
# same shape: events/<id>/{raw,timeline.jsonl,event.json} with tier EVENT.
#
# Facts verified against iswa.gsfc.nasa.gov/hapi on 2026-08-12:
#
#   * Two 1-minute L1 pairs exist and FEEDS carries both. 'ace' is ACE's own
#     stream (ace_mag_P1M + ace_swepam_P1M, source 'ACE', isPrimary 0 while
#     SWPC flies SOLAR1); 'rtsw' is whatever spacecraft SWPC currently flies as
#     primary (swpc_rtsw_mag_P1M + swpc_rtsw_plasma_P1M). Same parameter NAMES,
#     DIFFERENT declared order - the reader enforces the order, so it lives in
#     the table. Measured over 2026-08-08..12: ace 85.3% / 91.9% of raw minutes
#     against rtsw's 99.7% / 97.0%, but ACE's dropouts are mostly shorter than
#     one 5-minute bin, so ace still yielded 96.6% / 97.1% of BINS observed.
#     Measure before switching; the manifest reports what was actually got.
#   * B IS ALREADY GSM. Verified by comparing ace_mag_P1M against SWPC's own
#     /json/rtsw/rtsw_mag_1m.json for the same minutes and source: B_x/B_y/B_z
#     equal bx_gsm/by_gsm/bz_gsm to the published 2 dp and differ from the GSE
#     columns. swpc_rtsw_mag_P1M says so in metadata too (Latitude/Longitude
#     are documented 'GSM Coordinates'). So NO frame rotation is applied here -
#     the state's bz_gsm is the delivered B_z. If ISWA ever ships GSE, this is
#     the line to change, and geo.gse_to_gsm_matrix() (recalc per step) is the
#     tool for it.
#   * The plasma products carry a bulk SPEED only, no velocity vector, so
#     vx_gse = -speed and vy_gse = vz_gse = 0 (radial flow). Said out loud in
#     the manifest, because the live feed does ship real components.
#   * Kp: GFZ_Indices_P3H 'Kp_observed', 3-hourly, Time = START of the window,
#     already in thirds (1.667 = 2-). Dst: dst_quicklook (Kyoto), hourly.
#     Both lag the L1 feed by an hour or two, so the newest epochs step-hold
#     them - which is what the live daemon does with the same indices.
#   * L1 position: SWPC_{ACE,RTSW}_ephemeris_P1H, hourly, GSE km. Unlike OMNI
#     these data are NOT time-shifted, so lead_s = x_gse_km / speed is a real
#     transit time, and l1_gse_re is a measured position.
#
# Real-time data is gappy. The gap policy above applies unchanged (<= 15 min
# interpolate, longer step-hold + manifest gap list, leading trim), and the
# manifest reports the observed fraction per block so the honesty is auditable.

ISWA_SERVER = "https://iswa.gsfc.nasa.gov/hapi"

LOOKBACK_PREFIX = "lookback-"
LOOKBACK_DAYS = 4

# Same names in both feed sets; only the parameter strings (the server's
# declared order, which the reader enforces) differ, so those live in the table.
RT_MAG_MAP = {"bt": "B_t", "bx_gsm": "B_x", "by_gsm": "B_y", "bz_gsm": "B_z"}
RT_WIND_MAP = {"speed": "BulkSpeed", "density": "ProtonDensity",
               "temperature": "IonTemperature"}
RT_BLOCKS = (("mag", RT_MAG_MAP), ("wind", RT_WIND_MAP))

FeedSet = namedtuple("FeedSet", "name mag mag_params wind wind_params eph")

EPH_PARAMS = "source,isPrimary,x_gse,y_gse,z_gse"

FEEDS: dict[str, FeedSet] = {
    "ace": FeedSet(
        "ace",
        "ace_mag_P1M", "B_x,B_y,B_z,B_t,isPrimary,source",
        "ace_swepam_P1M", "ProtonDensity,IonTemperature,BulkSpeed,isPrimary,source",
        "SWPC_ACE_ephemeris_P1H"),
    "rtsw": FeedSet(
        "rtsw",
        "swpc_rtsw_mag_P1M", "B_x,B_y,B_z,B_t,isPrimary,source",
        "swpc_rtsw_plasma_P1M", "ProtonDensity,BulkSpeed,IonTemperature,isPrimary,source",
        "SWPC_RTSW_ephemeris_P1H"),
}
DEFAULT_FEED = "ace"

KP_RT_DATASET = "GFZ_Indices_P3H"
KP_RT_PARAMS = "Kp_observed"
DST_RT_DATASET = "dst_quicklook"
DST_RT_PARAMS = "Dst"

# Everything build_lookback_timeline needs, so the tests can hand it synthetic
# HAPI-shaped arrays exactly as test_events does for the curated path.
LookbackFeeds = namedtuple(
    "LookbackFeeds",
    "mag mag_meta wind wind_meta kp kp_meta dst dst_meta eph eph_meta")
LookbackFeeds.__new__.__defaults__ = (None, None)


def _fetch_hint(event_id: str) -> str:
    """The command that would create this event id."""
    if str(event_id).startswith(LOOKBACK_PREFIX):
        return "python -m magneto.events fetch-lookback"
    return f"python -m magneto.events fetch {event_id}"


def _feed(name) -> FeedSet:
    try:
        return FEEDS[str(name)]
    except KeyError:
        raise KeyError(f"unknown feed {name!r}; known: {sorted(FEEDS)}") from None


def _floor_cadence(dt: datetime) -> datetime:
    """Floor a UTC datetime onto the 5-minute timeline grid."""
    dt = dt.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return dt - timedelta(minutes=dt.minute % BIN_MINUTES)


def _hapi_info(dataset: str, server: str = ISWA_SERVER) -> dict:
    """/hapi/info for one dataset."""
    from . import hapi_csv
    return hapi_csv.info(server, dataset)


def latest_iswa_epoch(feed: str = DEFAULT_FEED, server: str = ISWA_SERVER
                      ) -> datetime:
    """Newest epoch BOTH driving datasets can serve, floored to the cadence.

    The two products advance independently (mag typically a minute or two ahead
    of plasma), and a window that ends past either stopDate would trail an
    invented step-hold, so the earlier of the two wins.
    """
    fs = _feed(feed)
    stops = []
    for dataset in (fs.mag, fs.wind):
        meta = _hapi_info(dataset, server)
        stop = meta.get("stopDate")
        if not stop:
            raise RuntimeError(f"{dataset}: /hapi/info declares no stopDate")
        stops.append(_parse_iso(stop))
        log.info("%s serves through %s", dataset, stop)
    return _floor_cadence(min(stops))


def lookback_id(start: datetime, stop: datetime) -> str:
    return f"{LOOKBACK_PREFIX}{start:%Y%m%d}-{stop:%Y%m%d}"


def lookback_spec(days: float = LOOKBACK_DAYS, end_utc=None,
                  feed: str = DEFAULT_FEED, server: str = ISWA_SERVER
                  ) -> EventSpec:
    """The dynamic EventSpec for a look-back window.

    ``end_utc`` (ISO-8601 Z string or datetime) pins the window end; the
    default asks ISWA what it can actually serve, which is the only honest
    answer for a feed that is minutes-fresh and occasionally behind.
    """
    if end_utc is None:
        end = latest_iswa_epoch(feed, server)
    else:
        end = _floor_cadence(_parse_iso(end_utc) if isinstance(end_utc, str)
                             else end_utc)
    start = end - timedelta(days=float(days))
    n_bins = int((end - start).total_seconds() // CADENCE_S)
    if n_bins < 2:
        raise ValueError(f"--days {days:g} is under two {CADENCE_S}s bins")
    title = (f"Last {float(days):g} days - {start:%Y-%m-%d} to {end:%Y-%m-%d} "
             f"(look-back)")
    return EventSpec(lookback_id(start, end), title, _iso(start), _iso(end))


def _modal(values: list[str]) -> tuple[str | None, dict]:
    """Most common non-empty string, plus the full count (provenance)."""
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None, {}
    return max(counts, key=counts.get), counts


def _lookback_raw_peaks(mag_cols: dict, wind_cols: dict, kp_vals, dst_vals,
                        cadence: str) -> dict:
    """Extremes of the un-resampled 1-minute feed - the same cross-check the
    curated events keep, so a manifest never overstates what playback shows."""
    dens, spd = wind_cols.get("ProtonDensity"), wind_cols.get("BulkSpeed")
    pdyn = None if dens is None or spd is None else pdyn_npa(dens, spd)
    return {
        "cadence": cadence,
        "min_dst_nt": _nan_ext(dst_vals, np.nanmin),
        "min_bz_nt": _nan_ext(mag_cols.get("B_z"), np.nanmin),
        "max_bt_nt": _nan_ext(mag_cols.get("B_t"), np.nanmax),
        "max_speed_kms": _nan_ext(spd, np.nanmax),
        "max_density_cm3": _nan_ext(dens, np.nanmax),
        "max_pdyn_npa": _nan_ext(pdyn, np.nanmax),
        "max_kp": _nan_ext(kp_vals, np.nanmax),
    }


def build_lookback_timeline(spec: EventSpec, feeds: LookbackFeeds,
                            generated: datetime | None = None):
    """Raw ISWA arrays -> (states, build report) for a look-back window.

    Pure function of its inputs, exactly like build_timeline: the tests drive
    it with synthetic HAPI-shaped arrays, so the resampler, the gap policy and
    every STATE_SCHEMA invariant are covered without touching the network.

    Differences from the curated path, all of them data facts rather than
    choices: two separate 1-minute feeds instead of one merged product, T89 off
    observed Kp instead of T96 (no reanalysed parameters exist yet for
    yesterday), Kyoto Dst instead of SYM/H, a measured L1 position and transit
    time instead of a nominal stand-off, and vx from the scalar speed.
    """
    generated = generated or datetime.now(timezone.utc)
    start, stop = _parse_iso(spec.start), _parse_iso(spec.stop)
    n_bins = int((stop - start).total_seconds() // CADENCE_S)
    if n_bins < 1:
        raise ValueError(f"{spec.id}: window shorter than one {CADENCE_S}s bin")
    epochs = [start + timedelta(seconds=CADENCE_S * (k + 1)) for k in range(n_bins)]

    raw = {"mag": (feeds.mag, feeds.mag_meta), "wind": (feeds.wind, feeds.wind_meta)}
    cols, binned, offsets, spacecraft, primary = {}, {}, {}, {}, {}
    for block, mapping in RT_BLOCKS:
        data, meta = raw[block]
        if data is None or len(data) == 0:
            raise ValueError(f"{spec.id}: the {block} feed returned no rows for "
                             f"{spec.start}..{spec.stop}")
        cols[block] = _columns(data, meta)
        times = _times(data)
        for field, param in mapping.items():
            if param not in cols[block]:
                raise KeyError(f"{spec.id}: {block} response lacks {param!r}")
            binned[field], offsets[field] = _bin_median(
                times, _physical(field, cols[block][param]), start, n_bins)
        spacecraft[block] = _modal(_str_col(data, "source"))
        prim = cols[block].get("isPrimary")
        primary[block] = (None if prim is None or not len(prim)
                          else round(float(np.nanmean(prim)), 4))

    first_bin, gaps, stale, observed = 0, [], {}, {}
    for block, mapping in RT_BLOCKS:
        fields = list(mapping)
        valid = np.ones(n_bins, dtype=bool)
        for f in fields:
            valid &= ~np.isnan(binned[f])
        observed[block] = int(valid.sum())
        stale[block] = _observed_age_s(
            np.min([offsets[f] for f in fields], axis=0), valid, n_bins)
        f0, block_gaps = _fill_gaps({f: binned[f] for f in fields}, valid,
                                    epochs, block)
        first_bin = max(first_bin, f0)
        gaps.extend(block_gaps)

    # Kp is not optional: iopt, and therefore the T89 field itself, is Kp.
    if feeds.kp is None or len(feeds.kp) == 0:
        raise ValueError(f"{spec.id}: no Kp rows for {spec.start}..{spec.stop}")
    kp_cols = _columns(feeds.kp, feeds.kp_meta)
    kp_series, kp_tags, n_kp_before = _step_held(
        _times(feeds.kp), kp_cols["Kp_observed"], epochs, "Kp")

    # Dst IS optional (T89 needs none, STATE_SCHEMA allows null) - a Kyoto
    # outage must not cost the show its replay.
    dst_vals = None
    dst_series = dst_tags = None
    if feeds.dst is None or not len(feeds.dst):
        log.warning("%s: no Dst feed - states carry dst null", spec.id)
    else:
        dst_vals = _columns(feeds.dst, feeds.dst_meta)["Dst"]
        try:
            dst_series, dst_tags, _ = _step_held(
                _times(feeds.dst), dst_vals, epochs, "Dst")
        except ValueError:
            log.warning("%s: no valid Dst in the window - states carry dst null",
                        spec.id)

    # Measured L1 position (hourly, step-held). Without it the state falls back
    # to the nominal stand-off, and the manifest says which one it got.
    l1_re, l1_mode = None, "nominal"
    if feeds.eph is not None and len(feeds.eph):
        e_cols = _columns(feeds.eph, feeds.eph_meta)
        try:
            xyz = np.column_stack([e_cols["x_gse"], e_cols["y_gse"], e_cols["z_gse"]])
            l1_km, _tags, _n = _step_held(_times(feeds.eph), xyz, epochs, "L1 position")
            l1_re, l1_mode = l1_km / RE_KM, "measured"
        except (KeyError, ValueError):
            log.warning("%s: no usable L1 ephemeris - nominal stand-off used",
                        spec.id)

    source = (spacecraft["mag"][0] or spacecraft["wind"][0] or "L1")
    states = []
    for k in range(first_bin, n_bins):
        sw_vals = {f: float(binned[f][k]) for f in list(RT_MAG_MAP) + list(RT_WIND_MAP)}
        speed = round(sw_vals["speed"], _ROUND["speed"])
        sw_vals.update(vx_gse=-speed, vy_gse=0.0, vz_gse=0.0)
        l1 = None if l1_re is None else [float(v) for v in l1_re[k]]
        states.append(_state(
            spec, epochs[k], generated, sw_vals,
            dst=None if dst_series is None else round(float(dst_series[k]), 1),
            dst_time=None if dst_tags is None else dst_tags[k],
            kp=float(kp_series[k]), kp_time=kp_tags[k],
            stale_mag=float(stale["mag"][k]), stale_wind=float(stale["wind"][k]),
            source=source, dst_source="kyoto", external="t89",
            l1_gse_re=l1,
            lead_s=None if l1 is None or speed <= 0 else l1[0] * RE_KM / speed))

    kept_from = epochs[first_bin]
    gaps = [g for g in gaps if _parse_iso(g["end"]) >= kept_from]
    report = {
        "n_bins": n_bins,
        "first_bin": first_bin,
        "trimmed_leading_min": first_bin * BIN_MINUTES,
        "observed_bins": observed,
        "gaps": sorted(gaps, key=lambda g: (g["start"], g["block"])),
        "kp_epochs_before_first_sample": n_kp_before,
        "raw_peaks": _lookback_raw_peaks(
            cols["mag"], cols["wind"], kp_cols["Kp_observed"], dst_vals,
            "1 min (L1 real-time feed), before 5-minute median resampling"),
        "source": source,
        "spacecraft_counts": {b: spacecraft[b][1] for b in spacecraft},
        "is_primary_fraction": primary,
        "l1_mode": l1_mode,
        "kp_last_tag": _iso(kp_tags[-1]),
        "dst_last_tag": None if dst_tags is None else _iso(dst_tags[-1]),
        "dst_present": dst_series is not None,
    }
    return states, report


def build_lookback_event(spec: EventSpec, feeds: LookbackFeeds, root: Path,
                         generated: datetime | None = None, *,
                         feed: str = DEFAULT_FEED, requested_end=None,
                         server: str = ISWA_SERVER, days: float | None = None
                         ) -> dict:
    """Raw ISWA arrays -> timeline.jsonl + event.json under root. Returns manifest.

    Split out of fetch_lookback for the same reason build_event is split out of
    fetch_event: the whole assembly path is exercised offline by the tests.
    """
    generated = generated or datetime.now(timezone.utc)
    root = Path(root)
    raw_dir = root / "raw"
    fs = _feed(feed)
    states, report = build_lookback_timeline(spec, feeds, generated)

    lead_in = _iso(_parse_iso(spec.start) - timedelta(hours=KP_LEAD_H))
    wanted = [(fs.mag, fs.mag_params, feeds.mag_meta, spec.start, spec.stop),
              (fs.wind, fs.wind_params, feeds.wind_meta, spec.start, spec.stop),
              (KP_RT_DATASET, KP_RT_PARAMS, feeds.kp_meta, lead_in, spec.stop),
              (DST_RT_DATASET, DST_RT_PARAMS, feeds.dst_meta, lead_in, spec.stop)]
    if feeds.eph is not None:
        wanted.append((fs.eph, EPH_PARAMS, feeds.eph_meta, lead_in, spec.stop))
    sources = [_source_block(ds, params, (meta or {}).get("x_time.min") or t0,
                             (meta or {}).get("x_time.max") or t1, meta or {},
                             raw_dir, server)
               for ds, params, meta, t0, t1 in wanted]

    extra = {"lookback": {
        "days": None if days is None else float(days),
        "feed": fs.name,
        "requested_end": requested_end if isinstance(requested_end, (str, type(None)))
                         else _iso(requested_end),
        "resolved_end": spec.stop,
        "resolved_from": ("caller --end" if requested_end is not None
                          else f"{fs.mag} / {fs.wind} stopDate"),
        "spacecraft": {"source": report["source"],
                       "counts": report["spacecraft_counts"],
                       "is_primary_fraction": report["is_primary_fraction"],
                       "note": "isPrimary 0 means SWPC was flying a different "
                               "spacecraft as the operational source; the "
                               "measurements are still this spacecraft's own"},
        "kp": {"dataset": KP_RT_DATASET, "last_tag": report["kp_last_tag"],
               "note": "3-hourly observed Kp, Time = START of the window; "
                       "step-held, never interpolated"},
        "dst": {"dataset": DST_RT_DATASET, "source": "kyoto",
                "present": report["dst_present"],
                "last_tag": report["dst_last_tag"],
                "note": "hourly Kyoto quicklook Dst; step-held. Quicklook is "
                        "revised later into provisional/final"},
        "l1": {"dataset": fs.eph, "mode": report["l1_mode"],
               "first_gse_re": states[0]["propagation"]["l1_gse_re"],
               "last_gse_re": states[-1]["propagation"]["l1_gse_re"]},
        "velocity_note": (
            "the real-time plasma product carries a bulk SPEED only, so "
            "vx_gse = -speed and vy_gse = vz_gse = 0 (radial flow assumed). "
            "The live SWPC feed does ship real components; these do not."),
    }}
    manifest = _manifest(
        spec, states, report, sources, generated, feed=report["source"],
        model_block={
            "external": "t89",
            "iopt": "clamp(round(Kp) + 1, 1, 7) from observed 3-hourly Kp",
            "parmod": None,
            "note": "T89, not the curated events' T96: for the last few days no "
                    "reanalysed parameter set exists yet, and observed Kp does. "
                    "Same model the live mode runs, so a look-back and the live "
                    "dome look like the same instrument.",
        },
        propagation_note=(
            "real-time L1 measurements are NOT time-shifted (unlike OMNI), so "
            "the epoch is the measurement at the spacecraft and lead_s = "
            "x_gse_km / speed is a genuine transit time to Earth, with "
            "l1_gse_re the measured spacecraft position."
            if report["l1_mode"] == "measured" else
            f"no L1 ephemeris was available, so lead_s falls back to the "
            f"nominal {L1_STANDOFF_RE:g} R_E / speed with l1_gse_re = "
            f"{L1_GSE_RE}."),
        b_note=("B is delivered in GSM by this feed (verified against SWPC's "
                "own rtsw_mag_1m.json: B_x/B_y/B_z match bx_gsm/by_gsm/bz_gsm "
                "and differ from the GSE columns), so no frame rotation is "
                "applied."),
        extra=extra)
    write_timeline(root, states, manifest)
    peaks = manifest["peaks"]
    log.info("%s: %d states %s..%s, %d gaps, coverage %s, max Kp %.2f, "
             "min Dst %s nT", spec.id, len(states), states[0]["epoch"],
             states[-1]["epoch"], len(report["gaps"]),
             manifest["coverage"]["observed_fraction"], peaks["max_kp"],
             peaks.get("min_dst_nt"))
    return manifest


def fetch_lookback(days: float = LOOKBACK_DAYS, end_utc=None,
                   events_dir: Path | str | None = None, *,
                   feed: str = DEFAULT_FEED, server: str = ISWA_SERVER,
                   with_ephemeris: bool = True) -> str:
    """Fetch the last ``days`` days of real solar wind as a playable event.

    Returns the dynamic event id (``lookback-<YYYYMMDD>-<YYYYMMDD>``), which
    load_event, `magneto.events list|summary` and `magneto.render event` all
    accept - a look-back is an event like any other, it is simply not in the
    registry. Re-running for the same window rewrites that directory.

    ``feed`` picks the L1 pair: 'ace' (ACE's own instruments) or 'rtsw'
    (whatever spacecraft SWPC currently flies as primary). Coverage differs
    between them and both are reported in the manifest, so measure before
    choosing.
    """
    fs = _feed(feed)
    spec = lookback_spec(days, end_utc, feed, server)
    root = Path(events_dir or EVENTS_DIR) / spec.id
    raw_dir = root / "raw"
    generated = datetime.now(timezone.utc)
    lead_in = _iso(_parse_iso(spec.start) - timedelta(hours=KP_LEAD_H))
    log.info("look-back %s: %s .. %s (feed %s)", spec.id, spec.start, spec.stop,
             fs.name)

    mag, mag_meta = _hapi_fetch(fs.mag, fs.mag_params, spec.start, spec.stop,
                                raw_dir, server)
    wind, wind_meta = _hapi_fetch(fs.wind, fs.wind_params, spec.start, spec.stop,
                                  raw_dir, server)
    kp, kp_meta = _hapi_fetch(KP_RT_DATASET, KP_RT_PARAMS, lead_in, spec.stop,
                              raw_dir, server)
    dst = dst_meta = None
    try:
        dst, dst_meta = _hapi_fetch(DST_RT_DATASET, DST_RT_PARAMS, lead_in,
                                    spec.stop, raw_dir, server)
    except Exception:            # never an error: T89 needs no Dst
        log.exception("%s unavailable - the look-back will carry dst null",
                      DST_RT_DATASET)
    eph = eph_meta = None
    if with_ephemeris:
        try:
            eph, eph_meta = _hapi_fetch(fs.eph, EPH_PARAMS, lead_in, spec.stop,
                                        raw_dir, server)
        except Exception:        # fall back to the nominal L1 stand-off
            log.exception("%s unavailable - nominal L1 stand-off will be used",
                          fs.eph)

    feeds = LookbackFeeds(mag, mag_meta, wind, wind_meta, kp, kp_meta,
                          dst, dst_meta, eph, eph_meta)
    build_lookback_event(spec, feeds, root, generated, feed=feed,
                         requested_end=end_utc, server=server, days=days)
    return spec.id


# --- CLI ------------------------------------------------------------------------

def _print_summary(s: dict) -> None:
    p = s["peaks"]
    flag = " [PARTIAL]" if s["partial"] else ""
    label, dst = _dst_peak(p)                # SYM-H for OMNI, Dst for look-backs
    print(f"{s['event_id']}{flag}  {s['title']}")
    print(f"  {s['first_epoch']} -> {s['last_epoch']}  "
          f"{s['n_steps']} steps @ {s['cadence_s']}s ({s['duration_h']} h)")
    print(f"  peaks: min {label} "
          f"{'n/a' if dst is None else format(dst, '.0f') + ' nT'} "
          f"(at {s['peak_epoch']})  "
          f"max Kp {p['max_kp']:g}  max|Bz| {p['max_abs_bz_nt']:.1f} nT  "
          f"max pdyn {p['max_pdyn_npa']:.1f} nPa  max V {p['max_speed_kms']:.0f} km/s")
    raw = s.get("peaks_raw_1min") or {}
    raw_label, raw_dst = _dst_peak(raw)
    if raw_dst is not None:
        print(f"  raw 1-min: min {raw_label} {raw_dst:.0f} nT  "
              f"min Bz {raw['min_bz_nt']:.1f} nT  "
              f"max pdyn {raw['max_pdyn_npa']:.1f} nPa "
              f"(5-min medians above clip the sharpest spikes)")
    cov = s.get("coverage_fraction")
    if cov:
        print("  coverage: " + "  ".join(f"{b} {100 * v:.1f}%"
                                         for b, v in sorted(cov.items())))
    print(f"  gaps: {s['n_gaps']} ({s['gap_minutes_interp']} min interpolated, "
          f"{s['gap_minutes_held']} min step-held)  stalest frame reads "
          f"'{s['stalest_readout']}'")
    print(f"  sources: {'; '.join(s['sources'])}   fetched {s['fetched']}")
    for r in s["partial_reason"]:
        print(f"  ! {r}")
    for n in s.get("notes", []):
        print(f"  - {n}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m magneto.events",
                                 description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="registry + what is on disk")
    f = sub.add_parser("fetch", help="fetch one event from CDAWeb HAPI")
    f.add_argument("event_id")
    sub.add_parser("fetch-all", help="fetch every registered event")
    s = sub.add_parser("summary", help="summarise a fetched event")
    s.add_argument("event_id")
    lb = sub.add_parser("fetch-lookback",
                        help="fetch the last N days of real solar wind (ISWA)")
    lb.add_argument("--days", type=float, default=LOOKBACK_DAYS,
                    help="window length in days (default %(default)s)")
    lb.add_argument("--end", default=None,
                    help="ISO-8601 Z window end (default: newest data ISWA serves)")
    lb.add_argument("--feed", choices=sorted(FEEDS), default=DEFAULT_FEED,
                    help="L1 pair: ace = ACE's own instruments, rtsw = SWPC's "
                         "current primary spacecraft (default %(default)s)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.cmd == "list":
        for row in available():
            mark = "on disk" if row["fetched"] else "not fetched"
            if row["partial"]:
                mark += ", PARTIAL"
            if row["dynamic"]:
                mark += ", dynamic"
            steps = f"{row['n_steps']} steps" if row["n_steps"] else "-"
            print(f"{row['id']:<26} {row['start']} -> {row['stop']}  "
                  f"{mark:<30} {steps:<12} {row['title']}")
        return 0
    if args.cmd == "summary":
        _print_summary(event_summary(args.event_id))
        return 0
    if args.cmd == "fetch-lookback":
        try:
            eid = fetch_lookback(days=args.days, end_utc=args.end, feed=args.feed)
        except Exception as exc:
            log.exception("fetch-lookback failed")
            print(f"fetch-lookback: FAILED - {exc}")
            return 1
        _print_summary(event_summary(eid))        # read back what was written
        print(f"  render it: python -m magneto.render event {eid} --t89 "
              f"--step-min 10 --frame gsm --root output/magneto_lookback")
        return 0

    ids = [args.event_id] if args.cmd == "fetch" else list(EVENTS)
    rc = 0
    for eid in ids:
        try:
            fetch_event(eid)
            _print_summary(event_summary(eid))    # read back what was written
        except Exception as exc:
            log.exception("%s: fetch failed", eid)
            print(f"{eid}: FAILED - {exc}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
