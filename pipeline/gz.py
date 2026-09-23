"""Wire encoding for the geometry buffers: ``delta-k/1`` then deterministic gzip.

GitHub Pages will not set Content-Encoding on a .bin, so the file itself is
gzip and the browser inflates it with DecompressionStream('gzip').

Plain gzip only takes the bundles from 4.38 to 3.45 MB (Gannon mobile): uint16
positions look like noise byte by byte. The morph contract says vertex i is the
same physical thing in every keyframe, so consecutive keyframes are close, and
``delta-k/1`` exploits exactly that (measured: 3.45 -> 2.41 MB mobile,
15.8 -> 9.9 MB desktop):

  for every per-keyframe block of a layer (pos, alpha, lum -- shape[0] is the
  layer's keyframe count):
    1. d[0] = a[0]; d[k] = a[k] - a[k-1]   (modular in the block's own dtype)
    2. uint16 blocks only: write all low bytes of the block, then all high bytes

Every other byte (color0, faces, line tables, padding) is untouched, block
offsets and lengths are unchanged, so the engine's manifest describes the
DECODED buffer verbatim. The index's ``bin_sha256`` is of the decoded buffer.
Decoding is the inverse: re-interleave, then a running sum over keyframes.
mtime=0 keeps the gzip byte-stable, so an unchanged render republishes identical bytes.
"""

from __future__ import annotations

import gzip
import hashlib
import io

import numpy as np

ENCODING = "delta-k/1"
DELTA_BLOCKS = ("pos", "alpha", "lum")
_DT = {"uint8": np.uint8, "uint16": np.uint16}


def compress(data: bytes, level: int = 9) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=level, mtime=0) as fh:
        fh.write(data)
    return buf.getvalue()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _blocks(meta: dict):
    for L in meta.get("layers", []):
        for name in DELTA_BLOCKS:
            b = L.get("blocks", {}).get(name)
            if b and b.get("dtype") in _DT and b.get("shape") and b["shape"][0] == L.get("keyframes"):
                yield b


def encode(meta: dict, raw: bytes) -> bytes:
    out = bytearray(raw)
    for b in _blocks(meta):
        dt = _DT[b["dtype"]]
        a = np.frombuffer(raw, dt, count=b["length"] // dt().itemsize, offset=b["offset"])
        a = a.reshape(b["shape"][0], -1)
        d = a.copy()
        d[1:] = a[1:] - a[:-1]
        enc = d.tobytes()
        if dt is np.uint16:
            v = np.frombuffer(enc, np.uint8)
            enc = np.concatenate([v[0::2], v[1::2]]).tobytes()
        out[b["offset"]:b["offset"] + b["length"]] = enc
    return bytes(out)


def decode(meta: dict, enc: bytes) -> bytes:
    out = bytearray(enc)
    for b in _blocks(meta):
        dt = _DT[b["dtype"]]
        seg = np.frombuffer(enc, np.uint8, count=b["length"], offset=b["offset"])
        if dt is np.uint16:
            half = b["length"] // 2
            inter = np.empty(b["length"], np.uint8)
            inter[0::2], inter[1::2] = seg[:half], seg[half:]
            seg = inter
        d = np.frombuffer(seg.tobytes(), dt).reshape(b["shape"][0], -1)
        a = np.cumsum(d, axis=0, dtype=dt)
        out[b["offset"]:b["offset"] + b["length"]] = a.tobytes()
    return bytes(out)


def pack(meta: dict, raw: bytes) -> bytes:
    """raw engine buffer -> published .bin.gz bytes."""
    return compress(encode(meta, raw))


def unpack(meta: dict, packed: bytes) -> bytes:
    return decode(meta, gzip.decompress(packed))


def record(raw: bytes, packed: bytes) -> dict:
    """The size/sha/encoding block every index entry carries for one geometry buffer."""
    return {"encoding": ENCODING, "bin_bytes": len(raw), "bin_gz_bytes": len(packed),
            "bin_sha256": sha256(raw)}
