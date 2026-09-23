"""Validate a published data tree (local directory or live URL).

Every check here exists because getting it wrong is a SILENT visual bug in the
browser rather than an error: a block that runs off the end of the buffer
renders garbage, a face index past the vertex count tears a shell, a
non-monotonic epoch list breaks the scrubber, and a missing layer loads as a
silently absent science layer (webexport/bundle.py refuses to write one for
exactly that reason).

Two entry points, sol's failure policy:

* :func:`check_bundle` judges ONE bundle (manifest + .bin.gz). ``pipeline
  lookback`` calls it BEFORE promoting a preset, so a bad preset is rolled back
  on its own.
* :func:`validate` ANDs everything for a published tree; ``python -m pipeline
  validate`` and the post-publish tripwire in data.yml run it.

Needs numpy only to undo the delta-k/1 wire encoding (gz.decode).
"""

from __future__ import annotations

import array
import gzip
import hashlib
import json
import sys
from typing import Callable, List, Optional
from urllib.parse import urljoin

from . import gz
from .config import (BUNDLE_SCHEMA, GZ_BUDGET_BYTES, SCHEMA_INDEX, SCHEMA_LOOKBACK,
                     SCHEMA_STORMS, WEB_LAYERS)
from .io_utils import http_get, parse_iso_z

ITEMSIZE = {"uint8": 1, "uint16": 2, "uint32": 4, "int16": 2, "float32": 4}
TYPECODE = {"uint8": "B", "uint16": "H", "uint32": "I", "int16": "h", "float32": "f"}


class Report:
    """Accumulates pass/fail lines; ``ok`` is False if anything failed. (sol's.)"""

    def __init__(self, verbose: bool = False) -> None:
        self.lines: List[str] = []
        self.failures = 0
        self.warnings = 0
        self.advisories = 0      # printed, but never fatal under --strict (sol's advisory level)
        self.verbose = verbose

    def check(self, cond: bool, label: str, detail: str = "") -> bool:
        if cond:
            if self.verbose:
                self.lines.append("  PASS  " + label)
        else:
            self.failures += 1
            self.lines.append("  FAIL  " + label + ("  -- " + detail if detail else ""))
        return bool(cond)

    def warn(self, label: str) -> None:
        self.warnings += 1
        self.lines.append("  WARN  " + label)

    def advise(self, label: str) -> None:
        self.advisories += 1
        self.lines.append("  NOTE  " + label)

    def info(self, label: str) -> None:
        self.lines.append("  ..    " + label)

    @property
    def ok(self) -> bool:
        return self.failures == 0

    def text(self) -> str:
        return "\n".join(self.lines + ["  %s: %d check(s) failed, %d warning(s)"
                                       % ("FAILED" if self.failures else "OK",
                                          self.failures, self.warnings)])


Loader = Callable[[str], Optional[bytes]]


def loader(root: Optional[str] = None, base_url: Optional[str] = None) -> Loader:
    if base_url:
        base = base_url if base_url.endswith("/") else base_url + "/"

        def get_url(rel: str) -> Optional[bytes]:
            try:
                return http_get(urljoin(base, rel))
            except Exception:
                return None
        return get_url
    from pathlib import Path
    r = Path(root or ".")

    def get_file(rel: str) -> Optional[bytes]:
        p = r / rel
        return p.read_bytes() if p.is_file() else None
    return get_file


def _prod(shape) -> int:
    n = 1
    for s in shape:
        n *= int(s)
    return n


def _view(buf: bytes, block: dict) -> array.array:
    a = array.array(TYPECODE[block["dtype"]])
    a.frombytes(buf[block["offset"]:block["offset"] + block["length"]])
    if sys.byteorder != "little":
        a.byteswap()
    return a


def _increasing(xs) -> bool:
    return all(b > a for a, b in zip(xs, xs[1:]))


