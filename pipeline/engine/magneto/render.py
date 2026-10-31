"""The render orchestrator: one state object in, one Digistar Models tree out.

This is the module STATE_SCHEMA.md's central claim rests on - "same renderer,
archived inputs". It takes a schema-1 state and renders it, and there is
deliberately NO branch anywhere below on `state["tier"]` or on
`state["event_id"]`. A LIVE state, a CACHED state and an EVENT frame all take
exactly the same path; the only thing that differs is what the state says, and
the only thing that differs in the OUTPUT is what the state's own provenance
comments say (model.state_comments, stamped into every file).

The one place the live and event paths genuinely diverge is the aurora, and
even that is data-driven rather than tier-driven: the renderer draws the
OVATION grid when it is HANDED one and the modeled Kp oval when it is not.
OVATION is not archived, so an event physically cannot supply one and lands on
the modeled oval by construction - captioned as modeled, every time, per
zhang_paxton.label().

EXPORT FRAME AND UNITS. Everything below goes out through magneto.geo's frame
helper, in LIGHT YEARS (Digistar reads a 'coordsys RIGHT' file's numbers as
light years - nothing on the dome side scales them), and in 'j2000' by default:
geocentric equatorial J2000, the same axes as the Digistar WCS, so the dome
holder is just 'Earth add <holder>' + 'standardSpace on' with no attitude at
all. The structure is still sun-fixed - every frame is baked at its own epoch,
so the magnetopause nose points where the Sun really was - and what moves is
real physics: dipole-tilt wobble in the field lines and the aurora sweeping
underneath as the Earth turns. frame='gsm' is the raw physics frame (its holder
needs an externally driven attitude) and frame='ecef' the Earth-fixed bake, in
which an event replay spins the whole magnetosphere with the planet. The frame
is recorded in the manifest and in every file's own header comments, because
the three need different things from the dome holder.

Cost model (measured on the reference box, Ryzen laptop):

    boundaries   milliseconds        (two bisected meshes)
    field lines  ~17-18 s  (t89)     ~2 min (t96) - the whole cycle budget
    aurora       ~2-5 s              33120 dots + 4680 curtains, x2 for morph
    wind         tens of seconds     30 frames x 10000 parcels
    L1 marker    milliseconds

which is why the live daemon re-bakes wind on its own 5-minute cadence and the
aurora only when OVATION publishes a new observation, while the field lines go
every cycle. Everything here is single-THREADED: geopack keeps its epoch in
module globals and model.recalc_for_state() is the only call site. Event
renders may still spread themselves across PROCESSES - see magneto/plan.py for
the plan every executor consumes and magneto/parallel.py for why separate
interpreters leave the one-gateway rule intact. The live cycle below never
does: it is a single state and the trace is its whole cost anyway.

Continuity across cycles - the three things that must be carried in memory
rather than recomputed - is explicit in the signature:

    prev_state   -> the parametric morph source for boundaries
    prev_trace   -> the parametric morph source for field lines (re-tracing the
                    previous epoch would double the most expensive layer)
    prev_break   -> which field-line rows broke topology in the PREVIOUS pair,
                    so this cycle can fade them back in where the last one
                    faded them out (model.fieldlines.break_mask)
    wind_pool    -> the parcel pool, so parcels advect continuously instead of
                    teleporting back to the launch plane every cycle

render_cycle() returns them again under "_carry" so the caller can feed them
straight back in.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from dsexport import validate, write_dots_vlc, write_lines_vlc
from dsexport.vla import coord_formatter, val_formatter
from dsexport import ds as dsds
from dsexport.publish import publish, write_into
from livecore.state import write_state_atomic

from . import geo
from . import plan as plan_mod
from . import paths as _paths
from . import presets as _presets
from .model import recalc_for_state, state_comments
from .model import aurora as aurora_mod
from .model import wind as wind_mod
from .model import zhang_paxton as zp
from .model import fieldlines as fieldlines_mod
from .model.boundaries import (bow_shock_radius, drawn_radius_lut,
                               shock_compression,
                               export_boundaries, magnetopause_radius,
                               radius_lut)
from .model import boundaries as _bnd
from .model import imf as imf_mod
from .model import plasmasphere as plasmasphere_mod
from .model import ringcurrent as ringcurrent_mod
from .model.fieldlines import export_fieldlines, trace_all
from .model.fieldpulses import export_fieldpulses

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
ALL_LAYERS = ("boundaries", "fieldlines", "aurora", "wind", "l1",
              "ringcurrent", "plasmasphere", "imf")
DEFAULT_FRAME = geo.DEFAULT_FRAME
DEFAULT_COORD_DIGITS = geo.DEFAULT_COORD_DIGITS

# Show-look decision (David, 2026-08-12): the curtain look comes from
# Digistar's OWN planet aurora (dsexport.ds.write_native_aurora_ds), not from
# 4,680 exported polylines. The curtain writer stays; opt back in with
# --with-curtains (events) or with_curtains=True (render_cycle).
EMIT_AURORA_CURTAINS = False

# Wind bake geometry. n_frames is part of the STABLE FILENAME contract with the
# live loader (WindParticles_0000 .. _0029), so it is a constant, not a knob the
# daemon turns per cycle.
N_WIND_FRAMES = 30
WIND_FRAME_DT_S = 10.0

# Layer -> (files it owns, .vlc kind) for the manifest and the .ds players.
LAYER_KIND = plan_mod.LAYER_KIND

MANIFEST_NAME = dsds.DS_LIVE_MANIFEST_NAME
STATE_NAME = "current_state.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def check_state(state: dict) -> None:
    """Reject anything that is not the frozen schema (STATE_SCHEMA.md rule 1)."""
    if not isinstance(state, dict):
        raise TypeError(f"state must be a dict, got {type(state).__name__}")
    got = state.get("schema")
    if got != SCHEMA_VERSION:
        raise ValueError(f"state schema {got!r} - this renderer implements "
                         f"schema {SCHEMA_VERSION} only (see STATE_SCHEMA.md)")
    if (state.get("tier") == "EVENT") != (state.get("event_id") is not None):
        raise ValueError("STATE_SCHEMA invariant 2: tier EVENT <=> event_id set")


# --- individual layers ------------------------------------------------------------

def _aurora_ovation(cur_grid, prev_grid, epoch, out_dir, prev_epoch=None,
                    frame=DEFAULT_FRAME,
                    with_curtains: bool = EMIT_AURORA_CURTAINS,
                    coord_digits: int = DEFAULT_COORD_DIGITS) -> dict:
    """OVATION layers, parametric previous -> current."""
    if prev_grid is None:
        # First cycle after a restart: morph the grid into itself. A static
        # aurora for one cycle is honest; inventing a previous frame is not.
        prev_grid, prev_epoch = cur_grid, epoch
    return aurora_mod.export_aurora(cur_grid, prev_grid, epoch, out_dir,
                                    prev_epoch=prev_epoch, frame=frame,
                                    with_curtains=with_curtains,
                                    coord_digits=coord_digits)


def _aurora_modeled(state: dict, prev_state, out_dir,
                    frame=DEFAULT_FRAME,
                    with_curtains: bool = EMIT_AURORA_CURTAINS,
                    coord_digits: int = DEFAULT_COORD_DIGITS) -> dict:
    """Kp-driven modeled oval on OVATION's own topology, parametric prev -> cur.

    Not aurora.export_aurora(): that writer's header states the layer came from
    the SWPC OVATION product, which would be a lie on an event replay. The
    layers themselves are aurora.oval_layer / aurora.curtain_layer unchanged -
    identical cell topology, identical colours, identical dayside dimming - so
    the only thing that differs from the live path is the grid and the honest
    provenance block from zhang_paxton.comments().
    """
    out_dir = Path(out_dir)
    cfmt, vfmt = coord_formatter(coord_digits), val_formatter(coord_digits)
    epoch = state["epoch"]
    kp = float(state["kp"]["value"])
    cur_grid = zp.oval_probability_grid(kp, epoch)
    if prev_state is None:
        prev_grid, prev_kp, prev_epoch = cur_grid, kp, epoch
    else:
        prev_kp = float(prev_state["kp"]["value"])
        prev_epoch = prev_state["epoch"]
        prev_grid = zp.oval_probability_grid(prev_kp, prev_epoch)

    comments = state_comments(state, zp.comments(kp, epoch) + [
        "no OVATION grid was supplied for this epoch - OVATION is a nowcast "
        "product and is not archived, so replays use the modeled oval",
        "morph: D/P/L rows = previous grid, V rows = current grid",
        f"previous grid Kp {prev_kp:g} at {prev_epoch}",
        f"terminator dimming x{aurora_mod.DAYSIDE_DIM} where solar zenith < "
        f"{aurora_mod.TERMINATOR_ZENITH_DEG:g} deg at epoch {epoch}",
    ] + geo.coord_comments(coord_digits), frame=frame)

    # Previous rows are built at the PREVIOUS epoch: in the sun-fixed frame the
    # cells themselves move, and that Earth rotation between the two epochs IS
    # the aurora sweeping under a stationary magnetosphere.
    prev_oval = aurora_mod.oval_layer(prev_grid, prev_epoch, frame)

    oval_path = out_dir / "AuroraOval.vlc"
    n_oval = write_dots_vlc(oval_path, prev_oval,
                            next_points=aurora_mod.oval_layer(cur_grid, epoch,
                                                              frame),
                            comments=comments, coord_fmt=cfmt, val_fmt=vfmt)
    curtain_path = None
    n_rows = 0
    n_curtains = 0
    if with_curtains:
        prev_curtains = aurora_mod.curtain_layer(prev_grid, prev_epoch, frame)
        curtain_path = out_dir / "AuroraCurtains.vlc"
        n_rows = write_lines_vlc(
            curtain_path, prev_curtains,
            next_polylines=aurora_mod.curtain_layer(cur_grid, epoch, frame),
            comments=comments, coord_fmt=cfmt, val_fmt=vfmt)
        n_curtains = aurora_mod.N_CURTAIN_CELLS
    log.info("aurora (modeled Kp %g): %d oval dots, %d curtain polylines -> %s",
             kp, n_oval, n_curtains, out_dir)
    return {"oval": oval_path, "curtains": curtain_path, "n_oval": n_oval,
            "n_curtains": n_curtains, "n_curtain_rows": n_rows,
            "modeled": True, "label": zp.label(kp), "kp": kp,
            "prev_kp": prev_kp, "prev_epoch": prev_epoch}


_TIMELINE_CACHE: dict = {}


def _event_timeline(event_id):
    """The full stored timeline for an event, cached per process.

    The plasmasphere needs Kp over the 24 h BEFORE each step, which reaches back
    past the start of most render windows. Cached because every step asks, and in
    a worker process the answer never changes.
    """
    if event_id in _TIMELINE_CACHE:
        return _TIMELINE_CACHE[event_id]
    try:
        from .events import load_event
        tl = list(load_event(event_id))
    except Exception:                     # a synthetic or unregistered event
        tl = []
    _TIMELINE_CACHE[event_id] = tl
    return tl


# acos(-1/e) for the Slavin & Holzer conic: the ray angle beyond which a ray
# from Earth never meets the shock at all (149.5 deg at e = 1.16).
BS_ASYMPTOTE_RAD = float(np.arccos(-1.0 / _bnd.BS_ECCENTRICITY))


def wind_sheath(state: dict, tilt_rad=None) -> wind_mod.Sheath:
    """THE injection site: the real boundary surfaces -> the wind's flow field.

    wind.py never imports the boundary module (it takes its geometry injected),
    so this is where boundaries.magnetopause_radius and bow_shock_radius, bound
    to THIS state, become the obstacle the parcels steer around and the shock
    they cross. Both are sampled into radius tables here because the sheath
    steering evaluates them for 10 000 parcels on every advection substep -
    bisecting geopack per parcel is not on the table.

    The dipole tilt goes in too: it is what orients the two cusp funnels, so
    the funnels lean with the epoch exactly as the field lines do.
    """
    if tilt_rad is None:
        tilt_rad = recalc_for_state(state)
    # The DRAWN magnetopause (Lin hybrid), azimuth-averaged - not the Shue
    # reference, which sits ~4% inside it and let parcels flow through the
    # visible shell. See boundaries.drawn_radius_lut for why it is 1-D.
    th, mp = drawn_radius_lut(state)
    _th, bs = radius_lut(bow_shock_radius, state)
    bs_r = np.maximum(bs, mp + 0.25)
    return wind_mod.Sheath(theta=th, mp_r=mp, mp_dr=np.gradient(mp, th),
                           bs_r=bs_r, bs_dr=np.gradient(bs_r, th),
                           # The Rankine-Hugoniot jump the parcels cross, from the
                           # same Mach number the drawn shock's standoff uses.
                           compression=shock_compression(state),
                           # Past the Slavin & Holzer asymptote the shock radius is
                           # clamped, not computed, so the jump is faded out there.
                           bs_theta_max_rad=BS_ASYMPTOTE_RAD,
                           tilt_rad=float(tilt_rad))


def _render_wind(state: dict, out_dir, pool, n_frames: int, frame_dt_s: float,
                 frame=DEFAULT_FRAME,
                 coord_digits: int = DEFAULT_COORD_DIGITS):
    """Bake the parcel stream against the REAL magnetopause + bow shock.

    The pool is threaded through cycles by the caller. Re-creating it every
    cycle would teleport all 10000 parcels back to a fresh random draw on the
    launch plane once a minute, which reads as a strobe on the dome.
    """
    out_dir = Path(out_dir)
    sheath = wind_sheath(state)

    fresh = pool is None
    if fresh:
        pool = wind_mod.new_pool(state, sheath)
    frames, _recycled, _speeds = wind_mod.build_frames(
        state, sheath, n_frames=n_frames, frame_dt_s=frame_dt_s, pool=pool)

    recalc_for_state(state)          # one rotation for the whole stack
    pts = geo.gsm_re_to_frame(np.stack([f[:, :3] for f in frames]), frame)
    rows = [np.column_stack([pts[k], frames[k][:, 3:7]]) for k in range(n_frames)]

    pool_note = ("parcel pool created this cycle" if fresh else
                 "parcel pool carried from the previous cycle - parcels "
                 "advect continuously across polls")
    base = state_comments(state, wind_mod.stream_comments(
        state, sheath, len(rows[0]), frame_dt_s) + [
        f"{n_frames} frames ({(n_frames - 1) * frame_dt_s:g} s of stream)",
        f"{int((pool.mode == 1).sum())} parcels are in a cusp descent in the "
        f"last baked frame",
        pool_note,
    ] + geo.coord_comments(coord_digits), frame=frame)
    paths = []
    for k in range(n_frames):
        nxt = rows[k + 1] if k < n_frames - 1 else None
        tail = (f"frame {k:04d} of {n_frames}, V rows = frame {k + 1:04d}"
                if nxt is not None else
                f"frame {k:04d} of {n_frames}, final frame, loop closure "
                "handled by the .ds player")
        path = out_dir / f"WindParticles_{k:04d}.vlc"
        write_dots_vlc(path, rows[k], next_points=nxt, comments=base + [tail],
                       coord_fmt=coord_formatter(coord_digits),
                       val_fmt=val_formatter(coord_digits))
        paths.append(path)
    log.info("wind: %d frames x %d parcels -> %s", n_frames, len(rows[0]), out_dir)
    return paths, pool


def _render_l1(state: dict, out_dir, frame=DEFAULT_FRAME,
               coord_digits: int = DEFAULT_COORD_DIGITS) -> Path | None:
    """L1 spacecraft marker, or None when the state carries no L1 position.

    l1_gse_re is nullable in the schema (the ephemerides product can be
    missing). A marker at a guessed position would be worse than no marker, so
    this returns None and the manifest records the absence.
    """
    if not state.get("propagation", {}).get("l1_gse_re"):
        log.warning("no l1_gse_re in state %s - skipping L1Marker",
                    state["epoch"])
        return None
    path = Path(out_dir) / "L1Marker.vlc"
    write_dots_vlc(path, wind_mod.l1_marker(state, frame),
                   coord_fmt=coord_formatter(coord_digits),
                   val_fmt=val_formatter(coord_digits),
                   comments=state_comments(state, [
                       f"L1 monitor {state['source']} at GSE "
                       f"{state['propagation']['l1_gse_re']} R_E",
                       f"propagation lead {state['propagation'].get('lead_s')} s, "
                       f"arrival {state['propagation'].get('arrival')}",
                   ] + geo.coord_comments(coord_digits), frame=frame))
    return path


# --- one cycle ----------------------------------------------------------------------

def render_cycle(state: dict, prev_state, out_models_dir, ovation_grid=None,
                 prev_ovation_grid=None, wind_pool=None, *, prev_trace=None,
                 prev_break=None, trace=None,
                 out_scripts_dir=None, cycle: int = 0, layers=None,
                 n_wind_frames: int = N_WIND_FRAMES,
                 wind_frame_dt_s: float = WIND_FRAME_DT_S,
                 now=None, note: str | None = None, mode: str | None = None,
                 armed: bool = False, frame=DEFAULT_FRAME,
                 with_curtains: bool = EMIT_AURORA_CURTAINS,
                 with_shells: bool = False,
                 with_pulses: bool = False,
                 with_ring: bool = False,
                 with_plasmasphere: bool = False,
                 with_imf: bool = False,
                 pulse_step: int = 0,
                 coord_digits: int = DEFAULT_COORD_DIGITS) -> dict:
    """Render one state into a Digistar Models tree. Returns the manifest.

    out_models_dir      where the .vla/.vlc/.json land (a staging Models/ when
                        called under dsexport.publish, which is the normal way)
    out_scripts_dir     where the .ds lands; defaults to the sibling Scripts/
    ovation_grid        parse_ovation() dict or bare (360, 181) grid; None ->
                        the modeled Kp oval (see the module docstring)
    trace               a trace_all() result for THIS state, computed
                        elsewhere (the event executor traces every epoch up
                        front, in parallel). When given, the field-line layer
                        skips its own trace_all; the trace's epoch and export
                        frame are asserted against this cycle's, because a
                        mismatched trace would silently bake the wrong pose.
                        The live path never passes it.
    layers              subset of ALL_LAYERS to (re-)render this cycle. The
                        daemon skips 'wind' and 'aurora' on most cycles and
                        relies on publish(carry_forward=True) to keep the
                        previous cycle's files in the tree.
    frame               'j2000' (default - the Digistar WCS axes), 'gsm' or
                        'ecef'. Threaded into every layer and recorded in the
                        manifest and in every file's own header comments,
                        because each needs a different dome holder setup.
    coord_digits        significant figures for the light-year coordinates: 9
                        (default, float32-exact - what Digistar cooks to) or 17
                        (full float64 round-trip). Threaded into every science
                        layer; EarthFrameCheck.vla is never affected.

    The returned dict is the live_manifest.json contents PLUS a "_carry" key
    holding this cycle's field-line trace and parcel pool - the two objects the
    next cycle needs in memory. "_carry" is stripped before the manifest is
    written to disk, so what lands in Models/ stays JSON.
    """
    check_state(state)
    t_cycle = time.time()
    now = now or _utcnow()
    frame = geo.check_frame(frame)
    want = set(ALL_LAYERS if layers is None else layers)
    unknown = want - set(ALL_LAYERS)
    if unknown:
        raise ValueError(f"unknown layers {sorted(unknown)}; "
                         f"known: {list(ALL_LAYERS)}")

    models = Path(out_models_dir)
    models.mkdir(parents=True, exist_ok=True)
    scripts = Path(out_scripts_dir) if out_scripts_dir is not None \
        else models.parent / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)

    timings: dict[str, float] = {}
    written: list[Path] = []
    detail: dict = {}

    # THE recalc for this cycle. Every layer below re-enters the same gateway
    # for its own epoch (boundaries walk previous -> current), and all of them
    # leave geopack sitting on THIS state's epoch when they are done.
    t0 = time.time()
    tilt_rad = recalc_for_state(state)
    timings["recalc"] = time.time() - t0

    if "boundaries" in want:
        t0 = time.time()
        res = export_boundaries(state, prev_state, models, frame=frame,
                                coord_digits=coord_digits)
        timings["boundaries"] = time.time() - t0
        written += [res[k]["path"] for k in ("MagnetopauseShell", "BowShockShell")]
        detail["boundaries"] = {
            "magnetopause_nose_re": res["nose_re"]["Magnetopause"],
            "bow_shock_nose_re": res["nose_re"]["BowShock"],
            "parametric": res["parametric"],
        }

    supplied_trace, trace = trace, prev_trace
    if "fieldlines" in want:
        t0 = time.time()
        if supplied_trace is None:
            trace = trace_all(state, frame=frame)
        else:
            if supplied_trace.get("epoch") != state["epoch"]:
                raise ValueError(
                    f"supplied trace is for epoch {supplied_trace.get('epoch')!r}, "
                    f"this cycle is {state['epoch']!r}")
            if supplied_trace.get("frame") != frame:
                raise ValueError(
                    f"supplied trace is baked in frame "
                    f"{supplied_trace.get('frame')!r}, this cycle exports {frame!r}")
            trace = supplied_trace
        res = export_fieldlines(trace, prev_trace, state,
                                models / "FieldLines.vlc",
                                broke_prev=prev_break,
                                coord_digits=coord_digits)
        # The crossfade's continuity across POLLS: this cycle's break mask is
        # next cycle's fade-in list, so it rides home in _carry beside the
        # trace it was computed from.
        if with_pulses:
            pres = export_fieldpulses(
                trace, prev_trace, pulse_step,
                None if prev_trace is None else pulse_step - 1,
                models / "FieldPulses.vlc", coord_digits=coord_digits)
            written.append(pres["path"])
            detail["fieldpulses"] = {"dots": pres["dots"], "n_hidden": pres["n_hidden"]}
        prev_break = (None if res["break"] is None
                      else fieldlines_mod.rows_to_mask(res["break"]["rows"]))
        timings["fieldlines"] = time.time() - t0
        written.append(res["path"])
        detail["fieldlines"] = {"lines": res["lines"], "counts": res["counts"],
                                "n_dead": res["n_dead"],
                                "parametric": res["parametric"],
                                "n_crossfade": res["n_crossfade"],
                                "trace_s": round(trace["elapsed_s"], 2)}

    if "aurora" in want:
        t0 = time.time()
        if ovation_grid is not None:
            res = _aurora_ovation(
                ovation_grid, prev_ovation_grid, state["epoch"], models,
                prev_epoch=(prev_state or state)["epoch"], frame=frame,
                with_curtains=with_curtains, coord_digits=coord_digits)
            src = "ovation"
            label = "SWPC OVATION Prime nowcast"
        else:
            res = _aurora_modeled(state, prev_state, models, frame=frame,
                                  with_curtains=with_curtains,
                                  coord_digits=coord_digits)
            src = "modeled_kp"
            label = res["label"]
        timings["aurora"] = time.time() - t0
        written.append(res["oval"])
        if res["curtains"] is not None:
            written.append(res["curtains"])
        detail["aurora"] = {"source": src, "label": label,
                            "n_oval": res["n_oval"],
                            "n_curtains": res["n_curtains"]}

    if "wind" in want:
        t0 = time.time()
        paths, wind_pool = _render_wind(state, models, wind_pool,
                                        n_wind_frames, wind_frame_dt_s,
                                        frame=frame,
                                        coord_digits=coord_digits)
        timings["wind"] = time.time() - t0
        written += paths
        detail["wind"] = {"n_frames": n_wind_frames,
                          "frame_dt_s": wind_frame_dt_s,
                          "n_parcels": int(len(wind_pool.speed_kms)),
                          "n_in_cusp": int((wind_pool.mode == 1).sum()),
                          "max_penetration_re": round(
                              float(wind_pool.diag["max_penetration_re"]), 4)}

    if "ringcurrent" in want:
        t0 = time.time()
        res = ringcurrent_mod.export_ring_current(
            state, prev_state, models / "RingCurrent.vlc",
            frame=frame, coord_digits=coord_digits)
        timings["ringcurrent"] = time.time() - t0
        if res is not None:
            written.append(res["path"])
        detail["ringcurrent"] = {"written": res is not None,
                                 "symh_nt": None if res is None else res["symh_nt"]}

    if "plasmasphere" in want:
        t0 = time.time()
        # Live has no stored timeline, so Kp_max comes from the state itself -
        # a 24 h maximum it cannot see. Recorded, not hidden: the label says
        # which Kp it used.
        res = plasmasphere_mod.export_plasmasphere(
            state, prev_state, [state], models / "Plasmasphere.vlc",
            frame=frame, coord_digits=coord_digits)
        timings["plasmasphere"] = time.time() - t0
        if res is not None:
            written.append(res["path"])
        detail["plasmasphere"] = {"written": res is not None,
                                  "l_pp": None if res is None else res["l_pp"]}

    if "imf" in want:
        t0 = time.time()
        res = imf_mod.export_imf_lines(
            state, prev_state, wind_sheath(state),
            None if prev_state is None else wind_sheath(prev_state),
            models / "IMFLines.vlc", frame=frame, coord_digits=coord_digits)
        timings["imf"] = time.time() - t0
        written.append(res["path"])
        detail["imf"] = {"merging_mv_m": round(res["merging_mv_m"], 3),
                         "clock_deg": round(res["clock_deg"], 1)}

    if "l1" in want:
        t0 = time.time()
        p = _render_l1(state, models, frame=frame, coord_digits=coord_digits)
        timings["l1"] = time.time() - t0
        if p is not None:
            written.append(p)
        detail["l1"] = {"written": p is not None}

    # The full state travels WITH the content, so a Models/ directory found on
    # a graphics PC can always be traced back to the conditions that made it.
    write_state_atomic(state, models / STATE_NAME)
    written.append(models / STATE_NAME)

    dsds.write_status_ds(state, scripts / dsds.DS_STATUS_NAME, now=now, note=note)

    elapsed = time.time() - t_cycle
    manifest = {
        "schema": SCHEMA_VERSION,
        "cycle": int(cycle),
        "generated": _iso(now),
        "epoch": state["epoch"],
        "tier": state["tier"],
        "event_id": state.get("event_id"),
        "source": state["source"],
        "age_s": state["age_s"],
        "kp": state["kp"]["value"],
        "model": state["model"]["external"],
        "frame": frame,
        "coord_digits": int(coord_digits),
        "dipole_tilt_deg": round(float(np.degrees(tilt_rad)), 4),
        "mode": mode or ("EVENT" if state.get("event_id") else "LIVE"),
        "armed": bool(armed),
        "rendered": True,
        "layers": sorted(want),
        "banner": dsds.status_text(state, now=now, note=note),
        "timings_s": {k: round(v, 3) for k, v in timings.items()},
        "render_s": round(elapsed, 3),
        "files": _file_rows(written),
        "scripts": sorted(p.name for p in scripts.glob("*.ds")),
        "detail": detail,
    }
    write_into(models / MANIFEST_NAME,
               json.dumps(manifest, indent=1, allow_nan=False))
    log.info("cycle %d rendered %s tier %s in %.2f s (%s)", cycle,
             state["epoch"], state["tier"], elapsed,
             " ".join(f"{k} {v:.2f}s" for k, v in timings.items()))
    return dict(manifest, _carry={"trace": trace, "wind_pool": wind_pool,
                                  "break": prev_break})


def talking_points(event_id) -> list[str]:
    """The registry's presenter notes for this event (empty for look-backs / unknown ids)."""
    from .events import EVENTS
    spec = EVENTS.get(str(event_id))
    return [str(t) for t in (getattr(spec, "talk", ()) or ())]


