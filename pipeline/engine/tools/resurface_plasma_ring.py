r"""resurface_plasma_ring.py - re-export the plasmasphere and ring current of an
existing render as effectModelClass SURFACES, without re-tracing anything.

WHY THIS IS CHEAP
    Neither layer depends on a field-line trace. The plasmasphere is a dipole
    L-shell whose radius comes from Kp_max over the previous 24 h (Carpenter &
    Anderson), and the ring current is a torus whose strength comes from SYM-H.
    Both are pure functions of the state series, which select_event_states()
    rebuilds exactly - so this runs in seconds per package where a TS05 storm
    re-render costs hours.

    It calls the SAME exporters the renderer calls, so there is one code path and
    a package produced here is byte-identical to one produced by a full render.

AFTERWARDS
    tools/retime_event.py --root <root> --step-seconds <its value>     (re-emit)
    tools/write_shims.py --all                                          (shims)

    The manifest rows are rewritten here, so the re-emit picks the surfaces up.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from magneto.model import recalc_for_state                    # noqa: E402
from magneto.model.plasmasphere import export_plasmasphere    # noqa: E402
from magneto.model.ringcurrent import export_ring_current     # noqa: E402

LAYERS = ("Plasmasphere", "RingCurrent")


def _states_for(root: Path, man: dict):
    """The exact state series the render used."""
    from magneto.events import load_event
    from magneto.render import select_event_states
    ev_id = man["event_id"]
    states = select_event_states(ev_id, man["step_min"], man.get("window"))
    if [s["epoch"] for s in states] != man["epochs"]:
        raise SystemExit(f"{root.name}: rebuilt state series does not match the manifest "
                         f"({len(states)} vs {len(man['epochs'])} epochs) - refusing")
    tl = load_event(ev_id)
    return states, (tl["states"] if isinstance(tl, dict) else tl)


def resurface(root: Path, dry_run=False) -> dict:
    models = root / "Models"
    man_path = models / "event_frames.json"
    man = json.loads(man_path.read_text(encoding="ascii", errors="replace"))
    pref = man.get("model_basename_prefix", "")
    conv = bool(man.get("model_convention_dirs"))
    frame = man["frame"]
    digits = int(man.get("coord_digits", 9))

    rows = {L["name"]: L for L in man["layers"]}
    todo = [n for n in LAYERS if n in rows]
    if not todo:
        return {"layers": 0, "files": 0}
    if all(rows[n].get("kind") == "mesh" for n in todo):
        return {"layers": 0, "files": 0, "already": True}

    states, timeline = _states_for(root, man)
    if dry_run:
        return {"layers": len(todo), "files": len(states) * len(todo), "dry": True}

    written = {n: [] for n in todo}
    for k, st in enumerate(states):
        prev = states[k - 1] if k else None
        recalc_for_state(st)
        for name in todo:
            base = f"{pref}{name}_{k:04d}"
            out = (models / base / base) if conv else (models / base)
            out.parent.mkdir(parents=True, exist_ok=True)
            if name == "Plasmasphere":
                res = export_plasmasphere(st, prev, timeline, out, frame=frame,
                                          coord_digits=digits)
            else:
                res = export_ring_current(st, prev, out, frame=frame, coord_digits=digits)
            if res is None:                       # no driver at this epoch - layer absent
                written[name].append(None)
                continue
            written[name].append(Path(res["path"]).name)

    n_files = 0
    for name in todo:
        files = [f for f in written[name] if f]
        if len(files) != len(states):
            raise SystemExit(f"{root.name}/{name}: {len(files)} files for {len(states)} "
                             "steps - the layer has gaps, refusing to write the manifest")
        row = rows[name]
        row["kind"] = "mesh"
        row["files"] = files
        row["parametric"] = False       # a mesh cuts; it has no D/V pair
        n_files += len(files)

    # Drop the superseded .vlc so the tree cannot carry two versions of a layer.
    for name in todo:
        for old in models.rglob(f"{pref}{name}_*.vlc"):
            old.unlink()
        for d in models.glob(f"{pref}{name}_*"):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()

    man["plasma_surfaces"] = True
    man_path.write_text(json.dumps(man, indent=1), encoding="ascii")
    return {"layers": len(todo), "files": n_files}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if a.root:
        roots = [Path(r) for r in a.root]
    else:
        base = Path(__file__).resolve().parents[2] / "Renders"
        roots = [d for d in sorted(base.glob("*"))
                 if (d / "Models" / "event_frames.json").is_file()]

    total = 0
    for r in roots:
        try:
            n = resurface(r, a.dry_run)
        except SystemExit as e:
            print(f"  {r.name:32s} {e}")
            return 1
        if n.get("already"):
            print(f"  {r.name:32s} already on surfaces")
        elif n["layers"]:
            print(f"  {r.name:32s} {n['layers']} layer(s), {n['files']} file(s)"
                  f"{'  [dry run]' if a.dry_run else ''}")
            total += n["files"]
        else:
            print(f"  {r.name:32s} no plasma layers")
    print(f"\n{'would write' if a.dry_run else 'wrote'} {total} surface file(s)")
    if total and not a.dry_run:
        print("Next: retime_event.py --root <each> --step-seconds <its value>, "
              "then write_shims.py --all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
