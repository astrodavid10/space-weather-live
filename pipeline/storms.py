"""Lay the dome's pre-rendered web bundle zip out as the published data tree.

    data/storms/<event_id>/<preset>/<event_id>.json      engine manifest, byte-for-byte
    data/storms/<event_id>/<preset>/<event_id>.bin.gz    geometry, gzip -9 mtime 0
    data/storms/index.json                               swl.storms/1

and, with ``with_lookback``, the zip's look-back as a seed for data/lookback/ so
the site has a real (honestly aged) look-back before the Actions render exists.

The storms are TS05 renders that CI cannot rebuild (~6 CPU-min per epoch), so
this runs once by hand (scripts/publish_storms.py) and the source zip is kept as
a GitHub Release asset. The data workflow seeds them and never republishes them.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import gz
from .config import (DATA_END_STALE_HOURS, PRESETS, SCHEMA_LOOKBACK, SCHEMA_STORMS,
                     STALE_AFTER_HOURS, STORMS)
from .io_utils import atomic_write_bytes, iso_z, utcnow, write_json


def _members(zf: zipfile.ZipFile) -> Dict[str, Dict[str, str]]:
    """{"<preset>/<folder>": {"json": name, "bin": name}} for mobile/desktop only."""
    out: Dict[str, Dict[str, str]] = {}
    for name in zf.namelist():
        parts = name.replace("\\", "/").split("/")
        if len(parts) != 3 or parts[0] not in PRESETS:
            continue
        key = parts[0] + "/" + parts[1]
        if name.endswith(".json"):
            out.setdefault(key, {})["json"] = name
        elif name.endswith(".bin"):
            out.setdefault(key, {})["bin"] = name
    return out


def _write_preset(zf: zipfile.ZipFile, m: Dict[str, str], dest: Path) -> Tuple[dict, dict]:
    """Write one manifest + gz buffer into ``dest``; return (manifest, index entry)."""
    raw_json = zf.read(m["json"])
    meta = json.loads(raw_json)
    eid = meta["event_id"]
    raw = zf.read(m["bin"])
    if len(raw) != meta.get("binary_bytes"):
        raise ValueError("%s: .bin is %d bytes, manifest says %s"
                         % (m["bin"], len(raw), meta.get("binary_bytes")))
    packed = gz.pack(meta, raw)
    assert gz.unpack(meta, packed) == raw, "delta-k round trip"
    dest.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(dest / (eid + ".json"), raw_json)
    atomic_write_bytes(dest / (eid + ".bin.gz"), packed)
    entry = {"json_url": eid + ".json", "bin_gz_url": eid + ".bin.gz",
             "keyframes": int(meta["keyframes"]["count"])}
    entry.update(gz.record(raw, packed))
    return meta, entry


def _prefix(entry: dict, rel: str) -> dict:
    e = dict(entry)
    e["json_url"] = rel + "/" + e["json_url"]
    e["bin_gz_url"] = rel + "/" + e["bin_gz_url"]
    return e


def _window(meta: dict) -> dict:
    src = meta.get("source", {})
    return dict(src.get("window", {}), step_min=src.get("step_min"),
                n_steps=src.get("n_steps_original"))


def build(zip_path: Path, out: Path, with_lookback: bool = False,
          release_tag: Optional[str] = None,
          repo: str = "astrodavid10/space-weather-live") -> dict:
    zip_path = Path(zip_path)
    out = Path(out)
    zip_sha = gz.sha256(zip_path.read_bytes())
    storms: Dict[str, dict] = {}
    lookback: Optional[dict] = None
    with zipfile.ZipFile(zip_path) as zf:
        for key, m in sorted(_members(zf).items()):
            if "json" not in m or "bin" not in m:
                continue
            preset, folder = key.split("/")
            if folder.startswith("storm_"):
                eid = folder[len("storm_"):]
                rel = eid + "/" + preset
                meta, entry = _write_preset(zf, m, out / "storms" / eid / preset)
                s = storms.setdefault(eid, {"meta": meta, "presets": {}})
                s["presets"][preset] = _prefix(entry, rel)
                print("  storms/%-32s %4d kf  %6.2f MB raw -> %6.2f MB gz"
                      % (rel, entry["keyframes"], entry["bin_bytes"] / 1048576,
                         entry["bin_gz_bytes"] / 1048576))
            elif folder == "lookback" and with_lookback:
                meta, entry = _write_preset(zf, m, out / "lookback" / preset)
                lookback = lookback or {"meta": meta, "presets": {}}
                lookback["presets"][preset] = _prefix(entry, preset)
                print("  lookback/%-30s %4d kf  %6.2f MB gz"
                      % (preset + " " + meta["event_id"], entry["keyframes"],
                         entry["bin_gz_bytes"] / 1048576))

    now = iso_z(utcnow())
    release = ({"release_tag": release_tag,
                "release_url": "https://github.com/%s/releases/tag/%s" % (repo, release_tag)}
               if release_tag else {})
    rows = []
    for eid, s in sorted(storms.items(), key=lambda kv: kv[1]["meta"]["source"]["window"]["start"]):
        meta = s["meta"]
        prov = meta.get("provenance", {})
        info = STORMS.get(eid, {"key": eid, "label": eid, "why": ""})
        rows.append({
            "event_id": eid, "key": info["key"], "label": info["label"], "why": info["why"],
            "title": meta.get("title", eid), "tier": "REPLAY",
            "model": meta.get("source", {}).get("model"),
            "window": _window(meta),
            "physics_version": prov.get("physics_version"), "physics_tag": prov.get("physics_tag"),
            "key_moments": prov.get("key_moments", ""),
            "talking_points": prov.get("talking_points", []),
            "presets": s["presets"],
        })
    first = rows[0] if rows else {}
    storms_index = {
        "schema": SCHEMA_STORMS, "published_iso": now,
        "bundle": dict({"file": zip_path.name, "sha256": zip_sha}, **release),
        "engine": {"physics_version": first.get("physics_version"),
                   "physics_tag": first.get("physics_tag")},
        "storms": rows,
    }
    write_json(out / "storms" / "index.json", storms_index)

    if lookback is not None:
        meta = lookback["meta"]
        prov = meta.get("provenance", {})
        epochs = meta["keyframes"]["epoch"]
        write_json(out / "lookback" / "index.json", {
            "schema": SCHEMA_LOOKBACK, "kind": "lookback", "tier": "REPLAY",
            "model": meta.get("source", {}).get("model"),
            "event_id": meta["event_id"], "title": meta.get("title"),
            "status": "ok", "partial": False, "degradation": [],
            # The README dates the bundle build; the look-back window itself ends 09-20.
            "generated_iso": "2026-09-22T00:00:00Z",
            "data_start_iso": epochs[0], "data_end_iso": epochs[-1],
            "stale_after_hours": STALE_AFTER_HOURS, "data_end_stale_hours": DATA_END_STALE_HOURS,
            "window": _window(meta),
            "presets": lookback["presets"],
            "feed": {"used": "ace", "spacecraft": "ACE"},
            "provenance": {
                "upstreams": [
                    {"name": "CCMC ISWA HAPI (ACE MAG + SWEPAM)", "url": "https://iswa.gsfc.nasa.gov/hapi"},
                    {"name": "GFZ Potsdam Kp (via ISWA)", "url": "https://kp.gfz-potsdam.de/"},
                    {"name": "WDC Kyoto Dst quick-look (via ISWA)", "url": "https://wdc.kugi.kyoto-u.ac.jp/dstdir/"}],
                "engine": {"physics_version": prov.get("physics_version"),
                           "physics_tag": prov.get("physics_tag")},
                "external": meta.get("source", {}).get("model"),
                "key_moments": prov.get("key_moments", "")},
            "note": "Hand-published from the 2026-09-22 dome bundle; the scheduled pipeline replaces this.",
        })
    return storms_index
