#!/usr/bin/env python
"""Fetch the last N days of REAL solar wind and render it as an event replay.

One command for the overnight chain: no prompts, no interactive state, one
summary line on stdout, a non-zero exit on any failure.

    python tools/run_lookback.py --days 4 --step-min 10 --step-seconds 0.8 \
        --root output/magneto_lookback

What it does, in order:

  1. magneto.events.fetch_lookback  - pulls the window from CCMC ISWA HAPI into
     events/lookback-<start>-<end>/ (raw responses, 5-minute timeline.jsonl,
     event.json manifest with coverage and gaps). The window ends at the newest
     epoch ISWA can actually serve unless --end pins it.
  2. magneto.render.render_event    - the SAME renderer the curated events use.
     T89 (a look-back's states are already T89; the flag is passed anyway so a
     T96 id handed to this script still previews fast), export frame 'gsm',
     the solar-wind parcel stream on.
  3. dsexport validation of every .vla/.vlc written, unless --no-validate.

Runtime is dominated by the field-line trace: ~7-17 s per step at T89, so the
default 4 days at --step-min 10 is 576 steps and hours on one core. The render
now spreads those traces across PROCESSES (--workers, default cpu count) for a
~10x wall-clock win with a byte-identical output tree; --fieldline-every N
trades field-line smoothness for a further N-fold cut in trace count. geopack
is still NOT thread-safe and each worker keeps its own - never run two of
these scripts at once, and never thread this.

Exit codes: 0 ok, 1 fetch/render failed, 2 files failed validation.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:                    # runnable from any cwd
    sys.path.insert(0, str(REPO))

from magneto import events, paths, presets, render          # noqa: E402

log = logging.getLogger("run_lookback")

SHOW = presets.SHOW
DEFAULT_ROOT = str(paths.render_root("lookback"))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python tools/run_lookback.py",
        description=__doc__.splitlines()[0],
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--days", type=float, default=SHOW.lookback_days,
                    help="length of the look-back window, days")
    ap.add_argument("--end", default=None,
                    help="ISO-8601 Z window end; default = newest epoch ISWA serves")
    ap.add_argument("--feed", choices=sorted(events.FEEDS),
                    default=events.DEFAULT_FEED,
                    help="L1 pair: ace = ACE's own instruments, "
                         "rtsw = SWPC's current primary spacecraft")
    ap.add_argument("--events-dir", default=None,
                    help="where the event directory is written (default: events/)")
    ap.add_argument("--event-id", default=None,
                    help="render this already-fetched event instead of fetching "
                         "(re-run a render without re-pulling the data)")
    ap.add_argument("--fetch-only", action="store_true",
                    help="fetch the window and stop, no render")
    ap.add_argument("--root", default=DEFAULT_ROOT,
                    help="render output root (Models/ + Scripts/ land here)")
    ap.add_argument("--window-start", default=None,
                    help="render only from this epoch (ISO-8601 Z); the whole "
                         "fetched window by default")
    ap.add_argument("--window-stop", default=None,
                    help="render only up to this epoch (ISO-8601 Z)")
    ap.add_argument("--step-min", type=float, default=SHOW.step_min,
                    help="simulated minutes per rendered step")
    ap.add_argument("--step-seconds", type=float, default=SHOW.step_seconds,
                    help="dome seconds per rendered step")
    ap.add_argument("--frame", default=SHOW.frame,
                    choices=list(render.geo.FRAMES), help="export frame")
    ap.add_argument("--fieldline-every", type=int, default=SHOW.fieldline_every, metavar="N",
                    help="trace and emit field lines every Nth step only, "
                         "morphing across the stride")
    ap.add_argument("--workers", type=int, default=paths.workers(),
                    help="render processes (1 = the serial reference path); "
                         "the output tree is byte-identical either way")
    ap.add_argument("--resume", action="store_true",
                    help="reuse frames a previous build of this exact plan "
                         "already wrote")
    ap.add_argument("--coord-digits", type=int, default=SHOW.coord_digits, choices=(9, 17),
                    help="significant figures for the light-year coordinates: "
                         "9 (default) is float32-exact, which is all Digistar "
                         "keeps; 17 is the full float64 round-trip")
    ap.add_argument("--wind-sub", type=int, default=SHOW.wind_sub,
                    help="wind frames per field step (morph resolution and "
                         "file count only - the apparent flow rate is "
                         "wind_stream_s / step_seconds, independent of this)")
    ap.add_argument("--wind-stream-s", type=float,
                    default=SHOW.wind_stream_s,
                    help="seconds of real parcel flow per field step; scale "
                         "it with step_seconds to keep the tuned 150x look "
                         "(e.g. 47 at 0.3125 s steps)")
    ap.add_argument("--shells", dest="shells", action="store_true",
                    default=SHOW.with_shells,
                    help="export the translucent magnetopause / bow-shock shells "
                         "(.obj for effectModelClass); SHOW default on")
    ap.add_argument("--no-shells", dest="shells", action="store_false",
                    help="skip the shells")
    ap.add_argument("--pulses", dest="pulses", action="store_true",
                    default=SHOW.with_pulses,
                    help="export the field-line pulses (beads gliding along +B "
                         "on the traced lines); SHOW default on")
    ap.add_argument("--no-pulses", dest="pulses", action="store_false",
                    help="skip the pulses")
    ap.add_argument("--preload-window", type=int, default=SHOW.preload_window,
                    help="frame objects per layer kept alive ahead of the "
                         "playhead in the .ds player (0 = preload every frame)")
    ap.add_argument("--aurora-max-hold-min", type=float,
                    default=SHOW.aurora_max_hold_min,
                    help="cap on how long one aurora frame may hold")
    ap.add_argument("--ring", dest="with_ring", action="store_true",
                    default=SHOW.with_ring,
                    help="ring current from the measured Dst (default: on)")
    ap.add_argument("--no-ring", dest="with_ring", action="store_false")
    ap.add_argument("--plasmasphere", dest="with_plasmasphere", action="store_true",
                    default=SHOW.with_plasmasphere,
                    help="plasmasphere with Kp-driven erosion (default: on)")
    ap.add_argument("--no-plasmasphere", dest="with_plasmasphere", action="store_false")
    ap.add_argument("--imf", dest="with_imf", action="store_true", default=SHOW.with_imf,
                    help="IMF drape and Kan & Lee reconnection (default: on)")
    ap.add_argument("--no-imf", dest="with_imf", action="store_false")
    ap.add_argument("--shell-mode", choices=["step", "library"], default=SHOW.shell_mode)
    ap.add_argument("--shell-preset", default=SHOW.shell_preset,
                    help="shell look preset (P1 limb-only, P2 soft rim, P3 veil)")
    ap.add_argument("--holder-intensity", action="append", default=[],
                    metavar="LAYER=NN",
                    help="starting intensity for one asset-type holder (repeatable)")
    ap.add_argument("--no-wind", action="store_true",
                    help="skip the solar-wind parcel stream")
    ap.add_argument("--no-validate", action="store_true",
                    help="skip validating the emitted .vla/.vlc files")
    ap.add_argument("--no-publish", action="store_true",
                    help="build straight into --root, no staging/rotation")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,             # stdout = summary only
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    t0 = time.time()

    try:
        if args.event_id:
            event_id = args.event_id
            log.info("rendering existing event %s (no fetch)", event_id)
        else:
            event_id = events.fetch_lookback(
                days=args.days, end_utc=args.end, events_dir=args.events_dir,
                feed=args.feed)
        t_fetch = time.time() - t0
        summary = events.event_summary(event_id, args.events_dir)
        log.info("%s: %d states %s .. %s, coverage %s", event_id,
                 summary["n_steps"], summary["first_epoch"],
                 summary["last_epoch"], summary.get("coverage_fraction"))
        if args.fetch_only:
            print(f"LOOKBACK FETCH OK {event_id}  {summary['n_steps']} states  "
                  f"{summary['duration_h']} h  {t_fetch:.1f} s wall")
            return 0

        window = ((args.window_start, args.window_stop)
                  if (args.window_start or args.window_stop) else None)
        intensities = {}
        for spec in args.holder_intensity:
            layer, _, value = str(spec).partition("=")
            if not value:
                raise ValueError(f"--holder-intensity wants LAYER=NN, got {spec!r}")
            intensities[layer] = float(value)
        m = render.render_event(
            event_id, args.root, step_min=args.step_min, window=window,
            events_root=args.events_dir, step_seconds=args.step_seconds,
            frame=args.frame, with_wind=not args.no_wind,
            wind_sub=args.wind_sub, wind_stream_s=args.wind_stream_s,
            fieldline_every=args.fieldline_every, workers=args.workers,
            resume=args.resume, coord_digits=args.coord_digits,
            external=presets.external_for("lookback"),
            do_publish=not args.no_publish,
            with_shells=args.shells, with_pulses=args.pulses,
            preload_window=(args.preload_window or None),
            aurora_max_hold_min=args.aurora_max_hold_min,
            holder_intensities=intensities or None,
            shell_preset=args.shell_preset, shell_mode=args.shell_mode,
            with_ring=args.with_ring, with_plasmasphere=args.with_plasmasphere,
            with_imf=args.with_imf)
    except Exception as exc:
        log.exception("look-back failed")
        print(f"LOOKBACK FAILED - {type(exc).__name__}: {exc}")
        return 1

    models = Path(m.get("published") or (Path(args.root) / "Models"))
    n_ok, errors = (0, [])
    if not args.no_validate:
        n_ok, errors = render._validate_tree(models)
        log.info("validated %d files under %s, %d bad", n_ok, models, len(errors))
    # PACKAGING LAYOUT FIRST, shims second: the shims read model_convention_dirs, so a
    # shim written before the flip would describe the layout we are about to leave.
    # The look-back ships in the .dsz exactly like the storms, so it needs the same
    # event-prefixed basenames - otherwise its models collide with a storm's when the
    # packager flattens the tree by type. This path did not do it until 2026-09-20,
    # so a freshly rendered look-back came out flat and unprefixed while the storms
    # were prefixed: the one file structure David asked for, undone by whichever
    # render happened to run last.
    try:
        from tools import rename_models
        rename_models.prepare_for_packaging(args.root)
        log.info("laid out for packaging (Models\\<event>_<name>\\), scripts re-emitted")
    except Exception as exc:                       # never fail a good render on this
        log.warning("could not lay out for packaging (%s) - the render is good and "
                    "plays, but run tools/rename_models.py --root %s --convention "
                    "before packaging", exc, args.root)

    # Repoint the panel's buttons at what was just published. Without this the
    # look-back shim still names the PREVIOUS event id, whose Scripts the publish
    # has already rotated out - so the button plays nothing at all. A dead button
    # is exactly what the shim machinery exists to prevent, and calling
    # run_lookback.py directly (rather than through the panel kickoff) used to
    # skip it. Found on 2026-09-16 when a verification still came back as bare
    # Earth.
    try:
        from tools import write_shims
        written = write_shims.write_all()
        log.info("shims repointed at %s (%d files)", m["event_id"], len(written or []))
    except Exception as exc:                       # never fail a good render on this
        log.warning("could not rewrite shims (%s) - run tools/write_shims.py --all", exc)

    wall = time.time() - t0
    print(f"LOOKBACK OK {m['event_id']}  {m['n_frames']} steps  "
          f"{m.get('mb')} MB  {wall / 60:.1f} min wall "
          f"(fetch {t_fetch:.0f} s, render {m.get('build_s')} s, "
          f"{n_ok} files validated)  {m['epochs'][0]} .. {m['epochs'][-1]}  "
          f"-> {models}")
    if errors:
        for e in errors[:10]:
            print(f"  INVALID {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
