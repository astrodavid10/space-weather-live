"""SWPC endpoint registry + parsers for the live-magnetosphere data core.

One place that knows every URL we poll, how often (tier), how to turn the raw
JSON into normalized rows, and how stale a payload may get before it is
useless. Everything downstream (poller, state builder, tests) goes through
this registry so adding or fixing an endpoint is a one-file change.

Endpoint URL facts verified 2026-08-11 against services.swpc.noaa.gov (the
build brief's /products/solar-wind/ URLs are stale):

  * /json/rtsw/* files are arrays of objects, NEWEST-FIRST, with every
    available spacecraft interleaved; rows carry "active": true for the
    source SWPC currently designates. We always filter on that flag and
    never branch on a spacecraft name (today it is "SOLAR1"; ACE and IMAP
    are present but inactive, DSCOVR is absent).
  * /products/geospace/propagated-solar-wind.json is the old array-of-arrays
    format whose first row is the header. propagated_time_tag - time_tag is
    the official L1->Earth lead time.
  * /products/noaa-planetary-k-index.json rows use capital-K "Kp";
    the forecast file uses lowercase "kp".
  * /json/ovation_aurora_latest.json is a single object, not an array.

Parsers take the parsed JSON (json.loads output) and return a list of dicts,
newest-first, with time_tag as a timezone-aware UTC datetime, numeric fields
as float (None where the feed had null), active==true rows only where the
flag exists, and repeated time_tags deduped keeping the first occurrence in
the newest-first file. Rows that cannot be normalized are skipped, never
raised on - real-time SWPC data has gaps and garbage and the parsers are the
bulkhead.
"""
from __future__ import annotations

import logging
import os
from collections import namedtuple
from datetime import datetime, timezone
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# The origin every endpoint hangs off. Overridable ONLY so an offline test can
# point the whole registry at a local fixture server (validation gate 4 serves
# tests/fixtures/payloads/ over http and then kills the server mid-run):
# export LIVECORE_BASE_URL before import, or call set_base_url() at runtime.
# Nothing in the production path sets either - the default is the real service.
BASE = os.environ.get("LIVECORE_BASE_URL",
                      "https://services.swpc.noaa.gov").rstrip("/")

# Polling tiers (seconds between polls; see poller.py):
#   tier 1 = 60 s   (1-min cadence products)
#   tier 2 = 300 s  (Kp / Dst / ovation - slower cadence)
#   tier 3 = 3600 s (ephemerides - hourly cadence)
Endpoint = namedtuple("Endpoint", "url tier parse_fn max_staleness_s")


