"""Dome probe helper for the shell shape-library design (2026-09-14).

Writes a bow-shock OBJ in GSM AXES (nose along the model's +X) for the Bastille storm's
first state, and prints the J2000 direction (RA hours, Dec deg) of the GSM +X, +Y, +Z axes
at that epoch - what an 'attitude cel RA Dec 0' probe on the dome has to be compared with.

    python tools\dev_shell_probe.py [out_dir]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from magneto import geo                                             # noqa: E402
from magneto.model import recalc_for_state                          # noqa: E402
from magneto.model.boundaries import export_boundaries              # noqa: E402
from magneto.render import select_event_states                      # noqa: E402


def ra_dec(v):
    x, y, z = (float(c) for c in v)
    r = math.sqrt(x * x + y * y + z * z)
    ra_h = (math.degrees(math.atan2(y, x)) % 360.0) / 15.0
    dec = math.degrees(math.asin(z / r))
    return ra_h, dec


def main(argv=None) -> int:
    argv = list(argv or sys.argv[1:])
    out = Path(argv[0]) if argv else Path(__file__).resolve().parents[2] / "Renders" / "_smoke" / "probe"
    out.mkdir(parents=True, exist_ok=True)
    states = select_event_states("2000-07-15_bastille", 10.0,
                                 window=("2000-07-15T00:00:00Z", "2000-07-15T00:20:00Z"))
    st = states[0]
    res = export_boundaries(st, None, out, frame="gsm")
    recalc_for_state(st)
    axes = geo.gsm_re_to_frame(np.eye(3), "j2000")
    print("epoch", st["epoch"], "nose MP", round(res["nose_re"]["Magnetopause"], 3),
          "BS", round(res["nose_re"]["BowShock"], 3))
    for name, v in zip(("GSM +X (to Sun)", "GSM +Y", "GSM +Z"), axes):
        ra_h, dec = ra_dec(v)
        print(f"{name}: RA {ra_h:.4f} h  Dec {dec:.3f} deg   (J2000 unit vector {np.round(v / np.linalg.norm(v), 5)})")
    print("wrote", res["BowShockShell"]["path"], res["MagnetopauseShell"]["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