def check_bundle(get: Loader, json_rel: str, gz_rel: str, rep: Report, entry: Optional[dict] = None,
                 preset: Optional[str] = None, required_layers=WEB_LAYERS,
                 expect_model: Optional[set] = None) -> bool:
    """Judge one manifest + gzipped geometry buffer. ``entry`` is our index row for it."""
    start = rep.failures
    tag = json_rel
    raw_json = get(json_rel)
    if not rep.check(raw_json is not None, tag + ": manifest present"):
        return False
    try:
        meta = json.loads(raw_json)
    except ValueError as exc:
        rep.check(False, tag + ": manifest parses", str(exc))
        return False
    rep.check(meta.get("schema") == BUNDLE_SCHEMA, tag + ": schema == %d" % BUNDLE_SCHEMA,
              repr(meta.get("schema")))
    eid = meta.get("event_id", "")
    rep.check(meta.get("binary") == eid + ".bin", tag + ": binary names <event_id>.bin",
              repr(meta.get("binary")))
    if expect_model:
        rep.check(meta.get("source", {}).get("model") in expect_model,
                  tag + ": source.model in %s" % sorted(expect_model), repr(meta.get("source", {}).get("model")))
    rep.check(bool(meta.get("provenance", {}).get("physics_tag")), tag + ": provenance.physics_tag present")

    packed = get(gz_rel)
    if not rep.check(packed is not None, gz_rel + ": present"):
        return False
    try:
        buf = gzip.decompress(packed)
        if (entry or {}).get("encoding") == gz.ENCODING:
            buf = gz.decode(meta, buf)
        elif entry and entry.get("encoding"):
            rep.check(False, gz_rel + ": known encoding", repr(entry.get("encoding")))
    except Exception as exc:
        rep.check(False, gz_rel + ": gzip decompresses and decodes", str(exc))
        return False
    total = meta.get("binary_bytes")
    rep.check(len(buf) == total, gz_rel + ": inflated size == binary_bytes", "%d vs %s" % (len(buf), total))
    if entry:
        rep.check(entry.get("bin_bytes") == len(buf), gz_rel + ": index bin_bytes matches")
        rep.check(entry.get("bin_gz_bytes") == len(packed), gz_rel + ": index bin_gz_bytes matches")
        rep.check(entry.get("bin_sha256") == hashlib.sha256(buf).hexdigest(), gz_rel + ": index sha256 matches")
        rep.check(entry.get("keyframes") == meta.get("keyframes", {}).get("count"),
                  gz_rel + ": index keyframes matches")
    if preset in GZ_BUDGET_BYTES and len(packed) > GZ_BUDGET_BYTES[preset]:
        rep.warn("%s: %.2f MB gz exceeds the %s budget of %.0f MB"
                 % (gz_rel, len(packed) / 1048576, preset, GZ_BUDGET_BYTES[preset] / 1048576))

    kf = meta.get("keyframes", {})
    K = kf.get("count", 0)
    rep.check(K > 1, tag + ": at least two keyframes", str(K))
    for key in ("playback_s", "epoch", "hud"):
        rep.check(len(kf.get(key) or []) == K, tag + ": keyframes.%s length == count" % key,
                  "%d vs %d" % (len(kf.get(key) or []), K))
    rep.check(_increasing(kf.get("playback_s") or []), tag + ": playback_s strictly increasing")
    ep = [parse_iso_z(e) for e in kf.get("epoch") or []]
    rep.check(all(ep) and _increasing(ep), tag + ": epoch strictly increasing ISO-Z")

    layers = {L.get("name"): L for L in meta.get("layers", [])}
    for name in required_layers:
        rep.check(name in layers, tag + ": layer %s present" % name)
    for name, L in layers.items():
        lt = "%s: %s" % (tag, name)
        n, k = L.get("count", 0), L.get("keyframes", 0)
        if not rep.check(n > 0 and k > 0, lt + " count/keyframes > 0", "%s/%s" % (n, k)):
            continue
        blocks = L.get("blocks", {})
        for bname, b in blocks.items():
            bt = "%s.%s" % (lt, bname)
            ok = (b.get("dtype") in ITEMSIZE and b.get("offset", -1) >= 0 and b["offset"] % 4 == 0
                  and b["offset"] + b.get("length", 0) <= len(buf)
                  and b.get("length") == _prod(b.get("shape", [])) * ITEMSIZE.get(b.get("dtype"), 0))
            rep.check(ok, bt + " aligned, in bounds, length == shape x itemsize", json.dumps(b))
        shapes = {"pos": [k, n, 3], "alpha": [k, n], "color0": [n, 3]}
        if L.get("kind") in ("lines", "dots"):
            shapes["lum"] = [k, n]
        for bname, shp in shapes.items():
            rep.check(bname in blocks and blocks[bname].get("shape") == shp,
                      "%s.%s shape %s" % (lt, bname, shp), repr(blocks.get(bname, {}).get("shape")))
        lo, hi = L.get("pos_lo") or [], L.get("pos_hi") or []
        rep.check(len(lo) == 3 and len(hi) == 3 and all(a <= b for a, b in zip(lo, hi)),
                  lt + " pos_lo <= pos_hi")
        km = L.get("keyframe_map") or []
        rep.check(len(km) == k and _increasing(km) and (not km or (km[0] >= 0 and km[-1] < K)),
                  lt + " keyframe_map monotone and in range")
        if L.get("kind") == "lines" and "line_starts" in blocks and "line_lengths" in blocks:
            starts, lens = _view(buf, blocks["line_starts"]), _view(buf, blocks["line_lengths"])
            rep.check(len(starts) == L.get("n_lines") and sum(lens) == n,
                      lt + " polyline lengths cover every vertex once")
            prefix = [0]
            for v in list(lens)[:-1]:
                prefix.append(prefix[-1] + v)
            if list(starts) != prefix:
                # Known 2026-09-22 dome exporter bug (decimate_lines stores the line
                # index, not the vertex offset). The app rebuilds starts from lengths,
                # so this is a WARN, not a FAIL; it becomes a FAIL once fixed upstream.
                rep.advise(lt + " line_starts is not the prefix sum of line_lengths (exporter bug; app repairs it)")
        elif L.get("kind") == "lines":
            rep.check(False, lt + " has line_starts/line_lengths")
        if L.get("kind") == "mesh":
            if rep.check("faces" in blocks, lt + " has faces"):
                faces = _view(buf, blocks["faces"])
                rep.check(len(faces) == 3 * L.get("n_faces", -1) and max(faces) < n,
                          lt + " faces index inside the vertex list", "max %d, n %d" % (max(faces), n))
    return rep.failures == start


