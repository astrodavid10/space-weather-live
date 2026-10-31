r"""check_alignment.py - do the drawn field lines stay inside the drawn shells?

RUN THIS AFTER EVERY RENDER, AND AFTER EVERY CHANGE TO THE CLIP, THE BOUNDARY OR THE
TRACER. It is the gate for the single fault that has come back three times on this
project, each time reported by David as "the shells are not aligned with the magnetic
field lines", and each time missed by checks that were individually correct:

  2026-09-17  the shells were drawn rotated, because Digistar imports .obj through a
              Y-up pipeline. Every file-level check passed; nothing compared the two
              FORMATS after import.
  2026-09-20  the clip tested against the ANALYTIC magnetopause, which flares to 21x
              the drawn tail. Every orientation check passed; the boundary being tested
              was not the boundary on screen.
  2026-09-21  the clip tested against the drawn surface AVERAGED OVER AZIMUTH. The
              look-back and Gannon looked fine; Jan 2026 had open lines 4.45x outside
              the shell, because at the flank the drawn radius varies more than tenfold
              with azimuth.

The through-line: each fix was verified on the packages where the fault was mildest.
So this tool scans EVERY step of a render, reports the tail of the distribution rather
than a sample, and exits non-zero on the thresholds below.

    python tools/check_alignment.py                 # every render under Renders/
    python tools/check_alignment.py --root <render> # one
    python tools/check_alignment.py --every 1       # every step, not every 5th

WHAT IT MEASURES. For each field-line vertex: its direction, the DRAWN shell's radius in
that same direction (nearest lattice vertex, exact to the mesh's own facets), and the
ratio. The magnetopause is star-shaped about Earth, so a radius comparison IS an
inside/outside test. Both files are exported in the same frame and units, so no
transform is involved and none can be got wrong.

WHAT IS AND IS NOT A FAULT. OPEN lines are clipped and must stay inside: they are the
assertion. CLOSED lines are deliberately NOT clipped - both feet on the Earth is what
closed MEANS, and cutting one would draw a gold line ending in mid-air - so a closed
line outside the boundary is a real disagreement between the field model and the
boundary model, which the show counts rather than hides. They are reported separately
and do not fail the check.

WHAT THIS TOOL CANNOT SEE. It reads the exported files. It cannot see a per-format
import transform, which is what went wrong in September - for that, draw one layer's
vertices in the other's format on the dome and compare (tools in Renders\_clips\).
A green result here plus an unverified importer is exactly the combination that let the
2026-09-17 fault run for three weeks.

WHERE IT RUNS, AND THE LIMIT OF THAT. `magneto.render ... --validate` calls this at the
end of every render, so it cannot be forgotten. But --validate runs AFTER the publish,
so a failing render is REPORTED, not blocked: the bad tree is already installed and the
previous one is in ModelsPrevious. Read the "alignment:" line before capturing or
shipping anything - a FAIL there means the package on disk draws field lines outside its
shells, whatever the banner above it says.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ENGINE = Path(__file__).resolve().parents[1]
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from magneto import paths                                    # noqa: E402
from webexport.read_obj import read_mesh                     # noqa: E402
from webexport.read_vlc import read_vlc                      # noqa: E402

#: 2% slack for the shell mesh's own facets - a vertex ON the surface can read slightly
#: outside because the nearest lattice vertex is up to half a facet away.
TOL = 1.02
FAR_RE = 40.0

#: Thresholds. OPEN lines are the ones the clip owns, so they carry the hard limits.
#: Set from the v6 measurements: the worst storm epoch gives 1.9% open outside at
#: worst ratio 1.28, and the quiet look-back 0.3% at 1.08. A worst ratio of 2 means a
#: line is drawn at twice the radius of the shell that is supposed to contain it, which
#: is visible from any camera angle.
MAX_OPEN_OUT_PCT = 3.0
MAX_WORST_RATIO = 2.0


def _prefix(models: Path) -> str:
    try:
        m = json.loads((models / "event_frames.json").read_text(encoding="utf-8"))
        return str(m.get("model_basename_prefix") or "")
    except (OSError, ValueError):
        return ""


def _find(models: Path, stem: str, pre: str):
    for s in ((pre + stem, stem) if pre else (stem,)):
        for ext in (".glb", ".obj", ".vlc"):
            for p in (models / f"{s}{ext}", models / s / f"{s}{ext}"):
                if p.is_file():
                    return p
    return None


def _families(colour: np.ndarray) -> np.ndarray:
    """0 closed, 1 open north, 2 open south, from the vertex hue.

    Brightness encodes log |B| and scales all three channels, so the normalised colour
    direction survives it and the hue alone identifies the topology.
    """
    cn = colour / np.maximum(np.linalg.norm(colour, axis=1, keepdims=True), 1e-9)
    hues = np.array([(1.0, 0.85, 0.2), (0.3, 0.55, 1.0), (1.0, 0.4, 0.1)])
    hues = hues / np.linalg.norm(hues, axis=1, keepdims=True)
    return np.argmax(cn @ hues.T, axis=1)


def _shell_radius(shell: np.ndarray, vdir: np.ndarray) -> np.ndarray:
    r"""Radius of the drawn shell in each query direction, INTERPOLATED.

    Nearest-vertex lookup was the first version. It is not biased - it picks a
    neighbouring vertex whose radius may be above or below the true one - but it is
    NOISY, and this gate reports the MAXIMUM ratio over ~12,000 vertices. A maximum
    samples the tail of that noise, so scatter alone inflates the worst ratio and tips
    borderline vertices past the 1.02 threshold. On Gannon that turned a clean render
    into "4.04% outside, worst 1.38" when the truth was 1.57% and 1.27 - a measurement
    artefact that looks exactly like the fault this tool exists to find, which is the
    worst possible way for a gate to be wrong.

    Linear interpolation over the shell's own vertices in (theta, phi) instead. The
    seam at phi = 0 is handled by repeating the points either side of it, so a query
    near due-dawn is not extrapolated off the end of the table.
    """
    from scipy.interpolate import LinearNDInterpolator

    sr = np.linalg.norm(shell, axis=1)
    sd = shell / np.maximum(sr, 1e-12)[:, None]
    st = np.arccos(np.clip(sd[:, 0], -1.0, 1.0))
    sp = np.arctan2(sd[:, 2], sd[:, 1])
    pts = np.column_stack([np.concatenate([st, st, st]),
                           np.concatenate([sp - 2 * np.pi, sp, sp + 2 * np.pi])])
    vals = np.concatenate([sr, sr, sr])
    interp = LinearNDInterpolator(pts, vals)

    vt = np.arccos(np.clip(vdir[:, 0], -1.0, 1.0))
    vp = np.arctan2(vdir[:, 2], vdir[:, 1])
    out = interp(np.column_stack([vt, vp]))
    # outside the triangulation (the poles of the theta-phi chart) fall back to the
    # nearest vertex rather than dropping the point - a missing measurement is worse
    # than an approximate one here
    bad = ~np.isfinite(out)
    if bad.any():
        out[bad] = sr[np.argmax(vdir[bad] @ sd.T, axis=1)]
    return out


def step_stats(models: Path, step: int, pre: str):
    mp = _find(models, f"MagnetopauseShell_{step:04d}", pre)
    fl = _find(models, f"FieldLines_{step:04d}", pre)
    if mp is None or fl is None:
        return None
    shell = read_mesh(mp).verts
    sr = np.linalg.norm(shell, axis=1)

    f = read_vlc(fl)
    pos = np.asarray(f.pos, dtype=float)
    keep = (np.asarray(f.opacity, dtype=float) > 0) & (np.linalg.norm(pos, axis=1) > 1.05)
    pos = pos[keep]
    if not len(pos):
        return None
    col = np.asarray(f.color, dtype=float)[keep]
    r = np.linalg.norm(pos, axis=1)
    vdir = pos / r[:, None]

    rmp = _shell_radius(shell, vdir)
    ratio = r / np.maximum(rmp, 1e-9)
    out = ratio > TOL
    fam = _families(col)
    op = fam != 0
    return {
        "step": step, "nose": float(sr.min()), "n": int(r.size),
        "open_out_pct": 100.0 * float((out & op).sum()) / max(int(op.sum()), 1),
        "open_worst": float(ratio[op].max()) if op.any() else 0.0,
        "closed_out_pct": 100.0 * float((out & ~op).sum()) / max(int((~op).sum()), 1),
        "far_pct": 100.0 * float((out & (r > FAR_RE)).sum()) / r.size,
    }


def check_render(root: Path, every: int = 5, quiet: bool = False) -> dict:
    models = root / "Models"
    if not models.is_dir():
        return {"root": root, "skipped": "no Models"}
    pre = _prefix(models)
    rows = [s for s in (step_stats(models, k, pre) for k in range(0, 400, every))
            if s is not None]
    if not rows:
        return {"root": root, "skipped": "no field-line frames"}

    worst = max(rows, key=lambda d: d["open_worst"])
    worst_pct = max(rows, key=lambda d: d["open_out_pct"])
    bad = (worst["open_worst"] > MAX_WORST_RATIO
           or worst_pct["open_out_pct"] > MAX_OPEN_OUT_PCT)
    res = {"root": root, "n_steps": len(rows), "worst": worst, "worst_pct": worst_pct,
           "bad": bad,
           "median_worst": float(np.median([d["open_worst"] for d in rows]))}
    if not quiet:
        flag = "FAIL" if bad else "ok  "
        print(f"  {flag} {root.name:30s} {len(rows):3d} steps   "
              f"open outside max {worst_pct['open_out_pct']:5.2f}% "
              f"(step {worst_pct['step']})   worst ratio {worst['open_worst']:5.2f} "
              f"(step {worst['step']}, nose {worst['nose']:.1f})   "
              f"median worst {res['median_worst']:.2f}")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--every", type=int, default=5,
                    help="sample every Nth step (1 = all of them)")
    a = ap.parse_args(argv)

    roots = [Path(r) for r in a.root]
    if not roots:
        base = paths.renders_root()
        roots = [d for d in sorted(base.glob("*"))
                 if (d / "Models").is_dir() and d.name != "live"]

    print(f"field lines vs the drawn shells   (open outside <= {MAX_OPEN_OUT_PCT}%, "
          f"worst ratio <= {MAX_WORST_RATIO})")
    results = [check_render(r, a.every) for r in roots]
    failed = [r for r in results if r.get("bad")]
    print()
    if failed:
        print(f"FAIL: {len(failed)} render(s) draw field lines outside the shells.")
        print("A worst ratio near or above 2 is the fault David reports as "
              "\"the shells are not aligned with the field lines\".")
        return 1
    print(f"OK: {len([r for r in results if not r.get('skipped')])} render(s) contain "
          f"their field lines.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
