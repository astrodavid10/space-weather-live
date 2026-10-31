r"""The live loop: poll, build a state, decide, render only when it matters.

One cycle, every `cycle_s` seconds (default 60, matching SWPC's 1-minute
tier-1 cadence):

    1. read the presenter override        <root>/state/override.json
    2. build a schema-1 state from the cache (livecore never touches HTTP here
       - the Poller thread does that on its own schedule)
    3. decide the mode: LIVE, LIVE-but-ARMED, or EVENT playback
    4. change detection: is this state materially different from the one
       currently on the dome?
    5. render + publish the layers that changed, or refresh only the readout

Rule 5 is the whole point. A full cycle is ~17 s of field-line tracing plus
tens of seconds of wind baking, and SWPC's 1-minute samples usually differ by
less than the dome can show. So:

    * driving values inside the thresholds below -> NO render. The banner and
      current_state.json are still rewritten, so the age on the dome keeps
      climbing and an operator can always tell the feed is alive.
    * field lines re-trace whenever the driving values move (they are the
      layer that actually responds, and ~17 s fits inside 60 s)
    * the parcel stream re-bakes on its own 5-minute cadence (WIND_EVERY_S) -
      it is a 30-frame animation, and re-baking it every minute buys nothing
      because the parcels advect continuously from a carried-over pool
    * the aurora re-exports only when OVATION publishes a new observation
      time (it updates every ~5 min, and the layer costs ~5 s)

Degradation, straight from STATE_SCHEMA.md's staleness policy:

    age <= 15 min   LIVE     (the state builder's own tier)
    age >  15 min   CACHED   frozen state, age climbing, banner says so
    age >  3 h      EVENT ARMED - still showing the cached state, but the
                    banner says the curated event is armed
    age >  6 h      auto-switch to the default curated event
    no cache at all boot straight into the default curated event

PRESENTER OVERRIDE. Every cycle the daemon reads <root>/state/override.json:

    {"force": "EVENT:2024-05-10_gannon"}   play that event now
    {"force": "LIVE"}                      pin the live feed, no auto-switch
    {"force": null}                        release, back to automatic

A dome key writes that file (small JSON, tmp + rename) and runs the matching
Scripts\event-<id>-on.ds / live-restore.ds. The daemon picks the change up on
its next cycle - within cycle_s seconds. It is a FILE and not a socket on
purpose: it survives a daemon restart, it is greppable, and a presenter can
fix a stuck show with a text editor.

CLI:
    python -m magneto.daemon --once  --root output/magneto
    python -m magneto.daemon --loop --minutes 10 --root output/magneto
    python -m magneto.daemon --loop --cycle-s 30
"""
from __future__ import annotations

import argparse
import json
import logging
import atexit
import faulthandler
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dsexport import ds as dsds
from dsexport.publish import (LIVE_KEEP_CYCLES, MODELS_DIRNAME, SCRIPTS_DIRNAME,
                              STATE_DIRNAME, cycle_dirname, publish_cycle,
                              write_into)
from livecore.cache import CacheStore
from livecore.poller import Poller
from livecore.state import build_state, data_age_str, hud_text, write_state_atomic

from . import render as render_mod
from .events import DEFAULT_EVENT, EVENTS, load_event
from .joblock import pid_alive
from . import model as model_mod
from .model import aurora as aurora_mod

log = logging.getLogger(__name__)

from magneto import paths as _paths
from dsexport.mesh import MESH_FORMAT as _MESH_EXT

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = _paths.render_root("live")      # <package>\Renders\live (or <checkout>\output\live)
DEFAULT_CACHE_DIR = REPO / "cache"

CYCLE_S = 60.0                  # SWPC tier-1 cadence
WIND_EVERY_S = 300.0            # parcel re-bake cadence
FIRST_POLL_WAIT_S = 20.0        # --loop waits this long for the poller's first sweep
HEARTBEAT_NAME = "heartbeat.json"   # <root>/state/heartbeat.json, rewritten every loop pass
EVENT_ARM_S = 3 * 3600.0        # cache older than this arms the event
EVENT_SWITCH_S = 6 * 3600.0     # ... and older than this switches to it
OVATION_MAX_SKEW_S = 2 * 3600.0 # OVATION is a nowcast: never paint it on an
                                # epoch it does not describe (e.g. a 2024 event)

# Change-detection thresholds on the DRIVING values. Below all of these the
# geometry would move by less than the dome can resolve, so the render is
# skipped and only the readout is refreshed.
DBZ_NT = 0.1
DV_KMS = 5.0
DN_CM3 = 0.2

OVERRIDE_NAME = "override.json"
PID_NAME = "daemon.pid"            # <root>/state/daemon.pid while a loop runs
STOP_NAME = "stop.flag"            # the panel's Stop Live Feed writes it
STOP_POLL_S = 1.0
STATE_NAME = render_mod.STATE_NAME
MANIFEST_NAME = render_mod.MANIFEST_NAME

