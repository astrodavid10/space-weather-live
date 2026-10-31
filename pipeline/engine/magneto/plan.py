"""Pure render-plan arithmetic - no geopack, no I/O, importable anywhere.

Phase 2 of the speed plan started this module with the one provable fact the
retime tool is built on: EVERY dome time in an event package is linear in
step_seconds. render_event bakes layer times as k * step_seconds
(magneto/render.py), the wind cue grid as j * (step_seconds / sub), and
playback_s as (n - 1) * step_seconds - so scaling them all by one factor
re-times the package exactly, and the geometry (which never encodes dome
time) needs no re-render.

Phase 3 grew it into the full plan/executor split. A RenderPlan is the
COMPLETE, side-effect-free answer to "what files does this render produce, in
what order, out of which states?" - computed before a single geopack call
happens. That buys four things at once:

  * the executor becomes interchangeable. Serial and multiprocess backends
    consume the same plan, so "same bytes either way" is a property of the
    plan, not a coincidence of two loops that happen to agree.
  * every item is INDEPENDENT. An item names its own source/target state
    indices (src_k -> D rows, dst_k -> V rows) and its own destination
    filename, so nothing has to be executed in order to know where it lands.
  * resume becomes trivial: item + receipt + digest.
  * the manifest can be built from the plan instead of from a directory scan,
    which is what keeps a parallel build's manifest identical to a serial
    one's even though the files landed in a different order.

Everything here is arithmetic on state dicts (epoch strings, Kp, propagation
presence) - numpy/datetime/dataclasses only. Anything that needs geopack, a
model, or a file lives in magneto/render.py or magneto/parallel.py.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

from dsexport.vla import COORD_SIG_DIGITS
from livecore.state import hud_text
# model/__init__ keeps geopack behind lazy imports, so this does not break
# the no-geopack rule above.
from .model import physics_stamp
from dsexport.mesh import MESH_FORMAT as _MESH_EXT

# Manifest keys that carry DOME SECONDS (not simulated time, which a retime
# must never touch: step_min, epochs, window and the wind's dt_s are all
# statements about the physics).
_ROUND_TIMES = 6      # matches render_event's round(k * step_seconds, 6)
_ROUND_PLAYBACK = 2   # matches round((n - 1) * step_seconds, 2)

# Bumped whenever the plan's ITEM SEMANTICS change (filenames, pairing, the
# emission cadences). It is folded into opts_digest, so a resume across a
# version bump can never reuse frames built under the old meaning.
# 5 (2026-09-16): opts_digest carries model.physics_stamp(), so a --resume can
# never splice frames built by two different magnetopause models into one tree.
PLAN_VERSION = 5

# The files each work layer produces, in the order render_event has always
# harvested them, and the manifest-layer name each belongs to. One work item
# can produce two files (boundaries always does; the aurora does when
# curtains are on) - which is why a receipt records a LIST of files.
# 2026-09-14: the boundary layer IS the two translucent shells (OBJ for
# effectModelClass). The .vla ring/meridian wireframes were retired (David:
# the effect-model look is the one to keep).
LAYER_PRODUCTS = {
    # Extension follows dsexport.mesh.MESH_FORMAT so the plan, the writer and the
    # daemon sentinel can never disagree about which file a render produces.
    "boundaries": (("MagnetopauseShell", f"MagnetopauseShell.{_MESH_EXT}"),
                   ("BowShockShell", f"BowShockShell.{_MESH_EXT}")),
    "fieldlines": (("FieldLines", "FieldLines.vlc"),
                   # phase 5 (2026-09-05): beads gliding along +B on the same
                   # traced lines, emitted only when RenderPlan.with_pulses is set
                   ("FieldPulses", "FieldPulses.vlc")),
    "aurora": (("AuroraOval", "AuroraOval.vlc"),
               ("AuroraCurtains", "AuroraCurtains.vlc")),
    "l1": (("L1Marker", "L1Marker.vlc"),),
    # 2026-09-16: two index-driven layers, originally .vlc DOTS with a fixed row
    # count and a parametric morph like the aurora oval. 2026-09-20: both became
    # effectModelClass SURFACES in MESH_FORMAT - the lattice is the same, it just
    # carries faces now, and a mesh cuts between steps because it cannot morph.
    "ringcurrent": (("RingCurrent", f"RingCurrent.{_MESH_EXT}"),),
    "plasmasphere": (("Plasmasphere", f"Plasmasphere.{_MESH_EXT}"),),
    "imf": (("IMFLines", "IMFLines.vlc"),),
}
# Manifest layer order == the .ds player's layer order == the order
# render_event's FRAME_LAYER_FILES has always used. The player's output is
# byte-compared, so this order is part of the contract.
# APPEND only - never insert. The player's output is byte-compared.
MANIFEST_LAYER_ORDER = ("MagnetopauseShell", "BowShockShell", "FieldLines", "AuroraOval",
                        "AuroraCurtains", "L1Marker", "FieldPulses",
                        "RingCurrent", "Plasmasphere", "IMFLines")
# Every layer whose product is a MESH_FORMAT file, DERIVED from LAYER_PRODUCTS rather
# than listed beside it. Listing it is what broke: the plasmasphere and ring current
# became surfaces on 2026-09-20, LAYER_PRODUCTS was updated and the two tables below
# were not, so a fresh render emitted dotModelClass prototypes over .glb files - zero
# geometry, silently, with --validate still passing because every FILE was well formed.
# The converters that repaired the already-rendered trees (tools/resurface_plasma_ring.py)
# carried the right kind the whole time; only the renderer disagreed. Derived, adding a
# mesh layer to LAYER_PRODUCTS is the one edit and the tables cannot drift apart again.
MESH_LAYERS = tuple(n for prods in LAYER_PRODUCTS.values()
                    for n, f in prods if f.rsplit(".", 1)[-1] == _MESH_EXT)
# Each mesh layer has its OWN dome holder (MagnetoEvent<Layer>Holder) whose COLOUR is
# its brightness (dsexport.ds); the player crossfades three fixed slot objects per mesh.
# Meshes cannot morph, so the mesh layers are exactly the non-parametric rows.
SHELL_LAYERS = ("MagnetopauseShell", "BowShockShell")
NON_PARAMETRIC_LAYERS = ("L1Marker",) + MESH_LAYERS
PULSE_LAYERS = ("FieldPulses",)
WIND_LAYER = "WindParticles"

# Layer -> model kind for the manifest and the .ds players. A DOTS file in a
# lineModelClass cooks to zero geometry, so this table is load-bearing.
LAYER_KIND = {"AuroraOval": "dots", "L1Marker": "dots", "WindParticles": "dots",
              "FieldPulses": "dots",
              "IMFLines": "lines"}
LAYER_KIND.update({name: "mesh" for name in MESH_LAYERS})

WORK_LAYERS = ("boundaries", "fieldlines", "aurora", "l1",
               "ringcurrent", "plasmasphere", "imf")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def wind_speedup(factor: float) -> float:
    """Apparent parcel-flow multiplier a retime by `factor` causes.

    The dome flow rate is wind_stream_s / step_seconds (seconds of real flow
    shown per dome second). A retime scales step_seconds by `factor` and
    leaves the real-flow clock alone, so the stream runs 1/factor times as
    fast as the tuned look.
    """
    return 1.0 / float(factor)


def rescale_times(manifest: dict, factor: float) -> dict:
    """A deep-copied manifest with every dome-seconds field scaled by factor.

    Scales: step_seconds, playback_s, every layers[].times[], and the wind
    block's cue_seconds. Leaves untouched: step_min, epochs, window, dt_s,
    and every statistic. Does NOT record provenance - the caller owns the
    'retimed'/'provenance_notes' story.
    """
    factor = float(factor)
    if factor <= 0:
        raise ValueError(f"factor must be positive, got {factor}")
    out = copy.deepcopy(manifest)
    step = float(out["step_seconds"]) * factor
    out["step_seconds"] = round(step, _ROUND_TIMES)
    n = int(out.get("n_frames") or 0)
    if n > 1:
        out["playback_s"] = round((n - 1) * step, _ROUND_PLAYBACK)
    elif "playback_s" in out:
        out["playback_s"] = round(float(out["playback_s"]) * factor,
                                  _ROUND_PLAYBACK)
    for lay in out.get("layers") or []:
        times = lay.get("times")
        if times:
            lay["times"] = [round(float(t) * factor, _ROUND_TIMES)
                            for t in times]
    wind = out.get("wind")
    if isinstance(wind, dict) and "cue_seconds" in wind:
        wind["cue_seconds"] = round(float(wind["cue_seconds"]) * factor,
                                    _ROUND_TIMES)
    return out


# --- the plan -----------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    """One unit of render work: everything needed to produce its files.

    layer       'boundaries' | 'fieldlines' | 'aurora' | 'l1'
    index       the layer's OWN running index - the %04d in the filenames.
                It is not the field-step index: the aurora runs on the Kp
                cadence, the field lines can run on a stride, and the L1
                marker skips states with no ephemeris, so the layers stopped
                sharing a numbering when the three cadences landed.
    dest        the item's primary output filename (boundaries also emit
                BowShock_<index>.vla - see RenderPlan.item_products).
    time_s      the dome second at which this file becomes the active frame.
    src_k       state index on the D/P/L rows, or None for a non-parametric
                item (the last frame of a layer, and every L1 marker).
    dst_k       state index on the V rows - and the state whose provenance
                comments the file carries.
    parametric  src_k is not None, i.e. this item spans two distinct states.
                NOTE this describes the MORPH PAIRING, not the file's own
                'set parametric' token: the aurora's final frame is written
                PARAMETRIC-but-static (its grid morphs into itself), because
                the player emits no morph cue for a frame with no successor.
    """
    layer: str
    index: int
    dest: str
    time_s: float
    src_k: int | None
    dst_k: int
    parametric: bool

    @property
    def key(self) -> str:
        """Receipt/scratch name for this item - unique within a plan."""
        return f"{self.layer}_{self.index:04d}"


@dataclass(frozen=True)
class WindPlan:
    """The parcel stream's own file grid.

    The wind is the one layer whose work is NOT decomposable into independent
    items: it is a single pool advected continuously through the whole event,
    so snapshot j cannot be computed without having computed j-1. What the
    plan CAN state up front is exactly which files come out and when, because
    both are pure arithmetic on (n_states, sub, step_seconds).
    """
    sub: int
    stream_s: float
    files: tuple[str, ...]
    times: tuple[float, ...]
    dt_s: float
    cue_s: float


@dataclass(frozen=True)
class RenderPlan:
    """Every file an event render will produce, decided before any physics."""
    event_id: str
    frame: str
    step_min: float
    step_seconds: float
    state_epochs: tuple[str, ...]
    trace_epochs: tuple[int, ...]
    items: tuple[Item, ...]
    wind: WindPlan | None
    opts_digest: str
    # items[i] is emitted at field step item_steps[i]. Kept beside items
    # rather than inside Item because a step index is a fact about the LOOP
    # that drives the wind, not about the item, which knows only the two
    # states it pairs and where it lands.
    item_steps: tuple[int, ...] = ()
    layers: tuple[str, ...] = ()
    fieldline_every: int = 1
    aurora_max_hold_min: float = 60.0
    with_curtains: bool = False
    with_shells: bool = False
    with_pulses: bool = False
    with_ring: bool = False
    with_plasmasphere: bool = False
    with_imf: bool = False
    aurora_steps: tuple[int, ...] = ()
    external: str | None = None
    # Significant figures for every light-year coordinate the science layers
    # write: 9 (float32-exact, the default) or 17 (full float64 round-trip).
    # Part of opts_digest, because half a tree at one precision and half at the
    # other is exactly the mixed package --resume exists to prevent.
    coord_digits: int = COORD_SIG_DIGITS

    @property
    def n_frames(self) -> int:
        return len(self.state_epochs)

    def item_products(self, item: Item) -> tuple[tuple[str, str, str], ...]:
        """(manifest layer, name the writer emits, name it lands under)."""
        out = []
        for name, filename in LAYER_PRODUCTS[item.layer]:
            if name == "AuroraCurtains" and not self.with_curtains:
                continue
            if name == "RingCurrent" and not self.with_ring:
                continue
            if name == "Plasmasphere" and not self.with_plasmasphere:
                continue
            if name == "IMFLines" and not self.with_imf:
                continue
            if name in PULSE_LAYERS and not self.with_pulses:
                continue
            stem, _, suffix = filename.rpartition(".")
            out.append((name, filename, f"{stem}_{item.index:04d}.{suffix}"))
        return tuple(out)

    def dest_names(self) -> tuple[str, ...]:
        """Every file this plan promises, wind included, in emission order."""
        names = [dest for it in self.items
                 for _n, _s, dest in self.item_products(it)]
        if self.wind is not None:
            names.extend(self.wind.files)
        return tuple(names)

    def layer_rows(self) -> list[dict]:
        """The manifest's layers[] for everything except the wind.

        Built from the plan rather than from a directory scan, so a parallel
        build - whose files land in whatever order the pool finishes them -
        produces exactly the serial build's manifest and therefore exactly the
        serial build's .ds player.
        """
        files: dict[str, list[str]] = {n: [] for n in MANIFEST_LAYER_ORDER}
        times: dict[str, list[float]] = {n: [] for n in MANIFEST_LAYER_ORDER}
        for it in self.items:
            for name, _src, dest in self.item_products(it):
                files[name].append(dest)
                times[name].append(it.time_s)
        rows = []
        for name in MANIFEST_LAYER_ORDER:
            if not files[name]:
                continue
            rows.append({"name": name, "kind": LAYER_KIND.get(name, "lines"),
                         # One dome holder per ASSET TYPE, keyed by the layer's
                         # own name, so a presenter can dim or hide this layer
                         # alone (dsexport.ds).
                         "holder": name,
                         "files": files[name],
                         # The L1 marker is a single dot written
                         # NON_PARAMETRIC, so it must get no morph cue.
                         "parametric": name not in NON_PARAMETRIC_LAYERS,
                         "times": times[name]})
        return rows

    def summary(self) -> dict:
        """The JSON-safe fingerprint written to Models/.plan.json for --resume."""
        return {"plan_version": PLAN_VERSION, "opts_digest": self.opts_digest,
                "event_id": self.event_id, "frame": self.frame,
                "n_frames": self.n_frames, "n_items": len(self.items),
                "n_traces": len(self.trace_epochs),
                "n_wind_files": 0 if self.wind is None else len(self.wind.files),
                "fieldline_every": self.fieldline_every,
                "coord_digits": self.coord_digits,
                "window": ([self.state_epochs[0], self.state_epochs[-1]]
                           if self.state_epochs else [])}


# --- emission cadences --------------------------------------------------------

def _emission_indices(states, stride=None, max_hold_min=None) -> list[int]:
    """Step indices at which a layer emits a frame. Two cadences, one rule.

    stride=N        every Nth step (the field lines' --fieldline-every knob).
    max_hold_min=M  step 0, every step where Kp CHANGED, and whenever the hold
                    since the last emitted frame reaches M minutes (the aurora
                    economy - Kp is a 3-hourly index, so emitting a 6 MB oval
                    every 10 minutes writes ~18 identical copies of each
                    distinct one). See EVENT_AURORA_MAX_HOLD_MIN in render.py
                    for why the hold is capped even when Kp is flat.

    Both always include step 0 and the LAST step, because the final frame is
    the one the series comes to rest on.
    """
    n = len(states)
    if n == 0:
        return []
    if max_hold_min is not None:
        idx = [0]
        last_kp = float(states[0]["kp"]["value"])
        last_t = _parse_iso(states[0]["epoch"])
        for k in range(1, n):
            kp = float(states[k]["kp"]["value"])
            t = _parse_iso(states[k]["epoch"])
            if kp != last_kp or (t - last_t).total_seconds() >= max_hold_min * 60.0:
                idx.append(k)
                last_kp, last_t = kp, t
    else:
        idx = list(range(0, n, max(1, int(stride or 1))))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def _has_l1(state: dict) -> bool:
    """Exactly render._render_l1's own test: no ephemeris, no marker file."""
    return bool(state.get("propagation", {}).get("l1_gse_re"))


def opts_digest(**opts) -> str:
    """sha1 of every render-shaping option. Stable across runs and dict order."""
    payload = json.dumps({"plan_version": PLAN_VERSION, **opts},
                         sort_keys=True, separators=(",", ":"),
                         default=str).encode("utf-8")
    return hashlib.sha1(payload).hexdigest()


def build_plan(states, *, event_id: str,
               layers=("boundaries", "fieldlines", "aurora"),
               step_min: float, step_seconds: float, frame: str,
               fieldline_every: int = 1, aurora_max_hold_min: float = 60.0,
               with_wind: bool = True, wind_sub: int = 4,
               wind_stream_s: float = 120.0, with_curtains: bool = False,
               with_shells: bool = False,
               with_pulses: bool = False,
               with_ring: bool = False,
               with_plasmasphere: bool = False,
               with_imf: bool = False,
               external: str | None = None,
               coord_digits: int = COORD_SIG_DIGITS) -> RenderPlan:
    """Decide the whole render up front. No geopack, no I/O, no side effects.

    The pairing rule, which every item below is an instance of: FRAME k
    carries state k on its D/P/L rows and state k+1 on its V rows, so the .ds
    plays the storm as continuous motion. The LAST frame of every layer has no
    successor and is written non-parametric, so the series comes to rest on
    the true final state.

    layers is the subset of ('boundaries', 'fieldlines', 'aurora', 'l1') to
    render - 'l1' present is what --with-l1 means. Layers the caller did not
    ask for contribute no items and therefore no manifest rows.
    """
    n = len(states)
    want = set(layers)
    unknown = want - set(WORK_LAYERS)
    if unknown:
        raise ValueError(f"unknown layers {sorted(unknown)}; "
                         f"known: {list(WORK_LAYERS)}")
    fieldline_every = max(1, int(fieldline_every))
    step_seconds = float(step_seconds)
    if int(coord_digits) not in (COORD_SIG_DIGITS, 17):
        raise ValueError(
            f"coord_digits must be {COORD_SIG_DIGITS} or 17, got {coord_digits}")
    coord_digits = int(coord_digits)

    fl_idx = (_emission_indices(states, stride=fieldline_every)
              if "fieldlines" in want else [])
    aur_idx = (_emission_indices(states, max_hold_min=aurora_max_hold_min)
               if "aurora" in want else [])
    fl_at = {k: j for j, k in enumerate(fl_idx)}
    aur_at = {k: j for j, k in enumerate(aur_idx)}

    items: list[Item] = []
    item_steps: list[int] = []
    n_bound = n_l1 = n_ring = n_plasma = n_imf = 0

    def emit(item: Item, step: int) -> None:
        items.append(item)
        item_steps.append(step)

    for k in range(n):
        last = (k == n - 1)
        # "cur" for this field step: state k+1, or state k again on the last
        # step, which is what makes the last frame non-parametric.
        dst = k if last else k + 1
        src = None if last else k
        t = round(k * step_seconds, _ROUND_TIMES)

        if "boundaries" in want:
            emit(Item("boundaries", n_bound, f"Magnetopause_{n_bound:04d}.vla",
                      t, src, dst, src is not None), k)
            n_bound += 1

        # Index-driven layers: one frame per step, morphing like the boundaries
        # did. Both re-bake at their own epoch, so the dipole tilt (ring current)
        # and the corotation (plasmasphere) come out right frame by frame.
        if "ringcurrent" in want and with_ring:
            emit(Item("ringcurrent", n_ring, f"RingCurrent_{n_ring:04d}.vlc",
                      t, src, dst, src is not None), k)
            n_ring += 1
        if "plasmasphere" in want and with_plasmasphere:
            emit(Item("plasmasphere", n_plasma, f"Plasmasphere_{n_plasma:04d}.vlc",
                      t, src, dst, src is not None), k)
            n_plasma += 1
        if "imf" in want and with_imf:
            emit(Item("imf", n_imf, f"IMFLines_{n_imf:04d}.vlc",
                      t, src, dst, src is not None), k)
            n_imf += 1

        j = fl_at.get(k)
        if j is not None:
            f_last = (j == len(fl_idx) - 1)
            emit(Item("fieldlines", j, f"FieldLines_{j:04d}.vlc", t,
                      None if f_last else fl_idx[j],
                      n - 1 if f_last else fl_idx[j + 1], not f_last), k)

        a = aur_at.get(k)
        if a is not None:
            # The aurora renders the segment from THIS emission step to the
            # NEXT one, so its morph spans the whole Kp interval rather than
            # one field step.
            a_last = (a == len(aur_idx) - 1)
            emit(Item("aurora", a, f"AuroraOval_{a:04d}.vlc", t,
                      None if a_last else aur_idx[a],
                      aur_idx[a] if a_last else aur_idx[a + 1], not a_last), k)

        if "l1" in want and _has_l1(states[dst]):
            emit(Item("l1", n_l1, f"L1Marker_{n_l1:04d}.vlc", t, None, dst,
                      False), k)
            n_l1 += 1

    wind = None
    if with_wind and n >= 2:
        sub = max(1, int(wind_sub))
        cue_s = step_seconds / sub
        n_files = (n - 1) * sub + 1
        wind = WindPlan(sub=sub, stream_s=float(wind_stream_s),
                        files=tuple(f"WindParticles_{i:04d}.vlc"
                                    for i in range(n_files)),
                        times=tuple(round(i * cue_s, _ROUND_TIMES)
                                    for i in range(n_files)),
                        dt_s=float(wind_stream_s) / sub, cue_s=cue_s)

    digest = opts_digest(
        event_id=event_id, frame=frame, step_min=float(step_min),
        step_seconds=step_seconds, layers=sorted(want),
        fieldline_every=fieldline_every,
        aurora_max_hold_min=float(aurora_max_hold_min),
        with_curtains=bool(with_curtains), external=external,
        with_shells=bool(with_shells), with_pulses=bool(with_pulses),
        with_ring=bool(with_ring), with_plasmasphere=bool(with_plasmasphere),
        with_imf=bool(with_imf), coord_digits=coord_digits,
        wind=(None if wind is None
              else {"sub": wind.sub, "stream_s": wind.stream_s}),
        epochs=[s["epoch"] for s in states],
        models=[s["model"]["external"] for s in states],
        kp=[float(s["kp"]["value"]) for s in states],
        # Which PHYSICS built the geometry. Not a formatting option: change the
        # magnetopause model and every cached frame becomes incompatible, so the
        # digest has to move or --resume will happily mix them.
        physics=physics_stamp())

    return RenderPlan(
        event_id=event_id, frame=frame, step_min=float(step_min),
        step_seconds=step_seconds,
        state_epochs=tuple(s["epoch"] for s in states),
        trace_epochs=tuple(fl_idx), items=tuple(items), wind=wind,
        opts_digest=digest, item_steps=tuple(item_steps),
        layers=tuple(sorted(want)),
        fieldline_every=fieldline_every,
        aurora_max_hold_min=float(aurora_max_hold_min),
        with_curtains=bool(with_curtains), with_shells=bool(with_shells),
        with_pulses=bool(with_pulses), with_ring=bool(with_ring),
        with_plasmasphere=bool(with_plasmasphere), with_imf=bool(with_imf),
        aurora_steps=tuple(aur_idx),
        external=external, coord_digits=coord_digits)


# --- the manifest -------------------------------------------------------------

def peak_caption(states: list[dict]) -> str:
    """'Peak Kp 9, min Bz -50 nT, min SYM-H -412 nT' - the event banner line.

    The disturbance index is named after what the state actually carries
    (dst.source): curated OMNI events store 1-minute SYM/H, look-backs store
    hourly Kyoto Dst. They are not the same measurement, so the banner must
    not call one by the other's name.
    """
    kp = max(float(s["kp"]["value"]) for s in states)
    dsts = [s["dst"]["value"] for s in states if s.get("dst")]
    bz = min(float(s["sw"]["bz_gsm"]) for s in states)
    out = f"Peak Kp {kp:g}, min Bz {bz:.0f} nT"
    if dsts:
        sources = {(s.get("dst") or {}).get("source") for s in states}
        label = "SYM-H" if sources == {"omni_symh"} else "Dst"
        out += f", min {label} {min(dsts):.0f} nT"
    return out


def _when(ep: str) -> str:
    try:
        return datetime.fromisoformat(str(ep).replace("Z", "+00:00")).strftime("%H:%M UT %d %b")
    except ValueError:
        return str(ep)


def key_moments(states: list[dict]) -> str:
    """One presenter line of DATA-DERIVED highlights, <= 255 chars, e.g.
    'KEY MOMENTS: shock 14:40 UT 15 Jul (3 -> 30 nPa) | Bz min -60 nT at 19:50 UT 15 Jul | ...'

    Pure: the largest one-step pressure jump (the shock), the Bz minimum, the speed
    peak, when Kp first reaches its maximum, the Dst/SYM-H minimum, and how many hours
    Bz spent below -10 nT. Nothing here is typed in by hand, so it is right for every
    event, the look-back included.
    """
    if len(states) < 2:
        return ""
    sw = [s["sw"] for s in states]
    ep = [str(s["epoch"]) for s in states]
    parts = []
    pd = [float(x.get("pdyn_npa", 0.0)) for x in sw]
    kj = max(range(1, len(pd)), key=lambda k: pd[k] - pd[k - 1])
    if pd[kj] >= 1.5 * max(pd[kj - 1], 0.1) and pd[kj] - pd[kj - 1] >= 1.0:
        parts.append(f"shock {_when(ep[kj])} ({pd[kj - 1]:.0f} -> {pd[kj]:.0f} nPa)")
    kb = min(range(len(sw)), key=lambda k: float(sw[k]["bz_gsm"]))
    parts.append(f"Bz min {float(sw[kb]['bz_gsm']):.0f} nT at {_when(ep[kb])}")
    kv = max(range(len(sw)), key=lambda k: float(sw[k]["speed"]))
    parts.append(f"wind peaks {float(sw[kv]['speed']):.0f} km/s {_when(ep[kv])}")
    kps = [float(s["kp"]["value"]) for s in states]
    kmax = max(kps)
    parts.append(f"Kp {kmax:g} from {_when(ep[kps.index(kmax)])}")
    dsts = [(float(s["dst"]["value"]), k) for k, s in enumerate(states) if s.get("dst")]
    if dsts:
        sources = {(s.get("dst") or {}).get("source") for s in states}
        label = "SYM-H" if sources == {"omni_symh"} else "Dst"
        dmin, kd = min(dsts)
        parts.append(f"{label} min {dmin:.0f} nT at {_when(ep[kd])}")
    try:
        t0 = datetime.fromisoformat(ep[0].replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(ep[1].replace("Z", "+00:00"))
        step_h = abs((t1 - t0).total_seconds()) / 3600.0
    except ValueError:
        step_h = 0.0
    south_h = step_h * sum(1 for x in sw if float(x["bz_gsm"]) < -10.0)
    if south_h > 0:
        parts.append(f"Bz below -10 nT for {south_h:.0f} h")
    out = "KEY MOMENTS: " + " | ".join(parts)
    while len(out) > 255 and len(parts) > 1:
        parts.pop()
        out = "KEY MOMENTS: " + " | ".join(parts)
    return "".join(ch if 32 <= ord(ch) < 127 and ch not in '";' else " " for ch in out)


def plan_to_manifest(plan: RenderPlan, states: list[dict],
                     extra: dict | None = None) -> dict:
    """The frames manifest, decided entirely by the plan.

    Everything here is knowable before the first geopack call; the executor
    only ever ADDS to it afterwards (the wind's layers[] row, the wind stats
    block, and the built/build_s/bytes timing keys).

    `extra` carries the values a pure module cannot compute - the event title
    (registry/disk lookup) and aurora_label (zhang_paxton) - and is merged
    last, so a caller can override anything.
    """
    n = plan.n_frames
    aur_idx = list(plan.aurora_steps)
    manifest = {
        "schema": 1,
        "event_id": plan.event_id,
        "title": plan.event_id,
        "step_min": plan.step_min,
        "step_seconds": plan.step_seconds,
        "n_frames": n,
        "frame": plan.frame,
        "epochs": list(plan.state_epochs),
        "window": {"start": plan.state_epochs[0], "stop": plan.state_epochs[-1]},
        "model": states[0]["model"]["external"],
        "layers": plan.layer_rows(),
        "aurora_steps": aur_idx,
        # Kp at each emission step, in step order - what the AuroraOval frames
        # were built from AND what drives the optional native-aurora script
        # (dsexport.ds.write_native_aurora_ds), so the two ovals cannot drift.
        "aurora_kp": [float(states[k]["kp"]["value"]) for k in aur_idx],
        "aurora_max_hold_min": plan.aurora_max_hold_min,
        # The dome-visible readout, one string per field step (dsexport.ds emits
        # them on a textClass; MagnetoStyle.js layer('HUD') is the panel toggle).
        "hud": [hud_text(s) for s in states],
        "peak_caption": peak_caption(states),
        # presenter line of data-derived highlights (dsexport.ds posts it after the banner)
        "key_moments": key_moments(states),
        "playback_s": round((n - 1) * plan.step_seconds, _ROUND_PLAYBACK),
        # --- additive since the Phase 3 plan/executor split -----------------
        "fieldline_every": plan.fieldline_every,
        "coord_digits": plan.coord_digits,
        "plan_digest": plan.opts_digest,
        # What physics drew this. tools/check_physics.py compares it across every
        # rendered package and fails on a mixed set - that is how "is everything
        # agreeable?" gets answered with a fact instead of an opinion.
        "physics": physics_stamp(),
    }
    manifest.update(extra or {})
    return manifest
