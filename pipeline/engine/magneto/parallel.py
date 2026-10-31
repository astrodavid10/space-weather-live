r"""The multiprocess event executor - same plan, same bytes, N cores.

WHY THIS IS ALLOWED AT ALL, given that geopack is not thread-safe and
magneto/model/__init__.py owns the only recalc() call site: nothing here shares
a geopack. Every worker is a separate PROCESS with its own module globals, so
"one recalc gateway per interpreter" holds in each of them exactly as it holds
in a serial render. The rule that would be broken by threads - two recalcs
racing on one set of module globals - is untouched, and the parent keeps its
own geopack strictly to itself (the wind).

WHY THE BYTES COME OUT IDENTICAL. Two claims, both checked by
tests/test_render_parallel.py rather than merely asserted here:

  1. every plan item is a pure function of (its two states, the export frame),
     because each of the four writers re-enters the recalc gateway for every
     epoch it touches before it converts a coordinate. Order of execution
     therefore cannot be observed in the output.
  2. the manifest is built from the PLAN, not from a directory scan, so the
     order files happen to land in cannot leak into event_frames.json or into
     the .ds player cut from it.

WHAT RUNS WHERE.

  Stage A (pool)    one task per field-line epoch. trace_all is ~90% of an
                    event render - ~7 s per epoch at T89, ~124 s at T96 - and
                    it is embarrassingly parallel across epochs. Results go to
                    <Models>\.tracecache\<key>.npz and only ~100 bytes of
                    metadata comes back through the pipe.
  Stage A (parent)  the wind, concurrently. The parcel pool is ONE population
                    advected continuously, so snapshot j strictly depends on
                    j-1 and the advection cannot be split - but formatting
                    20 000 float rows into ASCII can be, and that is about half
                    the wind's cost. The parent runs the physics and the
                    statistics (identical order, identical numbers) and hands
                    each finished morph pair to the pool as a write task.
  Stage B (pool)    one task per remaining plan item. Field-line items load
                    two .npz traces and do no physics at all; boundaries,
                    aurora and L1 recalc for themselves.

WINDOWS SPAWN RULES, which shape most of what follows: worker entry points are
module-level functions (no closures, no lambdas), the heavy shared inputs (the
state list, the plan, the cache keys) travel through ONE payload file the
initializer reads once per process instead of riding on every task, and the
parent pins OMP/MKL/OpenBLAS to one thread each BEFORE the pool is built -
otherwise 32 workers each start an N-thread BLAS pool and the machine spends
its time context-switching. Nothing in this render is BLAS-bound anyway: the
hot loop is geopack's scalar ODE stepper.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import threading
import time
from concurrent.futures import (FIRST_COMPLETED, ProcessPoolExecutor,
                                as_completed, wait)
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

TRACE_CACHE_V = 1          # bump when trace_all's OUTPUT changes meaning
PAYLOAD_NAME = "_payload.pkl"
# How many wind writes may be in flight before the parent stops advecting and
# lets the pool catch up. Bounded because each carries two 10 000 x 7 float
# blocks (~1.1 MB): unbounded, a multi-day look-back would queue gigabytes of
# pickled rows in the parent.
WIND_QUEUE_PER_WORKER = 4

# Set by _init_worker in each child; never touched in the parent.
_W: dict = {}


# --- the trace cache ----------------------------------------------------------

def trace_key(state: dict, frame: str, internal: str = "igrf") -> str:
    """Cache key for one trace_all result: everything trace_all actually reads.

    trace_all uses the epoch (via recalc, which fixes the dipole tilt and the
    SM->GSM basis), the external-field arguments from state['model'], the
    export frame and the internal-field choice. Nothing else about the state
    reaches the field lines - the driving values shape the boundaries and the
    wind, not the trace.
    """
    model = state["model"]
    parmod = model.get("parmod")
    payload = json.dumps({
        "v": TRACE_CACHE_V,
        "epoch": state["epoch"],
        "external": model["external"],
        "iopt": model.get("iopt"),
        "parmod": (None if parmod is None
                   else [round(float(v), 9) for v in parmod]),
        "frame": frame,
        "internal": internal,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("ascii")).hexdigest()[:20]


def trace_cache_path(models_dir, key: str) -> Path:
    from .render import TRACE_DIRNAME
    return Path(models_dir) / TRACE_DIRNAME / f"{key}.npz"


def save_trace(path, traced: dict, frame: str) -> int:
    """Persist a trace_all result. Returns bytes written.

    'ecef' is dropped for every frame but 'ecef' itself: it is a cross-check
    array no exporter reads (export_fieldlines takes traced['export']) and it
    is a third of the file. In frame 'ecef' the two ARE the same array, so it
    is rebuilt on load.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays = {"xyz": traced["xyz"], "export": traced["export"],
              "seed_gsm": traced["seed_gsm"], "seed_sm": traced["seed_sm"],
              # |B| per vertex drives the line colour (fieldlines.brightness_from_b).
              # It MUST ride in the cache: without it the parallel path silently
              # falls back to flat topology colour while the serial path writes the
              # graded one, and the two executors stop producing the same bytes.
              # Caught by test_parallel_matches_serial_byte_for_byte, 2026-09-16.
              "bmag_nt": traced["bmag_nt"],
              "kind": np.asarray(traced["kind"], dtype="U8")}
    if frame != "ecef":
        arrays["ecef"] = traced["ecef"]
    # NOT sort_keys: 'counts' is repr()'d straight into a FileLines.vlc comment
    # line ("classification {'closed': 121, 'open_n': ...}"), so its key order
    # is part of the exported bytes. json round-trips insertion order.
    arrays["meta"] = np.asarray(json.dumps(
        {"epoch": traced["epoch"], "frame": traced["frame"],
         "internal": traced["internal"], "counts": traced["counts"],
         "n_failed": traced["n_failed"],
         "elapsed_s": round(float(traced["elapsed_s"]), 3)}))
    tmp = path.with_name(path.name + ".tmp.npz")
    with open(tmp, "wb") as fh:
        np.savez(fh, **arrays)                 # uncompressed: this is scratch
    os.replace(tmp, path)
    return path.stat().st_size