# One sentinel file per render layer: if it is missing from the cycle directory
# the manifest points that layer at, the layer must be rendered regardless of
# change detection (first run, a pruned tree, a hand-deleted directory). No L1
# marker on the dome (show-look decision 2026-08-12), so it is not a live layer.
# The file names come FROM dsds.LIVE_LAYERS, the list the writers and the loader use (2026-10-04). The
# hand-written version said RingCurrent.vlc / Plasmasphere.vlc after those two layers became .glb meshes
# (2026-09-20), so both were "missing" on every pass: every 60 s cycle re-rendered and crossfaded them
# (6,976 of 6,976 cycles in live-20260929T2348Z.log). The tests' stub wrote the same wrong names, which is
# why nothing caught it - test_live_feed.py now checks these against LIVE_LAYERS.
_LIVE_FILE = {name: fname for name, fname, _w, _kind in dsds.LIVE_LAYERS}
LAYER_SENTINEL = {
    "boundaries": _LIVE_FILE["MagnetopauseShell"],
    "fieldlines": _LIVE_FILE["FieldLines"],
    "aurora": _LIVE_FILE["AuroraOval"],
    "wind": _LIVE_FILE["WindParticles"],
    "ringcurrent": _LIVE_FILE["RingCurrent"],
    "plasmasphere": _LIVE_FILE["Plasmasphere"],
    "imf": _LIVE_FILE["IMFLines"],
}
# Layers re-rendered whenever the driving state changes. The three added on
# 2026-09-16 belong here for the same reason the boundaries do: they are
# functions of the measured state (Dst for the ring current, Kp for the
# plasmapause, the IMF clock angle for the drape and its merging), so leaving
# them out would draw each one ONCE on the first cycle and then freeze it while
# the indices moved underneath - the live feed would show a ring current that
# never responded to the storm it was in.
GEOMETRY_LAYERS = ("boundaries", "fieldlines",
                   "ringcurrent", "plasmasphere", "imf")
