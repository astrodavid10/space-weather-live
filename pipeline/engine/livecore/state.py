"""State builder: cached SWPC payloads -> the schema-1 state object.

Produces exactly the contract in STATE_SCHEMA.md (frozen; bump `schema` to
change it). One state object describes the solar-wind driving conditions at
one epoch, identical in shape for live, cached, and event playback - this
module only ever emits LIVE or CACHED; the EVENT tier and the presenter
override belong to the daemon.

Rules implemented here (all from STATE_SCHEMA.md):

  * A sample row is valid iff every needed key is non-null AND its quality
    is good. Assumption (2026-08-11): overall_quality == 0 means good. If
    the live feed clearly uses another convention - e.g. every row of the
    last hour is quality 5 - we log that once and treat the modal value of
    the trailing hour as 'good' instead of discarding a healthy feed.
  * Driving values are the median of valid samples in the trailing 5 minutes
    (measured back from each product's newest valid sample - no forward
    extrapolation is ever invented).
  * pdyn_npa = 1.6726e-6 * density * speed**2  (proton-only, brief formula).
  * iopt = clamp(round(Kp) + 1, 1, 7)          (T89 input).
  * tier LIVE -> CACHED when the newest valid driving sample is > 15 min old.
    epoch is the older of the two driving products' newest valid samples: it
    is the newest instant at which the *complete* driving state is known, so
    one dead product cannot masquerade as live.
  * propagation.lead_s comes from the propagated product's official
    propagated_time_tag - time_tag; fallback when that product is missing:
    x_l1_km / speed from the ephemerides. l1_gse_re = position km / 6371.2.
  * dst may be null - consumers tolerate that (the T89 path needs no Dst).

build_state returns None when invariant 1 (no nulls in driving fields)
cannot be satisfied; the daemon then holds its previous state and lets the
staleness rule flip the tier.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import median

from .cache import CacheStore, write_atomic
from .endpoints import ENDPOINTS

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
RE_KM = 6371.2                 # Earth radius, km, per STATE_SCHEMA.md
STALE_LIMIT_S = 15 * 60        # LIVE -> CACHED threshold
DRIVING_WINDOW_S = 5 * 60      # median window for driving values

MAG_KEYS = ("bx_gsm", "by_gsm", "bz_gsm", "bt")
WIND_KEYS = ("speed", "density", "temperature", "vx_gse", "vy_gse", "vz_gse")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def tier_for(age_s: float) -> str:
    """LIVE while the newest valid driving sample is <= 15 min old."""
    return "LIVE" if age_s <= STALE_LIMIT_S else "CACHED"


def _good_quality(rows: list[dict], product: str) -> int:
    """The overall_quality value that counts as 'good' for these rows.

    Assumed 0 (see module docstring). If the modal quality over the trailing
    hour is something else, the feed is using a different convention: log it
    and accept the modal value rather than discarding a healthy feed.
    """
    if not rows:
        return 0
    newest = rows[0]["time_tag"]
    hour = [r.get("overall_quality") for r in rows
            if r["time_tag"] >= newest - timedelta(hours=1)]
    hour = [q for q in hour if q is not None]
    if not hour:
        return 0
    modal = Counter(hour).most_common(1)[0][0]
    if modal != 0:
        log.warning("%s: modal overall_quality over the last hour is %r, "
                    "not 0 - treating %r as 'good'", product, modal, modal)
    return modal


POSITIVE_KEYS = ("density", "speed", "temperature")   # strictly positive by physics


def _valid_rows(rows: list[dict], keys, product: str) -> list[dict]:
    """Rows with every needed key non-null and good quality, newest-first.

    A density, speed or temperature of 0 (or below) is a bad sample, not a quiet one -
    the ACE real-time feed served density 0.0 beside a valid speed for hours on
    2026-09-21/22 - and a zero dynamic pressure would crash the boundary model, so
    such rows are treated exactly like a null: skipped, and the previous state holds.
    """
    good = _good_quality(rows, product)
    return [r for r in rows
            if r.get("overall_quality") == good
            and all(r.get(k) is not None for k in keys)
            and all((r.get(k) is None) or (r.get(k) > 0) for k in POSITIVE_KEYS if k in keys)]


def _window_medians(valid: list[dict], keys) -> dict:
    """Median of each key over the trailing 5 min of valid samples."""
    newest = valid[0]["time_tag"]
    cutoff = newest - timedelta(seconds=DRIVING_WINDOW_S)
    window = [r for r in valid if r["time_tag"] >= cutoff]
    return {k: median(r[k] for r in window) for k in keys}


def _parsed(cache: CacheStore, key: str) -> list[dict]:
    payload, _meta, _age = cache.latest(key)
    if payload is None:
        return []
    try:
        return ENDPOINTS[key].parse_fn(payload)
    except Exception:
        log.exception("%s: cached payload failed to parse", key)
        return []


def build_state(cache: CacheStore, now: datetime | None = None) -> dict | None:
    """Build one schema-1 state object from the cache, or None.

    None means invariant 1 (no nulls in driving fields) cannot be met - no
    valid mag samples, no valid wind samples, or no Kp. The caller holds its
    previous state in that case; nothing partial is ever emitted.
    """
    now = now or datetime.now(timezone.utc)

    mag_valid = _valid_rows(_parsed(cache, "mag"), MAG_KEYS, "mag")
    wind_valid = _valid_rows(_parsed(cache, "wind"), WIND_KEYS, "wind")
    kp_rows = _parsed(cache, "kp")
    if not mag_valid or not wind_valid or not kp_rows:
        log.warning("state not emitted: valid mag=%d wind=%d kp=%d",
                    len(mag_valid), len(wind_valid), len(kp_rows))
        return None

    sw = _window_medians(mag_valid, MAG_KEYS)
    sw.update(_window_medians(wind_valid, WIND_KEYS))
    sw["pdyn_npa"] = 1.6726e-6 * sw["density"] * sw["speed"] ** 2

    mag_newest = mag_valid[0]["time_tag"]
    wind_newest = wind_valid[0]["time_tag"]
    epoch = min(mag_newest, wind_newest)
    age_s = round((now - epoch).total_seconds())

    kp_row = kp_rows[0]
    kp_value = kp_row["kp"]
    iopt = max(1, min(7, int(round(kp_value)) + 1))

    dst_rows = _parsed(cache, "dst")
    dst = None
    if dst_rows:
        dst = {"value": dst_rows[0]["dst"],
               "time_tag": _iso(dst_rows[0]["time_tag"]),
               "source": "geospace_pred"}

    lead_s = None
    for row in _parsed(cache, "propagated"):
        if row.get("lead_s") is not None:
            lead_s = row["lead_s"]
            break
    l1_gse_re = None
    eph_rows = _parsed(cache, "ephemerides")
    if eph_rows:
        eph = eph_rows[0]
        if all(eph.get(k) is not None for k in ("x_gse", "y_gse", "z_gse")):
            l1_gse_re = [eph["x_gse"] / RE_KM, eph["y_gse"] / RE_KM,
                         eph["z_gse"] / RE_KM]
            if lead_s is None and sw["speed"] > 0:
                lead_s = eph["x_gse"] / sw["speed"]  # x_l1_km / speed
    arrival = _iso(epoch + timedelta(seconds=lead_s)) if lead_s is not None \
        else None

    return {
        "schema": SCHEMA_VERSION,
        "epoch": _iso(epoch),
        "generated": _iso(now),
        "tier": tier_for(age_s),
        "event_id": None,
        "source": mag_valid[0].get("source") or wind_valid[0].get("source"),
        "age_s": age_s,
        "sw": {
            "bx_gsm": sw["bx_gsm"], "by_gsm": sw["by_gsm"],
            "bz_gsm": sw["bz_gsm"], "bt": sw["bt"],
            "density": sw["density"], "speed": sw["speed"],
            "temperature": sw["temperature"],
            "vx_gse": sw["vx_gse"], "vy_gse": sw["vy_gse"],
            "vz_gse": sw["vz_gse"],
            "pdyn_npa": sw["pdyn_npa"],
            "quality": {"mag": mag_valid[0].get("overall_quality"),
                        "wind": wind_valid[0].get("overall_quality")},
            "stale_s": {"mag": round((now - mag_newest).total_seconds()),
                        "wind": round((now - wind_newest).total_seconds())},
        },
        "kp": {"value": kp_value, "time_tag": _iso(kp_row["time_tag"]),
               "iopt": iopt},
        "dst": dst,
        "propagation": {
            "lead_s": round(lead_s) if lead_s is not None else None,
            "arrival": arrival,
            "l1_gse_re": l1_gse_re,
        },
        "model": {"external": "t89", "iopt": iopt, "parmod": None},
    }


def data_age_str(age_s: float, now: datetime | None = None) -> str:
    """Dome readout: 'as of HH:MM UT (Xm ago)'. Honest at any staleness."""
    now = now or datetime.now(timezone.utc)
    asof = now - timedelta(seconds=age_s)
    if age_s < 60:
        rel = "<1m"
    elif age_s < 3600:
        rel = f"{int(age_s // 60)}m"
    else:
        h = int(age_s // 3600)
        rel = f"{h}h {int(age_s % 3600 // 60)}m"
    return f"as of {asof.strftime('%H:%M')} UT ({rel} ago)"


def write_state_atomic(state: dict, path) -> None:
    """Atomically write the state JSON (tmp + os.replace) for consumers."""
    write_atomic(path, json.dumps(state, indent=1,
                                  allow_nan=False).encode("ascii"))


HUD_SEGMENTS = 6


def _hud_ascii(s: str) -> str:
    return "".join(ch if 32 <= ord(ch) < 127 and ch not in '";|' else " " for ch in str(s)).strip()


def hud_segments(state: dict, live: bool = False) -> list[str]:
    """The DOME-VISIBLE readout as six short segments (one textClass each, laid along the
    base of the dome by dsexport.ds / MagnetoLiveLoader.js - David 2026-09-14):

        ["Wind 452 km/s", "Bz -5.3 nT", "n 6.2 /cc", "Kp 3.7", "2026-08-11 17:32 UT",
         "SOLAR1 - REPLAY"]                      # an archive replay
        ...                                      "LIVE 8m old - SOLAR1"     # the live feed
                                                 "CACHED 1h05m old - SOLAR1"

    The honesty law in six strings: the driving values, WHEN they were measured, WHICH
    spacecraft, and how OLD the data is. ASCII only, no quotes/semicolons/pipes - they
    live inside .ds strings and are joined by '|' in the manifests (see hud_text).
    """
    sw = state.get("sw") or {}
    kp = float((state.get("kp") or {}).get("value", 0.0))
    ep = str(state.get("epoch", ""))
    try:
        when = datetime.fromisoformat(ep.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M UT")
    except ValueError:
        when = ep
    source = str(state.get("source", "")) or "-"
    if live or not state.get("event_id"):
        age = float(state.get("age_s", 0.0))
        if age < 60:
            rel = "<1m"
        elif age < 3600:
            rel = f"{int(age // 60)}m"
        else:
            rel = f"{int(age // 3600)}h{int(age % 3600 // 60):02d}m"
        tail = f"{state.get('tier') or 'LIVE'} {rel} old - {source}"
    else:
        tail = f"{source} - REPLAY"
    segs = [f"Wind {float(sw.get('speed', 0.0)):.0f} km/s",
            f"Bz {float(sw.get('bz_gsm', 0.0)):+.1f} nT",
            f"n {float(sw.get('density', 0.0)):.1f} /cc",
            f"Kp {kp:.1f}", when, tail]
    return [_hud_ascii(s) for s in segs]


def hud_text(state: dict, live: bool = False) -> str:
    """The six readout segments joined by '|' - the one-string-per-step form the manifests
    carry; dsexport.ds and MagnetoLiveLoader.js split it back into the segment objects."""
    return "|".join(hud_segments(state, live))
