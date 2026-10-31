"""Quantise and write the web bundle: one .bin of typed arrays + one .json that describes it.

WHY NOT glTF for the animated layers: glTF's animation samplers want one accessor per
animated node, which for ~8,000 vertices x 60 keyframes is a pathological fit. A flat binary
you index yourself is smaller, simpler, and loads in a single fetch. The two shells are the
one static-topology triangulated thing in the set, so `--gltf` emits those separately for
tools that want them.

QUANTISATION. Positions go to uint16 against a per-layer bounding box:

    q = round((p - lo) / (hi - lo) * 65535)          # write
    p = lo + q / 65535 * (hi - lo)                   # read

Over a +-220 R_E box that is ~7 metres of error at Earth, which is four orders of magnitude
below anything visible. Colour is shipped ONCE per vertex as uint8 RGB; per keyframe each
vertex carries uint8 alpha, and line layers additionally carry uint8 luminance so the
field-line |B| modulation survives (hue = topology, brightness = log|B|).

ALPHA IS NOT OPTIONAL. Field lines whose identity breaks between epochs - reconnection, a
failed trace, or a vertex jump over 15 R_E - are CROSSFADED rather than morphed: the row sits
at opacity 0 on both poses while it moves. A renderer that interpolates position but ignores
alpha will sweep those lines across the screen.
"""
from __future__ import annotations

import json
import pathlib

import numpy as np

SCHEMA = 1


class BinWriter:
    """Append typed arrays to one buffer, handing back a JSON-serialisable descriptor."""

    def __init__(self):
        self._parts: list[bytes] = []
        self._offset = 0

    def add(self, arr: np.ndarray, dtype=None) -> dict:
        a = np.ascontiguousarray(arr if dtype is None else arr.astype(dtype))
        if a.dtype.byteorder not in ("=", "|"):
            a = a.astype(a.dtype.newbyteorder("="))
        b = a.tobytes()
        d = {"offset": self._offset, "length": len(b),
             "dtype": a.dtype.name, "shape": list(a.shape)}
        self._parts.append(b)
        self._offset += len(b)
        # 4-byte align so a JS TypedArray can be created as a view rather than a copy
        pad = (-len(b)) % 4
        if pad:
            self._parts.append(b"\0" * pad)
            self._offset += pad
        return d

    def tobytes(self) -> bytes:
        return b"".join(self._parts)

    @property
    def size(self) -> int:
        return self._offset


def quantise_positions(pos_frames: np.ndarray):
    """(K, N, 3) float -> (uint16 array, lo, hi). One box for the whole layer and all frames,
    so a vertex's quantisation does not shift between keyframes (which would read as jitter)."""
    p = np.asarray(pos_frames, dtype=np.float64)
    lo = p.reshape(-1, 3).min(axis=0)
    hi = p.reshape(-1, 3).max(axis=0)
    span = np.where(hi - lo > 0, hi - lo, 1.0)
    q = np.clip(np.round((p - lo) / span * 65535.0), 0, 65535).astype(np.uint16)
    return q, lo, hi


def quantise_unit(a: np.ndarray) -> np.ndarray:
    return np.clip(np.round(np.asarray(a, dtype=np.float64) * 255.0), 0, 255).astype(np.uint8)


def write_bundle(out_dir, event_id: str, layers: list[dict], meta: dict) -> dict:
    """Write <event>.bin + <event>.json.

    `layers` entries carry, per layer: name, kind, pos (K,N,3 float), alpha (K,N),
    color0 (N,3 float 0..1), and optionally lum (K,N), starts/lengths (lines), faces (mesh).
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bw = BinWriter()
    out_layers = []

    for L in layers:
        pos = np.asarray(L["pos"], dtype=np.float64)
        if pos.size == 0 or pos.shape[1] == 0:
            # Never write an empty layer as if it were real - a zero-count layer in the
            # manifest would load as a silently missing science layer on the page.
            print(f"  WARNING: {L['name']} has no vertices after decimation - layer omitted")
            continue
        q, lo, hi = quantise_positions(pos)
        entry = {
            "name": L["name"],
            "kind": L["kind"],
            "count": int(pos.shape[1]),
            "keyframes": int(pos.shape[0]),
            # dequantise: p = lo + q / 65535 * (hi - lo), in EARTH RADII
            "pos_lo": [float(v) for v in lo],
            "pos_hi": [float(v) for v in hi],
            "blocks": {
                "pos": bw.add(q),
                "alpha": bw.add(quantise_unit(L["alpha"])),
                "color0": bw.add(quantise_unit(L["color0"])),
            },
        }
        if L.get("lum") is not None:
            entry["blocks"]["lum"] = bw.add(quantise_unit(L["lum"]))
        if L.get("starts") is not None:
            entry["blocks"]["line_starts"] = bw.add(np.asarray(L["starts"], dtype=np.uint32))
            entry["blocks"]["line_lengths"] = bw.add(np.asarray(L["lengths"], dtype=np.uint32))
            entry["n_lines"] = int(np.asarray(L["starts"]).size)
        if L.get("faces") is not None:
            f = np.asarray(L["faces"], dtype=np.int64)
            dt = np.uint16 if f.max(initial=0) < 65536 else np.uint32
            entry["blocks"]["faces"] = bw.add(f, dtype=dt)
            entry["n_faces"] = int(f.shape[0])
        if L.get("lattice"):
            # the SOURCE grid this mesh was strided from (u x v); the shipped vertex
            # count is `count`, so this is provenance for a reader, not an index
            entry["source_lattice"] = [int(x) for x in L["lattice"]]
        # per-keyframe index into the global keyframe list - the aurora only changes on Kp
        # change, so it has its own sparse schedule and must not be assumed aligned
        entry["keyframe_map"] = [int(i) for i in L["keyframe_map"]]
        if L.get("occluded_dropped"):
            entry["occluded_dropped"] = int(L["occluded_dropped"])
        out_layers.append(entry)

    bin_name = f"{event_id}.bin"
    (out_dir / bin_name).write_bytes(bw.tobytes())

    manifest = dict(meta)
    manifest.update({"schema": SCHEMA, "binary": bin_name,
                     "binary_bytes": bw.size, "layers": out_layers})
    (out_dir / f"{event_id}.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8")
    return manifest