def _file_rows(paths) -> list[dict]:
    """[{name, bytes}] for the manifest, de-duplicated, in name order."""
    rows, seen = [], set()
    for p in paths:
        p = Path(p)
        if p.name in seen:
            continue
        seen.add(p.name)
        try:
            size = p.stat().st_size
        except OSError:                                   # pragma: no cover
            size = None
        rows.append({"name": p.name, "bytes": size})
    return sorted(rows, key=lambda r: r["name"])


def strip_carry(manifest: dict) -> dict:
    """The JSON-safe half of a render_cycle() result."""
    return {k: v for k, v in manifest.items() if k != "_carry"}


# --- curated-event frame series -------------------------------------------------

# (manifest layer, the filename its writer emits) in player order. Derived
# from the plan's own product table so the two cannot drift; the frame series
# lands these under indexed names (Magnetopause_0007.vla) chosen by the plan.
FRAME_LAYER_FILES = tuple(
    pair for layer in plan_mod.LAYER_PRODUCTS.values() for pair in layer)
# Show-look decision (David, 2026-08-12): no L1 marker on the dome. The layer
# still renders with --with-l1 (or layers=EVENT_LAYERS_WITH_L1).
EVENT_LAYERS = ("boundaries", "fieldlines", "aurora",
                "ringcurrent", "plasmasphere", "imf")
