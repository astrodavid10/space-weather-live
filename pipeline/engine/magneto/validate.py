"""Validation gates for Build A - run them, don't trust the pictures.

The brief lists four gates. The two this module owns are the ones a wrong
state object or a mis-wired model would fail silently:

  gate 1  subsolar magnetopause standoff sits at 10-11 R_E for nominal
          conditions, compresses to 6-8 R_E in a storm, and moves inward when
          the dynamic pressure doubles. If the standoff does not MOVE, the
          state object is not reaching the model and the dome is showing a
          pretty constant.
  gate 2  geopack's two magnetopause models (Shue 1998 and T96) agree to
          within their known difference over a Pdyn x Bz sweep. t96_mgnp has
          no Bz term at all, so the two must track each other for northward
          and weakly southward IMF and are EXPECTED to diverge for strongly
          southward IMF - that divergence is the Shue Bz dependence, and the
          Bz = -15 row is printed unasserted to show it.

  gate 5  the last closed field line vs the drawn magnetopause. The boundary
          we DRAW comes from the solar wind (Shue 1998); the field lines we
          draw come from a completely separate empirical model (T89/T96, from
          Kp or parmod). Nothing couples them, so nothing stops the dome from
          showing closed flux outside its own magnetopause. This bisects the
          traced field's own open/closed transition and puts the two side by
          side. Asserted on the QUIET state, where both models are in their
          comfort zone; the storm row is PRINTED UNASSERTED for the same
          reason gate 2's Bz = -15 row is - T89 has no dynamic-pressure term
          at all, so its last closed field line cannot follow a Shue nose that
          Pdyn is driving inward, and that divergence is the thing worth
          seeing.

  gate 4  kill the network mid-run and prove the show degrades instead of
          breaking: a local fixture server feeds the poller, the server is
          then killed, and the daemon must keep cycling with no traceback, a
          banner whose age climbs, a tier that flips to CACHED at 15 minutes
          and an event that arms past 3 hours. The clock is INJECTED, so
          those hours pass in seconds - waiting them out would make the gate
          untestable and it would still only prove the same three branches.

Gate 3 (aurora oval expands equatorward with Kp) needs a multi-day capture of
OVATION against Kp; the runner reports it as not implemented here rather than
pretending it passed.

    python -m magneto.validate --all
    python -m magneto.validate --gate 1
    python -m magneto.validate --gate 4
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))          # so gate 6 can import tools.check_physics

from magneto import model as model_mod          # noqa: E402
from magneto import paths as paths_mod          # noqa: E402

FIXTURES = REPO / "tests" / "fixtures" / "states"
PAYLOADS = REPO / "tests" / "fixtures" / "payloads"

# Gates named in the brief but implemented elsewhere - reported, never faked.
GATES_ELSEWHERE = {
    3: "aurora oval expands equatorward as Kp rises - needs a multi-day "
       "OVATION/Kp capture (see cache/ovation snapshots)",
}


def load_state(name: str) -> dict:
    """A fixture state object by stem, e.g. 'nominal' or 'storm'."""
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _with_sw(state: dict, **overrides) -> dict:
    """Copy of a state with solar-wind fields overridden (pdyn_npa, bz_gsm...)."""
    out = copy.deepcopy(state)
    out["sw"].update(overrides)
    return out


def _check(lines: list[str], label: str, ok: bool, detail: str) -> bool:
    lines.append(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    return ok


# --- gate 1 -------------------------------------------------------------------

def gate1_standoff():
    """Standoff lands in band for nominal + storm, and responds to pressure."""
    from magneto.model.boundaries import magnetopause_radius

    lines = ["gate 1 - subsolar magnetopause standoff"]
    nominal = load_state("nominal")
    storm = load_state("storm")
    doubled = _with_sw(nominal, pdyn_npa=2.0 * nominal["sw"]["pdyn_npa"])

    r_nom = float(magnetopause_radius(0.0, nominal))
    r_storm = float(magnetopause_radius(0.0, storm))
    r_doubled = float(magnetopause_radius(0.0, doubled))
    shift = r_nom - r_doubled

    ok = True
    ok &= _check(lines, "nominal in [10, 11] R_E",
                 10.0 <= r_nom <= 11.0,
                 f"{r_nom:.3f} R_E (Pdyn {nominal['sw']['pdyn_npa']:g} nPa, "
                 f"Bz {nominal['sw']['bz_gsm']:+g} nT)")
    ok &= _check(lines, "storm in [6, 8] R_E",
                 6.0 <= r_storm <= 8.0,
                 f"{r_storm:.3f} R_E (Pdyn {storm['sw']['pdyn_npa']:g} nPa, "
                 f"Bz {storm['sw']['bz_gsm']:+g} nT)")
    ok &= _check(lines, "Pdyn x2 moves the nose inward > 0.5 R_E",
                 shift > 0.5,
                 f"{r_nom:.3f} -> {r_doubled:.3f} R_E ({shift:.3f} R_E inward "
                 f"at Pdyn {doubled['sw']['pdyn_npa']:g} nPa)")
    return bool(ok), lines


# --- gate 2 -------------------------------------------------------------------

CROSSCHECK_PDYN = (1.0, 2.0, 4.0, 8.0)
CROSSCHECK_BZ = (5.0, 0.0, -5.0, -15.0)
CROSSCHECK_TOL_RE = 1.5
CROSSCHECK_ASSERT_BZ_MIN = -5.0     # rows below this are printed, not asserted


def gate2_crosscheck():
    """Shue vs T96 subsolar standoff over a Pdyn x Bz sweep."""
    from magneto.model.boundaries import mgnp_crosscheck

    lines = ["gate 2 - shuetal_mgnp vs t96_mgnp subsolar standoff",
             "  Pdyn/nPa   Bz/nT    shue      t96     |diff|   asserted",
             "  " + "-" * 54]
    nominal = load_state("nominal")
    ok = True
    worst = (0.0, None)
    for pdyn in CROSSCHECK_PDYN:
        for bz in CROSSCHECK_BZ:
            cc = mgnp_crosscheck(_with_sw(nominal, pdyn_npa=pdyn, bz_gsm=bz))
            asserted = bz >= CROSSCHECK_ASSERT_BZ_MIN
            within = cc["diff_re"] <= CROSSCHECK_TOL_RE
            mark = ("yes" if within else "YES - FAIL") if asserted else "no"
            lines.append(
                f"  {pdyn:8.1f} {bz:+7.1f} {cc['shue_nose_re']:8.3f} "
                f"{cc['t96_nose_re']:8.3f} {cc['diff_re']:8.3f}   {mark}")
            if asserted:
                ok &= within
                if cc["diff_re"] > worst[0]:
                    worst = (cc["diff_re"], (pdyn, bz))
    lines.append(f"  worst asserted |diff| {worst[0]:.3f} R_E at "
                 f"Pdyn {worst[1][0]:g} nPa Bz {worst[1][1]:+g} nT "
                 f"(tolerance {CROSSCHECK_TOL_RE:g} R_E for "
                 f"Bz >= {CROSSCHECK_ASSERT_BZ_MIN:+g} nT)")
    lines.append("  Bz -15 rows are unasserted: t96_mgnp has no Bz term, so the "
                 "gap there IS the Shue southward-IMF erosion")
    return bool(ok), lines


# --- gate 5 -------------------------------------------------------------------

GATE5_N_MLT = 8
GATE5_NOSE_TOL_PCT = 25.0        # generous: an honesty cross-check, not a fit
GATE5_FLANK_TOL_PCT = 25.0       # ... same band on the untruncated MLT rows


def _lcfl_table(lines: list[str], label: str, cc: dict) -> None:
    """One lcfl_crosscheck() result as a readable block of gate output."""
    nose = ("none - nothing closed at the bracket floor"
            if cc["lcfl_nose_re"] is None else
            f"{cc['lcfl_nose_re']:.3f} R_E vs Shue nose "
            f"{cc['shue_nose_re']:.3f} R_E ({cc['nose_pct_diff']:+.1f}%)")
    lines.append(f"  {label} ({cc['model'].upper()}, epoch {cc['epoch']}, "
                 f"{cc['n_traces']} traces in {cc['elapsed_s']:.1f} s)")
    lines.append(f"    subsolar last closed field line: {nose}")
    lines.append("     MLT   polar-cap lat   LCFL apex   apex theta   "
                 "Shue there    diff")
    for row in cc["rows"]:
        if row["polar_cap_lat_deg"] is None:
            lines.append(f"    {row['mlt_h']:4.1f}   (no closed line at the "
                         f"bracket floor)")
            continue
        note = "  rlim-truncated" if row["truncated"] else ""
        if row.get("shue_clamped"):
            note += "  Shue on the search bound"
        lines.append(
            f"    {row['mlt_h']:4.1f}   {row['polar_cap_lat_deg']:11.2f}   "
            f"{row['last_closed_eq_re']:9.2f}   {row['apex_theta_deg']:10.1f}   "
            f"{row['shue_re']:10.2f}  {row['pct_diff']:+6.1f}%{note}")


def gate5_lcfl():
    """Traced last-closed-field-line vs the drawn Shue magnetopause."""
    from magneto.model.boundaries import LCFL_RLIM_RE, lcfl_crosscheck

    lines = ["gate 5 - last closed field line vs the drawn magnetopause"]
    quiet = lcfl_crosscheck(load_state("nominal"), n_mlt=GATE5_N_MLT)
    storm = lcfl_crosscheck(load_state("storm"), n_mlt=GATE5_N_MLT)
    _lcfl_table(lines, "quiet state", quiet)
    _lcfl_table(lines, "storm state", storm)

    ok = True
    ok &= _check(lines, f"quiet subsolar LCFL within {GATE5_NOSE_TOL_PCT:g}% "
                 "of the Shue nose",
                 quiet["nose_pct_diff"] is not None
                 and abs(quiet["nose_pct_diff"]) <= GATE5_NOSE_TOL_PCT,
                 f"{quiet['lcfl_nose_re']:.3f} vs {quiet['shue_nose_re']:.3f} "
                 f"R_E ({quiet['nose_pct_diff']:+.1f}%)"
                 if quiet["nose_pct_diff"] is not None else "no closed line")

    usable = [r for r in quiet["rows"]
              if r["pct_diff"] is not None and not r["truncated"]]
    worst = max(usable, key=lambda r: abs(r["pct_diff"])) if usable else None
    ok &= _check(lines, f"every untruncated quiet MLT row within "
                 f"{GATE5_FLANK_TOL_PCT:g}% of Shue at the same angle",
                 bool(usable) and abs(worst["pct_diff"]) <= GATE5_FLANK_TOL_PCT,
                 f"{len(usable)} of {len(quiet['rows'])} rows usable "
                 f"(the rest sit on the {LCFL_RLIM_RE:g} R_E trace limit), "
                 f"worst {worst['pct_diff']:+.1f}% at MLT {worst['mlt_h']:g}"
                 if usable else "no untruncated rows")

    # The storm's MAGNITUDE is not assertable (see below), but its DIRECTION
    # is: a storm has to move the traced boundary inward, like the Shue one.
    both = quiet["lcfl_nose_re"] is not None and storm["lcfl_nose_re"] is not None
    ok &= _check(lines, "a storm moves the traced boundary inward too",
                 both and storm["lcfl_nose_re"] < quiet["lcfl_nose_re"],
                 f"subsolar LCFL {quiet['lcfl_nose_re']:.3f} -> "
                 f"{storm['lcfl_nose_re']:.3f} R_E while the Shue nose goes "
                 f"{quiet['shue_nose_re']:.3f} -> {storm['shue_nose_re']:.3f}"
                 if both else "no closed line in one of the two states")

    lines.append(f"  storm subsolar gap {storm['nose_pct_diff']:+.1f}% is "
                 "PRINTED, NOT ASSERTED: T89's external field has no dynamic-"
                 "pressure term at all, so its last closed field line cannot "
                 "follow a Shue nose that Pdyn is driving inward. The gap IS "
                 "that missing term, and it is why storm frames render on T96.")
    lines.append(f"  rows whose apex sits at the {LCFL_RLIM_RE:g} R_E trace "
                 "limit are labelled and never asserted on - the real "
                 "nightside last closed line stretches far past it.")
    return bool(ok), lines


# --- gate 4 -------------------------------------------------------------------

GATE4_CLOCK_STEP_S = 300.0       # simulated seconds per cycle
GATE4_SIM_S = 3.2 * 3600.0       # run just past the 3 h arm threshold
GATE4_WIND_FRAMES = 2            # keeps the one real render inside the budget
GATE4_WIND_EVERY_S = 12 * 3600.0  # see the comment in gate4_network_kill
GATE4_BUDGET_S = 180.0


class _SimClock:
    """Injected clock: every read returns a time `step` further on.

    The daemon reads the clock exactly once per cycle, so N cycles simulate
    N * step seconds of staleness. Waiting out three real hours to watch a
    threshold fire would not test anything the fast-forward does not.
    """

    def __init__(self, start: datetime, step_s: float = GATE4_CLOCK_STEP_S):
        self.t = start
        self.step = float(step_s)
        self.reads = 0

    def __call__(self) -> datetime:
        now = self.t
        self.t = self.t + timedelta(seconds=self.step)
        self.reads += 1
        return now


def _fixture_server(payload_dir: Path = PAYLOADS):
    """Serve tests/fixtures/payloads over http on 127.0.0.1. -> (srv, base_url).

    Routes are keyed by each registered endpoint's own URL PATH, so the poller
    reaches exactly the files it would reach live, including the conditional
    GET (an ETag is offered and honoured with a 304).
    """
    import hashlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlsplit

    from livecore.endpoints import ENDPOINTS

    routes = {}
    for key, ep in ENDPOINTS.items():
        f = payload_dir / f"{key}.json"
        if f.exists():
            body = f.read_bytes()
            routes[urlsplit(ep.url).path] = (
                body, '"' + hashlib.sha256(body).hexdigest()[:16] + '"')
    if not routes:
        raise FileNotFoundError(
            f"no recorded payloads in {payload_dir} - run "
            f"`python -m livecore.poller --record`")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):                                  # noqa: N802
            hit = routes.get(urlsplit(self.path).path)
            if hit is None:
                self.send_error(404)
                return
            body, etag = hit
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("ETag", etag)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):                      # keep the gate quiet
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, name="gate4-fixtures",
                     daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _age_from_banner(banner: str) -> str:
    """The '(Xm ago)' fragment of a status banner, for the climbing check."""
    m = re.search(r"\(([^)]*ago)\)", banner or "")
    return m.group(1) if m else ""


def gate4_network_kill():
    """Fixture server -> poller -> kill the server -> the show degrades.

    What this actually exercises, end to end and with no network: the real
    Poller against a real HTTP server, the real CacheStore, the real state
    builder, the real daemon loop, the real renderer and the real atomic
    publish. Only two things are simulated, both stated in the output:

      * the clock (injected, 5 simulated minutes per cycle) - so the 15-minute
        and 3-hour thresholds arrive in seconds instead of hours
      * the wind re-bake cadence is stretched to 12 h. With a 5-minute-per-
        cycle clock the real 5-minute cadence would make every cycle re-bake
        the parcels, and the gate would end up measuring the wind baker rather
        than the degradation path. One full render still happens (cycle 1).
    """
    import shutil
    import tempfile

    from livecore.cache import CacheStore
    from livecore.endpoints import set_base_url
    from livecore.poller import Poller
    from livecore.state import build_state

    from .daemon import EVENT_SWITCH_S, Daemon

    lines = ["gate 4 - network kill -> cache -> armed event"]
    t_gate = time.time()
    tmp = Path(tempfile.mkdtemp(prefix="gate4_"))
    srv = None
    previous_base = None
    ok = True
    try:
        srv, base = _fixture_server()
        previous_base = set_base_url(base)
        cache_dir, root = tmp / "cache", tmp / "out"
        cache = CacheStore(cache_dir)

        # -- 1. the network is UP: fill the cache the way production does ----
        poller = Poller(cache)
        polled = poller.run_once()
        keys = sorted(poller.poll_counts)
        ok &= _check(lines, "poller filled the cache from the fixture server",
                     polled and len(keys) >= 6,
                     f"{len(keys)} endpoints, 0 errors" if polled
                     else f"errors: {dict(poller.error_counts)}")

        seed = build_state(cache, datetime.now(timezone.utc))
        if seed is None:
            return False, lines + ["  [FAIL] state builder: no state from the "
                                   "recorded payloads"]
        epoch = datetime.fromisoformat(seed["epoch"].replace("Z", "+00:00"))
        clock = _SimClock(epoch + timedelta(seconds=60))

        # -- 2. KILL the network --------------------------------------------
        srv.shutdown()
        srv.server_close()
        srv = None
        lines.append(f"  server on {base} killed - cache holds "
                     f"{len(keys)} payloads, epoch {seed['epoch']}")
        lines.append("  (the ConnectionError tracebacks logged below the gate "
                     "output are the EXPECTED ones: the poller logs and "
                     "swallows every failed fetch so network weather can "
                     "never kill its thread)")

        # -- 3. the daemon keeps going, on the cache alone -------------------
        n_cycles = int(GATE4_SIM_S / GATE4_CLOCK_STEP_S)
        d = Daemon(root=root, cache_dir=cache_dir, cycle_s=0.0,
                   wind_every_s=GATE4_WIND_EVERY_S, now_fn=clock, poll=True,
                   n_wind_frames=GATE4_WIND_FRAMES)
        try:
            reports = d.loop(cycles=n_cycles, sleep=False)
        finally:
            d.close()

        errors = [r for r in reports if r.get("error")]
        ok &= _check(lines, "no cycle raised", not errors,
                     f"{len(reports)} cycles, {len(errors)} errors"
                     + (f" first: {errors[0]['error']}" if errors else ""))
        ok &= _check(lines, "the poller thread survived the kill and logged "
                     "failures instead of dying",
                     sum(d.poller.error_counts.values()) > 0,
                     f"poll errors {sum(d.poller.error_counts.values())}, "
                     f"successes {sum(d.poller.poll_counts.values())}")

        rendered = [r for r in reports if r.get("rendered")]
        # Until 2026-09-16 this asserted EXACTLY ONE render: with the driving wind
        # frozen, an axisymmetric Shue boundary is literally the same surface every
        # cycle. That is no longer true and it is not a regression - the drawn
        # magnetopause is now Lin's, whose shape depends on the DIPOLE TILT, and
        # the tilt keeps moving (~15 deg/h) while the Earth turns. The ring current
        # and the corotating plasmasphere move for the same reason. So the honest
        # invariant is narrower: WIND-DRIVEN layers must hold, orientation-driven
        # ones may legitimately refresh.
        #
        # The cost is bounded and was checked: Lin is closed-form (no bisection),
        # so a boundary re-render is cheap, and publish_cycle prunes to
        # LIVE_KEEP_CYCLES directories. What is NOT true any more is the operator
        # banner's old claim that only changed layers re-render.
        wind_layers = {"fieldlines", "aurora", "wind", "l1"}
        wind_renders = [r for r in reports
                        if set(r.get("layers") or []) & wind_layers]
        ok &= _check(lines, "wind-driven layers render once, then change detection holds",
                     len(wind_renders) == 1,
                     f"{len(wind_renders)} wind-driven render(s) of {len(reports)} "
                     f"cycles ({len(rendered)} cycles refreshed an "
                     f"orientation-driven layer, which the dipole tilt makes correct)")

        # status keeps refreshing, and the age climbs
        ages = [r["age_s"] for r in reports]
        climbing = all(b > a for a, b in zip(ages, ages[1:]))
        ok &= _check(lines, "the readout keeps refreshing with a climbing age",
                     climbing and ages[-1] > ages[0],
                     f"{_age_from_banner(reports[0]['banner'])} -> "
                     f"{_age_from_banner(reports[-1]['banner'])} "
                     f"({ages[0]} s -> {ages[-1]} s over {len(reports)} cycles)")
        status_ds = root / "Scripts" / "magneto-status.ds"
        body = status_ds.read_text(encoding="ascii") if status_ds.exists() else ""
        ok &= _check(lines, "magneto-status.ds on disk carries the last readout",
                     _age_from_banner(reports[-1]["banner"]) in body,
                     f"{status_ds.name}: "
                     f"{_age_from_banner(reports[-1]['banner']) or 'no age'}")

        # tier flips CACHED at 15 min
        live = [r for r in reports if r["age_s"] <= 15 * 60]
        cached = [r for r in reports if r["age_s"] > 15 * 60]
        ok &= _check(lines, "tier flips LIVE -> CACHED at 15 min",
                     all(r["tier"] == "LIVE" for r in live)
                     and bool(cached)
                     and all(r["tier"] == "CACHED" for r in cached),
                     f"{len(live)} cycles LIVE (age <= 900 s), "
                     f"{len(cached)} CACHED, first CACHED at "
                     f"{cached[0]['age_s'] if cached else '-'} s")

        # event arms past 3 h
        armed = [r for r in reports if r.get("armed")]
        unarmed = [r for r in reports if not r.get("armed")]
        ok &= _check(lines, "event auto-arms past 3 h of cache",
                     bool(armed)
                     and all(r["age_s"] > 3 * 3600 for r in armed)
                     and all(r["age_s"] <= 3 * 3600 for r in unarmed),
                     f"armed from age {armed[0]['age_s'] if armed else '-'} s; "
                     f"banner reads '{armed[-1]['banner'] if armed else '-'}'")

        # the 6 h auto-switch, asserted on the pure decision rather than by
        # rendering a 2-minute T96 event frame inside a 3-minute gate
        far = dict(seed, age_s=EVENT_SWITCH_S + 60)
        mode, eid, armed6, _why = d.decide(far, {})
        ok &= _check(lines, "past 6 h the decision is EVENT (decision only - "
                     "rendering a T96 event frame would blow the gate budget)",
                     mode == "EVENT" and eid == d.default_event and armed6,
                     f"age {far['age_s'] / 3600:.1f} h -> {mode} {eid}")

        # and the tree it published is a real one: the merged manifest names a
        # cycle directory per layer, and every file it lists is there
        models = root / "Models"
        missing = [n for n in ("EarthFrameCheck.vla", "live_manifest.json", ".run_stamp")
                   if not (models / n).exists()]
        try:
            merged = json.loads((models / "live_manifest.json").read_text(encoding="ascii"))
        except (OSError, ValueError):
            merged = {}
        layer_map = merged.get("layers") or {}
        for name in ("MagnetopauseShell", "BowShockShell", "FieldLines", "AuroraOval"):
            entry = layer_map.get(name)
            if not entry:
                missing.append(name)
                continue
            for f in entry.get("files", []):
                if not (models / entry["dir"] / f).exists():
                    missing.append(f"{entry['dir']}/{f}")
        ok &= _check(lines, "the one render published a complete per-cycle tree",
                     not missing,
                     f"{len(layer_map)} layers over "
                     f"{len([p for p in models.iterdir() if p.is_dir()])} cycle dir(s)"
                     + (f", missing {missing}" if missing else ""))

        elapsed = time.time() - t_gate
        ok &= _check(lines, f"bounded under {GATE4_BUDGET_S:g} s wall",
                     elapsed < GATE4_BUDGET_S,
                     f"{elapsed:.1f} s for {len(reports)} cycles simulating "
                     f"{ages[-1] / 3600:.1f} h of staleness")
    finally:
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        if previous_base is not None:
            set_base_url(previous_base)
        shutil.rmtree(tmp, ignore_errors=True)
    return bool(ok), lines


# --- runner -------------------------------------------------------------------

# --- gate 6 -------------------------------------------------------------------

def gate6_physics_consistency(root=None):
    """Every RENDERED package on disk was built by the same physics.

    Gates 1, 2 and 5 check that the physics is right. This one checks that the
    physics is the SAME everywhere - which is a different question, and the one
    that bites when new physics lands on a tree of packages rendered over
    several days. A dome that plays Gannon with a Lin magnetopause and Bastille
    with a Shue one is showing the audience two different Earths.

    Unlike gates 1-5 this one's verdict is a fact about what is on DISK, not
    about the code, so it can fail on a machine mid-migration while every line of
    the engine is correct. That is intended - it is the migration that is
    incomplete - but it is why tests pass it an explicit `root`.

    Tolerant of an empty tree (a fresh install has nothing to disagree about)
    and of a missing Renders dir. NOT tolerant of an unstamped package: a
    manifest with no physics block was built before the stamp existed, so its
    physics is unknown, and unknown is not the same as agreeing.
    """
    from tools.check_physics import find_manifests, read_physics, summarise

    lines = ["gate 6 - one physics across every rendered package"]
    if root is None:
        try:
            root = paths_mod.renders_root()
        except Exception as exc:                  # no config, bare checkout
            lines.append(f"  [SKIP] no renders root: {exc}")
            return True, lines
    root = Path(root)
    if not root.is_dir():
        lines.append(f"  [PASS] no renders tree at {root} - nothing to disagree")
        return True, lines

    records = [read_physics(p) for p in find_manifests(root)]
    if not records:
        lines.append(f"  [PASS] no rendered packages under {root}")
        return True, lines

    summ = summarise(records)
    lines.append(f"  engine physics v{summ['current']} "
                 f"({model_mod.physics_stamp()['tag']}), {len(records)} packages")
    for r in sorted(records, key=lambda r: r["package"]):
        v = (r.get("physics") or {}).get("physics_version")
        state = ("unreadable" if r.get("error") else
                 "UNSTAMPED" if v is None else
                 f"v{v}" + ("" if v == summ["current"] else " STALE"))
        lines.append(f"    {r['package']:<30} {r['kind']:<5} {state}")

    ok = _check(lines, "every package carries the engine's physics version",
                summ["ok"],
                f"versions {summ['versions'] or 'none'}, "
                f"{summ['n_unstamped']} unstamped"
                + ("" if summ["ok"] else
                   " - re-render the packages above, or ship only the agreeing set"))
    return ok, lines


def discover_gates() -> dict:
    """{number: function} for every gate<N>_* defined in this module."""
    found = {}
    for name, obj in globals().items():
        m = re.fullmatch(r"gate(\d+)_\w+", name)
        if m and callable(obj):
            found[int(m.group(1))] = obj
    return dict(sorted(found.items()))


def run_gate(number: int, out=print) -> bool | None:
    """Run one gate. True/False, or None when this module does not own it."""
    gates = discover_gates()
    fn = gates.get(number)
    if fn is None:
        note = GATES_ELSEWHERE.get(number, "no such gate in magneto.validate")
        out(f"gate {number} - SKIPPED (not implemented here): {note}")
        return None
    ok, lines = fn()
    for line in lines:
        out(line)
    out(f"gate {number} {'PASS' if ok else 'FAIL'}")
    return ok


def run_all(out=print) -> bool:
    """Run every implemented gate; note the ones named in the brief that aren't."""
    gates = discover_gates()
    results = {}
    for number in gates:
        results[number] = run_gate(number, out=out)
        out("")
    for number in sorted(GATES_ELSEWHERE):
        if number not in gates:
            run_gate(number, out=out)
    ran = [n for n, v in results.items() if v is not None]
    failed = [n for n, v in results.items() if v is False]
    out(f"\n{len(ran) - len(failed)}/{len(ran)} gates PASS"
        + (f" - failed: {failed}" if failed else ""))
    return not failed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build A validation gates")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--gate", type=int, metavar="N", help="run one gate")
    group.add_argument("--all", action="store_true", help="run every gate")
    args = ap.parse_args(argv)
    if args.all:
        return 0 if run_all() else 1
    result = run_gate(args.gate)
    return 0 if result is not False else 1


if __name__ == "__main__":
    sys.exit(main())