# render layer -> the manifest layer NAMES (= dome holder names) its files carry.
# The merged live manifest is keyed by these, one entry per name:
#   {"FieldLines": {"cycle": 42, "dir": "c000042", "files": ["FieldLines.vlc"], "kind": "lines"}}
LAYER_PRODUCTS = {
    "boundaries": ("MagnetopauseShell", "BowShockShell"),
    "fieldlines": ("FieldLines", "FieldPulses"),
    "aurora": ("AuroraOval",),
    "wind": ("WindParticles",),
    "ringcurrent": ("RingCurrent",),
    "plasmasphere": ("Plasmasphere",),
    "imf": ("IMFLines",),
}
LAYER_KIND = {name: kind for name, _f, _w, kind in dsds.LIVE_LAYERS}


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Daemon:
    """The live loop. One instance owns one output root and one cache."""

    def __init__(self, root=DEFAULT_ROOT, cache_dir=DEFAULT_CACHE_DIR,
                 cycle_s: float = CYCLE_S, wind_every_s: float = WIND_EVERY_S,
                 default_event: str = DEFAULT_EVENT, now_fn=None,
                 poll: bool = True, render: bool = True,
                 n_wind_frames: int = render_mod.N_WIND_FRAMES,
                 event_step: int = 1, events_root=None,
                 frame: str = render_mod.DEFAULT_FRAME,
                 with_shells: bool = True, with_pulses: bool = True,
                 keep_cycles: int = LIVE_KEEP_CYCLES):
        self.frame = frame
        self.with_shells = bool(with_shells)
        self.with_pulses = bool(with_pulses)
        self.keep_cycles = int(keep_cycles)
        self.pulse_step = 0                      # advances per field-line render
        self.stopped_by: str | None = None
        self.root = Path(root)
        self.cache = CacheStore(cache_dir)
        self.cycle_s = float(cycle_s)
        self.wind_every_s = float(wind_every_s)
        self.default_event = default_event
        self.n_wind_frames = n_wind_frames
        self.event_step = max(1, int(event_step))
        self.events_root = events_root
        self.render_enabled = render
        # Injectable clock. Gate 4 fast-forwards hours of staleness through
        # this in seconds; nothing else in the daemon reads the wall clock for
        # anything that affects a decision.
        self._now_fn = now_fn
        self.poller = Poller(self.cache) if poll else None

        self.cycle_n = 0
        self.live_state: dict | None = None     # newest state the cache allows
        self.rendered_state: dict | None = None  # what is on the dome now
        self.trace = None                        # its field-line trace
        self.trace_break = None                  # rows that flipped topology
        self.wind_pool = None                    # persistent parcel pool
        self.rendered_ovation = None             # grid dict last exported
        self.ovation_obs = None                  # its observation time
        self.last_wind_t: datetime | None = None
        self.mode = "LIVE"
        self.armed = False
        self.event_id: str | None = None
        self._event_states: list[dict] = []
        self._event_index = 0
        self.last_error: str | None = None

        for sub in (STATE_DIRNAME,):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        # the per-layer map of the tree already on disk (a restart must not
        # re-render layers whose cycle directories are still there)
        self.layer_map: dict = self._read_layer_map()

    # -- small accessors ---------------------------------------------------

    def now(self) -> datetime:
        return self._now_fn() if self._now_fn else datetime.now(timezone.utc)

    @property
    def models_dir(self) -> Path:
        return self.root / MODELS_DIRNAME

    @property
    def scripts_dir(self) -> Path:
        return self.root / SCRIPTS_DIRNAME

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIRNAME

    def read_override(self) -> dict:
        """The presenter override, or {} - never raises, never blocks a cycle.

        A malformed override is logged once per distinct body and ignored: a
        typo in a hand-edited file must not take the dome down.
        """
        path = self.state_dir / OVERRIDE_NAME
        try:
            raw = path.read_text(encoding="ascii")
        except OSError:
            return {}
        try:
            obj = json.loads(raw)
        except ValueError:
            if raw != getattr(self, "_bad_override", None):
                self._bad_override = raw
                log.warning("%s is not valid JSON - ignoring it", path)
            return {}
        return obj if isinstance(obj, dict) else {}

    # -- decisions ----------------------------------------------------------

    def decide(self, state, override: dict):
        """(mode, event_id, armed, reason) for this cycle. Pure - no I/O."""
        force = (override or {}).get("force")
        if isinstance(force, str):
            f = force.strip()
            if f.upper().startswith("EVENT"):
                _, _, eid = f.partition(":")
                eid = eid.strip() or self.default_event
                return "EVENT", eid, False, f"override force {force!r}"
            if f.upper() == "LIVE":
                return "LIVE", None, False, f"override force {force!r}"
            log.warning("override force %r not understood - ignoring", force)

        if state is None:
            return ("EVENT", self.default_event, True,
                    "cold start - no usable cache, booting the default event")
        age = float(state["age_s"])
        if age > EVENT_SWITCH_S:
            return ("EVENT", self.default_event, True,
                    f"cache {age / 3600:.1f} h old - auto-switched to the "
                    f"default event")
        if age > EVENT_ARM_S:
            return ("LIVE", None, True,
                    f"cache {age / 3600:.1f} h old - event armed")
        return "LIVE", None, False, ""

    def driving_changed(self, state) -> list[str]:
        """Reasons the driving conditions moved enough to be worth a render."""
        prev = self.rendered_state
        if prev is None:
            return ["first render"]
        if prev.get("event_id") != state.get("event_id"):
            return ["event changed"]
        a, b = prev["sw"], state["sw"]
        out = []
        if abs(b["bz_gsm"] - a["bz_gsm"]) >= DBZ_NT:
            out.append(f"dBz {b['bz_gsm'] - a['bz_gsm']:+.2f} nT")
        if abs(b["speed"] - a["speed"]) >= DV_KMS:
            out.append(f"dv {b['speed'] - a['speed']:+.1f} km/s")
        if abs(b["density"] - a["density"]) >= DN_CM3:
            out.append(f"dn {b['density'] - a['density']:+.2f} cm-3")
        if prev["kp"]["value"] != state["kp"]["value"]:
            out.append(f"Kp {prev['kp']['value']:g} -> {state['kp']['value']:g}")
        return out

    def _ovation_for(self, state):
        """(grid_dict, is_new) - the OVATION nowcast, IF it describes this epoch.

        OVATION is a nowcast for the next ~30 min. Painting today's oval onto a
        2024 storm replay would be a fabrication, so the grid is only offered
        to the renderer when its observation time is within
        OVATION_MAX_SKEW_S of the state's epoch. That rule is about the DATA,
        not the tier - an event epoch simply never matches, which is why the
        renderer never has to know what an event is.
        """
        payload, _meta, _age = self.cache.latest("ovation")
        if payload is None:
            return None, False
        try:
            grid = aurora_mod.parse_ovation(payload)
        except Exception:
            log.exception("cached OVATION payload will not parse - skipping")
            return None, False
        try:
            obs = datetime.fromisoformat(
                str(grid["obs_time"]).replace("Z", "+00:00"))
            if obs.tzinfo is None:
                obs = obs.replace(tzinfo=timezone.utc)
            epoch = datetime.fromisoformat(state["epoch"].replace("Z", "+00:00"))
        except ValueError:
            return None, False
        if abs((epoch - obs).total_seconds()) > OVATION_MAX_SKEW_S:
            return None, False
        return grid, grid["obs_time"] != self.ovation_obs

    def _event_state(self, event_id: str):
        """The next state of a curated event, advancing the playhead.

        The event plays at event_step timeline steps per cycle and loops. Each
        frame is a full schema-1 state, so it goes through render_cycle exactly
        as a live poll does - that identity IS the degradation story.
        """
        if event_id != self.event_id or not self._event_states:
            try:
                self._event_states = list(load_event(event_id, self.events_root))
            except Exception as exc:
                # Loud every cycle on purpose: a missing fallback event is a
                # misconfiguration, not weather. event_id is left unset so a
                # later `magneto.events fetch` is picked up without a restart.
                log.error("event %s is not on disk (%s) - run "
                          "'python -m magneto.events fetch %s'",
                          event_id, exc, event_id)
                self._event_states = []
                self.event_id = None
                return None
            self.event_id = event_id
            self._event_index = 0
            log.info("event %s loaded: %d states %s .. %s", event_id,
                     len(self._event_states), self._event_states[0]["epoch"],
                     self._event_states[-1]["epoch"])
        if not self._event_states:
            return None
        state = self._event_states[self._event_index % len(self._event_states)]
        self._event_index = ((self._event_index + self.event_step)
                             % len(self._event_states))
        return state

    def _read_layer_map(self) -> dict:
        """The per-layer map of the tree on disk; also resumes the cycle counter.

        A restarted daemon must never re-use a cycle number: publish_cycle
        replaces an existing c<N> directory, and the loader may be displaying it.
        """
        try:
            m = json.loads((self.models_dir / MANIFEST_NAME).read_text(encoding="ascii"))
        except (OSError, ValueError):
            return {}
        layers = m.get("layers")
        layers = dict(layers) if isinstance(layers, dict) else {}
        try:
            highest = max([int(m.get("cycle", 0))]
                          + [int(e.get("cycle", 0)) for e in layers.values()])
        except (TypeError, ValueError):
            highest = 0
        self.cycle_n = max(self.cycle_n, highest)
        return layers

    def _missing_layers(self, want) -> set:
        """Render layers whose sentinel is absent from the cycle dir the manifest names."""
        out = set()
        for name in want:
            entry = self.layer_map.get(LAYER_PRODUCTS[name][0])
            if not entry or not (self.models_dir / str(entry.get("dir", ""))
                                 / LAYER_SENTINEL[name]).exists():
                out.add(name)
        return out

    # -- outputs ------------------------------------------------------------

    def _note(self) -> str | None:
        if self.mode == "EVENT":
            return None                # the banner already names the event
        return "EVENT ARMED" if self.armed else None

    # The daemon used to hard-code hud_text(state, live=True) and status_text(now=now)
    # whatever the mode, so its stored-event fallback labelled 2024 data "EVENT 1m old"
    # on the dome and "as of <today> UT (1m ago) | ... Gannon" in the banner (the curated
    # timeline rows carry age_s 60). A replay must say REPLAY and be aged against its own
    # epoch - the same rule the event players and status_text() already follow.
    def _hud_live(self) -> bool:
        return self.mode != "EVENT"

    def _banner_now(self, now):
        return None if self.mode == "EVENT" else now

    def _scene_date(self, state) -> str | None:
        """'YYYY-MM-DD HH:MM:SS' for the loader to pin the dome clock to while a stored
        event stands in for the live feed; None (= 'scene date now') otherwise. A replay
        drawn against today's Sun is the 122-degree incoherence David kept reporting."""
        if self.mode != "EVENT":
            return None
        return dsds._scene_datetime(state["epoch"])

    def refresh_readout(self, state, now) -> None:
        """Rewrite the banner + state files without touching the geometry.

        Runs on EVERY cycle, rendered or not. The dome's honesty depends on
        this: a frozen magnetosphere with a ticking 'as of' age reads as a
        stale feed (true), while a frozen magnetosphere with a frozen clock
        reads as a working feed (false).
        """
        write_state_atomic(state, self.state_dir / STATE_NAME)
        if self.scripts_dir.exists():
            dsds.write_status_ds(state, self.scripts_dir / dsds.DS_STATUS_NAME,
                                 now=self._banner_now(now), note=self._note())
        models = self.models_dir
        if not models.exists():
            return
        write_state_atomic(state, models / STATE_NAME)
        path = models / MANIFEST_NAME
        try:
            manifest = json.loads(path.read_text(encoding="ascii"))
        except (OSError, ValueError):
            return
        # 'cycle' is the loader's re-read signal and must NOT move when no new
        # geometry was published - only the freshness fields are patched.
        manifest.update({
            "refreshed": _iso(now), "rendered": False,
            "age_s": state["age_s"], "tier": state["tier"],
            "readout_epoch": state["epoch"], "mode": self.mode,
            "armed": self.armed, "scene_date": self._scene_date(state),
            "banner": dsds.status_text(state, now=self._banner_now(now), note=self._note()),
            "hud": hud_text(state, live=self._hud_live()), "stopped": False,
        })
        write_into(path, json.dumps(manifest, indent=1, allow_nan=False))

    def _stamp(self, state) -> str:
        """This run's identity: the state's epoch MINUTE (+ the event id)."""
        stamp = state["epoch"][:16] + "Z"
        return f"{stamp} {state['event_id']}" if state.get("event_id") else stamp

    def _publish_render(self, state, layers, ovation, now) -> dict:
        prev_ovation = self.rendered_ovation
        prev_state = self.rendered_state

        cycle = self.cycle_n + 1
        new_dir = cycle_dirname(cycle)
        rendered_names = {n for lay in layers for n in LAYER_PRODUCTS.get(lay, ())}

        def build(models_dir, scripts_dir):
            return render_mod.render_cycle(
                state, prev_state, models_dir,
                ovation_grid=ovation if "aurora" in layers else None,
                prev_ovation_grid=prev_ovation,
                wind_pool=self.wind_pool, prev_trace=self.trace,
                prev_break=self.trace_break,
                out_scripts_dir=scripts_dir, cycle=cycle,
                layers=sorted(layers), n_wind_frames=self.n_wind_frames,
                now=now, note=self._note(), mode=self.mode, armed=self.armed,
                frame=self.frame, with_shells=self.with_shells,
                with_pulses=self.with_pulses, pulse_step=self.pulse_step)

        # cycle directories the merged manifest will still point at
        keep_dirs = {str(e.get("dir")) for n, e in self.layer_map.items()
                     if n not in rendered_names and e.get("dir")}
        res = publish_cycle(self.root, build, cycle, stamp=self._stamp(state),
                            keep_cycles=self.keep_cycles, referenced=keep_dirs)
        manifest = res["build"]
        carry = manifest.get("_carry", {})
        self.trace = carry.get("trace", self.trace)
        self.trace_break = carry.get("break", self.trace_break)
        self.wind_pool = carry.get("wind_pool", self.wind_pool)
        self.cycle_n = cycle
        self.rendered_state = state
        if "fieldlines" in layers:
            self.pulse_step += 1

        # The merged per-layer map: rendered layers point at the new directory
        # (files found on disk, so a layer that emitted nothing drops out),
        # everything else keeps pointing at the cycle that last drew it.
        layer_map = {n: e for n, e in self.layer_map.items() if n not in rendered_names}
        for name in sorted(rendered_names):
            files = sorted(p.name for p in res["models"].glob(f"{name}*")
                           if p.suffix.lower() in (".vlc", ".vla", ".obj", ".glb"))
            if files:
                layer_map[name] = {"cycle": cycle, "dir": new_dir, "files": files,
                                   "kind": LAYER_KIND.get(name, "lines")}
        self.layer_map = layer_map
        merged = dict(render_mod.strip_carry(manifest))
        merged.update({
            "layers": layer_map, "rendered_layers": sorted(layers), "dir": new_dir,
            "wind_frame_dt_s": render_mod.WIND_FRAME_DT_S,
            "hud": hud_text(state, live=self._hud_live()), "stopped": False,
            "mode": self.mode, "scene_date": self._scene_date(state),
            # Same block the event manifests carry, so tools/check_physics.py can
            # compare the live feed against the storms rather than assuming they
            # agree. A live feed on older physics than the storm beside it is
            # exactly the silent mixture the stamp exists to catch.
            "physics": model_mod.physics_stamp(),
        })
        write_into(self.models_dir / MANIFEST_NAME,
                   json.dumps(merged, indent=1, allow_nan=False))
        if "aurora" in layers and ovation is not None:
            self.rendered_ovation = ovation
            self.ovation_obs = ovation["obs_time"]
        if "wind" in layers:
            self.last_wind_t = now
        # The carry is memory, not report: it stays on the daemon and never
        # reaches a caller that might try to serialise it.
        return dict(render_mod.strip_carry(manifest),
                    publish={"dir": res["dir"], "pruned": res["pruned"],
                             "elapsed_s": round(res["elapsed_s"], 3)})

    # -- one cycle ----------------------------------------------------------

    def cycle(self) -> dict:
        """Run exactly one cycle. Returns a small report dict. Never raises.

        'Never raises' is deliberate and matches the poller thread's rule: a
        render that dies must cost one cycle, not the show. The failure is
        logged with its traceback and recorded in the report, publish leaves
        live + ModelsPrevious untouched, and the next cycle tries again.
        """
        t0 = time.time()
        now = self.now()
        report = {"cycle": self.cycle_n, "now": _iso(now), "rendered": False,
                  "layers": [], "reasons": [], "error": None}
        try:
            live = build_state(self.cache, now)
            if live is not None:
                self.live_state = live
            state = self.live_state

            override = self.read_override()
            mode, event_id, armed, reason = self.decide(state, override)
            if (mode, event_id) != (self.mode, self.event_id) and reason:
                log.info("mode -> %s%s: %s", mode,
                         f" ({event_id})" if event_id else "", reason)
            self.armed = armed
            if mode == "EVENT":
                ev_state = self._event_state(event_id)
                if ev_state is None:
                    # No event on disk and (possibly) no live state either.
                    # Say so and keep the loop alive - there is nothing to draw.
                    self.mode = "LIVE" if state is not None else mode
                    if state is None:
                        report["error"] = "no cache and no event on disk"
                        log.error("nothing to render: %s", report["error"])
                        report["elapsed_s"] = time.time() - t0
                        return report
                else:
                    self.mode, state = "EVENT", ev_state
            else:
                self.mode = "LIVE"
                self.event_id = None

            report["mode"], report["armed"] = self.mode, self.armed
            report["tier"] = state["tier"]
            report["epoch"] = state["epoch"]
            report["age_s"] = state["age_s"]
            report["banner"] = dsds.status_text(state, now=self._banner_now(now),
                                                note=self._note())

            # The readout is refreshed FIRST and unconditionally, so even a
            # cycle whose render fails leaves an honest clock on the dome.
            self.refresh_readout(state, now)

            ovation, ovation_new = self._ovation_for(state)
            reasons = self.driving_changed(state)
            wind_due = (self.last_wind_t is None
                        or (now - self.last_wind_t).total_seconds()
                        >= self.wind_every_s)

            layers = set()
            if reasons:
                layers |= set(GEOMETRY_LAYERS)
            if ovation_new and ovation is not None:
                layers.add("aurora")
                reasons.append(f"new OVATION obs {ovation['obs_time']}")
            if wind_due:
                layers.add("wind")
                reasons.append("wind re-bake due")
            # A first render, or a switch into / out of an event, invalidates
            # every layer at once: the aurora grid changes source (OVATION <->
            # modeled) and nothing on the dome belongs to this state any more.
            if (self.rendered_state is None
                    or self.rendered_state.get("event_id")
                    != state.get("event_id")):
                layers |= set(LAYER_SENTINEL)
            missing = self._missing_layers(LAYER_SENTINEL)
            if missing:
                layers |= missing
                reasons.append("missing: " + ",".join(sorted(missing)))
            report["reasons"] = reasons

            if not layers:
                log.info("cycle %d: no change (%s) - readout refreshed only, "
                         "%s", self.cycle_n, state["epoch"],
                         data_age_str(state["age_s"], now=now))
                report["elapsed_s"] = time.time() - t0
                return report
            if not self.render_enabled:
                report["reasons"].append("rendering disabled")
                report["elapsed_s"] = time.time() - t0
                return report

            manifest = self._publish_render(state, layers, ovation, now)
            report.update({"rendered": True, "layers": sorted(layers),
                           "cycle": manifest["cycle"],
                           "render_s": manifest["render_s"],
                           "timings_s": manifest["timings_s"],
                           "publish": manifest["publish"]})
        except Exception as exc:                       # never kill the loop
            self.last_error = repr(exc)
            report["error"] = repr(exc)
            log.exception("cycle %d failed (live output untouched)", self.cycle_n)

        report["elapsed_s"] = time.time() - t0
        log.info("cycle %d done in %.2f s: %s%s", report["cycle"],
                 report["elapsed_s"],
                 "rendered " + ",".join(report["layers"]) if report["rendered"]
                 else "skipped",
                 " | " + "; ".join(report["reasons"]) if report["reasons"] else "")
        return report

    # -- runners ------------------------------------------------------------

    def run_once(self) -> dict:
        """Poll every endpoint synchronously, then run one cycle."""
        if self.poller is not None:
            self.poller.run_once()
        return self.cycle()

    # -- pid file + stop flag (the panel's Start / Stop Live Feed) -----------

    @property
    def pid_path(self) -> Path:
        return self.state_dir / PID_NAME

    @property
    def stop_path(self) -> Path:
        return self.state_dir / STOP_NAME

    def running_pid(self) -> int | None:
        """The pid in state/daemon.pid if that process is alive, else None."""
        try:
            pid = int(self.pid_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None
        return pid if pid_alive(pid) else None

    def _clear_stopped_marker(self) -> None:
        """Remove `stopped` / `stopped_why` from the published live manifest."""
        p = self.models_dir / MANIFEST_NAME
        try:
            m = json.loads(p.read_text(encoding="ascii"))
        except (OSError, ValueError):
            return
        # pop BOTH: `a or b` short-circuits, so a truthy "stopped" would leave
        # "stopped_why" behind for the loader to quote back at the operator
        had = [m.pop("stopped", None), m.pop("stopped_why", None)]
        if not any(v is not None for v in had):
            return
        tmp = p.with_suffix(".json.starting")
        try:
            tmp.write_text(json.dumps(m, indent=1, allow_nan=False), encoding="ascii")
            tmp.replace(p)
        except OSError:
            pass

    def stop_requested(self) -> bool:
        return self.stop_path.exists()

    def mark_stopped(self, why: str) -> None:
        """Tell the dome the feed is OFF: the loader shows LIVE FEED STOPPED."""
        self.stopped_by = why
        path = self.models_dir / MANIFEST_NAME
        try:
            manifest = json.loads(path.read_text(encoding="ascii"))
        except (OSError, ValueError):
            manifest = {}
        manifest.update({"stopped": True, "stopped_why": why,
                         "stopped_at": _iso(self.now())})
        if manifest:
            write_into(path, json.dumps(manifest, indent=1, allow_nan=False))
        for p in (self.stop_path, self.pid_path):
            try:
                p.unlink()
            except OSError:
                pass
        log.info("live feed stopped: %s", why)

    def _sleep_watching_stop(self, seconds: float) -> bool:
        """Sleep up to `seconds`, waking every STOP_POLL_S; True if stop was asked."""
        t_end = time.monotonic() + max(0.0, seconds)
        while True:
            if self.stop_requested():
                return True
            left = t_end - time.monotonic()
            if left <= 0:
                return False
            time.sleep(min(STOP_POLL_S, left))

    def loop(self, minutes: float | None = None, cycles: int | None = None,
             sleep: bool = True, pidfile: bool = False) -> list[dict]:
        """Run cycles until `minutes` of wall time, `cycles` iterations, or a stop flag.

        The poller runs on its own background thread (its own schedule, its own
        jitter); this loop only ever reads the cache, so a network stall can
        slow the data but never the loop. With pidfile=True the loop refuses to
        start beside a live daemon on the same root, records its pid in
        state/daemon.pid, and exits cleanly when state/stop.flag appears.
        """
        if pidfile:
            other = self.running_pid()
            if other is not None and other != os.getpid():
                raise SystemExit(f"a live daemon is already running on {self.root} "
                                 f"(pid {other}) - stop it first")
            write_into(self.pid_path, str(os.getpid()))
            try:
                self.stop_path.unlink()          # a stale flag must not stop us at once
            except OSError:
                pass
        # A daemon that is starting is not a daemon that was stopped: drop the previous
        # run's marker before anything can read it. The dome loader polls this file every
        # 5 s and reports `stopped_why` verbatim, so a leftover "stopped by operator"
        # greets the operator seconds after they press Start (2026-09-16).
        self._clear_stopped_marker()
        if self.poller is not None:
            self.poller.start()
            # Decide the first cycle on DATA, not on whatever the cache held when the
            # thread started (2026-09-21: an 11.8 h-old cache chose the Gannon fallback
            # for 101 s while every endpoint had already answered 200).
            if not self.poller.wait_first(FIRST_POLL_WAIT_S):
                log.warning("first poll incomplete after %.0f s - deciding on the cache",
                            FIRST_POLL_WAIT_S)
        reports: list[dict] = []
        t_end = None if minutes is None else time.monotonic() + minutes * 60.0
        why = None
        try:
            while True:
                if pidfile and self.stop_requested():
                    why = "stopped by operator"
                    break
                t0 = time.monotonic()
                if pidfile:
                    self._heartbeat(len(reports))
                reports.append(self.cycle())
                if cycles is not None and len(reports) >= cycles:
                    why = f"{cycles} cycles done"
                    break
                if t_end is not None and time.monotonic() >= t_end:
                    why = f"{minutes:g} min elapsed"
                    break
                if sleep:
                    remaining = self.cycle_s - (time.monotonic() - t0)
                    if remaining > 0:
                        if t_end is not None:
                            remaining = min(remaining, t_end - time.monotonic())
                        if remaining > 0:
                            if pidfile:
                                if self._sleep_watching_stop(remaining):
                                    why = "stopped by operator"
                                    break
                            else:
                                time.sleep(remaining)
                    else:
                        log.warning("cycle overran its %.0f s budget by %.1f s",
                                    self.cycle_s, -remaining)
        finally:
            if self.poller is not None:
                self.poller.stop()
            if pidfile:
                self.mark_stopped(why or "loop ended")
        return reports

    def close(self) -> None:
        if self.poller is not None:
            self.poller.stop()

    @property
    def heartbeat_path(self) -> Path:
        return self.state_dir / HEARTBEAT_NAME

    def _heartbeat(self, n_cycles: int) -> None:
        """state/heartbeat.json, rewritten every loop pass. The 2026-09-21 daemon vanished
        mid-sleep with no log line (both processes gone at once - a tree kill, not a crash);
        a heartbeat lets `kickoff_live_daemon.py status` and the dome loader tell a dead
        daemon from a quiet one instead of showing the last age forever."""
        try:
            write_into(self.heartbeat_path, json.dumps({
                "pid": os.getpid(), "at": _iso(self.now()), "cycle": self.cycle_n,
                "passes": n_cycles, "mode": self.mode}))
        except OSError as exc:                                 # pragma: no cover
            log.warning("heartbeat not written: %s", exc)


# --- CLI -------------------------------------------------------------------------

def _install_exit_tracing() -> None:
    """Every way out of the process leaves a line in the worker log.

    faulthandler catches the hard crashes (a traceback on stderr, which run_step
    captures); atexit covers normal exits; the signal handlers name the signal.
    A TerminateProcess (taskkill /F, Task Manager) is uncatchable by design - that
    is what the heartbeat and the pid liveness check in kickoff_live_daemon.py are
    for. On 2026-09-21 the daemon and its worker vanished together inside one 60 s
    sleep with none of these in place, and the dome kept saying 'LIVE 8m old'.
    """
    try:
        faulthandler.enable()
    except Exception:                                          # pragma: no cover
        pass
    atexit.register(lambda: log.info("daemon pid %d exiting", os.getpid()))

    def _on_signal(signum, _frame):
        log.warning("daemon pid %d received signal %s - exiting", os.getpid(), signum)
        raise SystemExit(128 + int(signum))

    for name in ("SIGTERM", "SIGBREAK", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _on_signal)
            except (ValueError, OSError):                       # pragma: no cover
                pass


def _lower_priority() -> None:
    """BelowNormal: DsHost's model cooks and captures come first on this box.

    A 24-worker render at Normal priority took a dome capture from 6.6 to 0.13
    frames/s (2026-09-15); the live daemon's T89 trace is small but it runs every
    minute for as long as the show does, so it takes the same courtesy.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32                               # type: ignore[attr-defined]
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        if k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS):
            log.info("process priority BelowNormal (DsHost first)")
    except Exception as exc:                                       # pragma: no cover
        log.warning("could not lower the process priority: %s", exc)


def summarise(reports: list[dict]) -> dict:
    rendered = [r for r in reports if r.get("rendered")]
    errors = [r for r in reports if r.get("error")]
    walls = [r["elapsed_s"] for r in reports]
    return {
        "cycles": len(reports),
        "rendered": len(rendered),
        "skipped": len(reports) - len(rendered) - len(errors),
        "errors": len(errors),
        "wall_s": {"min": round(min(walls), 2), "max": round(max(walls), 2),
                   "mean": round(sum(walls) / len(walls), 2)} if walls else {},
        "render_s": [round(r.get("render_s", 0.0), 2) for r in rendered],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m magneto.daemon",
                                 description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true",
                       help="poll once, run one cycle, exit")
    group.add_argument("--loop", action="store_true",
                       help="run the background poller and cycle continuously")
    group.add_argument("--stop", action="store_true",
                       help="ask the daemon looping on --root to exit (writes state/stop.flag)")
    group.add_argument("--status", action="store_true",
                       help="print whether a daemon is looping on --root and exit")
    ap.add_argument("--minutes", type=float, default=None,
                    help="stop the loop after this many minutes")
    ap.add_argument("--cycle-s", type=float, default=CYCLE_S,
                    help="seconds per cycle (default: %(default)s)")
    ap.add_argument("--root", default=str(DEFAULT_ROOT),
                    help="output root (default: %(default)s)")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    ap.add_argument("--wind-every-s", type=float, default=WIND_EVERY_S)
    ap.add_argument("--event", default=DEFAULT_EVENT,
                    help="event to fall back to (default: %(default)s)")
    ap.add_argument("--no-poll", action="store_true",
                    help="never touch the network - render from the cache only")
    ap.add_argument("--no-render", action="store_true",
                    help="decide and refresh the readout, skip the geometry")
    ap.add_argument("--wind-frames", type=int,
                    default=render_mod.N_WIND_FRAMES)
    ap.add_argument("--frame", choices=["j2000", "gsm", "ecef"],
                    default=render_mod.DEFAULT_FRAME,
                    help="export frame: j2000 = Digistar WCS axes (default), "
                         "gsm = sun-fixed physics frame, ecef = Earth-fixed")
    ap.add_argument("--no-shells", action="store_true",
                    help="skip the effectModelClass boundary shells")
    ap.add_argument("--no-pulses", action="store_true",
                    help="skip the gliding field-line pulses")
    ap.add_argument("--keep-cycles", type=int, default=LIVE_KEEP_CYCLES,
                    help="cycle directories kept under Models/ beyond the referenced ones")
    ap.add_argument("--pidfile", action="store_true",
                    help="(--loop) record state/daemon.pid, refuse a second daemon, "
                         "exit when state/stop.flag appears - the panel's mode")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.loop:
        _install_exit_tracing()
        _lower_priority()

    d = Daemon(root=args.root, cache_dir=args.cache_dir, cycle_s=args.cycle_s,
               wind_every_s=args.wind_every_s, default_event=args.event,
               poll=not args.no_poll, render=not args.no_render,
               n_wind_frames=args.wind_frames, frame=args.frame,
               with_shells=not args.no_shells, with_pulses=not args.no_pulses,
               keep_cycles=args.keep_cycles)
    if args.status or args.stop:
        pid = d.running_pid()
        if args.status:
            print(f"live daemon on {d.root}: "
                  + (f"RUNNING pid {pid}" if pid else "not running"))
            return 0
        if pid is None:
            print(f"no live daemon is running on {d.root}")
            return 0
        write_into(d.stop_path, _iso(d.now()))
        print(f"stop requested for pid {pid} ({d.stop_path}) - it exits within "
              f"{STOP_POLL_S:g} s of its current cycle finishing")
        return 0
    try:
        reports = ([d.run_once()] if args.once
                   else d.loop(minutes=args.minutes, pidfile=args.pidfile))
    except KeyboardInterrupt:                            # pragma: no cover
        log.info("interrupted")
        d.close()
        return 130
    s = summarise(reports)
    print(f"cycles {s['cycles']}  rendered {s['rendered']}  "
          f"skipped {s['skipped']}  errors {s['errors']}")
    print(f"wall time per cycle: {s['wall_s']}")
    for r in reports:
        mark = "R" if r.get("rendered") else ("!" if r.get("error") else ".")
        print(f"  {mark} {r.get('epoch', '-')} {r.get('tier', '-'):<6} "
              f"{r['elapsed_s']:6.2f} s  {','.join(r.get('layers', [])) or '-'}"
              f"  {'; '.join(r.get('reasons', []))}")
        if r.get("error"):
            print(f"      ERROR {r['error']}")
        if r.get("banner"):
            print(f"      banner: {r['banner']}")
    return 1 if s["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