def load_trace(path) -> dict:
    """Rebuild a trace_all() result from its .npz. Exact float64 round-trip."""
    with np.load(Path(path), allow_pickle=False) as z:
        out = {"xyz": z["xyz"], "export": z["export"],
               "seed_gsm": z["seed_gsm"], "seed_sm": z["seed_sm"],
               "kind": [str(k) for k in z["kind"]]}
        if "bmag_nt" in z.files:               # absent in caches written before D3
            out["bmag_nt"] = z["bmag_nt"]
        out["ecef"] = z["ecef"] if "ecef" in z.files else out["export"]
        out.update(json.loads(str(z["meta"])))
    return out


def load_trace_topology(path) -> dict:
    """Only what break_mask() reads: the GSM vertices and the classification.

    A third of the .npz and none of the JSON, which matters because the break
    pass walks EVERY traced epoch - the export bake and the ECEF cross-check
    are three more (200, 64, 3) float64 arrays nobody looks at here.
    """
    with np.load(Path(path), allow_pickle=False) as z:
        return {"xyz": z["xyz"], "kind": [str(k) for k in z["kind"]]}


def break_masks(plan, models_dir, trace_keys) -> tuple[dict, list]:
    """Every field-line pair's identity-break mask, from the Stage A cache.

    Stage A has already traced every epoch the plan needs, so the whole break
    question is answerable BEFORE Stage B starts - which is what keeps the
    items independent: item j needs to know what broke in pair j-1, and it
    would otherwise have to have executed j-1 to find out.

    Returns ({plan item index -> the fade-in mask that item needs}, [one
    break_record() per pair, in emission order]) - the second is what the
    manifest's fieldline_churn block is aggregated from.
    """
    from .model.fieldlines import break_mask, break_record

    fl = [(i, it) for i, it in enumerate(plan.items) if it.layer == "fieldlines"]
    cache: dict[int, dict] = {}

    def topo(k: int) -> dict:
        if k not in cache:
            cache.clear()          # pairs are consecutive: one entry is enough
            cache[k] = load_trace_topology(
                trace_cache_path(models_dir, trace_keys[k]))
        return cache[k]

    fade_in: dict[int, np.ndarray] = {}
    records: list = []
    carry = None
    for i, it in fl:
        if carry is not None:
            fade_in[i] = carry
        if it.src_k is None:                       # the resting frame: no pair
            records.append(None)
            carry = None
            continue
        # topo(src) FIRST so the one-entry cache holds src while dst loads;
        # dst then survives as the next pair's src.
        prev_topo = topo(it.src_k)
        mask = break_mask(prev_topo, topo(it.dst_k))
        records.append(break_record(mask))
        carry = mask["broke"]
    return fade_in, records