def parse_time(s) -> datetime | None:
    """Parse an SWPC time string to an aware UTC datetime, or None.

    Handles both house formats: '2026-08-11T17:32:00' (rtsw JSON, optional
    trailing Z) and '2026-08-11 17:32:00.000' (products JSON).
    """
    if not isinstance(s, str) or not s:
        return None
    t = s.strip().rstrip("Z").replace(" ", "T")
    try:
        return datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _f(v) -> float | None:
    """Coerce a feed value to float; None for null/empty/garbage."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rows_active_newest_first(raw, convert) -> list[dict]:
    """Shared body for the /json/rtsw/* array-of-objects files.

    Filters active==true, converts each row via `convert` (which returns a
    dict with a datetime 'time_tag' or None to skip), dedupes repeated
    time_tags keeping the first occurrence in file order (the file is
    newest-first, so first == newest), and returns rows sorted newest-first.
    """
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for row in raw:
        if not isinstance(row, dict) or row.get("active") is not True:
            continue
        try:
            rec = convert(row)
        except Exception:
            rec = None
        if rec is None or rec["time_tag"] is None or rec["time_tag"] in seen:
            continue
        seen.add(rec["time_tag"])
        out.append(rec)
    out.sort(key=lambda r: r["time_tag"], reverse=True)
    return out


def parse_mag(raw) -> list[dict]:
    """rtsw_mag_1m: IMF at L1, active spacecraft only, newest-first."""
    def convert(row):
        return {
            "time_tag": parse_time(row.get("time_tag")),
            "source": row.get("source"),
            "bt": _f(row.get("bt")),
            "bx_gsm": _f(row.get("bx_gsm")),
            "by_gsm": _f(row.get("by_gsm")),
            "bz_gsm": _f(row.get("bz_gsm")),
            "bx_gse": _f(row.get("bx_gse")),
            "by_gse": _f(row.get("by_gse")),
            "bz_gse": _f(row.get("bz_gse")),
            "overall_quality": row.get("overall_quality"),
        }
    return _rows_active_newest_first(raw, convert)


def parse_wind(raw) -> list[dict]:
    """rtsw_wind_1m: plasma at L1. proton_* keys shortened to schema names."""
    def convert(row):
        return {
            "time_tag": parse_time(row.get("time_tag")),
            "source": row.get("source"),
            "speed": _f(row.get("proton_speed")),
            "density": _f(row.get("proton_density")),
            "temperature": _f(row.get("proton_temperature")),
            "vx_gse": _f(row.get("proton_vx_gse")),
            "vy_gse": _f(row.get("proton_vy_gse")),
            "vz_gse": _f(row.get("proton_vz_gse")),
            "overall_quality": row.get("overall_quality"),
        }
    return _rows_active_newest_first(raw, convert)


def parse_ephemerides(raw) -> list[dict]:
    """rtsw_ephemerides_1h: active spacecraft GSE position in km, hourly."""
    def convert(row):
        return {
            "time_tag": parse_time(row.get("time_tag")),
            "source": row.get("source"),
            "x_gse": _f(row.get("x_gse")),
            "y_gse": _f(row.get("y_gse")),
            "z_gse": _f(row.get("z_gse")),
        }
    return _rows_active_newest_first(raw, convert)


def parse_propagated(raw) -> list[dict]:
    """propagated-solar-wind: old array-of-arrays format, first row = header.

    Adds 'lead_s' = propagated_time_tag - time_tag (the official L1->Earth
    lead in seconds). No active flag in this product - it is already the
    blended operational stream.
    """
    if not isinstance(raw, list) or len(raw) < 2 or not isinstance(raw[0], list):
        return []
    header = [str(h) for h in raw[0]]
    idx = {name: i for i, name in enumerate(header)}
    needed = ("time_tag", "propagated_time_tag")
    if any(n not in idx for n in needed):
        return []
    out, seen = [], set()
    for row in raw[1:]:
        if not isinstance(row, list) or len(row) != len(header):
            continue
        t = parse_time(row[idx["time_tag"]])
        if t is None or t in seen:
            continue
        seen.add(t)
        prop_t = parse_time(row[idx["propagated_time_tag"]])
        rec = {"time_tag": t, "propagated_time_tag": prop_t,
               "lead_s": (prop_t - t).total_seconds() if prop_t else None}
        for name in ("speed", "density", "temperature", "bx", "by", "bz",
                     "bt", "vx", "vy", "vz"):
            rec[name] = _f(row[idx[name]]) if name in idx else None
        out.append(rec)
    out.sort(key=lambda r: r["time_tag"], reverse=True)
    return out


def parse_kp(raw) -> list[dict]:
    """noaa-planetary-k-index: observed Kp, 3-h cadence. Capital-K 'Kp'."""
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        t = parse_time(row.get("time_tag"))
        kp = _f(row.get("Kp"))
        if t is None or kp is None or t in seen:
            continue
        seen.add(t)
        out.append({"time_tag": t, "kp": kp,
                    "a_running": _f(row.get("a_running")),
                    "station_count": _f(row.get("station_count"))})
    out.sort(key=lambda r: r["time_tag"], reverse=True)
    return out


def parse_kp_forecast(raw) -> list[dict]:
    """noaa-planetary-k-index-forecast: lowercase 'kp', observed/estimated."""
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        t = parse_time(row.get("time_tag"))
        kp = _f(row.get("kp"))
        if t is None or kp is None or t in seen:
            continue
        seen.add(t)
        out.append({"time_tag": t, "kp": kp,
                    "observed": row.get("observed"),
                    "noaa_scale": row.get("noaa_scale")})
    out.sort(key=lambda r: r["time_tag"], reverse=True)
    return out


def parse_dst(raw) -> list[dict]:
    """geospace_dst_1_hour: predicted Dst, 1-min cadence. Values can arrive
    in scientific notation - float() handles that."""
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for row in raw:
        if not isinstance(row, dict):
            continue
        t = parse_time(row.get("time_tag"))
        dst = _f(row.get("dst"))
        if t is None or dst is None or t in seen:
            continue
        seen.add(t)
        out.append({"time_tag": t, "dst": dst})
    out.sort(key=lambda r: r["time_tag"], reverse=True)
    return out


def parse_ovation(raw) -> list[dict]:
    """ovation_aurora_latest: SINGLE object, not an array. Returned as a
    one-row list to keep the uniform rows contract: time_tag is the
    Observation Time; coordinates is the [[lon, lat, prob], ...] grid."""
    if not isinstance(raw, dict):
        return []
    t = parse_time(raw.get("Observation Time"))
    coords = raw.get("coordinates")
    if t is None or not isinstance(coords, list):
        return []
    return [{"time_tag": t,
             "forecast_time": parse_time(raw.get("Forecast Time")),
             "coordinates": coords}]


ENDPOINTS: dict[str, Endpoint] = {
    "mag": Endpoint(BASE + "/json/rtsw/rtsw_mag_1m.json",
                    1, parse_mag, 900),
    "wind": Endpoint(BASE + "/json/rtsw/rtsw_wind_1m.json",
                     1, parse_wind, 900),
    "propagated": Endpoint(BASE + "/products/geospace/propagated-solar-wind.json",
                           1, parse_propagated, 900),
    "kp": Endpoint(BASE + "/products/noaa-planetary-k-index.json",
                   2, parse_kp, 4 * 3600),
    "kp_forecast": Endpoint(BASE + "/products/noaa-planetary-k-index-forecast.json",
                            2, parse_kp_forecast, 4 * 3600),
    "dst": Endpoint(BASE + "/json/geospace/geospace_dst_1_hour.json",
                    2, parse_dst, 3600),
    "ovation": Endpoint(BASE + "/json/ovation_aurora_latest.json",
                        2, parse_ovation, 3600),
    "ephemerides": Endpoint(BASE + "/json/rtsw/rtsw_ephemerides_1h.json",
                            3, parse_ephemerides, 2 * 86400),
}


# Each endpoint's path, captured once from the registry above. set_base_url()
# rebuilds URLs from THESE rather than from the current ones, so repeated calls
# never compound a prefix and the original service is always one call away.
_PATHS = {key: urlsplit(ep.url).path for key, ep in ENDPOINTS.items()}


def set_base_url(base: str) -> str:
    """Repoint every registered endpoint at another origin. Returns the old one.

    Test-only hook (see BASE). ENDPOINTS is mutated IN PLACE so modules that
    already did `from .endpoints import ENDPOINTS` see the change.
    """
    global BASE
    previous = BASE
    BASE = str(base).rstrip("/")
    for key, ep in list(ENDPOINTS.items()):
        ENDPOINTS[key] = ep._replace(url=BASE + _PATHS[key])
    log.info("endpoint base url %s -> %s", previous, BASE)
    return previous


def fetch(key: str, session, timeout: float = 10,
          etag: str | None = None, last_modified: str | None = None):
    """GET one endpoint with optional conditional-GET validators.

    Returns (status_code, raw_bytes, etag, last_modified). On 304 the body
    is empty and the caller should just touch its cache freshness. Raises
    for HTTP errors and network failures - the poller catches everything.
    """
    ep = ENDPOINTS[key]
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    resp = session.get(ep.url, timeout=timeout, headers=headers)
    resp.raise_for_status()  # 304 is < 400, passes through
    return (resp.status_code, resp.content,
            resp.headers.get("ETag"), resp.headers.get("Last-Modified"))
