"""Build a slim, mobile-friendly web bundle from a published render tree.

    python -m tools.web_export --root Renders/storm_2024-05-10_gannon --out web --preset mobile

This is a POST-PASS. It reads the .vlc/.obj files the dome pipeline already published and
never touches the renderer, so it cannot regress a render that works, and it runs on storms
that were rendered weeks ago. See Engine/docs/WEB_EXPORT.md for the reasoning and the budget.

It needs numpy and nothing else - no geopack, no Digistar, no astropy - so it runs anywhere.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from webexport import bundle as bundle_mod          # noqa: E402
from webexport import decimate as dec               # noqa: E402
from webexport import wwt                           # noqa: E402
from webexport.presets import PRESETS               # noqa: E402
from webexport.read_obj import read_mesh            # noqa: E402
from webexport.read_vlc import read_vlc             # noqa: E402

N_PHI = 72          # magneto/model/__init__.py - azimuths per shell ring

# Points inside this radius are hidden behind Earth in any renderer that draws Earth, so they
# cost budget and show nothing. MEASURED on Gannon 2026-09-18: the Plasmasphere layer fills a
# solid ball from the geocentre outward (57% of its 4,000 dots inside 1 R_E at the storm peak,
# all at full opacity), because the drawing has no inner cutoff at the ionosphere. On the dome
# that is invisible - Earth is opaque - so it was never noticed. Dropping them here roughly
# DOUBLES the useful density of that layer for the same bytes. The count is recorded in the
# manifest so the bundle never quietly claims to be the full modelled population.
#
# The threshold is the SURFACE, 1.0 R_E, and it has to be: the AuroraOval is drawn at 110 km,
# which is 1.0173 R_E. A first attempt at 1.02 sat above the oval and deleted all 33,120 of
# its points - a filter meant to save bytes silently removing an entire science layer. Any
# future tightening of this number has to clear the aurora's altitude.
R_OCCLUDED_RE = 1.0

# Which preset field sets each layer's budget, and how the layer is decimated.
DOT_BUDGET = {"WindParticles": "keep_wind", "RingCurrent": "keep_ring",
              "Plasmasphere": "keep_plasma", "AuroraOval": "keep_aurora"}

# The dome's holder colour per surface layer (dsexport.ds shell_look / DS_MESH_LOOK, percent
# -> 0..1). Copied rather than imported: the web exporter's only dependency is numpy.
MESH_COLOUR = {"MagnetopauseShell": (0.14, 0.34, 0.40), "BowShockShell": (0.22, 0.15, 0.06),
               "RingCurrent": (0.34, 0.15, 0.08), "Plasmasphere": (0.22, 0.25, 0.36)}


def fine_windows_from_key_moments(km: str, epochs, times, half_h: float = 1.5):
    """Playback-second windows around the moments the render manifest already names.

    key_moments is prose ("shock 18:35 UT 10 May ... Bz min -44 nT at 00:35 UT 11 May ..."),
    so the times are lifted out of it rather than recomputed - one source of truth for what
    matters in this storm, shared with the dome player's banner.
    """
    if not km:
        return []
    t0 = np.asarray(times, dtype=float)
    ep = [e[11:16] for e in epochs]                      # 'HH:MM'
    out = []
    for hhmm in re.findall(r"\b([0-2]\d:[0-5]\d) UT\b", km):
        hits = [i for i, e in enumerate(ep) if e == hhmm]
        for i in hits:
            out.append((float(t0[i]) - half_h * 3600 / 600 * 0.8,
                        float(t0[i]) + half_h * 3600 / 600 * 0.8))
    return out


def layer_file_for_time(layer: dict, t: float) -> int:
    """Index of the layer file in effect at playback time t (step-held, like the player)."""
    lt = np.asarray(layer["times"], dtype=float)
    return int(np.searchsorted(lt, t, side="right") - 1)


def split_hue_lum(color: np.ndarray):
    """colour -> (unit-brightness hue, brightness). Field-line hue is topology and brightness
    is log|B|, so separating them lets hue ship once and brightness ship per keyframe."""
    m = color.max(axis=1)
    hue = np.zeros_like(color)
    nz = m > 1e-6
    hue[nz] = color[nz] / m[nz, None]
    return hue, m


def build(root: pathlib.Path, out: pathlib.Path, preset, csv: bool, emit_obj: bool):
    out.mkdir(parents=True, exist_ok=True)      # --obj writes into it during the loop
    models = root / "Models"
    man = json.loads((models / "event_frames.json").read_text(encoding="utf-8"))
    epochs, by_name = man["epochs"], {L["name"]: L for L in man["layers"]}

    base = by_name["FieldLines"] if "FieldLines" in by_name else man["layers"][0]
    times = np.asarray(base["times"], dtype=float)
    kf = dec.pick_keyframes(times, epochs, preset.keyframes,
                            fine_windows_from_key_moments(man.get("key_moments", ""),
                                                          epochs, times))
    kf_times = times[kf]
    print(f"{man['event_id']}: {len(times)} steps -> {kf.size} keyframes "
          f"({kf_times[0]:.1f}..{kf_times[-1]:.1f} playback s)")

    out_layers = []
    for name in preset.layers:
        if name not in by_name:
            print(f"  {name}: absent from this render, skipped")
            continue
        L = by_name[name]
        t_start = time.time()

        if L["kind"] == "mesh":
            idx = faces = None
            pos, alpha, col0 = [], None, None
            for t in kf_times:
                fi = layer_file_for_time(L, t)
                fn = L["files"][fi]
                m = read_mesh(models / fn.rsplit(".", 1)[0] / fn)
                if idx is None:
                    if m.lattice is not None:
                        # A structured (u, v) lattice - the ring current (120 x 50, a torus
                        # closed both ways) and the plasmasphere (50 x 80, closed in
                        # longitude only). The GLB says so in asset.extras, so the exporter
                        # never guesses; the shell grid below has no such stamp because it
                        # is the one shape with apex vertices.
                        idx, faces = dec.decimate_lattice(m.lattice[0], m.lattice[1],
                                                          m.wrap[0], m.wrap[1],
                                                          preset.shell_ring_stride,
                                                          preset.shell_phi_stride)
                    else:
                        idx, faces = dec.decimate_shell(m.verts.shape[0], N_PHI,
                                                        preset.shell_ring_stride,
                                                        preset.shell_phi_stride)
                    if not m.was_precompensated:
                        print(f"  {name}: WARNING - no y-up stamp, left untransformed")
                pos.append(m.verts[idx])
            pos = np.stack(pos)
            n = pos.shape[1]
            # The dome's holder colour for this surface, so a web renderer that lights the
            # mesh with color0 reproduces the show's palette instead of white.
            rgb = np.asarray(MESH_COLOUR.get(name, (1.0, 1.0, 1.0)), dtype=np.float32)
            entry = dict(name=name, kind="mesh", pos=pos,
                         alpha=np.ones((pos.shape[0], n), dtype=np.float32),
                         color0=np.tile(rgb, (n, 1)),
                         faces=faces, keyframe_map=list(range(pos.shape[0])),
                         lattice=(list(m.lattice) if m.lattice else None))
            if emit_obj:
                wwt.write_shell_obj(out / f"{name}_frame0.obj", pos[0], faces,
                                    "decimated %d verts / %d tris" % (n, faces.shape[0]))

        else:
            is_lines = L["kind"] == "lines"
            sched = kf_times
            if name == "AuroraOval" and preset.aurora_keyframes:
                sub = dec.stride_points(kf.size, preset.aurora_keyframes)
                sched = kf_times[sub]
                kmap = [int(i) for i in sub]
            else:
                kmap = list(range(kf.size))

            # Read every keyframe FIRST, then select. Selecting from frame 0 alone would be
            # wrong for the dot layers: a parcel inside Earth at one epoch can be outside it
            # at another, and the whole point of a fixed row count is that vertex i is the
            # same physical thing in every frame.
            first = None
            pos, alpha, lum = [], [], []
            for t in sched:
                fi = layer_file_for_time(L, t)
                fn = L["files"][fi]
                fr = read_vlc(models / fn.rsplit(".", 1)[0] / fn)
                if first is None:
                    first = fr
                pos.append(fr.pos)
                alpha.append(fr.opacity)
                lum.append(split_hue_lum(fr.color)[1])
            pos, alpha, lum = np.stack(pos), np.stack(alpha), np.stack(lum)

            if is_lines:
                kl = preset.keep_lines if name == "FieldLines" else preset.keep_imf_lines
                kv = (preset.keep_line_verts if name == "FieldLines"
                      else preset.keep_imf_verts)
                idx, starts, lengths = dec.decimate_lines(first, kl, kv)
                dropped = 0
            else:
                r = np.linalg.norm(pos, axis=2)
                visible = np.flatnonzero((r >= R_OCCLUDED_RE).any(axis=0))
                dropped = int(pos.shape[1] - visible.size)
                if dropped:
                    print(f"    {dropped} of {pos.shape[1]} points never leave "
                          f"{R_OCCLUDED_RE} R_E (occluded by Earth) - dropped")
                idx = visible[dec.stride_points(visible.size,
                                                getattr(preset, DOT_BUDGET[name]))]
                starts = lengths = None
            hue, _ = split_hue_lum(first.color[idx])

            entry = dict(name=name, kind=L["kind"], pos=pos[:, idx],
                         alpha=alpha[:, idx], color0=hue,
                         lum=lum[:, idx], keyframe_map=kmap, occluded_dropped=dropped)
            if is_lines:
                entry["starts"], entry["lengths"] = starts, lengths

        out_layers.append(entry)
        print("  %-18s %6d verts x %3d keyframes  (%.1fs)"
              % (name, entry["pos"].shape[1], entry["pos"].shape[0], time.time() - t_start))

    ph = man.get("physics", {})
    meta = {
        "event_id": man["event_id"],
        "title": man.get("title", ""),
        "preset": preset.as_json(),
        "units": {"length": "earth_radii", "re_km": 6371.0,
                  "frame": "J2000 geocentric equatorial (+Z north celestial pole, "
                           "+X vernal equinox) - the same axes as WWT's sky frame",
                  "note": "Earth is at the origin. Positions dequantise as "
                          "p = pos_lo + pos/65535 * (pos_hi - pos_lo)."},
        "keyframes": {"count": int(kf.size),
                      "playback_s": [float(v) for v in kf_times],
                      "epoch": [epochs[int(i)] for i in kf],
                      "hud": [man["hud"][int(i)] for i in kf] if man.get("hud") else []},
        "source": {"step_min": man.get("step_min"), "window": man.get("window"),
                   "model": man.get("model"), "n_steps_original": len(times),
                   "plan_digest": man.get("plan_digest"),
                   "built_from": str(root)},
        # The honesty chain. CLAUDE.md's law applies to the web build exactly as it does to
        # the dome: honest labels on model ovals, subsamples and derived drawings.
        "provenance": {
            "physics_version": ph.get("physics_version"),
            "physics_tag": ph.get("tag"),
            "magnetopause": ph.get("magnetopause"),
            "bow_shock": ph.get("bow_shock"),
            "aberration": ph.get("aberration"),
            "aurora": man.get("aurora_label",
                              "Modelled auroral oval - statistical Kp model, not an observation"),
            "key_moments": man.get("key_moments", ""),
            "talking_points": man.get("talking_points", []),
            "decimation": "Every layer is SUBSAMPLED for the web, never resampled: vertices "
                          "are a strict subset of the rendered ones. Densities are therefore "
                          "lower than the physics produced, and should be described as a "
                          "subsample rather than as the modelled density.",
            "occluded_points": "Dot layers drop points that never leave %g R_E: they sit "
                               "inside Earth and are occluded in any renderer that draws it. "
                               "Measured on Gannon: the Plasmasphere layer fills a solid ball "
                               "from the geocentre outward, so 57%% of its dots were in that "
                               "category at the storm peak. Per-layer counts are in "
                               "layers[].occluded_dropped." % R_OCCLUDED_RE,
            "field_pulses": "Not shipped. Beads gliding along +B are reconstructed in the "
                            "shader from the FieldLines vertices they ride on.",
            "surfaces": "MagnetopauseShell, BowShockShell, RingCurrent and Plasmasphere are "
                        "triangulated surfaces (kind 'mesh', a faces block per layer, color0 = "
                        "the dome's holder colour). The ring current is a torus whose strength "
                        "is the measured SYM-H energy but whose radius is climatology; the "
                        "plasmasphere radius IS the measurement (Carpenter & Anderson 1992).",
            "credits": "Engine/docs/CREDITS.html carries the full reference list with DOIs.",
        },
    }
    m = bundle_mod.write_bundle(out, man["event_id"], out_layers, meta)
    mb = m["binary_bytes"] / 1048576.0
    print(f"\nbundle: {out / (man['event_id'] + '.bin')}  {mb:.2f} MB "
          f"+ {(out / (man['event_id'] + '.json')).stat().st_size / 1024:.0f} KB manifest")

    if csv:
        for L in out_layers:
            if L["kind"] == "mesh":
                continue
            p = out / f"{man['event_id']}_{L['name']}.csv"
            n = wwt.write_layer_csv(p, L, meta["keyframes"]["epoch"])
            print(f"  csv {p.name}: {n} rows, {p.stat().st_size / 1048576:.1f} MB")
    return m


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="a render root (the dir holding Models/)")
    ap.add_argument("--out", default="web")
    ap.add_argument("--preset", default="mobile", choices=sorted(PRESETS))
    ap.add_argument("--csv", action="store_true",
                    help="also write per-layer CSV tables for WWT spreadsheet layers")
    ap.add_argument("--obj", action="store_true",
                    help="also write each decimated shell's frame 0 as a plain .obj")
    a = ap.parse_args(argv)
    build(pathlib.Path(a.root), pathlib.Path(a.out), PRESETS[a.preset], a.csv, a.obj)


if __name__ == "__main__":
    main()