# --- worker entry points (module level: Windows spawn re-imports this file) ---

def _init_worker(payload_path: str, log_level: int) -> None:
    """Load the shared inputs ONCE per process and quieten the child's logging.

    The state list, the plan and the trace-cache keys are needed by every task
    and change for none of them; shipping them per task would pickle the whole
    event hundreds of times over. Children log at WARNING because 32 workers
    echoing every traced epoch drowns the parent's own progress line.
    """
    logging.basicConfig(level=log_level)
    logging.getLogger().setLevel(log_level)
    with open(payload_path, "rb") as fh:
        _W.update(pickle.load(fh))


def _worker_trace(k: int) -> dict:
    """Stage A: trace one epoch into the cache. Returns metadata only."""
    from .model.fieldlines import trace_all

    t0 = time.perf_counter()
    models_dir, frame = _W["models_dir"], _W["plan"].frame
    path = trace_cache_path(models_dir, _W["trace_keys"][k])
    if path.exists():                       # a --resume, or a repeated epoch
        return {"k": k, "cached": True, "bytes": path.stat().st_size,
                "elapsed_s": 0.0}
    traced = trace_all(_W["states"][k], frame=frame)
    n_bytes = save_trace(path, traced, frame)
    return {"k": k, "cached": False, "bytes": n_bytes,
            "n_failed": traced["n_failed"],
            "elapsed_s": round(time.perf_counter() - t0, 2)}


def _worker_item(item_index: int, broke_prev=None) -> dict:
    """Stage B: run one plan item to its final filename + receipt.

    broke_prev rides on the TASK rather than in the shared payload: it is 200
    booleans, the payload is written before Stage A has traced anything, and a
    per-task argument keeps the item self-describing.
    """
    from .render import run_item

    t0 = time.perf_counter()
    plan, states, models_dir = _W["plan"], _W["states"], _W["models_dir"]
    item = plan.items[item_index]

    def get_trace(k: int) -> dict:
        return load_trace(trace_cache_path(models_dir, _W["trace_keys"][k]))

    rows = run_item(item, plan, states, models_dir, get_trace=get_trace,
                    broke_prev=broke_prev)
    return {"key": item.key, "layer": item.layer, "files": rows,
            "bytes": sum(r["bytes"] for r in rows),
            "elapsed_s": round(time.perf_counter() - t0, 2)}


def _worker_wind_write(name: str, rows, next_rows, comments,
                       index: int) -> dict:
    """Stage A (pool side): format and write one wind frame. Pure formatting."""
    from .render import _write_wind_file, write_receipt

    models_dir = _W["models_dir"]
    n_bytes = _write_wind_file(Path(models_dir) / name, rows, next_rows,
                               comments, index, _W["plan"].coord_digits)
    write_receipt(models_dir, f"wind_{index:04d}",
                  [{"name": name, "bytes": n_bytes}], [], index)
    return {"key": f"wind_{index:04d}", "bytes": n_bytes}


# --- the parent ---------------------------------------------------------------

def _pin_blas_to_one_thread() -> None:
    """One BLAS thread per worker. Set BEFORE the pool exists - children inherit."""
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, "1")


def _write_payload(models_dir, plan, states, trace_keys) -> Path:
    from .render import WORK_DIRNAME

    path = Path(models_dir) / WORK_DIRNAME / PAYLOAD_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        pickle.dump({"plan": plan, "states": states, "trace_keys": trace_keys,
                     "models_dir": str(models_dir)}, fh,
                    protocol=pickle.HIGHEST_PROTOCOL)
    return path


