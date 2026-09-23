"""delta-k/1 round trip, the bundle validator, and index status, on a tiny synthetic bundle."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from pipeline import gz
from pipeline.index import build_index
from pipeline.validate import Report, check_bundle, loader


def _bundle(K=3, N=4):
    """A 2-layer engine-grammar bundle: one line layer, one mesh layer."""
    parts, off = [], 0
    rng = np.random.default_rng(1)

    def add(a):
        nonlocal off
        b = a.tobytes()
        d = {"offset": off, "length": len(b), "dtype": a.dtype.name, "shape": list(a.shape)}
        pad = (-len(b)) % 4
        parts.append(b + b"\0" * pad)
        off += len(b) + pad
        return d

    lines = {"name": "FieldLines", "kind": "lines", "count": N, "keyframes": K,
             "pos_lo": [-1, -1, -1], "pos_hi": [1, 1, 1], "n_lines": 2, "keyframe_map": list(range(K)),
             "blocks": {"pos": add(rng.integers(0, 65535, (K, N, 3), dtype=np.uint16)),
                        "alpha": add(rng.integers(0, 255, (K, N), dtype=np.uint8)),
                        "color0": add(np.full((N, 3), 200, np.uint8)),
                        "lum": add(rng.integers(0, 255, (K, N), dtype=np.uint8)),
                        "line_starts": add(np.array([0, 2], np.uint32)),
                        "line_lengths": add(np.array([2, 2], np.uint32))}}
    mesh = {"name": "BowShockShell", "kind": "mesh", "count": N, "keyframes": K,
            "pos_lo": [0, 0, 0], "pos_hi": [5, 5, 5], "n_faces": 2, "keyframe_map": list(range(K)),
            "blocks": {"pos": add(rng.integers(0, 65535, (K, N, 3), dtype=np.uint16)),
                       "alpha": add(np.full((K, N), 255, np.uint8)),
                       "color0": add(np.full((N, 3), 50, np.uint8)),
                       "faces": add(np.array([[0, 1, 2], [0, 2, 3]], np.uint16))}}
    raw = b"".join(parts)
    meta = {"schema": 1, "event_id": "test", "binary": "test.bin", "binary_bytes": len(raw),
            "source": {"model": "t89"}, "provenance": {"physics_tag": "x"},
            "keyframes": {"count": K, "playback_s": [0.0, 1.0, 2.0],
                          "epoch": ["2026-09-01T00:00:00Z", "2026-09-01T00:10:00Z", "2026-09-01T00:20:00Z"],
                          "hud": ["a", "b", "c"]},
            "layers": [lines, mesh]}
    return meta, raw


def _write(tmp: Path, meta: dict, raw: bytes) -> dict:
    packed = gz.pack(meta, raw)
    (tmp / "test.json").write_text(json.dumps(meta))
    (tmp / "test.bin.gz").write_bytes(packed)
    e = {"json_url": "test.json", "bin_gz_url": "test.bin.gz", "keyframes": 3}
    e.update(gz.record(raw, packed))
    return e


def test_delta_round_trip():
    meta, raw = _bundle()
    assert gz.unpack(meta, gz.pack(meta, raw)) == raw
    assert gz.encode(meta, raw) != raw  # it actually transformed something


def test_validator_passes_good_bundle(tmp_path):
    meta, raw = _bundle()
    e = _write(tmp_path, meta, raw)
    rep = Report()
    assert check_bundle(loader(str(tmp_path)), "test.json", "test.bin.gz", rep, entry=e,
                        required_layers=("FieldLines", "BowShockShell")), rep.text()


@pytest.mark.parametrize("breaker", [
    lambda m: m["layers"][0]["blocks"]["pos"].__setitem__("length", 10 ** 9),
    lambda m: m["keyframes"].__setitem__("playback_s", [0.0, 2.0, 1.0]),
    lambda m: m["keyframes"].__setitem__("hud", ["a"]),
    lambda m: m["layers"][1].__setitem__("count", 2),
    lambda m: m["layers"].pop(0),
])
def test_validator_catches(tmp_path, breaker):
    meta, raw = _bundle()
    e = _write(tmp_path, meta, raw)
    breaker(meta)
    (tmp_path / "test.json").write_text(json.dumps(meta))
    rep = Report()
    assert not check_bundle(loader(str(tmp_path)), "test.json", "test.bin.gz", rep, entry=e,
                            required_layers=("FieldLines", "BowShockShell"))


def test_corrupt_gzip_fails(tmp_path):
    meta, raw = _bundle()
    e = _write(tmp_path, meta, raw)
    (tmp_path / "test.bin.gz").write_bytes(b"not gzip")
    assert not check_bundle(loader(str(tmp_path)), "test.json", "test.bin.gz", Report(), entry=e,
                            required_layers=())


def test_index_absent_then_stale(tmp_path):
    idx = build_index(tmp_path)
    assert idx["sources"]["lookback"]["status"] == "absent"
    assert idx["sources"]["storms"]["status"] == "absent"
    (tmp_path / "lookback").mkdir()
    (tmp_path / "lookback" / "index.json").write_text(json.dumps(
        {"event_id": "lb", "generated_iso": "2020-01-01T00:00:00Z", "data_end_iso": "2020-01-01T00:00:00Z"}))
    idx = build_index(tmp_path, "fetch_failed", failure="iswa down")
    lb = idx["sources"]["lookback"]
    assert lb["status"] == "stale" and lb["last_error"] == "iswa down"
    assert idx["last_attempt_status"] == "fetch_failed"