EVENT_LAYERS_WITH_L1 = EVENT_LAYERS + ("l1",)
EVENT_STEP_MIN = 10.0             # simulated minutes per event field step
EVENT_STEP_SECONDS = _presets.SHOW.step_seconds   # dome seconds per field step - the SHOW preset (0.8)
SHOW_EXTERNAL_STORM = _presets.external_for("storm") or "t96"   # the --model default
# 288 steps (48 h at 10 min) x 0.8 s = 3.8 dome minutes - inside the 3-6 minute
# budget, and slow enough that a 10-minute jump still reads as motion rather
# than a flicker. Was 1.3 s until 2026-09-14; every package now shares ONE value
# (magneto.presets.SHOW) so the panel's toggles and sliders behave identically.

# --- the wind's own cadence ---------------------------------------------------
# The parcel stream runs on a SEPARATE clock from the storm, and that is a
# deliberate, documented choice rather than an oversight: at the event's own
# rate a 450 km/s parcel covers ~42 R_E per 10-minute step, so a
# physically-timed stream would cross the whole scene between two frames and
# read as noise. Instead each field step advances the stream
# EVENT_WIND_STREAM_S seconds of REAL flow, split into EVENT_WIND_SUB output
# frames. The stream is therefore honest about shape, direction, speed ratios
# and density - and time-compressed ~5x relative to the storm clock. Every wind
# file says so in its header.
EVENT_PRELOAD_WINDOW = 60    # frame objects per layer kept alive ahead of the playhead (dsexport.ds)
EVENT_WIND_SUB = 4                # wind output frames per field step
EVENT_WIND_STREAM_S = 120.0       # seconds of real flow per field step
# Why 4 and not 1: Digistar morphs a parcel's D row STRAIGHT to its V row, but
# a sheath parcel's real path curves around the magnetopause. The chord sags by
# roughly L^2/(8 R); at 30 s per wind frame the fastest parcels move ~3.8 R_E
# and the sag near the flank is ~0.15 R_E, comfortably inside the 0.5 R_E
# no-visible-clipping tolerance. At 2 sub-frames (60 s) it is ~0.6 R_E and
# parcels visibly cut the corner through the boundary. render_event MEASURES
# this on the frames it actually wrote (wind.morph_clip_stats) and reports it.
WIND_CLIP_TOL_RE = 0.5
WIND_CLIP_NEAR_RE = 30.0

# --- the aurora's own cadence -------------------------------------------------
# The event oval is zhang_paxton(Kp) and Kp is a 3-HOURLY index, so emitting a
# 9 MB oval + curtain pair every 10 minutes writes ~18 identical copies of each
# distinct oval. Frames are emitted only when Kp actually changes - with a long
# morph between them - subject to a maximum hold, because in the sun-fixed
# frame the oval's own coordinates rotate with the Earth and a linear morph
# across too much rotation would drag the dots on a chord through the globe.
# 60 minutes = 15 deg of rotation = a 55 km chord sag at 110 km emission
# altitude: invisible at magnetosphere scale. 90+ minutes is not.
EVENT_AURORA_MAX_HOLD_MIN = 60.0
AURORA_LAYERS = ("AuroraOval", "AuroraCurtains")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def select_event_states(event_id: str, step_min: float = 30, window=None,
                        events_root=None) -> list[dict]:
    """Event timeline states at step_min spacing, optionally clipped to window.

    window is (start, stop) as ISO-8601 Z strings (either end may be None).
    Selection walks the timeline and keeps the first state at or after each
    step boundary, so it is correct for any stored cadence and does not skip a
    frame when the timeline has a gap.
    """
    from .events import load_event

    start = stop = None
    if window:
        w0, w1 = (window if not isinstance(window, dict)
                  else (window.get("start"), window.get("stop")))
        start = _parse_iso(w0) if w0 else None
        stop = _parse_iso(w1) if w1 else None

    picked, next_wanted = [], None
    for state in load_event(event_id, events_root):
        t = _parse_iso(state["epoch"])
        if start is not None and t < start:
            continue
        if stop is not None and t > stop:
            break
        if next_wanted is None or t >= next_wanted:
            picked.append(state)
            base = next_wanted if next_wanted is not None else t
            # Advance to the first boundary strictly after this state, so a
            # gap in the timeline costs one frame, not the whole alignment.
            while base <= t:
                base = base + timedelta(minutes=float(step_min))
            next_wanted = base
    return picked


def _disk_title(event_id: str, events_root=None) -> str | None:
    """Manifest title for an event that has no registry entry.

    Dynamic ids (look-backs) resolve by directory, not by registry, so
    dsexport.ds.event_title returns None for them and their real title lives in
    events/<id>/event.json. Nothing else about id resolution differs: the
    renderer already loads any id that has a timeline on disk.
    """
    try:
        from .events import load_manifest
        return load_manifest(event_id, events_root).get("title")
    except Exception:
        return None


def aurora_indices(states: list[dict],
                   max_hold_min: float = EVENT_AURORA_MAX_HOLD_MIN) -> list[int]:
    """Step indices at which the aurora layer emits a frame (the Kp economy).

    A frame is emitted on step 0, on every step where Kp CHANGED, whenever the
    hold since the last emitted frame reaches max_hold_min, and on the last
    step. Everything between is one long morph. See EVENT_AURORA_MAX_HOLD_MIN
    for why the hold is capped even when Kp is flat.

    Now one case of plan._emission_indices, which the field lines' stride
    cadence shares - two cadences, one rule, so they cannot drift apart.
    """
    return plan_mod._emission_indices(states, max_hold_min=max_hold_min)