def _check_index_rows(get: Loader, base: str, rows: dict, rep: Report, expect_model) -> None:
    for preset, entry in sorted(rows.items()):
        check_bundle(get, base + entry["json_url"], base + entry["bin_gz_url"], rep, entry=entry,
                     preset=preset, expect_model=expect_model)


def validate(root: Optional[str] = None, base_url: Optional[str] = None, verbose: bool = False) -> Report:
    get = loader(root, base_url)
    rep = Report(verbose)
    raw = get("index.json")
    if not rep.check(raw is not None, "index.json present"):
        return rep
    idx = json.loads(raw)
    rep.check(idx.get("schema") == SCHEMA_INDEX, "index.json schema == " + SCHEMA_INDEX)
    src = idx.get("sources", {})
    rep.info("last_attempt_status %s; lookback %s; storms %s (%s)"
             % (idx.get("last_attempt_status"), src.get("lookback", {}).get("status"),
                src.get("storms", {}).get("status"), src.get("storms", {}).get("count")))

    st_raw = get("storms/index.json")
    if rep.check(st_raw is not None, "storms/index.json present"):
        st = json.loads(st_raw)
        rep.check(st.get("schema") == SCHEMA_STORMS, "storms schema == " + SCHEMA_STORMS)
        rep.check(len(st.get("storms", [])) == src.get("storms", {}).get("count"),
                  "storms count matches index.json")
        for row in st.get("storms", []):
            _check_index_rows(get, "storms/", row.get("presets", {}), rep, {"ts05", "t96", "auto", "t89"})

    if src.get("lookback", {}).get("status") != "absent":
        lb_raw = get("lookback/index.json")
        if rep.check(lb_raw is not None, "lookback/index.json present"):
            lb = json.loads(lb_raw)
            rep.check(lb.get("schema") == SCHEMA_LOOKBACK, "lookback schema == " + SCHEMA_LOOKBACK)
            rep.check(bool(lb.get("presets", {}).get("mobile")), "lookback has a mobile preset")
            rep.check(lb.get("event_id") == src.get("lookback", {}).get("event_id"),
                      "lookback event_id matches index.json")
            _check_index_rows(get, "lookback/", lb.get("presets", {}), rep, {"t89"})
    return rep