def _wind_writer(pool, models_dir, resume: bool, pending: list, limit: int):
    """An _EventWindStream.write_fn that submits the write to the pool.

    Everything the stream needs to keep its own books - the file name, the cue
    time, the clip and gap statistics - is decided by the time this is called,
    and the row blocks it hands over are final (each snapshot's anti-streak
    mask is applied before it is ever written). So moving the formatting off
    the parent cannot change a number in the manifest. Returns 0 because the
    byte count arrives later; only the progress log reads it.
    """
    from .render import read_receipt, receipt_bytes

    def write(path, rows, next_rows, comments, index) -> int:
        key = f"wind_{index:04d}"
        if resume:
            rec = read_receipt(models_dir, key)
            if rec is not None:
                return receipt_bytes(rec)
        for fut in [f for f in pending if f.done()]:
            fut.result()                   # re-raise a worker failure here
            pending.remove(fut)
        while len(pending) >= limit:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                fut.result()               # re-raise a worker failure here
                pending.remove(fut)
        pending.append(pool.submit(_worker_wind_write, Path(path).name, rows,
                                   next_rows, list(comments), int(index)))
        return 0

    return write



def _tick(progress, done, n, per, phase):
    """Call a progress callback that may take 3 or 4 positional arguments.

    Four-argument callbacks (progress_file_writer) see every phase; the
    historical three-argument callbacks only ever counted ITEMS, so they
    keep that contract and are not told about the trace phase at all.
    """
    try:
        progress(done, n, per, phase)
    except TypeError:
        if phase == "write":
            progress(done, n, per)