def resolve_external(external, states, event_id=None, events_root=None) -> str | None:
    """'auto' -> 'ts05' or None by T96's range; validates the other names."""
    ext = str(external).lower() if external else None
    if ext in (None, "t96", "none"):
        return None
    if ext == "auto":
        from .model import ts05 as ts05_mod
        if ts05_mod.t96_in_range(states):
            log.info("%s: driving values inside T96's range - T96", event_id)
            return None
        log.info("%s: outside T96's range (%s) - TS05", event_id,
                 ts05_mod.out_of_range_summary(states))
        return "ts05"
    if ext in ("t89", "ts05"):
        return ext
    raise ValueError(f"external must be None, 't96', 't89', 'ts05' or 'auto', got {external!r}")


def _as_t89(state: dict) -> dict:
    """A copy of an event state forced onto T89 - the fast-preview path.

    Events are T96 by STATE_SCHEMA, at ~115 s/epoch against T89's ~17 s. For a
    preview that is the difference between minutes and hours, so --t89 rewrites
    model.external (and drops parmod, which invariant 3 only requires for T96).
    Everything else about the state, including the driving values the boundary
    and aurora layers use, is untouched.
    """
    out = json.loads(json.dumps(state))
    out["model"] = {"external": "t89", "iopt": int(state["kp"]["iopt"]),
                    "parmod": None}
    return out


def _write_wind_file(path, rows, next_rows, comments, index,
                     coord_digits: int = DEFAULT_COORD_DIGITS) -> int:
    """The default (in-process) wind-file writer. See _EventWindStream.write_fn."""
    write_dots_vlc(path, rows, next_points=next_rows, comments=comments,
                   coord_fmt=coord_formatter(coord_digits),
                   val_fmt=val_formatter(coord_digits))
    return Path(path).stat().st_size


class _EventWindStream:
    """ONE parcel pool advected through a whole event, emitted as its own series.

    The pool is never reset between field steps - that is the requirement: a
    parcel launched during the storm's onset is still in flight when the sudden
    impulse hits, and it is the SAME row in every file. Each field step hands
    the stream that step's state (speed, density -> launch opacity) and that
    step's Sheath (the compressed magnetopause and bow shock), so the flow
    reshapes itself around a magnetosphere that is moving underneath it.

    Files are written two snapshots behind, because the anti-streak rule needs
    both of a snapshot's neighbouring recycle masks before it can decide which
    parcels are hidden in it: snapshot j is hidden for the recycles in (j-1, j)
    AND in (j, j+1), and it is written as the D rows of file j and the V rows
    of file j-1, which must agree.
    """

    def __init__(self, models_dir, frame=DEFAULT_FRAME, sub: int = EVENT_WIND_SUB,
                 stream_s: float = EVENT_WIND_STREAM_S,
                 step_seconds: float = EVENT_STEP_SECONDS,
                 clip_tol_re: float = WIND_CLIP_TOL_RE,
                 coord_digits: int = DEFAULT_COORD_DIGITS,
                 write_fn=None):
        # THE parallelism seam. Everything above _write_pair - the pool, the
        # advection, the recycle masks, the clip/gap statistics - is a strict
        # chain and must stay on the parent's single geopack thread. Formatting
        # 20 000 float rows into ASCII is not: it is a pure function of the two
        # row blocks and the comment list, so it can be handed to a worker.
        # write_fn(path, rows, next_rows, comments, index) -> bytes written (or
        # None if it is going to happen later); the bookkeeping below never
        # depends on the answer.
        self.write_fn = write_fn or _write_wind_file
        self.bytes = 0
        self.dir = Path(models_dir)
        self.frame = geo.check_frame(frame)
        self.sub = max(1, int(sub))
        self.dt_s = float(stream_s) / self.sub
        self.cue_s = float(step_seconds) / self.sub
        self.clip_tol_re = float(clip_tol_re)
        self.coord_digits = int(coord_digits)
        self.pool = None
        self._buf: list[dict] = []
        self.files: list[str] = []
        self.times: list[float] = []
        self.clip = {"n_pairs": 0, "n_clipped": 0, "max_depth_re": 0.0,
                     "p99_depth_re": 0.0, "tol_re": self.clip_tol_re}
        self.gap = {"min_re": float("inf"), "mean_re": 0.0, "p05_re": 0.0,
                    "median_re": 0.0, "n": 0, "n_sheath": 0}
        self.max_penetration_re = 0.0
        self.n_cusp_seen = 0

    # -- internals -----------------------------------------------------------
    def _rows(self, snap, state):
        pos = snap[:, :3]
        if self.frame == geo.FRAME_ECEF:
            recalc_for_state(state)
        return np.column_stack([geo.gsm_re_to_frame(pos, self.frame),
                                snap[:, 3:7]])

    def _measure(self, prev, cur):
        """Deflection + morph-clipping statistics between two snapshots."""
        flow = prev["flow"] & cur["flow"]
        st = wind_mod.morph_clip_stats(prev["pos"], cur["pos"], cur["sheath"],
                                       mask=flow, near_re=WIND_CLIP_NEAR_RE,
                                       tol_re=self.clip_tol_re)
        self.clip["n_pairs"] += 1
        self.clip["n_clipped"] += st["n_clipped"]
        self.clip["max_depth_re"] = max(self.clip["max_depth_re"],
                                        st["max_depth_re"])
        self.clip["p99_depth_re"] = max(self.clip["p99_depth_re"],
                                        st["p99_depth_re"])
        # Parcel-to-magnetopause distance, measured only on parcels that are
        # actually IN the sheath (inside the bow shock, still flowing): the
        # upstream population would swamp the statistic with 200 R_E gaps and
        # the cusp population is legitimately inside the boundary.
        sh = cur["sheath"]
        pos = cur["pos"]
        rho = np.hypot(pos[:, 1], pos[:, 2])
        r = np.maximum(np.hypot(pos[:, 0], rho), 1e-6)
        theta = np.arctan2(rho, pos[:, 0])
        gap = r - sh.mp_radius(theta)
        inside = cur["flow"] & (r < sh.bs_radius(theta)) & (gap >= -0.05)
        if inside.any():
            g = gap[inside]
            self.gap["min_re"] = min(self.gap["min_re"], float(g.min()))
            self.gap["mean_re"] += float(g.mean())
            self.gap["p05_re"] += float(np.percentile(g, 5.0))
            self.gap["median_re"] += float(np.median(g))
            self.gap["n_sheath"] += int(inside.sum())
            self.gap["n"] += 1

    def _write_pair(self, a, b):
        idx = len(self.files)
        name = f"WindParticles_{idx:04d}.vlc"
        tail = ([f"wind frame {idx:04d}, V rows = wind frame {idx + 1:04d}, "
                 f"{self.dt_s:g} s of real flow between them"]
                if b is not None else
                [f"wind frame {idx:04d}, final frame, no successor"])
        self.bytes += self.write_fn(
            self.dir / name, a["rows"], (b["rows"] if b is not None else None),
            a["comments"] + tail, idx) or 0
        self.files.append(name)
        self.times.append(round(a["t"], 6))

    def _push(self, snap, mask, state, sheath, t_dome, comments):
        cur = {"rows": self._rows(snap, state), "pos": snap[:, :3].copy(),
               "flow": (self.pool.mode == 0).copy(), "t": t_dome,
               "sheath": sheath, "comments": comments}
        if self._buf and mask is not None:
            self._buf[-1]["rows"][mask, 3] = 0.0     # D side of the morph pair
            cur["rows"][mask, 3] = 0.0               # V side
        if self._buf:
            self._measure(self._buf[-1], cur)
        self._buf.append(cur)
        self.n_cusp_seen = max(self.n_cusp_seen, int((self.pool.mode == 1).sum()))
        while len(self._buf) >= 3:
            self._write_pair(self._buf[0], self._buf[1])
            self._buf.pop(0)

    def _comments(self, state, sheath):
        return state_comments(state, wind_mod.stream_comments(
            state, sheath, len(self.pool.speed_kms), self.dt_s) + [
            f"ONE pool advected continuously through the whole event - parcel "
            f"row i is the same parcel in every file, never reset per frame",
            f"stream clock: {self.sub} wind frames per field step, "
            f"{self.dt_s:g} s of real flow each ({self.sub * self.dt_s:g} s per "
            f"field step) - TIME-COMPRESSED relative to the storm clock, which "
            f"advances minutes per step, so the flow reads as a stream instead "
            f"of a blur. Shape, direction and speed ratios are honest, the "
            f"elapsed time is not",
        ] + geo.coord_comments(self.coord_digits), frame=self.frame)

    # -- driving -------------------------------------------------------------
    def start(self, state, sheath) -> None:
        self.pool = wind_mod.new_pool(state, sheath)
        self._push(wind_mod.snapshot(self.pool), None, state, sheath, 0.0,
                   self._comments(state, sheath))

    def advance(self, state, sheath, step_index: int) -> None:
        """Run one field step's worth of sub-frames on this step's driving."""
        comments = self._comments(state, sheath)
        for i in range(self.sub):
            mask = wind_mod.step(self.pool, self.dt_s, state, sheath)
            j = step_index * self.sub + i + 1
            self._push(wind_mod.snapshot(self.pool), mask, state, sheath,
                       j * self.cue_s, comments)
        self.max_penetration_re = max(
            self.max_penetration_re,
            float(self.pool.diag["max_penetration_re"]))

    def finish(self) -> dict:
        while len(self._buf) >= 2:
            self._write_pair(self._buf[0], self._buf[1])
            self._buf.pop(0)
        if self._buf:
            self._write_pair(self._buf[0], None)
            self._buf.pop(0)
        d = self.pool.diag if self.pool is not None else {}
        draws, caps = int(d.get("cusp_draws", 0)), int(d.get("cusp_captures", 0))
        n_g = max(self.gap["n"], 1)
        return {
            "sheath_gap_re": {
                "min": (round(self.gap["min_re"], 4)
                        if np.isfinite(self.gap["min_re"]) else None),
                "p05_mean": round(self.gap["p05_re"] / n_g, 4),
                "median_mean": round(self.gap["median_re"] / n_g, 4),
                "mean": round(self.gap["mean_re"] / n_g, 4),
                "n_sheath_mean": int(self.gap["n_sheath"] / n_g),
                "note": "distance from a magnetosheath parcel to the "
                        "magnetopause, over parcels inside the bow shock only",
            },
            "n_files": len(self.files),
            "sub_per_step": self.sub,
            "dt_s": self.dt_s,
            "cue_seconds": self.cue_s,
            "n_parcels": int(len(self.pool.speed_kms)) if self.pool else 0,
            "max_penetration_re": round(self.max_penetration_re, 4),
            "min_gap_re": (round(self.gap["min_re"], 4)
                           if np.isfinite(self.gap["min_re"]) else None),
            "mean_gap_re": round(self.gap["mean_re"] / n_g, 4),
            "cusp_draws": draws,
            "cusp_captures": caps,
            "cusp_fraction": round(caps / draws, 4) if draws else 0.0,
            "cusp_max_resident": self.n_cusp_seen,
            "exits_tail": int(d.get("exits_tail", 0)),
            "exits_cusp": int(d.get("exits_cusp", 0)),
            "swept_by_boundary": int(d.get("swept", 0)),
            "clip": {k: (round(v, 4) if isinstance(v, float) else v)
                     for k, v in self.clip.items()},
            "clip_ok": bool(self.clip["max_depth_re"] <= self.clip_tol_re),
        }


