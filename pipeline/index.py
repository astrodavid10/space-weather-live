"""data/index.json (swl.index/1): the one file the app reads first.

Rebuilt from what is on disk under ``out`` every run, whatever happened, so the
app always learns how old each source is. Status vocabulary is sol's:
ok | degraded | stale | absent, plus last_attempt_status ok | <stage>_failed.

``age_hours`` is how old the MANIFEST is; ``data_age_hours`` is how old the
newest epoch is. The guest is told the second one, never the first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import PIPELINE_VERSION
from .config import DATA_END_STALE_HOURS, SCHEMA_INDEX, STALE_AFTER_HOURS
from .io_utils import age_hours, iso_z, parse_iso_z, read_json, unix_s, utcnow, write_json


def _lookback_entry(out: Path, now, failure: Optional[str]) -> dict:
    lb = read_json(out / "lookback" / "index.json")
    if not lb:
        return {"url": "lookback/index.json", "status": "absent", "stale": True,
                "note": "no look-back has been published yet", "last_error": failure or ""}
    gen = parse_iso_z(lb.get("generated_iso") or "")
    end = parse_iso_z(lb.get("data_end_iso") or "")
    a = age_hours(gen, now) if gen else None
    d = age_hours(end, now) if end else None
    stale = (a is None or a > STALE_AFTER_HOURS) or (d is None or d > DATA_END_STALE_HOURS)
    status = lb.get("status", "ok")
    if stale:
        status = "stale"
    elif failure or lb.get("partial"):
        status = "degraded"
    e = {"url": "lookback/index.json", "status": status, "stale": stale,
         "event_id": lb.get("event_id"), "title": lb.get("title"),
         "generated_iso": lb.get("generated_iso"),
         "generated_unix": unix_s(gen) if gen else None,
         "age_hours": round(a, 2) if a is not None else None,
         "data_end_iso": lb.get("data_end_iso"),
         "data_age_hours": round(d, 2) if d is not None else None,
         "feed": (lb.get("feed") or {}).get("used"),
         "note": lb.get("note", "")}
    if lb.get("timing"):
        e["timing"] = lb["timing"]
    if failure:
        e["last_error"] = failure
    return e


def _storms_entry(out: Path) -> dict:
    st = read_json(out / "storms" / "index.json")
    if not st:
        return {"url": "storms/index.json", "status": "absent", "count": 0}
    pub = parse_iso_z(st.get("published_iso") or "")
    return {"url": "storms/index.json", "status": "ok" if st.get("storms") else "absent",
            "count": len(st.get("storms") or []), "published_iso": st.get("published_iso"),
            "published_unix": unix_s(pub) if pub else None,
            "release_tag": (st.get("bundle") or {}).get("release_tag")}


def build_index(out: Path, attempt_status: str = "ok", failure: Optional[str] = None,
                run_id: Optional[str] = None, engine: Optional[dict] = None) -> dict:
    out = Path(out)
    now = utcnow()
    prev = read_json(out / "index.json") or {}
    idx = {
        "schema": SCHEMA_INDEX, "pipeline_version": PIPELINE_VERSION,
        "generated_iso": iso_z(now), "generated_unix": unix_s(now), "run_id": run_id,
        "stale_after_hours": STALE_AFTER_HOURS, "data_end_stale_hours": DATA_END_STALE_HOURS,
        "engine": engine or prev.get("engine") or {},
        "sources": {"lookback": _lookback_entry(out, now, failure), "storms": _storms_entry(out)},
        "last_attempt_iso": iso_z(now), "last_attempt_status": attempt_status,
    }
    write_json(out / "index.json", idx)
    return idx