def execute(plan, states, models_dir, *, workers: int, resume: bool = False,
            progress=None) -> dict:
    """Run a RenderPlan across `workers` processes. Same tree as serial.

    Returns the same dict render.execute_serial does, so render_event does not
    know - and must not care - which backend built its tree.
    """
    from .render import (_EventWindStream, _finish, read_receipt,
                         receipt_bytes, wind_sheath)

    models_dir = Path(models_dir)
    n = plan.n_frames
    t_start = time.time()
    _pin_blas_to_one_thread()

    trace_keys = {k: trace_key(states[k], plan.frame) for k in plan.trace_epochs}
    payload = _write_payload(models_dir, plan, states, trace_keys)

    done_keys = {it.key for it in plan.items
                 if resume and read_receipt(models_dir, it.key) is not None}
    todo = [(i, it) for i, it in enumerate(plan.items)
            if it.key not in done_keys]
    n_bytes = sum(receipt_bytes(read_receipt(models_dir, key) or {})
                  for key in done_keys)
    wind = None
    wind_s = 0.0
    wind_stats = None
    fl_records: list = []
    pending_wind: list = []

    pool = ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                               initargs=(str(payload), logging.WARNING))
    try:
        # ---- Stage A: every trace at once, and the wind beside them ---------
        trace_futs = [pool.submit(_worker_trace, k) for k in plan.trace_epochs]
        # The parent's wind loop below blocks behind the trace futures (its
        # writes queue in the same FIFO pool), so on a T96 storm it cannot tick
        # progress for the whole trace phase (Bastille 2026-09-14: 38 min, 260
        # traces done, no tick). A pump thread reports fut.done() counts instead.
        pump_stop = threading.Event()

        def _pump():
            n_tr = len(trace_futs)
            while not pump_stop.wait(5.0):
                d = sum(1 for f in trace_futs if f.done())
                if d and progress:
                    _tick(progress, min(d, n_tr - 1), n_tr,
                          (time.time() - t_start) / d, "trace")

        pump = threading.Thread(target=_pump, name="trace-progress", daemon=True)
        if progress:
            pump.start()
        if plan.wind is not None:
            wind = _EventWindStream(
                models_dir, frame=plan.frame, sub=plan.wind.sub,
                stream_s=plan.wind.stream_s, step_seconds=plan.step_seconds,
                coord_digits=plan.coord_digits,
                write_fn=_wind_writer(pool, models_dir, resume, pending_wind,
                                      max(1, WIND_QUEUE_PER_WORKER * workers)))
            t_w = time.time()
            # Identical driving order to the serial path - wind_sheath(k),
            # start on step 0, advance on every step but the last - so the
            # pool, the recycle masks and every statistic match exactly. This
            # is also the parent's ONLY geopack use, which is what keeps the
            # single-gateway rule true in this process.
            for k in range(n):
                sheath = wind_sheath(states[k])
                if k == 0:
                    wind.start(states[k], sheath)
                if k < n - 1:
                    wind.advance(states[k], sheath, k)
            # Drain the two buffered snapshots HERE, while the pool is still
            # alive - finish() writes the tail of the series, and those writes
            # go to workers like every other one.
            wind_stats = wind.finish()
            wind_s = time.time() - t_w

        # Stage A is where a T96 render spends its time, so tick the progress
        # callback per completed trace (phase 'trace'); Stage B ticks 'write'.
        pump_stop.set()
        if pump.is_alive():
            pump.join(timeout=6.0)
        traced_bytes = 0
        n_traces = len(trace_futs)
        for t_done, fut in enumerate(as_completed(trace_futs), 1):
            traced_bytes += fut.result()["bytes"]
            if progress:
                _tick(progress, t_done, n_traces,
                      (time.time() - t_start) / max(t_done, 1), "trace")
        log.info("stage A: %d traces (%.1f MB cached), wind advected in %.1f s "
                 "-> %.1f s wall", len(trace_futs), traced_bytes / 1e6, wind_s,
                 time.time() - t_start)

        # Every field-line pair's identity-break mask, decided here from the
        # completed trace cache - INCLUDING the pairs whose items a --resume
        # will skip, so the churn statistics do not depend on how the build
        # was interrupted.
        fade_in, fl_records = break_masks(plan, models_dir, trace_keys)

        # ---- Stage B: every remaining item, submitted in plan order ---------
        item_futs = [pool.submit(_worker_item, i, fade_in.get(i))
                     for i, _it in todo]
        done_ticks = 0
        for (_i, item), fut in zip(todo, item_futs):
            n_bytes += fut.result()["bytes"]
            if item.layer == "boundaries" or "boundaries" not in plan.layers:
                done_ticks += 1
                if progress:
                    per = (time.time() - t_start) / max(done_ticks, 1)
                    _tick(progress, min(done_ticks, n), n, per, "write")

        for fut in pending_wind:
            fut.result()
    finally:
        # wait=True, deliberately, on BOTH the success and the failure path. Every queued
        # task is cancelled first, so the wait is bounded by the one task each worker is
        # already running (a trace, ~20 s; an item write, seconds). With wait=False the
        # Python 3.8 executor (before the 3.9 rewrite, bpo-39205) deadlocks at interpreter
        # exit with its idle workers still alive - measured 2026-09-22 on a Digistar-style
        # 3.8 venv: a look-back that had rendered, published and repointed its shims never
        # exited, the kickoff never posted REBUILT, and RUNNING.lock stayed held for good;
        # a render that FAILED on its first epoch did the same for 20+ minutes because its
        # whole queue kept running.
        try:
            pool.shutdown(wait=True, cancel_futures=True)
        except TypeError:                     # Python 3.8: no cancel_futures - by hand
            for f in (list(locals().get("trace_futs", [])) + list(locals().get("item_futs", []))
                      + list(pending_wind)):
                f.cancel()
            pool.shutdown(wait=True)

    if wind is not None:
        # The writes went to workers, so the wind's byte total comes from the
        # receipts they left rather than from the stream's own counter.
        wind.bytes = sum(
            receipt_bytes(read_receipt(models_dir, f"wind_{i:04d}") or {})
            for i in range(len(wind.files)))
    from .model.fieldlines import churn_stats

    out = _finish(plan, wind, wind_s, n_bytes, len(done_keys), t_start,
                  stats=wind_stats, churn=churn_stats(fl_records))
    log.info("parallel build: %d items + %d traces on %d workers in %.1f s "
             "(%.1f MB)", len(todo), len(plan.trace_epochs), workers,
             time.time() - t_start, out["bytes"] / 1e6)
    return out