# --- the executor: shared scaffolding ------------------------------------------
# A plan says WHAT to build; an executor decides WHERE the work runs. The two
# backends (serial here, multiprocess in magneto/parallel.py) share everything
# below, which is why their output trees are byte-identical rather than merely
# similar: the same _run_item writes the same file from the same two states, and
# the manifest is built from the plan instead of from a directory listing.
#
# All four item layers are SELF-CONTAINED with respect to geopack: each one
# re-enters model.recalc_for_state() for every epoch it touches, in its own
# fixed order, before it converts a single coordinate. That is what makes an
# item safe to run in any order, in any process - and it is a property of the
# existing model code, not something added here (export_boundaries recalcs
# previous-then-current, trace_all recalcs its own state, l1_marker recalcs,
# aurora._frame_positions recalcs per layer; export_fieldlines is pure).

WORK_DIRNAME = ".work"           # per-item scratch, removed as we go
RECEIPTS_DIRNAME = ".receipts"   # one JSON per completed item (--resume)
TRACE_DIRNAME = ".tracecache"    # Stage A field-line traces (--workers > 1)
PLAN_NAME = ".plan.json"         # the digest a --resume is checked against
BUILD_SCRATCH = (WORK_DIRNAME, RECEIPTS_DIRNAME, TRACE_DIRNAME)


def _receipt_path(models_dir, key: str) -> Path:
    return Path(models_dir) / RECEIPTS_DIRNAME / f"{key}.json"


def write_receipt(models_dir, key: str, files: list[dict], epochs, t,
                  extra: dict | None = None) -> None:
    """Record that one plan item is DONE and what it left on disk.

    `extra` carries anything a LATER item needs from this one. Today that is
    the field lines' break record: item j's fade-in mask is item j-1's break
    mask, and a --resume that skips j-1 must not re-trace two epochs just to
    learn 200 booleans (nor, worse, guess them and write different bytes).
    """
    write_into(_receipt_path(models_dir, key),
               json.dumps({"files": files, "epochs": list(epochs), "t": t,
                           **(extra or {})}, allow_nan=False))


