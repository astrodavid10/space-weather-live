r"""reshell_event.py - move an EXISTING render's boundary shells to the shape LIBRARY.

    python tools\reshell_event.py --root ..\Renders\storm_2024-05-10_gannon [--keep-step-files]

No re-trace: the per-step standoffs and flaring come from the event's own state timeline,
the library OBJs are written into Models\, the manifest's two shell rows are replaced by
library rows (+ a 'shells' block), the old per-step OBJs are deleted (unless
--keep-step-files), and every script is re-emitted through retime_event (same timing).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dsexport.publish import write_into                        # noqa: E402
from magneto.model import shell_library                        # noqa: E402
from magneto.render import select_event_states                 # noqa: E402
from tools import retime_event                                  # noqa: E402

MANIFEST_EVENT_NAME = retime_event.MANIFEST_NAME                # event_frames.json


def reshell(root, keep_step_files: bool = False) -> dict:
    # Fail before touching a published render, not halfway through it: the library
    # is only a valid representation while the magnetopause family is self-similar
    # (see shell_library.require_self_similar). export_shell_library raises the same
    # check, but by then this function has already read and rewritten a manifest.
    shell_library.require_self_similar("reshell_event")
    root = Path(root)
    models, scripts = root / "Models", root / "Scripts"
    mpath = models / MANIFEST_EVENT_NAME
    m = json.loads(mpath.read_text(encoding="ascii"))
    eid = str(m["event_id"])
    win = m.get("window") or {}
    states = select_event_states(eid, float(m["step_min"]),
                                 window=(win.get("start"), win.get("stop")))
    if len(states) != int(m["n_frames"]):
        raise SystemExit(f"{eid}: timeline gives {len(states)} states for {m['n_frames']} frames")
    shells = shell_library.export_shell_library(
        states, models, frame=m.get("frame", "j2000"),
        coord_digits=int(m.get("coord_digits", 9)))
    old_rows = [r for r in m["layers"] if r["name"] in shell_library.SHELL_NAMES]
    rows = [r for r in m["layers"] if r["name"] not in shell_library.SHELL_NAMES]
    fl = next((r for r in rows if r["name"] == "FieldLines"), None)
    times = (fl or {}).get("times") or [k * float(m["step_seconds"]) for k in range(len(states))]
    m["layers"] = shell_library.library_layer_rows(shells, times) + rows
    m["shells"] = shells
    m["shell_mode"] = "library"
    removed = 0
    if not keep_step_files:
        for r in old_rows:
            for f in r.get("files", []):
                if "_lib" in f or "_a" in Path(f).stem[-5:]:
                    continue
                try:
                    (models / f).unlink()
                    removed += 1
                except OSError:
                    pass
    write_into(mpath, json.dumps(m, indent=1, allow_nan=False))
    print(f"{eid}: shell library {len(shells['MagnetopauseShell']['files'])} magnetopause "
          f"shape(s) + 1 bow shock (alpha bins {shells['MagnetopauseShell']['alpha_bins']}), "
          f"{removed} per-step OBJ(s) removed")
    retime_event.retime(root, step_seconds=float(m["step_seconds"]))
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True)
    ap.add_argument("--keep-step-files", action="store_true")
    args = ap.parse_args(argv)
    reshell(args.root, keep_step_files=args.keep_step_files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
