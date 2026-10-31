r"""TS05 (Tsyganenko & Sitnov 2005) driving for storm renders - geopack's `t04`.

T96 is fitted for Pdyn 0.5-10 nPa, Dst -100..+20 nT and |By|, |Bz| <= 10 nT; the curated
storms peak far outside that (Bastille: 45 nPa, Dst -335, Bz -60). TS05 was built on 37
storms and takes the SAME first four parameters plus six "W" indices that integrate the
solar-wind history from the start of the storm (David's decision 2026-09-14: "swap from
T96 to TS05 where appropriate for the values").

    parmod = [pdyn (nPa), Dst (nT), By (nT), Bz (nT), W1, W2, W3, W4, W5, W6]

W_i(t) = (r_i / 12) * SUM_k S_i(t_k) * exp(r_i * (t_k - t) / 60)      (t, t_k in minutes,
5-minute samples; 1/12 = 5 min in hours), with the source function
S_i = (N/5)^lambda_i * (V/400)^beta_i * (Bs/5)^gamma_i, Bs = -Bz for southward IMF else 0,
N in cm^-3, V in km/s - Tsyganenko & Sitnov (2005), JGR 110, A03208, eq. 7 and Table 1.
The coefficient table below is transcribed from that paper / the TS05 driver notes.
NEEDS-VERIFY against the official TS05 input files (Tsyganenko's site publishes W1-W6 for
1995-2015 at 5-min cadence); the shapes it produces are in the right family either way,
which is more than can be said for T96 at Bz -60 nT.
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np

# Table 1, Tsyganenko & Sitnov (2005): relaxation rates r (1/hour) and the source exponents.
TS05_R = (0.39, 0.70, 0.031, 0.58, 1.15, 0.88)
TS05_LAMBDA = (0.39, 0.46, 0.39, 0.42, 0.41, 1.29)
TS05_BETA = (0.80, 0.18, 2.32, 1.25, 1.60, 2.40)
TS05_GAMMA = (0.87, 0.67, 1.32, 1.29, 0.69, 0.53)
SAMPLE_MIN = 5.0                 # the event timeline's cadence (magneto.events CADENCE_S)

# T96's stated range - outside it a storm gets TS05 ("auto" model choice)
T96_PDYN_MAX = 10.0
T96_DST_MIN = -100.0
T96_B_MAX = 10.0


def _minutes(ep: str) -> float:
    return datetime.fromisoformat(str(ep).replace("Z", "+00:00")).timestamp() / 60.0


def source_terms(n_cm3: float, v_kms: float, bz_nt: float) -> np.ndarray:
    """S_i for one sample (six values)."""
    bs = max(-float(bz_nt), 0.0)
    n = max(float(n_cm3), 0.0) / 5.0
    v = max(float(v_kms), 0.0) / 400.0
    return np.array([(n ** la) * (v ** be) * ((bs / 5.0) ** ga)
                     for la, be, ga in zip(TS05_LAMBDA, TS05_BETA, TS05_GAMMA)])


def w_indices(states: list[dict]) -> dict[str, list[float]]:
    """W1..W6 at every state's epoch, integrated over the WHOLE timeline given.

    `states` must be the event's full timeline in time order (5-minute cadence); the
    integral starts at zero at its first sample, so hand in the timeline from the storm's
    quiet run-in, not just the frames being rendered.
    """
    if not states:
        return {}
    t = np.array([_minutes(s["epoch"]) for s in states])
    S = np.array([source_terms(s["sw"]["density"], s["sw"]["speed"], s["sw"]["bz_gsm"])
                  for s in states])                            # (n, 6)
    r = np.array(TS05_R)
    out = {}
    W = np.zeros(6)
    prev_t = t[0]
    for k in range(len(states)):
        dt_h = (t[k] - prev_t) / 60.0                          # hours since the last sample
        W = W * np.exp(-r * dt_h) + (r / 12.0) * S[k] * (max(dt_h, 0.0) * 60.0 / SAMPLE_MIN
                                                          if k else 1.0)
        prev_t = t[k]
        out[str(states[k]["epoch"])] = [float(round(x, 5)) for x in W]
    return out


def t96_in_range(states: list[dict]) -> bool:
    """True when every state sits inside T96's fitted range."""
    for s in states:
        sw = s["sw"]
        if float(sw.get("pdyn_npa", 0.0)) > T96_PDYN_MAX:
            return False
        if abs(float(sw["bz_gsm"])) > T96_B_MAX or abs(float(sw.get("by_gsm", 0.0))) > T96_B_MAX:
            return False
        d = (s.get("dst") or {}).get("value")
        if d is not None and float(d) < T96_DST_MIN:
            return False
    return True


def out_of_range_summary(states: list[dict]) -> str:
    sw = [s["sw"] for s in states]
    pd = max(float(x.get("pdyn_npa", 0.0)) for x in sw)
    bz = min(float(x["bz_gsm"]) for x in sw)
    dsts = [float(s["dst"]["value"]) for s in states if s.get("dst")]
    return (f"peak Pdyn {pd:.1f} nPa (T96 <= {T96_PDYN_MAX:g}), min Bz {bz:.1f} nT "
            f"(|Bz| <= {T96_B_MAX:g}), min Dst {min(dsts):.0f} nT (>= {T96_DST_MIN:g})"
            if dsts else f"peak Pdyn {pd:.1f} nPa, min Bz {bz:.1f} nT")


def as_ts05(selected: list[dict], timeline: list[dict]) -> list[dict]:
    """Copies of `selected` driven by TS05: model.external 'ts05', 10-element parmod."""
    W = w_indices(timeline)
    out = []
    for s in selected:
        w = W.get(str(s["epoch"]))
        if w is None:
            raise ValueError(f"{s['epoch']} is not on the event timeline handed to as_ts05")
        c = dict(s)
        sw = s["sw"]
        dst = float((s.get("dst") or {}).get("value", 0.0))
        c["model"] = {"external": "ts05", "iopt": int(s["model"].get("iopt", 3)),
                      "parmod": [float(sw["pdyn_npa"]), dst, float(sw["by_gsm"]),
                                 float(sw["bz_gsm"])] + list(w)}
        out.append(c)
    return out
