#!/usr/bin/env python
r"""Render the curated storms one after another with the SHOW preset.

    python tools\rebuild_all_events.py                       # all five, in registry order
    python tools\rebuild_all_events.py --events 2024-05-10_gannon,2026-01-19_kp9
    python tools\rebuild_all_events.py --dry-run             # print the commands only
    python tools\rebuild_all_events.py --skip-current        # skip storms already on the preset

Also the engine behind the panel's "Rebuild <storm>" and "Rebuild All Storms"
buttons (Scripts\kickoff_event_rebuild.py / kickoff_rebuild_all.py call
run_queue()). Rules:

  * ONE render at a time - magneto.joblock's RUNNING.lock. A second queue is
    refused with a banner naming the owner.
  * Every storm: `python -m magneto.render event <id> --start --end <SHOW flags>
    --root Renders\storm_<id> --validate`, T96 (the state's own model - the
    physically right one for storms; see magneto.presets).
  * A failed storm never clobbers the installed one (publish.py stages and
    promotes atomically); the queue reports it and continues unless
    --stop-on-fail.
  * After each success the panel shims are regenerated (tools\write_shims.py),
    so the storm's button works the moment it publishes.
  * Scripts\STOP.request or Renders\_kickoffs\STOP ends the queue after the
    current storm (never mid-render).
  * Banners go through `say()` (<= 255 chars); the kickoff wrapper's say()
    writes them where kickoff_watch.js pushes them to the dome.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from magneto import events, joblock, paths, presets          # noqa: E402

STOP_FILES = ("STOP.request", "STOP")


def _print_say(text: str, logfile=None) -> None:
    print("[banner] " + text, flush=True)


def stop_requested() -> Path | None:
    for d in (paths.SCRIPTS_DIR, paths.kickoff_log_dir()):
        for name in STOP_FILES:
            p = d / name
            if p.exists():
                return p
    return None


def clear_stop_requests() -> None:
    p = stop_requested()
    while p is not None:
        try:
            p.unlink()
        except OSError:
            break
        p = stop_requested()


def n_steps(event_id: str, step_min: float) -> int:
    spec = events.EVENTS[event_id]
    a = datetime.fromisoformat(spec.start.replace("Z", "+00:00"))
    b = datetime.fromisoformat(spec.stop.replace("Z", "+00:00"))
    return int(round((b - a).total_seconds() / 60.0 / step_min)) + 1


def on_show_preset(event_id: str) -> bool:
    """True when the installed render of this storm already carries the SHOW preset."""
    mf = paths.render_root("storm", event_id) / "Models" / "event_frames.json"
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    show = presets.SHOW
    try:
        return (bool(m.get("with_shells")) == show.with_shells
                and bool(m.get("with_pulses")) == show.with_pulses
                and abs(float(m.get("step_seconds", -1)) - show.step_seconds) < 1e-9
                and str(m.get("frame", "")).lower() == show.frame
                and (m.get("model") or "").lower() != "t89")
    except (TypeError, ValueError):
        return False


def render_cmd(event_id: str, *, workers=None, resume=False, progress_file=None,
               progress_label=None) -> list[str]:
    spec = events.EVENTS[event_id]
    cmd = [str(paths.python_exe()), "-m", "magneto.render", "event", event_id,
           "--start", spec.start, "--end", spec.stop,
           *presets.SHOW.cli_args(workers=workers or paths.workers()),
           "--root", str(paths.render_root("storm", event_id)), "--validate"]
    if resume:
        cmd.append("--resume")
    if progress_file:
        cmd += ["--progress-file", str(progress_file)]
        if progress_label:
            cmd += ["--progress-label", str(progress_label)]
    return cmd


def _tree_gb(root: Path) -> float:
    total = 0
    models = root / "Models"
    if models.exists():
        # rglob, not iterdir: a finished render is laid out Models\<name>\<name>.<ext>
        # (see _lay_out_for_packaging), so a flat listing would measure an empty tree.
        for p in models.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    return total / 1e9


def _lay_out_for_packaging(event_id: str, say=_print_say, logfile=None) -> bool:
    r"""Put a finished render into the shape a .dsz can carry, and re-emit its scripts.

    A render comes off the writers FLAT and with bare basenames
    (Models\FieldLines_0000.vlc), which is wrong for a package in two independent ways:

      * every render uses the SAME basenames, and the packager keeps a model's path only
        relative to the nearest Models\ folder, so six renders collapse onto one set and
        five storms would silently play another storm's geometry;
      * Digistar's own packager writes Models\<name>\<name>.<ext>, so a source tree in
        that shape makes the .dsz round trip a no-op instead of a rewrite.

    tools/rename_models.py --convention does both and re-emits the scripts from the
    manifest afterwards, which is the half that is easy to forget: renaming the files
    without re-emitting leaves every model line naming the old name.

    This belongs HERE, on the panel's Rebuild path, rather than in a step an operator
    has to remember. Run by hand it was simply not run: before 2026-09-20 a freshly
    rendered Gannon had neither the prefix nor the layout, and the fault is invisible
    until a dome comes up empty.

    Returns True when the package ends up packageable.
    """
    root = paths.render_root("storm", event_id)
    try:
        from tools import rename_models                                      # noqa: E402
        rename_models.prepare_for_packaging(root)
        return True
    except Exception as exc:                                                 # noqa: BLE001
        say(f"Rebuild {event_id}: the render is good and plays, but it is NOT packageable "
            f"- the layout step failed: {exc}. Run "
            f"python -m tools.rename_models --root <render> --convention before packaging.",
            logfile)
        return False


def _fmt_dur(s: float) -> str:
    s = int(round(s))
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def run_queue(ids=None, *, say=_print_say, logfile=None, job="rebuild-all",
              workers=None, resume=False, skip_current=False, stop_on_fail=False,
              dry_run=False, progress=True, retry_failed=True) -> int:
    ids = list(ids) if ids else list(events.EVENTS)
    unknown = [i for i in ids if i not in events.EVENTS]
    if unknown:
        say("Rebuild refused: unknown event id(s) %s" % ", ".join(unknown), logfile)
        return 2
    try:
        from tools import write_shims                                   # noqa: E402
    except Exception:                                                   # noqa: BLE001
        write_shims = None

    log_dir = paths.kickoff_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    progress_file = (log_dir / "progress.json") if progress else None
    n = len(ids)
    step_min = presets.SHOW.step_min

    if dry_run:
        for i, eid in enumerate(ids, 1):
            print(f"[{i}/{n}] {eid}  ({n_steps(eid, step_min)} steps)")
            print("   " + " ".join(render_cmd(eid, workers=workers, resume=resume,
                                              progress_file=progress_file,
                                              progress_label=f"{i}/{n} {presets.event_label(eid)}")))
        return 0

    with joblock.JobLock(job, event=",".join(ids)) as lock:
        if not lock.acquired:
            say("Rebuild REFUSED: another render is running - %s. Try again when it posts "
                "DONE." % lock.describe_owner(), logfile)
            return 3
        clear_stop_requests()
        t_all = time.time()
        results = []                                    # (id, rc, seconds, gb)
        for i, eid in enumerate(ids, 1):
            label = presets.event_label(eid)
            tag = f"{i}/{n}" if n > 1 else ""
            if skip_current and on_show_preset(eid):
                say(f"Rebuild {tag} {label}: already on the SHOW preset - skipped.", logfile)
                results.append((eid, 0, 0.0, _tree_gb(paths.render_root('storm', eid))))
                continue
            rest = ", ".join(presets.event_label(x) for x in ids[i:]) or "none"
            say(f"Rebuild {tag} {label} STARTED: TS05, {n_steps(eid, step_min)} steps at "
                f"{workers or paths.workers()} workers. Queue after this: {rest}", logfile)
            cmd = render_cmd(eid, workers=workers, resume=resume, progress_file=progress_file,
                             progress_label=f"{tag} {label}".strip())
            t0 = time.time()
            rc = _run_logged(f"render {eid}", cmd, logfile)
            dt = time.time() - t0
            gb = _tree_gb(paths.render_root("storm", eid))
            results.append((eid, rc, dt, gb))
            if rc == 0:
                # Layout FIRST: the shims read model_convention_dirs, so a shim
                # written before the flip would describe the layout we just left.
                _lay_out_for_packaging(eid, say, logfile)
                gb = _tree_gb(paths.render_root("storm", eid))
                results[-1] = (eid, rc, dt, gb)
                if write_shims is not None:
                    try:
                        write_shims.write_all()
                    except Exception as exc:                            # noqa: BLE001
                        say(f"Rebuild {tag} {label} DONE in {_fmt_dur(dt)} but shim rewrite "
                            f"failed: {exc}", logfile)
                nxt = presets.event_label(ids[i]) if i < n else "none"
                say(f"Rebuild {tag} {label} DONE in {_fmt_dur(dt)}, {gb:.1f} GB. Its button "
                    f"plays the new render now. Next: {nxt}", logfile)
            else:
                say(f"Rebuild {tag} {label} FAILED rc={rc} after {_fmt_dur(dt)} - last-good "
                    f"still installed. {'Stopping.' if stop_on_fail else 'Continuing.'} "
                    f"Log: {os.path.basename(logfile) if logfile else 'stdout'}", logfile)
                if stop_on_fail:
                    break
            if i < n and stop_requested() is not None:
                clear_stop_requests()
                say(f"Rebuild queue STOPPED by operator after {tag} {label}. "
                    f"Remaining: {', '.join(presets.event_label(x) for x in ids[i:])}", logfile)
                break
        # One retry pass for whatever failed (unless the operator asked to stop or
        # --stop-on-fail broke the loop). A render that died mid-way left its trace
        # cache in the same-stamp staging, so the second attempt is mostly writes.
        failed = [r[0] for r in results if r[1] != 0]
        if retry_failed and failed and not stop_on_fail and stop_requested() is None:
            for eid in failed:
                label = presets.event_label(eid)
                say(f"Rebuild RETRY {label}: second attempt (the trace cache from the first "
                    f"run is reused).", logfile)
                cmd = render_cmd(eid, workers=workers, resume=resume,
                                 progress_file=progress_file,
                                 progress_label=f"retry {label}")
                t0 = time.time()
                rc = _run_logged(f"render {eid} (retry)", cmd, logfile)
                dt = time.time() - t0
                gb = _tree_gb(paths.render_root("storm", eid))
                if rc == 0:
                    _lay_out_for_packaging(eid, say, logfile)
                    gb = _tree_gb(paths.render_root("storm", eid))
                results = [r for r in results if r[0] != eid] + [(eid, rc, dt, gb)]
                if rc == 0:
                    if write_shims is not None:
                        try:
                            write_shims.write_all()
                        except Exception as exc:                        # noqa: BLE001
                            say(f"Rebuild RETRY {label} DONE but shim rewrite failed: {exc}",
                                logfile)
                    say(f"Rebuild RETRY {label} DONE in {_fmt_dur(dt)}, {gb:.1f} GB. Its "
                        f"button plays the new render now.", logfile)
                else:
                    say(f"Rebuild RETRY {label} FAILED again rc={rc} after {_fmt_dur(dt)} - "
                        f"last-good still installed. Log: "
                        f"{os.path.basename(logfile) if logfile else 'stdout'}", logfile)
                if stop_requested() is not None:
                    break
        if progress_file is not None:
            try:
                progress_file.unlink()
            except OSError:
                pass
        ok = [r for r in results if r[1] == 0]
        bad = [r for r in results if r[1] != 0]
        total_gb = sum(r[3] for r in results)
        if n > 1:
            say(f"Rebuild ALL finished: {len(ok)}/{n} OK in {_fmt_dur(time.time() - t_all)}, "
                f"{total_gb:.1f} GB. Failed: "
                f"{', '.join(presets.event_label(r[0]) for r in bad) or 'none'}. Shims regenerated.",
                logfile)
        return 0 if not bad else 1


def _run_logged(label: str, cmd: list[str], logfile) -> int:
    if logfile:
        with open(logfile, "a", buffering=1, encoding="utf-8", errors="replace") as fh:
            fh.write("\n--- {} : {}\n--- {}\n".format(
                datetime.now(timezone.utc).isoformat(), label, " ".join(cmd)))
            fh.flush()
            t0 = time.time()
            rc = subprocess.run(cmd, cwd=str(ENGINE), stdin=subprocess.DEVNULL,
                                stdout=fh, stderr=subprocess.STDOUT).returncode
            fh.write("--- {} finished rc={} in {:.1f}s\n".format(label, rc, time.time() - t0))
        return rc
    return subprocess.run(cmd, cwd=str(ENGINE)).returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--events", default=None, help="comma-separated ids (default: all five)")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--skip-current", action="store_true",
                    help="skip storms whose installed render already carries the SHOW preset")
    ap.add_argument("--stop-on-fail", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--logfile", default=None)
    args = ap.parse_args(argv)
    ids = [x.strip() for x in args.events.split(",") if x.strip()] if args.events else None
    return run_queue(ids, logfile=args.logfile, workers=args.workers, resume=args.resume,
                     skip_current=args.skip_current, stop_on_fail=args.stop_on_fail,
                     dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
