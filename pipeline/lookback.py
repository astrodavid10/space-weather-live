"""The 48-hour look-back: fetch -> render -> export (mobile + desktop) -> gzip -> index.

Runs the vendored dome engine (pipeline/engine, see engine/SNAPSHOT.md) as
SUBPROCESSES, the way the dome invokes it, so geopack's process-global state
never touches this process. Only ``magneto.events`` is imported in-process, for
the ISWA feed probe and the fetch.

Failure policy (sol's): nothing here ever deletes a published look-back. Any
failure leaves the seeded ``<out>/lookback`` untouched and rewrites
``<out>/index.json`` with the reason, so the site keeps serving the previous
look-back with its true age. Exit 0 whenever index.json was written.

ENGINE CLI. The render command below is the dome's documented shape
(magneto/presets.py SHOW.cli_args + events.py's fetch hint). The exact flag
spelling for the external model and the render root is question B1 to the
dome-side engineer; ENGINE_RENDER_EXTRA lets CI adjust it without a code change.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional

from . import gz
from .config import (DATA_END_STALE_HOURS, ENGINE_DIR, SCHEMA_LOOKBACK, STALE_AFTER_HOURS,
                     WORK_DIR)
from .index import build_index
from .io_utils import atomic_write_bytes, iso_z, read_json, utcnow, write_json
from .validate import Report, check_bundle, loader

HERE = Path(__file__).resolve().parent
RENDERS = WORK_DIR / "Renders"
EVENTS = WORK_DIR / "events"


class StageError(RuntimeError):
    def __init__(self, stage: str, msg: str) -> None:
        super().__init__(msg)
        self.stage = stage


def _env(workers: int) -> Dict[str, str]:
    env = dict(os.environ)
    env.update({"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
                "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(ENGINE_DIR)})
    return env


def _engine_ready() -> Optional[str]:
    """None if the snapshot looks usable, else why not."""
    for rel in ("magneto/events.py", "magneto/render.py", "tools/web_export.py", "webexport/bundle.py"):
        if not (ENGINE_DIR / rel).is_file():
            return "engine snapshot not vendored yet (missing pipeline/engine/%s)" % rel
    return None


def _write_local_config(workers: int) -> None:
    """magneto/paths.py reads PACKAGE/Config/magneto.local.json; PACKAGE is pipeline/ here."""
    cfg = HERE / "Config"
    cfg.mkdir(exist_ok=True)
    RENDERS.mkdir(parents=True, exist_ok=True)
    write_json(cfg / "magneto.local.json", {"renders_root": str(RENDERS), "workers": workers})


def _run(cmd: List[str], stage: str, workers: int, log: Path) -> None:
    print("  $ " + " ".join(cmd), flush=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write("\n$ %s\n" % " ".join(cmd))
        fh.flush()
        rc = subprocess.call(cmd, cwd=str(ENGINE_DIR), env=_env(workers), stdout=fh, stderr=subprocess.STDOUT)
    if rc != 0:
        raise StageError(stage, "%s exited %d (see %s)" % (cmd[2] if len(cmd) > 2 else cmd, rc, log.name))


def _fetch(days: float, end: Optional[str], feed: str) -> dict:
    sys.path.insert(0, str(ENGINE_DIR))
    from magneto import events  # noqa: E402  (vendored engine)
    probes = {}
    for name in ("ace", "rtsw"):
        try:
            probes[name] = {"ok": True, "stop_iso": str(events.latest_iswa_epoch(name))}
        except Exception as exc:  # a dead feed is data, not a crash
            probes[name] = {"ok": False, "error": str(exc)[:200]}
    if feed == "auto":
        live = [(p["stop_iso"], n) for n, p in probes.items() if p.get("ok")]
        if not live:
            raise StageError("fetch", "neither ISWA feed answered: %s" % json.dumps(probes))
        feed = max(live)[1]
    EVENTS.mkdir(parents=True, exist_ok=True)
    eid = events.fetch_lookback(days=days, end_utc=end, events_dir=EVENTS, feed=feed)
    man = events.load_manifest(eid, root=EVENTS)
    return {"event_id": eid, "feed": feed, "probes": probes, "manifest": man}


def run(a) -> int:
    out = Path(a.out)
    t0 = time.time()
    timing: Dict[str, object] = {"workers": a.workers}
    log = WORK_DIR / "render.log"
    presets = [p for p in a.presets.split(",") if p]
    status, failure = "ok", None
    try:
        why = _engine_ready()
        if why:
            raise StageError("engine", why)
        _write_local_config(a.workers)

        t = time.time()
        got = _fetch(a.days, a.end, a.feed)
        timing["fetch_s"] = round(time.time() - t, 1)
        eid = got["event_id"]
        prev = read_json(out / "lookback" / "index.json") or {}
        if prev.get("event_id") == eid and not a.force and \
                prev.get("data_end_iso") == got["manifest"].get("last_epoch"):
            print("no new data: %s already published" % eid)
            build_index(out, "ok", run_id=os.environ.get("GITHUB_RUN_ID"))
            return 0

        render_root = RENDERS / "lookback"
        t = time.time()
        cmd = [sys.executable, "-m", "magneto.render", "event", eid, "--root", str(render_root)]
        sys.path.insert(0, str(ENGINE_DIR))
        from magneto.presets import SHOW  # noqa: E402
        cmd += SHOW.cli_args(workers=a.workers)
        cmd += os.environ.get("ENGINE_RENDER_EXTRA", "--external t89").split()
        if not a.force:
            cmd.append("--resume")
        _run(cmd, "render", a.workers, log)
        timing["render_s"] = round(time.time() - t, 1)

        stage = out / ".staging-lookback"
        shutil.rmtree(stage, ignore_errors=True)
        rows, export_s, notes = {}, {}, []
        for preset in presets:
            t = time.time()
            pdir = stage / preset
            _run([sys.executable, "-m", "tools.web_export", "--root", str(render_root),
                  "--out", str(pdir), "--preset", preset], "export", a.workers, log)
            raw_path = pdir / (eid + ".bin")
            raw = raw_path.read_bytes()
            meta = json.loads((pdir / (eid + ".json")).read_text(encoding="utf-8"))
            packed = gz.pack(meta, raw)
            atomic_write_bytes(pdir / (eid + ".bin.gz"), packed)
            raw_path.unlink()
            for obj in pdir.glob("*.obj"):
                obj.unlink()
            entry = {"json_url": preset + "/" + eid + ".json", "bin_gz_url": preset + "/" + eid + ".bin.gz",
                     "keyframes": meta["keyframes"]["count"]}
            entry.update(gz.record(raw, packed))
            rep = Report()
            if check_bundle(loader(str(stage)), entry["json_url"], entry["bin_gz_url"], rep,
                            entry=entry, preset=preset, expect_model={"t89"}):
                rows[preset] = entry
            else:
                print(rep.text())
                notes.append("%s preset failed validation and was not published" % preset)
            export_s[preset] = round(time.time() - t, 1)
        timing["export_s"] = export_s
        if not rows:
            raise StageError("validate", "no preset passed validation")

        man = got["manifest"]
        epochs = meta["keyframes"]["epoch"]
        prov = meta.get("provenance", {})
        cov = man.get("coverage", {})
        timing["total_s"] = round(time.time() - t0, 1)
        index = {
            "schema": SCHEMA_LOOKBACK, "kind": "lookback", "tier": "REPLAY", "model": "t89",
            "event_id": eid, "title": meta.get("title"),
            "status": "degraded" if (man.get("partial") or notes) else "ok",
            "partial": bool(man.get("partial")),
            "degradation": list(man.get("partial_reason") or []) + notes,
            "generated_iso": iso_z(utcnow()),
            "data_start_iso": epochs[0], "data_end_iso": epochs[-1],
            "stale_after_hours": STALE_AFTER_HOURS, "data_end_stale_hours": DATA_END_STALE_HOURS,
            "window": dict(meta.get("source", {}).get("window", {}),
                           step_min=meta.get("source", {}).get("step_min"),
                           n_steps=meta.get("source", {}).get("n_steps_original")),
            "presets": rows,
            "feed": {"used": got["feed"], "probed": got["probes"], "coverage": cov,
                     "spacecraft": (man.get("lookback") or {}).get("spacecraft")},
            "provenance": {"engine": {"physics_version": prov.get("physics_version"),
                                      "physics_tag": prov.get("physics_tag")},
                           "external": "t89", "key_moments": prov.get("key_moments", ""),
                           "render_args": cmd[3:]},
            "timing": timing, "note": "",
        }
        write_json(stage / "index.json", index)
        # Promote: replace <out>/lookback wholesale (only the newest look-back is kept).
        dest = out / "lookback"
        shutil.rmtree(dest, ignore_errors=True)
        shutil.move(str(stage), str(dest))
        if notes or man.get("partial"):
            status = "ok"  # built and published; degradation is carried per source
    except StageError as exc:
        status, failure = exc.stage + "_failed", str(exc)
        print("LOOKBACK %s: %s" % (status, exc), flush=True)
    except Exception as exc:  # never leave the run without an index
        status, failure = "failed", "%s: %s" % (type(exc).__name__, exc)
        traceback.print_exc()
    idx = build_index(out, status, failure=failure, run_id=os.environ.get("GITHUB_RUN_ID"))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("### look-back\n\n```\n%s\n```\n" % json.dumps(
                {"status": status, "failure": failure, "timing": timing,
                 "lookback": idx["sources"]["lookback"].get("status")}, indent=1))
    return 0
