"""Digistar VLA/VLC writers + validator shared by Build A (magneto) and Build B (rubin).

Implements the house subset of the format:
  * VLC DOTS   (8-col: D x y z opacity R G B)          - particles, alert points
  * VLC LINES  (8-col: P/L x y z opacity R G B)        - field lines, curtains
  * VLA LINES  (4-col FULL or 5-col EXPLICIT)          - boundary meshes, markers
  * PARAMETRIC morph mode: every D/P/L row immediately followed by a V row with
    the morph-target values. Topology (row count, order, leading chars) must
    match between current and target - enforced here, not hoped for.
  * Chunked DOTS output (~10 MB parts, full header per part).

Grammar per the digistar-vlc skill: only allow-listed header keys, all comments
before the first data row, ASCII only, no semicolons.

Writers accept numpy arrays or plain sequences. Rows:
  DOTS  : (x, y, z, opacity, r, g, b)
  LINES : list of polylines, each a sequence of vertex rows -
          VLC: (x, y, z, opacity, r, g, b); VLA FULL: (x, y, z);
          VLA EXPLICIT: (x, y, z, opacity)
"""
from __future__ import annotations

import struct as _struct
from pathlib import Path

AUTHOR = "A. David Weigel"
SITE = "INTUITIVE Planetarium"
CHUNK_TARGET_BYTES = 10 * 1_048_576  # ~10 MB per part, per house convention

ALLOWED_SET_KEYS = {
    "filecontent": {"LINES", "DOTS"},
    "filetype": {"NEW"},
    "defaultdraw": {"STELLAR"},
    "coordsys": {"RIGHT", "LEFT"},
    "intensity": {"EXPLICIT", "FULL"},
    "parametric": {"NON_PARAMETRIC", "PARAMETRIC"},
    "depthcue": {"0"},
    "author": None,
    "site": None,
    "comment": None,
}
REQUIRED_KEYS = {"filecontent", "filetype", "defaultdraw", "coordsys",
                 "intensity", "parametric", "depthcue", "author", "site"}


def _fmt(v: float) -> str:
    """Compact numeric: '1' not '1.0', '0.5' not '0.5000'.

    Fine for colour/opacity and for coordinates whose interesting detail is
    within ~6 decimals of their magnitude (metres, sky-sphere light
    years). NOT for coordinates far from the origin with sub-1e-6 relative
    detail - use fmt_coord there.
    """
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return s or "0"


def fmt_coord(v: float) -> str:
    """Full float64 round-trip precision for far-from-origin coordinates."""
    return repr(float(v))


# Digistar cooks positions to DXGI_FORMAT_R32G32B32_FLOAT (float32, ~7.2
# significant digits) and colour/opacity to 8 bits (1/255) - measured in the
# install's own cook requests, 2026-08-13 (see docs/HANDOFF_DOME.md section 3).
# 9 significant digits keeps ~1.8 digits of headroom over what the GPU stores
# while cutting roughly a third off every file (and off cook time, which is
# byte-bound). fmt_coord stays the default and remains MANDATORY for
# EarthFrameCheck.vla, whose whole job is metric truth.
COORD_SIG_DIGITS = 9


def fmt_coord_sig(v: float) -> str:
    """9-significant-digit coordinate: full float32 fidelity, ~2/3 the bytes.

    The value is rounded THROUGH float32 first, then printed at 9 significant
    digits - the pairing IEEE 754 guarantees round-trips binary32 exactly.
    (Printing the float64 at %.9g instead can land one float32 ULP away when
    the value sits on a rounding boundary.)
    """
    v32 = _struct.unpack("<f", _struct.pack("<f", float(v)))[0]
    return f"{v32:.{COORD_SIG_DIGITS}g}"


def fmt_val(v: float) -> str:
    """4-decimal colour/opacity - 8-bit storage (1/255) with headroom."""
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s or "0"


def coord_formatter(digits: int):
    """The coord formatter for a --coord-digits setting (9 or 17 only)."""
    if int(digits) == COORD_SIG_DIGITS:
        return fmt_coord_sig
    if int(digits) == 17:
        return fmt_coord
    raise ValueError(f"coord digits must be 9 or 17, got {digits}")


def val_formatter(digits: int):
    """The colour/opacity formatter paired with a --coord-digits setting."""
    return fmt_val if int(digits) == COORD_SIG_DIGITS else _fmt


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def header_lines(filecontent: str, intensity: str, parametric: bool,
                 comments=None) -> list[str]:
    if filecontent not in ("LINES", "DOTS"):
        raise ValueError(f"filecontent must be LINES or DOTS, got {filecontent!r}")
    if intensity not in ("FULL", "EXPLICIT"):
        raise ValueError(f"intensity must be FULL or EXPLICIT, got {intensity!r}")
    lines = [
        f"set filecontent {filecontent}",
        "set filetype NEW",
        "set defaultdraw STELLAR",
        "set coordsys RIGHT",
        f"set intensity {intensity}",
        f"set parametric {'PARAMETRIC' if parametric else 'NON_PARAMETRIC'}",
        "set depthcue 0",
        f"set author {AUTHOR}",
        f"set site {SITE}",
    ]
    for c in comments or []:
        lines.append("set comment " + " ".join(str(c).split()))
    return [ln + "\n" for ln in lines]