def read_receipt(models_dir, key: str) -> dict | None:
    """A receipt whose files all still exist at the recorded size, else None.

    Size is the whole check on purpose: every writer here is deterministic, so
    a file of the right name and the right length is the file we would write
    again. Hashing 6 MB of ASCII per frame to learn the same thing would cost
    more than re-rendering the cheap layers.
    """
    try:
        rec = json.loads(_receipt_path(models_dir, key).read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None
    for row in rec.get("files") or []:
        try:
            if Path(models_dir, row["name"]).stat().st_size != row["bytes"]:
                return None
        except (OSError, KeyError):
            return None
    return rec


def receipt_bytes(rec: dict) -> int:
    return sum(int(r.get("bytes") or 0) for r in rec.get("files") or [])


def prepare_build_dir(models_dir, plan, resume: bool) -> bool:
    """Set up a build directory for `plan`. Returns whether resume is in effect.

    Three cases, and only the first keeps anything:

      resume and the stored digest matches   -> keep the tree, honour receipts
      resume and it does not                 -> the frames on disk were built
                                                to a DIFFERENT plan (a changed
                                                wind_sub, window, model...) and
                                                mixing them would publish a
                                                package that is half one render
                                                and half another. Wipe.
      no resume                              -> leave any existing content
                                                alone (that is what a plain
                                                re-render into --root has
                                                always done), but drop the
                                                scratch, because a stale
                                                receipt must never be trusted.
    """
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    stored = None
    try:
        stored = json.loads(
            (models_dir / PLAN_NAME).read_text(encoding="ascii")).get("opts_digest")
    except (OSError, ValueError):
        stored = None
    resumed = bool(resume) and stored == plan.opts_digest

    if resume and not resumed and stored is not None:
        log.info("plan digest changed (%s -> %s) - dropping stale build content",
                 stored, plan.opts_digest)
    for name in BUILD_SCRATCH:
        if not resumed:
            shutil.rmtree(models_dir / name, ignore_errors=True)
    if resume and not resumed:
        for p in sorted(models_dir.iterdir()):
            if p.is_file() and p.name != PLAN_NAME:
                p.unlink(missing_ok=True)
    write_into(models_dir / PLAN_NAME, json.dumps(plan.summary(), indent=1))
    return resumed


def clear_build_scratch(models_dir) -> None:
    """Drop receipts / trace cache / item scratch. Called on SUCCESS only.

    Before publish() promotes, so a published Models\\ never carries build
    bookkeeping onto the dome - and after a failure they survive, which is the
    whole point of --resume.
    """
    models_dir = Path(models_dir)
    for name in BUILD_SCRATCH:
        shutil.rmtree(models_dir / name, ignore_errors=True)
    (models_dir / PLAN_NAME).unlink(missing_ok=True)


def run_item(item, plan, states, models_dir, get_trace=None,
             broke_prev=None, breaks_out: dict | None = None) -> list[dict]:
    """Execute ONE plan item and land its files under their final names.

    Each item builds into its own scratch directory and the products are then
    renamed into place, because the model writers own their output FILENAMES
    (export_boundaries always writes 'Magnetopause.vla') while the plan owns
    the indexed name it has to land under. The rename is within one directory,
    so it is atomic and free - and it is what the frame loop has always done,
    just per item instead of per step, which is what lets two workers write
    two frames of the same layer at the same time.

    broke_prev is the field lines' fade-in mask - the rows that broke in the
    PREVIOUS field-line pair (see model.fieldlines.break_mask). It is the one
    piece of state an item cannot derive from its own two states, so it is
    handed in; breaks_out receives this item's own break record, which the
    serial backend carries into the next item and both backends aggregate into
    the manifest's fieldline_churn block.

    Returns [{name, bytes}] and writes the item's receipt.
    """
    models_dir = Path(models_dir)
    work = models_dir / WORK_DIRNAME / item.key
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    src_state = None if item.src_k is None else states[item.src_k]
    dst_state = states[item.dst_k]
    rows: list[dict] = []
    receipt_extra: dict = {}
    try:
        if item.layer == "boundaries":
            export_boundaries(dst_state, src_state, work, frame=plan.frame,
                              coord_digits=plan.coord_digits)
        elif item.layer == "fieldlines":
            res = export_fieldlines(
                get_trace(item.dst_k),
                None if item.src_k is None else get_trace(item.src_k),
                dst_state, work / "FieldLines.vlc", broke_prev=broke_prev,
                coord_digits=plan.coord_digits)
            if plan.with_pulses:
                # phase 5: beads on the same two traces, phases keyed on the
                # STATE indices so they advance evenly whatever the stride
                export_fieldpulses(
                    get_trace(item.dst_k),
                    None if item.src_k is None else get_trace(item.src_k),
                    item.dst_k, item.src_k, work / "FieldPulses.vlc",
                    coord_digits=plan.coord_digits)
            if res["break"] is not None:
                receipt_extra["break"] = res["break"]
            if breaks_out is not None:
                breaks_out["break"] = res["break"]
        elif item.layer == "aurora":
            _aurora_modeled(dst_state, src_state, work, frame=plan.frame,
                            with_curtains=plan.with_curtains,
                            coord_digits=plan.coord_digits)
        elif item.layer == "ringcurrent":
            # Emits NOTHING when the state carries no Dst index - absent is
            # honest, invented is not (the _render_l1 rule).
            ringcurrent_mod.export_ring_current(
                dst_state, src_state, work / "RingCurrent.vlc",
                frame=plan.frame, coord_digits=plan.coord_digits)
        elif item.layer == "plasmasphere":
            # The FULL timeline, not the render window: Kp_max looks 24 h back
            # and a curated window starts only ~10 h before onset.
            plasmasphere_mod.export_plasmasphere(
                dst_state, src_state, _event_timeline(plan.event_id),
                work / "Plasmasphere.vlc",
                frame=plan.frame, coord_digits=plan.coord_digits)
        elif item.layer == "imf":
            # The drape is convected through the WIND's flow field, so it needs a
            # sheath for both epochs of the morph. Building them here rather than
            # threading one in keeps the item a pure function of its own inputs,
            # which is what lets the parallel and serial executors agree.
            imf_mod.export_imf_lines(
                dst_state, src_state,
                wind_sheath(dst_state),
                None if src_state is None else wind_sheath(src_state),
                work / "IMFLines.vlc",
                frame=plan.frame, coord_digits=plan.coord_digits)
        elif item.layer == "l1":
            if _render_l1(dst_state, work, frame=plan.frame,
                          coord_digits=plan.coord_digits) is None:
                raise RuntimeError(
                    f"{item.key}: the plan promised an L1 marker for "
                    f"{dst_state['epoch']} but the state carries no l1_gse_re")
        else:                                              # pragma: no cover
            raise ValueError(f"unknown plan layer {item.layer!r}")

        for _layer, produced, dest_name in plan.item_products(item):
            src = work / produced
            if not src.exists():
                raise RuntimeError(f"{item.key}: writer produced no {produced}")
            dest = models_dir / dest_name
            src.replace(dest)
            rows.append({"name": dest_name, "bytes": dest.stat().st_size})
    finally:
        shutil.rmtree(work, ignore_errors=True)

    epochs = [s["epoch"] for s in (src_state, dst_state) if s is not None]
    write_receipt(models_dir, item.key, rows, epochs, item.time_s,
                  extra=receipt_extra or None)
    return rows


def wind_writer(models_dir, resume: bool,
                coord_digits: int = DEFAULT_COORD_DIGITS):
    """An _EventWindStream.write_fn that receipts its files (and skips resumed)."""
    models_dir = Path(models_dir)

    def write(path, rows, next_rows, comments, index) -> int:
        key = f"wind_{index:04d}"
        if resume:
            rec = read_receipt(models_dir, key)
            if rec is not None:
                return receipt_bytes(rec)
        n_bytes = _write_wind_file(path, rows, next_rows, comments, index,
                                   coord_digits)
        write_receipt(models_dir, key,
                      [{"name": Path(path).name, "bytes": n_bytes}], [], index)
        return n_bytes

    return write


def _trace_cache(plan):
    """state index -> the fieldline items that still need its trace, by step."""
    need: dict[int, int] = {}
    for it in plan.items:
        if it.layer != "fieldlines":
            continue
        for k in (it.src_k, it.dst_k):
            if k is not None:
                need[k] = need.get(k, 0) + 1
    return need


def worker_count(workers, plan) -> int:
    """How many processes to actually spawn. 1 means the pure serial path.

    Never more than there are pieces of work, so a two-state preview does not
    pay for 32 interpreter startups to run four tasks.
    """
    n = os.cpu_count() or 1 if workers is None else int(workers)
    return max(1, min(n, max(1, len(plan.trace_epochs) + len(plan.items))))


def execute_serial(plan, states, models_dir, *, resume: bool = False,
                   progress=None) -> dict:
    """The reference backend: one process, one thread, plan order.

    A literal port of the frame loop render_event has always run - same call
    order, same two-traces-alive economy, same wind driving - with the loop
    body replaced by "run the items the plan put at this step". Every byte
    comparison in tests/test_render_parallel.py is ultimately against this.
    """
    models_dir = Path(models_dir)
    n = plan.n_frames
    by_step: dict[int, list] = {}
    for item, k in zip(plan.items, plan.item_steps):
        by_step.setdefault(k, []).append(item)

    traces: dict[int, dict] = {}
    remaining = _trace_cache(plan)

    def get_trace(k: int) -> dict:
        if k not in traces:
            traces[k] = trace_all(states[k], frame=plan.frame)
        remaining[k] = remaining.get(k, 1) - 1
        if remaining[k] <= 0:              # exactly two traces are ever alive
            return traces.pop(k)
        return traces[k]

    wind = None
    if plan.wind is not None:
        wind = _EventWindStream(models_dir, frame=plan.frame, sub=plan.wind.sub,
                                stream_s=plan.wind.stream_s,
                                step_seconds=plan.step_seconds,
                                coord_digits=plan.coord_digits,
                                write_fn=wind_writer(models_dir, resume,
                                                     plan.coord_digits))
    wind_s = 0.0
    n_bytes = 0
    n_skipped = 0
    t_start = time.time()
    # The field lines' crossfade continuity, carried frame to frame: this is
    # the previous PAIR's break mask, which the next pair owes a fade-in. A
    # resumed item hands it back through its receipt rather than re-tracing.
    fl_records: list = []
    fl_broke = None

    for k in range(n):
        t0 = time.time()
        for item in by_step.get(k, ()):
            rec = read_receipt(models_dir, item.key) if resume else None
            if rec is not None:
                # Still retire the traces this item would have consumed, so a
                # resumed run's two-trace window does not leak.
                if item.layer == "fieldlines":
                    for idx in (item.src_k, item.dst_k):
                        if idx is None:
                            continue
                        remaining[idx] = remaining.get(idx, 1) - 1
                        if remaining[idx] <= 0:
                            traces.pop(idx, None)
                    brk = rec.get("break")
                    fl_records.append(brk)
                    fl_broke = (None if not brk
                                else fieldlines_mod.rows_to_mask(brk["rows"]))
                n_bytes += receipt_bytes(rec)
                n_skipped += 1
                continue
            out: dict = {}
            n_bytes += sum(r["bytes"] for r in
                           run_item(item, plan, states, models_dir,
                                    get_trace=get_trace, broke_prev=fl_broke,
                                    breaks_out=out))
            if item.layer == "fieldlines":
                brk = out.get("break")
                fl_records.append(brk)
                fl_broke = (None if not brk
                            else fieldlines_mod.rows_to_mask(brk["rows"]))
        if wind is not None:
            t_w = time.time()
            # The wind ALWAYS re-advects from step 0, resume or not: the pool
            # lives in memory only, and a parcel's position at step k is the
            # sum of every step before it. Only the FILE WRITES are skipped.
            sheath = wind_sheath(states[k])
            if k == 0:
                wind.start(states[k], sheath)
            if k < n - 1:
                wind.advance(states[k], sheath, k)
            wind_s += time.time() - t_w
        done, per = k + 1, (time.time() - t_start) / (k + 1)
        log.info("  step %d/%d %s in %.1f s (mean %.1f s, eta %.1f min, "
                 "%.1f MB written)", done, n, states[min(k + 1, n - 1)]["epoch"],
                 time.time() - t0, per, per * (n - done) / 60.0, n_bytes / 1e6)
        if progress:
            progress(done, n, per)

    return _finish(plan, wind, wind_s, n_bytes, n_skipped, t_start,
                   churn=fieldlines_mod.churn_stats(fl_records))


def _finish(plan, wind, wind_s, n_bytes, n_skipped, t_start,
            stats=None, churn=None) -> dict:
    """The common tail of both backends: drain the wind, hand back the stats.

    `stats` lets a backend that has already drained the stream (the parallel
    one must, while its worker pool is still alive) hand the result in rather
    than have finish() called a second time.
    """
    out = {"wind": None, "wind_files": [], "wind_times": [],
           "wind_build_s": round(wind_s, 2), "bytes": n_bytes,
           "skipped": n_skipped, "build_s": time.time() - t_start,
           "fieldline_churn": churn}
    if wind is not None:
        t_w = time.time()
        if stats is None:
            stats = wind.finish()
        out["wind_build_s"] = round(wind_s + (time.time() - t_w), 2)
        out["wind"] = stats
        out["wind_files"] = list(wind.files)
        out["wind_times"] = list(wind.times)
        out["bytes"] = n_bytes + wind.bytes
        if list(wind.files) != list(plan.wind.files):       # pragma: no cover
            raise RuntimeError(
                f"wind emitted {len(wind.files)} files, plan promised "
                f"{len(plan.wind.files)}")
    return out


def verify_plan_on_disk(plan, models_dir) -> None:
    """Every file the plan promised exists. The determinism guard.

    The manifest is built from the plan, never from a directory scan, so a
    worker that died quietly would otherwise publish a player addressing a
    file that is not there. Checked once, at the end, for both backends.
    """
    models_dir = Path(models_dir)
    missing = [name for name in plan.dest_names()
               if not (models_dir / name).exists()]
    if missing:
        raise RuntimeError(
            f"{len(missing)} planned file(s) missing from {models_dir}: "
            f"{missing[:6]}{' ...' if len(missing) > 6 else ''}")


def render_event(event_id: str, out_root, step_min: float = EVENT_STEP_MIN,
                 window=None, *,
                 events_root=None, step_seconds: float = EVENT_STEP_SECONDS,
                 layers=EVENT_LAYERS, parent=None, do_publish: bool = True,
                 progress=None, frame=DEFAULT_FRAME, with_wind: bool = True,
                 wind_sub: int = EVENT_WIND_SUB,
                 wind_stream_s: float = EVENT_WIND_STREAM_S,
                 aurora_max_hold_min: float = EVENT_AURORA_MAX_HOLD_MIN,
                 external: str | None = None,
                 with_curtains: bool = EMIT_AURORA_CURTAINS,
                 shells_hidden: bool = False,
                 with_shells: bool = False,
                 with_pulses: bool = False,
                 with_ring: bool = False,
                 with_plasmasphere: bool = False,
                 with_imf: bool = False,
                 holder_intensities: dict | None = None,
                 fieldline_every: int = 1, workers=None,
                 coord_digits: int = DEFAULT_COORD_DIGITS,
                 resume: bool = False,
                 preload_window: int | None = EVENT_PRELOAD_WINDOW,
                 shell_preset: str | None = None,
                 shell_mode: str = "step") -> dict:
    """Render a curated event as a morphing frame series and publish it.

    Frame k (k < M-1) carries state k on its D/P/L rows and state k+1 on its V
    rows - the same parametric pairing the live cycle uses between consecutive
    polls, so the .ds plays the storm as continuous motion. The LAST frame is
    non-parametric (no successor), exactly like the solar animation's final
    step, so the series comes to rest on the true final state.

    The expensive layer is the field lines: events run T96 (STATE_SCHEMA
    model.external), measured at ~115 s per epoch against T89's ~17 s. Each
    state is traced ONCE and its trace is carried into the next frame as that
    frame's morph source, so M states cost M traces, not 2(M-1). Pass
    external='t89' for a fast preview.

    THREE CADENCES, not one - the whole point of the rework:

      * boundaries / field lines / L1  one file per step, morphing step -> step
      * the aurora                     one file per Kp CHANGE (see
                                       aurora_indices), long morphs between
      * the wind                       wind_sub files per step, one continuous
                                       pool advected through the entire event
                                       and driven by each step's own state

    The .ds player merges them onto one timeline under ONE HOLDER PER ASSET
    TYPE - each layer's rows carry their own holder key (its name), so the
    presenter can drop the parcel stream, dim the bow shock or hide the L1
    marker independently of every other layer.

    fieldline_every  trace (and emit) the field lines every Nth step only,
                     morphing across the stride. N is the one knob that trades
                     field-line smoothness for wall clock, because the trace
                     IS the render: everything else in a step is milliseconds.
    workers          processes for the multiprocess executor; None means
                     os.cpu_count(), 1 is the serial reference path. Whatever
                     it is, the output tree is the same bytes - see
                     tests/test_render_parallel.py.
    coord_digits     significant figures for every light-year coordinate: 9
                     (default) is float32-exact, which is all Digistar keeps -
                     it cooks positions to DXGI_FORMAT_R32G32B32_FLOAT - and
                     costs about a third of the package's bytes. 17 is the full
                     float64 round-trip. It is folded into the plan digest, so
                     a --resume can never mix the two precisions in one tree.
    resume           reuse the frames a previous, dead build of THIS EXACT
                     plan already wrote (matched by receipt + plan digest).
                     Off by default: a resume that silently mixed two plans
                     would publish half of one render and half of another.

    Returns the frames manifest (also written to Models/event_frames.json).
    """
    frame = geo.check_frame(frame)
    states = select_event_states(event_id, step_min, window, events_root)
    # The external field model: None / 't96' = the state's own (T96 for curated storms),
    # 't89' = fast preview, 'ts05' = Tsyganenko-Sitnov 2005 storm model (geopack t04, W
    # indices integrated over the event's whole timeline), 'auto' = TS05 whenever the
    # driving values leave T96's fitted range, else T96 (David 2026-09-14).
    external = resolve_external(external, states, event_id, events_root)
    if external == "t89":
        states = [_as_t89(s) for s in states]
    elif external == "ts05":
        from .events import load_event
        from .model import ts05 as ts05_mod
        timeline = list(load_event(event_id, events_root))
        states = ts05_mod.as_ts05(states, timeline)
    if len(states) < 2:
        raise ValueError(
            f"{event_id}: {len(states)} state(s) selected for step {step_min} "
            f"min window {window} - need at least 2 for a frame series")
    n = len(states)
    # Shell LIBRARY mode (magneto.model.shell_library): the boundaries are not plan
    # items at all - a handful of reference OBJs written once after the build, scaled
    # per step by the player - so the plan (and its --resume digest) has no boundaries.
    library_shells = bool(with_shells) and str(shell_mode).lower() == "library"
    if library_shells:
        layers = tuple(l for l in layers if l != "boundaries")
    plan = plan_mod.build_plan(
        states, event_id=event_id, layers=layers, step_min=step_min,
        step_seconds=step_seconds, frame=frame,
        fieldline_every=fieldline_every,
        aurora_max_hold_min=aurora_max_hold_min, with_wind=with_wind,
        wind_sub=wind_sub, wind_stream_s=wind_stream_s,
        with_curtains=with_curtains, with_shells=with_shells, with_pulses=with_pulses,
        with_ring=with_ring, with_plasmasphere=with_plasmasphere,
        with_imf=with_imf, external=external,
        coord_digits=coord_digits)
    n_workers = worker_count(workers, plan)
    title = dsds.event_title(event_id) or _disk_title(event_id, events_root) \
        or event_id
    log.info("render_event %s: %d steps, step %g min -> %g dome s, %s .. %s, "
             "layers %s, frame %s, wind %s, aurora frames %d/%d, "
             "fieldlines %d/%d, %d items, %d workers",
             event_id, n, step_min, step_seconds, states[0]["epoch"],
             states[-1]["epoch"], sorted(layers), frame,
             f"{wind_sub}x/step" if with_wind else "off",
             len(plan.aurora_steps), n, len(plan.trace_epochs), n,
             len(plan.items), n_workers)

    # Everything about the manifest except the wind block and the build timings
    # is decided by the plan - see magneto/plan.py::plan_to_manifest. The two
    # values a pure module cannot compute come in through `extra`.
    frames_manifest = plan_mod.plan_to_manifest(plan, states, extra={
        "schema": SCHEMA_VERSION,
        "title": title,
        "step_min": step_min,
        "step_seconds": step_seconds,
        "aurora_label": zp.label(max(s["kp"]["value"] for s in states)),
        "aurora_max_hold_min": aurora_max_hold_min,
        "shell_preset": dsds.shell_look(shell_preset)["name"],
        "talking_points": talking_points(event_id),
    })

    def build(models_dir: Path, scripts_dir: Path):
        t_start = time.time()
        resumed = prepare_build_dir(models_dir, plan, resume)
        if n_workers > 1:
            from . import parallel                # local: no import cycle
            res = parallel.execute(plan, states, models_dir, workers=n_workers,
                                   resume=resumed, progress=progress)
        else:
            res = execute_serial(plan, states, models_dir, resume=resumed,
                                 progress=progress)
        verify_plan_on_disk(plan, models_dir)
        if res["skipped"]:
            log.info("resumed: %d of %d plan items reused from receipts",
                     res["skipped"], len(plan.items))

        churn = res.get("fieldline_churn")
        if churn is not None and plan.trace_epochs:
            # Additive manifest key: how often the field lines had to crossfade
            # instead of morph, i.e. how much reconnection the window contains.
            frames_manifest["fieldline_churn"] = churn
            log.info("field-line churn: %d break(s) over %d pair(s) "
                     "(%d pair(s) affected, worst %d lines) - %s",
                     churn["n_breaks"], churn["n_pairs"],
                     churn["pairs_with_breaks"], churn["max_breaks_per_pair"],
                     churn["by_reason"])

        layer_rows = plan.layer_rows()
        stats = res["wind"]
        if stats is not None:
            layer_rows.append({"name": "WindParticles", "kind": "dots",
                               "holder": "WindParticles", "parametric": True,
                               "files": res["wind_files"],
                               "times": res["wind_times"]})
            frames_manifest["wind"] = stats
            if not stats["clip_ok"]:
                log.warning(
                    "WIND MORPH CLIPPING: midpoints reach %.3f R_E inside the "
                    "magnetopause (tolerance %.2f). Raise wind_sub (currently "
                    "%d) or lower wind_stream_s (currently %g s).",
                    stats["clip"]["max_depth_re"], stats["clip"]["tol_re"],
                    wind_sub, wind_stream_s)
            log.info("wind: %d files, %.1f s bake, clip max %.3f R_E (tol %.2f, "
                     "%s), gap min %s R_E, cusp %d/%d = %.3f, tail exits %d",
                     stats["n_files"], res["wind_build_s"],
                     stats["clip"]["max_depth_re"], stats["clip"]["tol_re"],
                     "OK" if stats["clip_ok"] else "CLIPPING",
                     stats["min_gap_re"], stats["cusp_captures"],
                     stats["cusp_draws"], stats["cusp_fraction"],
                     stats["exits_tail"])
        if library_shells:
            from .model import shell_library
            shells = shell_library.export_shell_library(
                states, models_dir, frame=frame, coord_digits=coord_digits)
            fl_times = next((row["times"] for row in layer_rows
                             if row["name"] == "FieldLines"), None)
            shell_times = fl_times or [k * float(step_seconds) for k in range(n)]
            layer_rows = shell_library.library_layer_rows(shells, shell_times) + list(layer_rows)
            frames_manifest["shells"] = shells
        frames_manifest["shell_mode"] = ("library" if library_shells
                                         else ("step" if with_shells else "none"))
        frames_manifest["layers"] = layer_rows
        # Build bookkeeping goes BEFORE the package is measured, so nothing
        # this run used to coordinate itself can ever reach the dome.
        clear_build_scratch(models_dir)
        frames_manifest["built"] = _iso(_utcnow())
        frames_manifest["build_s"] = round(time.time() - t_start, 2)
        frames_manifest["wind_build_s"] = res["wind_build_s"]
        frames_manifest["bytes"] = _tree_bytes(models_dir)
        frames_manifest["mb"] = round(frames_manifest["bytes"] / 1e6, 1)
        frames_manifest["mb_per_step"] = round(
            frames_manifest["bytes"] / 1e6 / n, 2)

        frames_manifest["preload_window"] = preload_window
        frames_manifest["with_shells"] = bool(with_shells)
        frames_manifest["shells_hidden"] = bool(shells_hidden)
        frames_manifest["with_pulses"] = bool(with_pulses)
        # WHICH LAYOUT THE FILES ARE ACTUALLY IN, recorded because the player's model
        # lines are written from it (dsexport.ds.write_event_player). The writers above
        # put every model FLAT in Models\, so this render is flat and says so. It is
        # tools/normalize_model_layout.py that moves them to Models\<name>\<name>.<ext>
        # and flips this flag, in that order, after which the package is re-emitted.
        #
        # Stamping it is not bookkeeping. Absent, write_event_player fell back to the
        # module default - which became True on 2026-09-20 - so a fresh render emitted
        # '..\Models\FieldLines_0000\FieldLines_0000.vlc' over a flat tree: every model
        # line in the player wrong at once, no error, an empty dome. --validate did not
        # catch it because it validates the FILES, not the references to them.
        frames_manifest["model_convention_dirs"] = False
        player = scripts_dir / dsds.DS_EVENT_PLAYER_FMT.format(event_id)
        dsds.write_event_player(frames_manifest, player, step_seconds,
                                parent=parent, frame=frame,
                                intensities=holder_intensities,
                                preload_window=preload_window,
                                shell_preset=shell_preset)
        dsds.write_shells_js(scripts_dir, shell_preset)
        dsds.write_event_teardown(
            frames_manifest, scripts_dir / dsds.DS_EVENT_TEARDOWN_FMT.format(event_id))
        dsds.write_player_js(frames_manifest, scripts_dir)
        # The optional Digistar-native aurora companion, cut from the same Kp
        # series as the AuroraOval frames (no-op when the aurora layer is off).
        dsds.write_native_aurora_ds(
            frames_manifest,
            scripts_dir / dsds.DS_NATIVE_AURORA_FMT.format(event_id))
        # The 'off' scripts name exactly the asset-type holders this render
        # built, so they never address a holder that does not exist.
        dsds.write_override_scripts(
            [SimpleNamespace(id=event_id, title=frames_manifest.get("title"))],
            scripts_dir,
            holders=[row.get("holder") or row["name"] for row in layer_rows])
        dsds.write_status_ds(states[-1], scripts_dir / dsds.DS_STATUS_NAME,
                             now=_parse_iso(states[-1]["epoch"]))
        write_into(models_dir / "event_frames.json",
                   json.dumps(frames_manifest, indent=1, allow_nan=False))
        write_state_atomic(states[-1], models_dir / STATE_NAME)
        return frames_manifest

    stamp = (f"{event_id} {states[0]['epoch']}..{states[-1]['epoch']} "
             f"step{step_min:g}min {frame}")
    if not do_publish:
        root = Path(out_root)
        build(root / "Models", root / "Scripts")
        return frames_manifest
    res = publish(out_root, build, stamp)
    frames_manifest["published"] = str(res["models"])
    frames_manifest["rotated"] = res["rotated"]
    frames_manifest["publish_s"] = round(res["elapsed_s"], 2)
    return frames_manifest


def _tree_bytes(models_dir) -> int:
    """Total bytes of the .vla/.vlc files written so far (the package size)."""
    d = Path(models_dir)
    # rglob so the number is the same whichever layout the tree is in: the renderer
    # writes flat, tools/normalize_model_layout.py puts each file in its own folder.
    return sum(p.stat().st_size for p in d.rglob("*")
               if p.is_file() and p.suffix.lower() in (".vla", ".vlc", ".obj", ".glb"))


def _peak_caption(states: list[dict]) -> str:
    """'Peak Kp 9, SYM-H -412 nT' - the honest one-liner for the event banner.

    The disturbance index is named after what the state actually carries
    (dst.source): curated OMNI events store 1-minute SYM/H, look-backs store
    hourly Kyoto Dst. They are not the same measurement, so the banner must not
    call one by the other's name. Lives in plan.py (it is pure arithmetic on
    the states and the manifest is built there).
    """
    return plan_mod.peak_caption(states)


# --- CLI ------------------------------------------------------------------------------

def _validate_tree(models_dir) -> tuple[int, list[str]]:
    """Validate every .vla/.vlc in a Models directory. Returns (n_ok, errors)."""
    models_dir = Path(models_dir)
    n_ok, errors = 0, []
    from dsexport.mesh import validate_obj, validate_glb
    # rglob: validate the same files whichever layout the tree is in (flat as the
    # renderer writes it, or Models\<name>\<name>.<ext> after normalize_model_layout).
    for p in sorted(list(models_dir.rglob("*.vla")) + list(models_dir.rglob("*.vlc"))
                    + list(models_dir.rglob("*.obj")) + list(models_dir.rglob("*.glb"))):
        # A .glb is binary: validate_obj reads it as ASCII and dies on the first byte
        # of the glTF magic. Caught by the first real render on the new writer, not by
        # the unit tests - which is the argument for rebuilding something real after a
        # format change instead of trusting a green suite.
        if p.suffix.lower() == ".glb":
            ok, errs = validate_glb(p)
        elif p.suffix.lower() == ".obj":
            ok, errs = validate_obj(p)
        else:
            ok, errs = validate(p, quiet=True)
        if ok:
            n_ok += 1
        else:
            errors.append(f"{p.name}: {errs[:3]}")
    return n_ok, errors


def progress_file_writer(path, label: str, min_interval_s: float = 5.0):
    """A progress callback that writes a small JSON file atomically.

    Accepts the executor's (done, n, per_item_s[, phase]) call. `pct` is
    the share of the WHOLE job: the trace phase is weighted 0.85 for a T96
    event (the trace is the render) and the write phase 0.15.
    """
    path = Path(path)
    state = {"last": 0.0, "t0": time.time()}
    weight = {"trace": (0.0, 0.85), "write": (0.85, 0.15)}

    def cb(done, n, per, phase="write"):
        now = time.time()
        final = done >= n
        if not final and now - state["last"] < min_interval_s:
            return
        state["last"] = now
        base, span = weight.get(phase, (0.0, 1.0))
        frac = (done / n) if n else 1.0
        pct = round(100.0 * (base + span * frac), 1)
        remaining = max(0, n - done) * float(per or 0.0)
        if phase == "trace":
            remaining += 0.15 / 0.85 * (now - state["t0"]) if done else 0.0
        doc = {"label": label, "phase": phase, "done": int(done), "n": int(n),
               "pct": pct, "eta_s": int(remaining), "per_item_s": round(float(per or 0), 2),
               "updated": _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_into(path, json.dumps(doc))
        except OSError:
            pass
    return cb


def main(argv=None) -> int:                                # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m magneto.render",
        description="Render a curated event as a Digistar frame series")
    ap.add_argument("--show-preset", action="store_true",
                    help="print the SHOW render preset (magneto.presets) as JSON and exit")
    sub = ap.add_subparsers(dest="cmd")
    SHOW = _presets.SHOW
    ev = sub.add_parser("event", help="render one curated event")
    ev.add_argument("event_id", nargs="?", default=None,
                    help="event id (see magneto.events list)")
    ev.add_argument("--event", dest="event_flag", default=None,
                    help="legacy alias for the positional event id")
    ev.add_argument("--root", default=None,
                    help="output root (Models/ + Scripts/ land here); default "
                         "Renders\\storm_<event id> under the package (magneto.paths)")
    ev.add_argument("--step-min", type=float, default=EVENT_STEP_MIN,
                    help="simulated minutes per field step (default %(default)s)")
    ev.add_argument("--start", default=None, help="ISO-8601 Z window start")
    ev.add_argument("--end", "--stop", dest="end", default=None,
                    help="ISO-8601 Z window end")
    ev.add_argument("--step-seconds", type=float, default=EVENT_STEP_SECONDS,
                    help="dome seconds per field step (default %(default)s)")
    ev.add_argument("--model", choices=["auto", "t96", "ts05", "t89"],
                    default=SHOW_EXTERNAL_STORM,
                    help="external field model: auto (default) = TS05 when the storm leaves "
                         "T96's fitted range, else T96; ts05 / t96 force one; t89 = fast preview")
    ev.add_argument("--t89", action="store_true",
                    help="force T89 instead of the event's T96 (fast preview)")
    ev.add_argument("--no-wind", action="store_true",
                    help="skip the solar-wind parcel stream")
    ev.add_argument("--wind-sub", type=int, default=EVENT_WIND_SUB,
                    help="wind frames per field step (default %(default)s)")
    ev.add_argument("--wind-stream-s", type=float, default=EVENT_WIND_STREAM_S,
                    help="seconds of real parcel flow per field step")
    ev.add_argument("--aurora-max-hold-min", type=float,
                    default=EVENT_AURORA_MAX_HOLD_MIN,
                    help="cap on how long one aurora frame may hold")
    ev.add_argument("--with-l1", action="store_true",
                    help="render the L1 marker layer (off by default since "
                         "the 2026-08-12 show-look decision)")
    ev.add_argument("--with-curtains", action="store_true",
                    help="render the AuroraCurtains layer (off by default; "
                         "the curtain look comes from Digistar's native "
                         "aurora, see magneto-native-aurora-<id>.ds)")
    ev.add_argument("--holder-intensity", action="append", default=[],
                    metavar="LAYER=NN",
                    help="starting intensity for one asset-type holder, e.g. "
                         "BowShock=25 (repeatable; default: boundaries 10, "
                         "everything else 92)")
    ev.add_argument("--frame", choices=list(geo.FRAMES), default=DEFAULT_FRAME,
                    help="export frame (default %(default)s)")
    ev.add_argument("--fieldline-every", type=int, default=1, metavar="N",
                    help="trace and emit field lines every Nth step only, "
                         "morphing across the stride (default 1 = every step). "
                         "The trace IS the render, so N is the one knob that "
                         "trades smoothness for wall clock")
    ev.add_argument("--workers", type=int, default=_paths.workers(),
                    help="render processes (default %(default)s = the machine "
                         "config or cpu count capped at 24; 1 = the serial "
                         "reference path). The output tree is byte-identical "
                         "either way")
    ev.add_argument("--resume", action="store_true",
                    help="reuse frames a previous build of this exact plan "
                         "already wrote (matched by receipt + plan digest)")
    ev.add_argument("--coord-digits", type=int, default=DEFAULT_COORD_DIGITS,
                    choices=(DEFAULT_COORD_DIGITS, 17),
                    help="significant figures for every light-year coordinate "
                         "(default %(default)s). 9 is float32-exact - Digistar "
                         "cooks positions to DXGI_FORMAT_R32G32B32_FLOAT - and "
                         "about a third smaller; 17 is the full float64 "
                         "round-trip. EarthFrameCheck.vla always stays at 17")
    ev.add_argument("--no-publish", action="store_true",
                    help="build straight into --root, no staging/rotation")
    ev.add_argument("--validate", action="store_true")
    ev.add_argument("--shells", dest="shells", action="store_true",
                    default=SHOW.with_shells,
                    help="(always on since 2026-09-14: the shells ARE the boundary layer; "
                         "kept for command-line compatibility)")
    ev.add_argument("--no-shells", dest="shells", action="store_false",
                    help="skip the shells")
    ev.add_argument("--pulses", dest="pulses", action="store_true",
                    default=SHOW.with_pulses,
                    help="export the field-line pulses (beads gliding along +B on "
                         "the traced lines); the SHOW default is on")
    ev.add_argument("--no-pulses", dest="pulses", action="store_false",
                    help="skip the pulses")
    ev.add_argument("--preload-window", type=int, default=EVENT_PRELOAD_WINDOW,
                    help="frame objects per layer kept alive ahead of the "
                         "playhead in the .ds player (0 = preload every frame)")
    ev.add_argument("--ring", dest="with_ring", action="store_true",
                    default=SHOW.with_ring,
                    help="ring current from the measured Dst/SYM-H (default: on)")
    ev.add_argument("--no-ring", dest="with_ring", action="store_false")
    ev.add_argument("--plasmasphere", dest="with_plasmasphere", action="store_true",
                    default=SHOW.with_plasmasphere,
                    help="plasmasphere with Kp-driven erosion (default: on)")
    ev.add_argument("--no-plasmasphere", dest="with_plasmasphere", action="store_false")
    ev.add_argument("--imf", dest="with_imf", action="store_true", default=SHOW.with_imf,
                    help="IMF drape and Kan & Lee reconnection (default: on)")
    ev.add_argument("--no-imf", dest="with_imf", action="store_false")
    ev.add_argument("--shell-mode", choices=["step", "library"], default=SHOW.shell_mode,
                    help="library (default): a few reference shells scaled per step - smooth; "
                         "step: one OBJ per step, crossfaded")
    ev.add_argument("--shell-preset", default=SHOW.shell_preset,
                    choices=sorted(dsds.DS_SHELL_PRESETS),
                    help="shell look (holder colour + abiThickness): P1 limb-only, "
                         "P2 soft rim (default), P3 veil - see dsexport.ds.DS_SHELL_PRESETS")
    ev.add_argument("--shells-hidden", action="store_true",
                    help="the package starts with its boundary shells HIDDEN (holder colour "
                         "0 0 0); the panel's Shells toggle raises them")
    ev.add_argument("--progress-file", default=None, metavar="PATH",
                    help="write {label, phase, done, n, pct, eta_s, updated} JSON "
                         "here (atomically, at most every 5 s) so a watcher can "
                         "post progress banners")
    ev.add_argument("--progress-label", default=None,
                    help="label carried in the progress file (e.g. '2/5 Nov 2003')")
    args = ap.parse_args(argv)
    if args.show_preset:
        print(_presets.as_json())
        return 0
    if args.cmd is None:
        ap.print_help()
        return 2
    event_id = args.event_id or args.event_flag
    if not event_id:
        ev.error("an event id is required")
    root = args.root or str(_paths.render_root("storm", event_id))
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    window = (args.start, args.end) if (args.start or args.end) else None
    intensities = {}
    for spec in args.holder_intensity:
        layer, _, value = str(spec).partition("=")
        if not value:
            ev.error(f"--holder-intensity wants LAYER=NN, got {spec!r}")
        intensities[layer] = float(value)
    t0 = time.time()
    progress_cb = (progress_file_writer(args.progress_file, args.progress_label or event_id)
                   if args.progress_file else None)
    m = render_event(event_id, root, step_min=args.step_min,
                     progress=progress_cb,
                     window=window, step_seconds=args.step_seconds,
                     layers=EVENT_LAYERS_WITH_L1 if args.with_l1
                     else EVENT_LAYERS,
                     frame=args.frame, with_wind=not args.no_wind,
                     wind_sub=args.wind_sub, wind_stream_s=args.wind_stream_s,
                     aurora_max_hold_min=args.aurora_max_hold_min,
                     external="t89" if args.t89 else args.model,
                     with_curtains=args.with_curtains, with_shells=args.shells,
                     shells_hidden=args.shells_hidden,
                     with_pulses=args.pulses,
                     holder_intensities=intensities or None,
                     fieldline_every=args.fieldline_every,
                     workers=args.workers, resume=args.resume, preload_window=(args.preload_window or None),
                     shell_preset=args.shell_preset, shell_mode=args.shell_mode,
                     with_ring=args.with_ring,
                     with_plasmasphere=args.with_plasmasphere,
                     with_imf=args.with_imf,
                     coord_digits=args.coord_digits,
                     do_publish=not args.no_publish)
    wall = time.time() - t0
    print(f"{m['event_id']}: {m['n_frames']} steps "
          f"{m['epochs'][0]} .. {m['epochs'][-1]} in {m.get('build_s')} s "
          f"({wall:.1f} s wall)")
    print(f"  frame {m['frame']}  model {m['model']}  "
          f"playback {m['playback_s']:g} s ({m['playback_s'] / 60:.1f} dome min)")
    print(f"  package {m.get('mb')} MB ({m.get('mb_per_step')} MB per step), "
          f"{m.get('build_s', 0) / max(m['n_frames'], 1):.1f} s per step")
    for layer in m["layers"]:
        t = layer.get("times") or []
        cad = (t[1] - t[0]) if len(t) > 1 else m["step_seconds"]
        print(f"  {layer['name']:<16} {len(layer['files']):>5} files "
              f"({layer['kind']}, "
              f"{dsds.event_holder(layer.get('holder') or layer['name'])}, "
              f"cadence {cad:g}s)")
    wind = m.get("wind")
    if wind:
        print(f"  wind: {wind['n_files']} frames, {wind['sub_per_step']}/step, "
              f"{wind['dt_s']:g} s of flow each")
        g = wind["sheath_gap_re"]
        print(f"        penetration max {wind['max_penetration_re']} R_E, "
              f"sheath gap min {g['min']} p05 {g['p05_mean']} "
              f"median {g['median_mean']} mean {g['mean']} R_E "
              f"({g['n_sheath_mean']} parcels in the sheath per frame)")
        print(f"        cusp {wind['cusp_captures']}/{wind['cusp_draws']} = "
              f"{wind['cusp_fraction']}, max resident {wind['cusp_max_resident']}, "
              f"tail exits {wind['exits_tail']}, cusp exits {wind['exits_cusp']}, "
              f"boundary-swept {wind['swept_by_boundary']}")
        print(f"        morph clip max {wind['clip']['max_depth_re']} R_E "
              f"(tol {wind['clip']['tol_re']}) over {wind['clip']['n_pairs']} "
              f"pairs -> {'OK' if wind['clip_ok'] else 'CLIPPING'}")
    if args.validate:
        models = Path(m.get("published") or (Path(root) / "Models"))
        n_ok, errors = _validate_tree(models)
        print(f"  validated {n_ok} files, {len(errors)} invalid")
        for e in errors[:10]:
            print("   !", e)
        # AND the field lines against the drawn shells. --validate checks that every
        # FILE is well formed; it has never checked that the geometry inside them
        # agrees. "The shells are not aligned with the magnetic field lines" has been
        # reported three times and each fix was verified by a tool someone had to
        # remember to run - so it runs here, on the published tree, every time.
        align_bad = False
        try:
            from tools.check_alignment import check_render
            res = check_render(Path(models).parent, every=5, quiet=True)
            w, wp = res.get("worst"), res.get("worst_pct")
            if w and wp:
                print(f"  alignment: open lines outside max {wp['open_out_pct']:.2f}% "
                      f"(step {wp['step']}), worst ratio {w['open_worst']:.2f} "
                      f"(step {w['step']}) -> {'FAIL' if res['bad'] else 'OK'}")
                if res["bad"]:
                    align_bad = True
                    print("   ! field lines are drawn outside the shells - this is the "
                          "fault reported as \"the shells are not aligned with the "
                          "field lines\". Do not ship or capture this render.")
        except Exception as exc:                            # never fail a good render
            print(f"  alignment: could not run the check ({exc}) - "
                  "run tools/check_alignment.py by hand")
        return 1 if (errors or align_bad) else 0
    return 0


if __name__ == "__main__":                                 # pragma: no cover
    import sys
    sys.exit(main())