def _row8(cmd: str, row, fc=_fmt, vf=_fmt) -> str:
    x, y, z, op, r, g, b = row
    return (f"{cmd} {fc(x)} {fc(y)} {fc(z)} {vf(_clamp01(op))} "
            f"{vf(_clamp01(r))} {vf(_clamp01(g))} {vf(_clamp01(b))}\n")


def write_dots_vlc(out_path, points, next_points=None, comments=None,
                   coord_fmt=_fmt, val_fmt=_fmt) -> int:
    """VLC DOTS file; parametric morph when next_points is given.

    points / next_points: (N, 7) array-likes of (x, y, z, opacity, r, g, b).
    coord_fmt formats x/y/z only - pass fmt_coord for coordinates whose detail
    lives below ~1e-6 of their own magnitude (anything in light years), or
    fmt_coord_sig for the float32-faithful 9-significant-digit form.
    val_fmt formats opacity/r/g/b (fmt_val pairs with fmt_coord_sig).
    Returns the number of dots written.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parametric = next_points is not None
    if parametric and len(next_points) != len(points):
        raise ValueError(
            f"morph target has {len(next_points)} rows, source has {len(points)}")
    buf = header_lines("DOTS", "EXPLICIT", parametric, comments)
    for i, row in enumerate(points):
        buf.append(_row8("D", row, coord_fmt, val_fmt))
        if parametric:
            buf.append(_row8("V", next_points[i], coord_fmt, val_fmt))
    with open(out_path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("".join(buf))
    return len(points)


def write_lines_vlc(out_path, polylines, next_polylines=None, comments=None,
                    coord_fmt=_fmt, val_fmt=_fmt) -> int:
    """VLC LINES file (per-vertex colour); parametric when next_polylines given.

    polylines: list of polylines, each an (n_i, 7) array-like.
    Returns total vertex rows written (excluding V rows).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parametric = next_polylines is not None
    if parametric and len(next_polylines) != len(polylines):
        raise ValueError(
            f"morph target has {len(next_polylines)} polylines, "
            f"source has {len(polylines)}")
    buf = header_lines("LINES", "EXPLICIT", parametric, comments)
    n = 0
    for pi, poly in enumerate(polylines):
        target = next_polylines[pi] if parametric else None
        if parametric and len(target) != len(poly):
            raise ValueError(
                f"polyline {pi}: morph target has {len(target)} vertices, "
                f"source has {len(poly)}")
        for i, row in enumerate(poly):
            buf.append(_row8("P" if i == 0 else "L", row, coord_fmt, val_fmt))
            if parametric:
                buf.append(_row8("V", target[i], coord_fmt, val_fmt))
            n += 1
    with open(out_path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("".join(buf))
    return n


def write_lines_vla(out_path, polylines, intensity="FULL",
                    next_polylines=None, comments=None, coord_fmt=_fmt,
                    val_fmt=_fmt) -> int:
    """VLA LINES file (single global colour).

    intensity 'FULL': vertex rows (x, y, z). 'EXPLICIT': (x, y, z, opacity).
    Parametric morph when next_polylines is given (same shapes required).
    Returns total vertex rows written (excluding V rows).
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    parametric = next_polylines is not None
    if parametric and len(next_polylines) != len(polylines):
        raise ValueError(
            f"morph target has {len(next_polylines)} polylines, "
            f"source has {len(polylines)}")

    def row(cmd, r):
        if intensity == "FULL":
            x, y, z = r
            return f"{cmd} {coord_fmt(x)} {coord_fmt(y)} {coord_fmt(z)}\n"
        x, y, z, op = r
        return (f"{cmd} {coord_fmt(x)} {coord_fmt(y)} {coord_fmt(z)} "
                f"{val_fmt(_clamp01(op))}\n")

    buf = header_lines("LINES", intensity, parametric, comments)
    n = 0
    for pi, poly in enumerate(polylines):
        target = next_polylines[pi] if parametric else None
        if parametric and len(target) != len(poly):
            raise ValueError(
                f"polyline {pi}: morph target has {len(target)} vertices, "
                f"source has {len(poly)}")
        for i, r in enumerate(poly):
            buf.append(row("P" if i == 0 else "L", r))
            if parametric:
                buf.append(row("V", target[i]))
            n += 1
    with open(out_path, "w", encoding="ascii", newline="\n") as fh:
        fh.write("".join(buf))
    return n


def write_dots_vlc_chunked(stem, points, comments=None,
                           target_bytes: int = CHUNK_TARGET_BYTES,
                           coord_fmt=_fmt) -> list[Path]:
    """Chunk a large DOTS set across ~target_bytes .vlc parts.

    stem: Path without extension; parts land as <stem>_part01.vlc, _part02...
    Every part carries the full header (loader never inherits one from part 1).
    NON_PARAMETRIC only - a morphing set that needs chunking should be rethought.
    Returns the list of part paths.
    """
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    header = "".join(header_lines("DOTS", "EXPLICIT", False, comments))
    parts: list[Path] = []
    fh = None

    def open_part():
        nonlocal fh
        path = stem.with_name(f"{stem.name}_part{len(parts) + 1:02d}.vlc")
        fh = open(path, "w", encoding="ascii", newline="\n")
        fh.write(header)
        parts.append(path)

    open_part()
    try:
        for row in points:
            if fh.tell() >= target_bytes:
                fh.close()
                open_part()
            fh.write(_row8("D", row, coord_fmt))
    finally:
        fh.close()
    return parts


def validate(path, quiet=False):
    """Sanity-check a generated VLA/VLC file, incl. PARAMETRIC V-row pairing.

    Returns (ok: bool, errors: list[str]). Adapted from the digistar-vlc skill
    validation snippet.
    """
    path = Path(path)
    is_vlc = path.suffix.lower() == ".vlc"
    errors: list[str] = []
    first_data = False
    expecting_v = False
    seen_keys: set[str] = set()
    intensity = filecontent = parametric = None

    def expected_cols():
        if intensity == "FULL":
            return None if is_vlc else 4
        if intensity == "EXPLICIT":
            return 8 if is_vlc else 5
        return None

    with open(path, encoding="ascii") as fh:
        for lineno, line in enumerate(fh, 1):
            s = line.strip()
            if not s:
                continue
            if s.startswith(";"):
                errors.append(f"L{lineno}: semicolon comment not allowed")
                continue
            if s.startswith("set "):
                toks = s.split(None, 2)
                key = toks[1] if len(toks) > 1 else ""
                val = toks[2] if len(toks) > 2 else ""
                if first_data:
                    errors.append(f"L{lineno}: 'set {key}' after data began")
                if key not in ALLOWED_SET_KEYS:
                    errors.append(f"L{lineno}: disallowed key 'set {key}'")
                    continue
                allowed = ALLOWED_SET_KEYS[key]
                if allowed is not None and val not in allowed:
                    errors.append(
                        f"L{lineno}: 'set {key} {val}' not in {sorted(allowed)}")
                if key == "intensity":
                    intensity = val
                    if is_vlc and val != "EXPLICIT":
                        errors.append(f"L{lineno}: .vlc requires intensity EXPLICIT")
                elif key == "filecontent":
                    filecontent = val
                elif key == "parametric":
                    parametric = val
                seen_keys.add(key)
                continue
            lead = s[0]
            if lead not in ("P", "L", "D", "V"):
                errors.append(f"L{lineno}: unexpected leading char '{lead}'")
                continue
            if lead == "V":
                if parametric != "PARAMETRIC":
                    errors.append(f"L{lineno}: V row but parametric not PARAMETRIC")
                if not expecting_v:
                    errors.append(f"L{lineno}: V row not paired with a D/P/L row")
                expecting_v = False
            else:
                if filecontent == "DOTS" and lead != "D":
                    errors.append(f"L{lineno}: DOTS row must start with D")
                if filecontent == "LINES" and lead == "D":
                    errors.append(f"L{lineno}: LINES file has a D row")
                if parametric == "PARAMETRIC" and expecting_v:
                    errors.append(f"L{lineno - 1}: D/P/L row missing its V sibling")
                expecting_v = (parametric == "PARAMETRIC")
                if not first_data and filecontent == "LINES" and lead != "P":
                    errors.append(f"L{lineno}: first LINES row must be P")
            exp = expected_cols()
            parts_ = s.split()
            if exp is None:
                errors.append(f"L{lineno}: data row before intensity set / bad combo")
            elif len(parts_) != exp:
                errors.append(f"L{lineno}: expected {exp} cols, got {len(parts_)}")
            else:
                try:
                    if intensity == "EXPLICIT":
                        op = float(parts_[4])
                        if not 0.0 <= op <= 1.0:
                            errors.append(f"L{lineno}: opacity {op} out of [0,1]")
                    if is_vlc:
                        for nm, idx in (("R", 5), ("G", 6), ("B", 7)):
                            v = float(parts_[idx])
                            if not 0.0 <= v <= 1.0:
                                errors.append(f"L{lineno}: {nm} {v} out of [0,1]")
                except ValueError:
                    errors.append(f"L{lineno}: non-numeric field in data row")
            first_data = True

    if parametric == "PARAMETRIC" and expecting_v:
        errors.append("final D/P/L row missing its V sibling")
    missing = REQUIRED_KEYS - seen_keys
    if missing:
        errors.append(f"missing required keys: {sorted(missing)}")
    if errors and not quiet:
        for e in errors[:25]:
            print("  WARN:", e)
        if len(errors) > 25:
            print(f"  ... +{len(errors) - 25} more")
    return (not errors), errors
