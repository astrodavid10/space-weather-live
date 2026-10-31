#!/usr/bin/env python
r"""Regenerate the panel's STABLE shims and .ds behaviour scripts in <package>\Scripts.

The control panel never addresses a render directly. Every event button plays a
shim with a fixed name in the package's Scripts folder, and the shim forwards
to whatever the CURRENT render of that event is called:

    Scripts\event-<id>-play.ds       Scripts\event-<id>-remove.ds     (x5 storms)
    Scripts\lookback-latest-on.ds    Scripts\lookback-latest-off.ds
    Scripts\live-play.ds             Scripts\live-remove.ds           (the live feed)

so the .dscp never chases date-stamped filenames or drive letters. Shims are
rewritten after every render (kickoffs call write_all()), after an install, and
by hand:

    python tools\write_shims.py --all        (or --event <id> | --lookback)

Four rules every shim obeys:

  1. Package-relative paths only ($Content\...) - magneto.paths.ds_token().
  2. Never point at a file that is not there. A storm that has not been
     rendered gets a one-line shim that posts an honest system message instead
     of a script error, so no button on the panel is ever dead.
  3. STANDALONE + SWAP. A source button needs no other button first: black, stop
     every other player by name, `system reset`, Earth insta-nav, the content,
     white - so the toggles behave as a radio group.
  4. .ds, not JavaScript, wherever .ds can do it (2026-10-04). State a .ds cannot
     read is kept in MagIf_* FLAGS - permanent toggleClass objects no button is
     bound to - and branched on (FLAGS, branch()). lint_ds() enforces the limits
     on every file this module writes.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from magneto import events, paths, presets                  # noqa: E402
from dsexport import ds as dsds                             # noqa: E402

HEADER_TAG = "# {[2026.0.3.3]}"
GAP_ON_S = 0.5          # swap/stop lines -> the event's own -on.ds
GAP_PLAYER_S = 1.0      # -on.ds clean slate -> the player (dome-tested 2026-09-05)
GAP_TEARDOWN_S = 3.2    # -off.ds holder fades -> player teardown (dome-tested)


# --- what is rendered ------------------------------------------------------------

def render_scripts_dir(event_id: str) -> Path:
    """Where a render of this event keeps its scripts on a BUILD box: Renders\\<root>\\Scripts."""
    kind = "lookback" if str(event_id).startswith(events.LOOKBACK_PREFIX) else "storm"
    return paths.render_root(kind, None if kind == "lookback" else event_id) / "Scripts"


RENDER_MANIFESTS_DIR = ENGINE / "render_manifests"     # <event_id>.json, unpacked from MagnetoEngine.zip


def manifest_path(event_id: str) -> Path:
    """The render's event_frames.json: Renders\\<root>\\Models\\event_frames.json on the build box,
    else Engine\\render_manifests\\<event_id>.json.

    Digistar's packager cannot ship six files that share the name event_frames.json (a .json is
    never renamed: the last copy wins), and an installed dome has no Renders\\ tree - the players
    and models arrive flat. So Engine\\tools\\build_payload.py carries every shipped render's
    manifest in MagnetoEngine.zip under its event id and the installer unpacks it here: the
    scene-date pin, the title, the physics note and the shells default then come from the same
    manifest on an installed dome as on the build box (without it a regenerated storm shim pinned
    no date at all)."""
    built = render_scripts_dir(event_id).parent / "Models" / "event_frames.json"
    if built.exists():
        return built
    shipped = RENDER_MANIFESTS_DIR / f"{event_id}.json"
    return shipped if shipped.exists() else built


def scripts_dir_for(event_id: str) -> Path:
    """Where THIS event's player lives right now.

    Two layouts are real, and a package must play in both:

      build box   Renders\\storm_<id>\\Scripts\\magneto-event-<id>.ds   (what a render writes)
      installed   Scripts\\magneto-event-<id>.ds                       (what a .dsz unpacks to)

    Digistar's own export layout - and Alex's make_dsz.py, which reproduces it - is FLAT: every
    .ds reached from the panel lands in <package>\\Scripts and every model in <package>\\Models.
    Until 2026-09-22 this function knew only the first layout, so on a dome that had merely
    installed the .dsz, `write_shims --all` (installer step 6, and every Rebuild Look-back)
    found no render for any storm and overwrote five WORKING shims with "not rendered on this
    system yet" placeholders - the package shipped the storms and then disowned them the first
    time the operator pressed Install. A render on the build box still wins when both exist.
    """
    rd = render_scripts_dir(event_id)
    if (rd / dsds.DS_EVENT_PLAYER_FMT.format(event_id)).exists():
        return rd
    flat = paths.SCRIPTS_DIR
    if (flat / dsds.DS_EVENT_PLAYER_FMT.format(event_id)).exists():
        return flat
    return rd


def player_path(event_id: str) -> Path:
    return scripts_dir_for(event_id) / dsds.DS_EVENT_PLAYER_FMT.format(event_id)


def current_lookback_id(scripts_dir: Path | None = None) -> str | None:
    """Newest 'lookback-YYYYMMDD-YYYYMMDD' whose PLAYER exists - in the look-back render root,
    or flat in <package>\\Scripts where a .dsz unpacks it. Newest file wins, so a look-back
    rebuilt on an installed dome takes over from the shipped one.

    The player, not event-<id>-on.ds (until 2026-10-15): nothing has played the -on.ds since
    2026-10-04, so Digistar's packager does not ship it, and an installed dome would have found
    no look-back and turned its working shims into "not rendered" placeholders."""
    dirs = [scripts_dir] if scripts_dir else [paths.render_root("lookback") / "Scripts", paths.SCRIPTS_DIR]
    pat = re.compile(r"magneto-event-(lookback-\d{8}-\d{8})\.ds$")
    assert dsds.DS_EVENT_PLAYER_FMT.format("X") == "magneto-event-X.ds", "update the pattern above"
    cands = []
    for sd in dirs:
        for p in glob.glob(str(sd / dsds.DS_EVENT_PLAYER_FMT.format("lookback-*"))):
            m = pat.match(os.path.basename(p))
            if m:
                cands.append((os.path.getmtime(p), m.group(1)))
    return sorted(cands)[-1][1] if cands else None


def is_rendered(event_id: str) -> bool:
    return player_path(event_id).exists()


def is_placeholder_shim(path: Path) -> bool:
    """Does an existing shim on disk merely announce that its event is not rendered?"""
    try:
        return "not rendered on this system" in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def rendered_ids() -> list[str]:
    """Every curated storm with a player on disk, plus the current look-back."""
    ids = [eid for eid in events.EVENTS if is_rendered(eid)]
    lb = current_lookback_id()
    if lb and is_rendered(lb):
        ids.append(lb)
    return ids


def all_event_holders() -> list[str]:
    """Every MagnetoEvent<Layer>Holder name any render may have created."""
    layers = list(dsds.DS_EVENT_HOLDER_LAYERS) + ["Shell", "FieldPulses",
                                                  "MagnetopauseShell", "BowShockShell"]
    seen, out = set(), []
    for k in layers:
        h = dsds.event_holder(k)
        if h not in seen:
            seen.add(h)
            out.append(h)
    out.extend(dsds.hud_object_names(dsds.DS_HUD_EVENT))   # the readout parent + segments
    return out


# --- text helpers -----------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _tok(p: Path) -> str:
    return paths.ds_token(p)


def _status_line(script_name: str) -> list[str]:
    r"""`script play <Scripts>\<script_name>` - the .ds replacement for a MagnetoToggles.js call.

    Emitted unconditionally: the status scripts are generated by write_toggles_status() in the
    same write_all() pass, so unlike the old JS line there is no "only if the file exists" guard
    to get wrong. A literal path here is also the whole point - it is what the .dsz packager
    follows.
    """
    return [f"\tscript play {_tok(paths.SCRIPTS_DIR / script_name)}"]


FADE_OUT_S = 1.0        # eye to black before anything moves
FADE_IN_S = 1.5         # eye back to white once the player is up
GAP_NAV_S = 0.3         # reset + flytodest -> skipAnimation (the house insta-nav's own gap)
LOAD_WAIT_S = 3.5       # player start -> fade in: its 12 model-declaration cues + first step (~2.7 s)
                        # plus a margin for the first models to cook

STANDALONE_NOTE = [
    "# STANDALONE (David, 2026-09-22): this button needs no other button first. It does what the",
    "# panel's Fade-Stop-Reset does, in a form a script survives: `system fadeStopReset` itself",
    "# runs `script stopAll` + `system clearNames` and the Users Guide says not to call it from a",
    "# script, so the sequence is `eye color black` -> `system reset` (the Rubin First Look",
    "# RubinSetupNavigation.ds pattern) -> Earth insta-nav at the right scene date -> content ->",
    "# `eye color white`. The reset deletes every object that is not permanent: the toggles and the",
    "# style proxies survive, the camera rig is recreated by the view buttons, xbox0 is recreated",
    "# here. The other players are stopped by name because `system reset` does not stop scripts.",
    "",
]


def _scene_date_for(event_id: str) -> str | None:
    """'YYYY-MM-DD HH:MM:SS' of the render's first epoch, from its manifest; None without one."""
    try:
        man = json.loads(manifest_path(event_id).read_text(encoding="utf-8"))
        first = (man.get("epochs") or [None])[0]
        if not first:
            return None
        return str(first).replace("T", " ").rstrip("Z")[:19]
    except (OSError, ValueError, TypeError):
        return None


def _reset_and_instanav_lines(scene_date: str | None) -> list[str]:
    """`system reset` and the Earth insta-nav, WITHOUT the eye fades (the caller owns the black).

    Mirrors Scripts\\view_earth_instanav.ds (the house InstantNavToEarth pattern) plus the xbox0
    lines of xboxReset.ds, because a reset deletes the controller object. The eye is re-blackened
    right after the reset - a reset turns it white (Rubin does the same) - and the scene date is
    set BEFORE the camera moves so the Earth's day side and terminator are right when the lights
    come up. None = the real-time clock: `scene date now` AND `scene date rate 1 seconds`, because a
    reset leaves the clock rate at 0 on this host and `scene date now` alone does not touch the rate
    (dome-measured 2026-10-07: 0 s advanced in 10 s after Remove / Live Feed). A storm's date gets no
    rate line - its player advances the clock with `scene date X duration Y` tweens.
    """
    out = ["\tsystem reset",
           "\teye color black"]
    out += [f"\tscene date {scene_date}"] if scene_date else ["\tscene date now", "\tscene date rate 1 seconds"]
    out += ["\txbox0 is xBoxClass",
           "\txbox0 on",
           "\tEarth on duration 0",
           "\tnavigation destObject Earth",
           "\tnavigation destRadius 6378137 m",
           "\tnavigation orbitRadiusScale 3",
           "\tnavigation minOrbitRadiusScale 1.1",
           "\tnavigation defaultOrbitRate 0.75",
           "\tnavigation flyToDuration 1",
           '\tnavigation orbitUpDirection "North Pole"',
           '\tnavigation destAction "Orbit"',
           "\tnavigation autoTerrainTransition on",
           "\tnavigation useCameraController on",
           "\tnavigation autoPitch on",
           "\tnavigation device xbox0",
           "\tnavigation on",
           "\tnavigation drift on",
           "\teye attitude rate 0 0 0",
           "\teye position rate 0 0 0",
           "\tskyObjects on duration 0",
           "\tscene attitude 0 -45 0",
           "\tnavigation flytodest"]
    return out


def _head(name: str, what: str) -> list[str]:
    return [HEADER_TAG,
            f"# {name}  -  STABLE SHIM, GENERATED by Engine\\tools\\write_shims.py {_now()}",
            f"# {what}",
            "# Do not hand-edit: every render regenerates this file. The panel plays",
            "# THIS name so it never chases date-stamped filenames or drive letters.",
            ""]


def _swap_lines(this_id: str | None) -> list[str]:
    """Stop every OTHER rendered event's scripts (radio behaviour). None = stop them all.

    No object teardown here any more (2026-10-04): every caller runs `system reset` 1.1 s later,
    which deletes every non-permanent object in one step. The MagnetoLive.js teardownEvents() this
    used to end with walked the holders' children just before that reset deleted them anyway.
    The players must still be stopped by NAME: `system reset` does not stop scripts.
    """
    out = []
    for oid in rendered_ids():
        if oid == this_id:
            continue
        out.append(f"\tscript stop {dsds.DS_EVENT_PLAYER_FMT.format(oid)[:-3]}")
        out.append(f"\tscript stop {dsds.DS_EVENT_TEARDOWN_FMT.format(oid)[:-3]}")
        out.append(f"\tscript stop {dsds.DS_NATIVE_AURORA_FMT.format(oid)[:-3]}")
    return out


def _write(dest: Path, lines: list[str]) -> Path:
    if dest.suffix.lower() == ".ds":
        lint_ds(dest.name, lines)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with open(tmp, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
        fh.write("\n".join(lines).rstrip("\n") + "\n")
    os.replace(tmp, dest)                                    # atomic; never a half file
    return dest


def _placeholder(name: str, event_id: str, verb: str) -> list[str]:
    label = presets.event_label(event_id)
    msg = (f"{label} is not rendered on this system yet|"
           f"Press Rebuild {label} (hours) or Rebuild All Storms - or install the render package")
    if verb == "remove":
        msg = f"{label} is not rendered on this system - nothing to remove"
    return _head(name, f"PLACEHOLDER - no render of {event_id} found under "
                       f"{_tok(scripts_dir_for(event_id).parent)}") + [
        "0:00", f'\tsystem message "{msg[:250]}"', ""]


# --- the shims -------------------------------------------------------------------

def event_shim_names(event_id: str) -> tuple[str, str]:
    if str(event_id).startswith(events.LOOKBACK_PREFIX):
        return "lookback-latest-on.ds", "lookback-latest-off.ds"
    return f"event-{event_id}-play.ds", f"event-{event_id}-remove.ds"


def _physics_note(event_id: str) -> list[str]:
    """One `system message` line when this package's physics is not the engine's.

    The honesty law says never blank and never a lie: a package rendered on older
    physics keeps its button and still plays, but it announces what it is instead
    of passing itself off as current. Returns [] when the package agrees, when it
    has no manifest, or when the message would exceed the 255-char cap.
    """
    from magneto.model import PHYSICS_VERSION
    man = manifest_path(event_id)
    try:
        ph = json.loads(man.read_text(encoding="ascii", errors="replace")).get("physics")
    except (OSError, ValueError):
        return []
    have = (ph or {}).get("physics_version")
    if have == PHYSICS_VERSION:
        return []
    was = f"physics v{have} ({(ph or {}).get('tag', 'unknown')})" if have else "older physics"
    msg = (f"NOTE: this render was built on {was} - the engine is now at v{PHYSICS_VERSION}. "
           "It plays correctly as rendered - rebuild it to match the other packages.")
    return [f'	system message "{msg}"'] if len(msg) <= 255 else []


STYLE_WAIT_S = 2.5      # player start -> applyStyles: its prototypes exist ~1.5 s into PREP (the
                        # delay MagnetoToggles.js eventStarted() used, 2026-09-16)


def _event_title(event_id: str) -> str:
    """The render's own title (what its player and its old -on.ds banner say), from the manifest."""
    try:
        t = json.loads(manifest_path(event_id).read_text(encoding="utf-8")).get("title")
    except (OSError, ValueError):
        t = None
    t = t or dsds.event_title(event_id) or presets.event_label(event_id)
    return str(t).encode("ascii", "replace").decode("ascii").replace('"', "'")


def write_event_shims(event_id: str, out_dir: Path | None = None) -> list[Path]:
    """event-<id>-play.ds / -remove.ds (or the look-back pair) for ONE event.

    A render is playable when it has its PLAYER and its -off.ds holder fades. The render's other
    two scripts are no longer played (2026-10-04): event-<id>-on.ds only stopped this event's own
    scripts (now two lines here), deleted objects through JavaScript just before this shim's own
    `system reset`, and posted the banner (now posted here); magneto-event-<id>-off.ds is the
    teardown, which the reset replaces. Neither ships any more, so neither may decide whether a
    render counts - an installed dome would otherwise disown every storm on its first Install.
    """
    out_dir = out_dir or paths.SCRIPTS_DIR
    on_name, off_name = event_shim_names(event_id)
    key = presets.event_key(event_id)
    sd = scripts_dir_for(event_id)
    player = sd / dsds.DS_EVENT_PLAYER_FMT.format(event_id)
    aurora = sd / dsds.DS_NATIVE_AURORA_FMT.format(event_id)
    off_a = sd / f"event-{event_id}-off.ds"
    aurora_off = sd / dsds.DS_NATIVE_AURORA_OFF_FMT.format(event_id)

    if not (player.exists() and off_a.exists()):
        # Loud, never silent: a real shim becoming a placeholder means content this panel
        # played a moment ago is now unreachable. Right when a render was deleted on purpose;
        # the fault of 2026-09-22 (an installed package's flat Scripts\ not recognised) when
        # it was not. Either way the operator deserves the line in the log.
        prev = out_dir / on_name
        if prev.exists() and not is_placeholder_shim(prev):
            print(f"write_shims: DOWNGRADE {on_name}: no render of {event_id} found "
                  f"(looked in {sd}) - replacing a working shim with a placeholder", file=sys.stderr)
        return [_write(out_dir / on_name, _placeholder(on_name, event_id, "play")),
                _write(out_dir / off_name, _placeholder(off_name, event_id, "remove"))]

    has_aurora = aurora.exists() and aurora_off.exists()
    player_stem = dsds.DS_EVENT_PLAYER_FMT.format(event_id)[:-3]
    aurora_stem = dsds.DS_NATIVE_AURORA_FMT.format(event_id)[:-3]
    teardown_stem = dsds.DS_EVENT_TEARDOWN_FMT.format(event_id)[:-3]
    start_date = _scene_date_for(event_id)
    banner = f"Curated event|{_event_title(event_id)}|Live layers off - press Live Space Weather Feed to return"

    on = _head(on_name, f"Play {event_id} STANDALONE: fade to black, reset, Earth at the storm's "
                        f"date, player" + (", native aurora" if has_aurora else "") + ", fade in")
    on += STANDALONE_NOTE
    on += ["0:00",
           f"\teye color black duration {FADE_OUT_S:g}"]
    on += flag_decl("event", "live", "rig", "drift")
    on += ["\t# the live loader is STOPPED, not asked to stop: its shutdown() fades and deletes what",
           "\t# the reset below deletes anyway, and when the feed was in EVENT fallback it ends with",
           "\t# `scene date now` - up to ~5 s from now, which could land on this storm's clock tween.",
           LOADER_STOP,
           "\t# this event's own teardown and player, in case Remove was pressed a moment ago",
           f"\tscript stop {teardown_stem}",
           f"\tscript stop {player_stem}"]
    on += _swap_lines(event_id)
    on += ["", f"+{FADE_OUT_S + 0.1:g}"]
    on += _reset_and_instanav_lines(start_date)
    on += ["", f"+{GAP_NAV_S:g}",
           "\tnavigation skipAnimation"]
    on += _after_reset_flags()
    on += _status_line(f"toggles-{key}-on.ds")
    on += ["", f"+{GAP_ON_S:g}",
           f'\tsystem message "{banner[:255]}"',
           "", f"+{GAP_PLAYER_S:g}",
           f"\tscript play {_tok(player)}"]
    if has_aurora:
        on += [f"\tscript play {_tok(aurora)}"]
    on += ["", f"+{STYLE_WAIT_S:g}",
           "\t# the player's PREP has declared its holders and prototypes: the layer buttons may",
           "\t# reach them now, and the operator's style sliders are re-applied to the new",
           "\t# prototypes (quiet: no banner, so the player's own title stays up)",
           f"\t{flag('event')} status on",
           # built outside the f-string: reusing its quote character inside one is 3.12+ only (PEP 701)
           "\t" + _js("MagnetoStyle.js", "applyStyles('quiet')")]
    on += ["", f"+{LOAD_WAIT_S - STYLE_WAIT_S:g}",
           "\t# the player's first step and its model loads are behind us: lights up",
           f"\teye color white duration {FADE_IN_S:g}"]
    # LAST, after the player's own title banner, so an older-physics render says so where it stays
    on += _physics_note(event_id) + [""]

    off = _head(off_name, f"Remove {event_id}: fade to black, stop its scripts, reset, Earth at the "
                          f"real-time clock, fade in")
    off += STANDALONE_NOTE
    off += ["0:00",
            f"\teye color black duration {FADE_OUT_S:g}"]
    off += flag_decl("event", "live", "rig", "drift")
    off += [f"\t{flag('event')} status off",
            # the holder fades in -off.ds happen under the black; they still matter for anyone
            # watching the dome's own preview, and they are what the layer buttons expect
            f"\tscript play {_tok(off_a)}"]
    if has_aurora:
        off += [f"\tscript play {_tok(aurora_off)}"]
    off += ["", f"+{FADE_OUT_S + 0.1:g}",
            "\t# the player must stop BEFORE the reset deletes its objects, or its next cue",
            "\t# fires at nothing (the HOSTERROR_INVALID_OBJECT race of 2026-08-18)",
            f"\tscript stop {player_stem}"]
    if has_aurora:
        off += [f"\tscript stop {aurora_stem}"]
    off += _reset_and_instanav_lines(None)
    off += ["", f"+{GAP_NAV_S:g}",
            "\tnavigation skipAnimation"]
    off += _after_reset_flags()
    off += _status_line(f"toggles-{key}-off.ds")
    off += ["", f"+{GAP_ON_S:g}",
            f"\teye color white duration {FADE_IN_S:g}", ""]
    return [_write(out_dir / on_name, on), _write(out_dir / off_name, off)]


def write_lookback_shims(out_dir: Path | None = None) -> list[Path]:
    lb = current_lookback_id()
    if not lb:
        out_dir = out_dir or paths.SCRIPTS_DIR
        fake = events.LOOKBACK_PREFIX + "none"
        return [_write(out_dir / "lookback-latest-on.ds", _placeholder("lookback-latest-on.ds", fake, "play")),
                _write(out_dir / "lookback-latest-off.ds", _placeholder("lookback-latest-off.ds", fake, "remove"))]
    return write_event_shims(lb, out_dir)


# ---- the live feed, standalone like the storms (David 2026-10-04) -----------------------------
# Live Feed ON used to be kickoff.js startLive(): it wrote a one-shot machine-specific .vbs, ran it,
# played clear-events.ds and restarted the loader after a 4 s wait. Now it is .ds plus STATIC
# self-locating .vbs launchers (write_vbs), and it starts like a storm: black, stop every player,
# `system reset`, Earth insta-nav at the real-time clock, the loader, white.
#
# The engine check comes FIRST and never touches the dome: detect_engine.vbs writes
# engine-status.ds (MagIf_engine on/off, plus the reason as a message when off), the shim plays it
# and branches. With no engine the toggle goes dark again and the dome is left exactly as it was -
# "every button works with the engine absent" (CLAUDE.md).
#
# The answer is read THROUGH THE FLAG'S OWN STORED COMMANDS, never a literal `script play`: Digistar
# parses a played .ds and every script it names literally at the press (PARSE RULE, build_views.ps1),
# so until 2026-10-07 the shim ran the engine-status.ds that was on disk BEFORE detect_engine.vbs
# rewrote it (0.3 s later, dome-measured) - every press read the previous press's answer, and the
# first one after write_all() read the shipped "not checked yet - press again" default.
ENGINE_WAIT_S = 1.0     # detect_engine.vbs -> engine-status.ds: 0.3 s measured here, x3 for a slower PC
ENGINE_READ_S = 0.3     # the stored command plays engine-status.ds -> its status line has run
ENGINE_STATUS = "engine-status.ds"
INSTALL_DS = "install.ds"
REMOVE_ENGINE_DS = "remove-engine.ds"
LIVE_SCRIPTS = ("live-play.ds", "live-play-go.ds", "live-remove.ds", "rebuild-lookback.ds",
                "rebuild-lookback-go.ds", INSTALL_DS, REMOVE_ENGINE_DS, ENGINE_STATUS)
# The Python engine as Digistar's packager carries it (2026-10-15). It ships .py files only flat in
# Scripts\ and only when a .ds names them (`py play`), drops .vbs/.bat/.ps1/.whl, and copies a .zip to
# the package root untouched. So: the panel-side Python as flat files, the engine itself as one
# archive (Engine\tools\build_payload.py), the installer launched from JavaScript (MagnetoSetup.js
# writes its .vbs at run time, the Terrain Pipeline way), all declared in PYTHON_FILES_DS.
SETUP_JS = "MagnetoSetup.js"
PYTHON_FILES_DS = "magnetoPythonFiles.ds"
ENGINE_ZIP = "MagnetoEngine.zip"
INSTALL_GUIDE = "INSTALL.txt"                       # the operator's install notes (the packager drops .md)
PYTHON_FILES = ("mag_setup.py", "_kickoff_common.py", "kickoff_live_daemon.py",
                "kickoff_lookback_rebuild.py", "kickoff_event_rebuild.py", "kickoff_rebuild_all.py")
VBS_DETECT = "detect_engine.vbs"
VBS_LIVE_START = "run_live_start.vbs"
VBS_LIVE_STOP = "run_live_stop.vbs"
VBS_LOOKBACK = "run_rebuild_lookback.vbs"
LIVE_START_MSG = ("Live Space Weather Feed starting: polling SWPC every 60 s, first frames in about a "
                  "minute. The dome readout shows the data age; CACHED means the feed is stale.")
LIVE_STOP_MSG = ("Live Space Weather Feed stopping: daemon asked to exit (within one cycle), live "
                 "layers clearing.")
LOOKBACK_MSG = ("48h look-back REBUILD started: fetch + render (~5 min), then the Look-back button "
                "repoints itself. You will get a message here when it is done. Log: Renders\\_kickoffs\\")
INSTALL_MSG = ("Install Python Engine: checking this PC - a question about the download appears on the "
               "console first. Nothing else on this panel needs it, and it needs nothing from the panel.")
REMOVE_MSG = "Remove Python Engine: a question appears on the console first."


def _engine_check(name: str, what: str) -> list[str]:
    """Run detect_engine.vbs and read its answer; the caller appends the branch on MagIf_engine."""
    lines = _head(name, what)
    lines += ["# MagIf_engine is written OFF first, so a missing answer reads as 'no engine' and the",
              "# dome is never touched on a guess. engine-status.ds is rewritten by detect_engine.vbs",
              "# on every press (it says off, with the reason, when the engine is not installed).",
              "", "0:00"]
    lines += flag_decl("engine")
    lines += [f"\t{flag('engine')} status off",
              f"\t{_exec_vbs(paths.SCRIPTS_DIR / VBS_DETECT)}",
              "", f"+{ENGINE_WAIT_S:g}",
              "\t# read the answer through the flag: both arms play engine-status.ds, which is parsed",
              "\t# when the stored command runs - after detect_engine.vbs rewrote it - and writes the status"]
    lines += branch("engine", if_on=[_sp(ENGINE_STATUS)], if_off=[_sp(ENGINE_STATUS)])
    lines += ["", f"+{ENGINE_READ_S:g}"]
    return lines


def write_live_shims(out_dir: Path | None = None) -> list[Path]:
    out_dir = Path(out_dir) if out_dir else paths.SCRIPTS_DIR
    written = []

    play = _engine_check("live-play.ds", "Live Feed ON: check the engine, then start the feed standalone")
    play += branch("engine", if_on=[_sp("live-play-go.ds")], if_off=[_sp("toggles-live-off.ds")])
    written.append(_write(out_dir / "live-play.ds", play + [""]))

    go = _head("live-play-go.ds", "Live Feed ON, engine present: black, stop every player, reset, Earth "
                                  "at the real-time clock, daemon + loader, white")
    go += STANDALONE_NOTE
    go += ["0:00", f"\teye color black duration {FADE_OUT_S:g}"]
    go += flag_decl("event", "live", "rig", "drift")
    go += ["\t# the daemon starts first: it imports the engine while the dome goes dark. It refuses",
           "\t# a second copy (pid file), so pressing this with the feed already running is safe.",
           f"\t{_exec_vbs(paths.SCRIPTS_DIR / VBS_LIVE_START)}",
           LOADER_STOP]
    go += _swap_lines(None)
    go += ["", f"+{FADE_OUT_S + 0.1:g}"]
    go += _reset_and_instanav_lines(None)
    go += ["", f"+{GAP_NAV_S:g}",
           "\tnavigation skipAnimation"]
    go += _after_reset_flags()
    go += _status_line("toggles-live-on.ds")
    go += ["", f"+{GAP_ON_S:g}",
           "\t# a fresh loader: it creates the live holders and HUD at once and polls the manifest",
           f"\t{_js('MagnetoLiveLoader.js', 'run()')}",
           "", f"+{GAP_PLAYER_S:g}",
           f"\t{flag('live')} status on",
           f"\teye color white duration {FADE_IN_S:g}",
           f'\tsystem message "{LIVE_START_MSG}"', ""]
    written.append(_write(out_dir / "live-play-go.ds", go))

    rm = _head("live-remove.ds", "Live Feed OFF: stop the daemon, let the loader fade and delete its objects")
    rm += ["# No reset here: the loader's own shutdown() fades the live holders over 2 s and deletes",
           "# every live object, which is what Live Feed off has always done.",
           "", "0:00"]
    rm += flag_decl("live")
    rm += [f"\t{flag('live')} status off",
           f"\t{_exec_vbs(paths.SCRIPTS_DIR / VBS_LIVE_STOP)}",
           LOADER_ASK_STOP]
    rm += _status_line("toggles-live-off.ds")
    rm += [f'\tsystem message "{LIVE_STOP_MSG}"', ""]
    written.append(_write(out_dir / "live-remove.ds", rm))

    lb = _engine_check("rebuild-lookback.ds", "Rebuild Look-back: check the engine, then start the rebuild")
    lb += branch("engine", if_on=[_sp("rebuild-lookback-go.ds")])
    written.append(_write(out_dir / "rebuild-lookback.ds", lb + [""]))

    lbg = _head("rebuild-lookback-go.ds", "Rebuild Look-back, engine present: start the job and its banner watcher")
    lbg += ["0:00",
            f"\t{_exec_vbs(paths.SCRIPTS_DIR / VBS_LOOKBACK)}",
            "\t" + _js("kickoff_watch.js", "watch('lookback')"),
            f'\tsystem message "{LOOKBACK_MSG}"', ""]
    written.append(_write(out_dir / "rebuild-lookback-go.ds", lbg))

    inst = _head(INSTALL_DS, "Install Python Engine: Scripts\\mag_setup.py, after the operator agrees")
    inst += [r"# MagnetoSetup.js writes runtime\launch_setup.bat + .vbs and starts them through `system",
             "# execute` on the .vbs (NEVER on a .bat or python.exe: a direct child of DsHost inherits the",
             "# host's API socket and can outlive it, 2026-09-17), then relays the installer's progress",
             "# here. Digistar's packager drops .vbs files, which is why it is written at run time.",
             "", "0:00",
             f'\tsystem message "{INSTALL_MSG}"',
             "\t" + _js(SETUP_JS, "install()"), ""]
    written.append(_write(out_dir / INSTALL_DS, inst))

    rm_eng = _head(REMOVE_ENGINE_DS, "Remove Python Engine: undo everything mag_setup.py installed, after the operator agrees")
    rm_eng += ["# The private Python (PythonEnv\\), the unpacked engine, the launchers and the machine config.",
               "# The stored storms, the look-back and the panel stay: the show plays without Python.",
               "", "0:00",
               f'\tsystem message "{REMOVE_MSG}"',
               "\t" + _js(SETUP_JS, "remove()"), ""]
    written.append(_write(out_dir / REMOVE_ENGINE_DS, rm_eng))
    return written


def write_python_files_ds(out_dir: Path | None = None) -> Path:
    r"""magnetoPythonFiles.ds - NEVER PLAYED. Names every Python file Digistar's packager must carry.

    The packager follows `<object> play <path>` for any object, so `py play <file>.py` puts the file
    in the package's Scripts\ folder and `py play <...>\MagnetoEngine.zip` puts the archive at the
    package root, unchanged (both read from its IL, 2026-10-15). Nothing here may run: Digistar has
    no `py play` command, and `script end` stops the host before the first one. package-manifest.ds
    names this file, which is how the packager reaches it.
    """
    out_dir = Path(out_dir) if out_dir else paths.SCRIPTS_DIR
    lines = _head(PYTHON_FILES_DS, "the Python the package carries, for Digistar's packager - NEVER PLAYED")
    lines += ["# Scripts\\mag_setup.py installs the engine (after the operator agrees) from the archive",
              "# MagnetoEngine.zip at the package root; the kickoffs run it from the panel's .vbs launchers.",
              "", "0:00",
              '\tsystem message "Live Magnetosphere Python file list - nothing to play"',
              "\tscript end", ""]
    missing = []
    for name in PYTHON_FILES:
        p = paths.SCRIPTS_DIR / name
        if p.exists():
            lines.append(f"\tpy play {_tok(p)}")
        else:
            missing.append(name)
    z = paths.PACKAGE / ENGINE_ZIP
    if z.exists():
        lines.append(f"\tpy play {_tok(z)}")
    else:
        missing.append(ENGINE_ZIP)
    guide = paths.PACKAGE / INSTALL_GUIDE           # a .txt lands at the package root
    if guide.exists():
        lines.append(f"\tMagnetoPkgDecl path {_tok(guide)}")
    else:
        missing.append(INSTALL_GUIDE)
    if missing:
        # loud, never silent: a package built now would ship without them (native_pack_check fails it)
        print(f"WARNING {PYTHON_FILES_DS}: not on disk, so not declared: {', '.join(missing)} "
              f"(build MagnetoEngine.zip with Engine\\tools\\build_payload.py)", file=sys.stderr)
        lines += ["", "# NOT DECLARED - missing when this file was written: " + ", ".join(missing)]
    return _write(out_dir / PYTHON_FILES_DS, lines)


# ---- the .vbs launchers: STATIC, self-locating, generated once ---------------------------------
# kickoff.js used to WRITE a one-shot .vbs per press with this machine's interpreter path baked in
# (run_kickoff_live.vbs etc.), because a .ds cannot read the config. These read it themselves, so
# the same four files work on every dome and in any install folder, and make_dsz ships them
# byte-for-byte (its Scripts sweep). Every Run is preceded by FileExists checks on the interpreter
# AND the .py: a missing exe would otherwise raise a modal WScript error dialog on the dome PC.
_VBS_COMMON = r'''Option Explicit
Dim fso, sh, scripts, pkg
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
scripts = fso.GetParentFolderName(WScript.ScriptFullName)
pkg = fso.GetParentFolderName(scripts)

' The engine interpreter, chosen as kickoff.js pythonExe() did: Config\magneto.local.json
' "python_exe" if that file exists, else Engine\.venv\Scripts\python.exe - and then its
' pythonw.exe twin when there is one (a venv python.exe opens a console window on this box
' however hidden the launch; the GUI-subsystem twin has none). "" = no engine.
Function EnginePython()
    Dim cfg, txt, re, m, p, w
    EnginePython = ""
    p = ""
    cfg = pkg & "\Config\magneto.local.json"
    If fso.FileExists(cfg) Then
        ' read as ASCII: a UTF-8 BOM becomes three junk characters the pattern never looks at
        txt = fso.OpenTextFile(cfg, 1, False, 0).ReadAll
        Set re = New RegExp
        re.Pattern = """python_exe""\s*:\s*""((?:[^""\\]|\\.)*)"""
        Set m = re.Execute(txt)
        If m.Count > 0 Then
            p = Replace(Replace(m(0).SubMatches(0), "\\", "\"), "\/", "/")
            If Not fso.FileExists(p) Then p = ""
        End If
    End If
    If p = "" Then
        p = pkg & "\Engine\.venv\Scripts\python.exe"
        If Not fso.FileExists(p) Then Exit Function
    End If
    If LCase(Right(p, 10)) = "python.exe" Then
        w = Left(p, Len(p) - 10) & "pythonw.exe"
        If fso.FileExists(w) Then p = w
    End If
    EnginePython = p
End Function

' Start Scripts\<pyFile> [arg] hidden and NON-blocking. Silent when anything is missing:
' detect_engine.vbs is the one that explains, and a dialog on the dome PC would be worse.
Sub RunPy(pyFile, arg)
    Dim py, target, cmd
    py = EnginePython()
    target = scripts & "\" & pyFile
    If py = "" Then Exit Sub
    If Not fso.FileExists(target) Then Exit Sub
    cmd = """" & py & """ """ & target & """"
    If arg <> "" Then cmd = cmd & " """ & arg & """"
    sh.Run cmd, 0, False
End Sub
'''

_VBS_DETECT_BODY = r'''
' Is the engine installed? Writes Scripts\engine-status.ds, which the panel's .ds plays 1 s
' later (through a stored command, so it is parsed then, not at the press) and branches on:
' MagIf_engine status on, or off plus the reason as a system message.
Dim reason, py, f, dest, tmp
reason = ""
If Not fso.FileExists(pkg & "\Engine\magneto\render.py") Then
    reason = "the render engine (Engine folder) is not part of this installation"
Else
    py = EnginePython()
    If py = "" Then reason = "Python is not set up on this system yet"
End If
If reason = "" And Not fso.FileExists(scripts & "\kickoff_live_daemon.py") Then reason = "Scripts\kickoff_live_daemon.py is missing"
If reason = "" And Not fso.FileExists(scripts & "\kickoff_lookback_rebuild.py") Then reason = "Scripts\kickoff_lookback_rebuild.py is missing"

dest = scripts & "\engine-status.ds"
tmp = scripts & "\engine-status.tmp"
Set f = fso.CreateTextFile(tmp, True, False)
f.WriteLine "# {[2026.0.3.3]}"
f.WriteLine "# engine-status.ds  -  WRITTEN AT RUN TIME by Scripts\detect_engine.vbs " & Now
f.WriteLine "# on every Live Feed / Rebuild Look-back press. Do not edit."
f.WriteLine ""
f.WriteLine "0:00"
f.WriteLine vbTab & "MagIf_engine is toggleClass type permanent"
If reason = "" Then
    f.WriteLine vbTab & "MagIf_engine status on"
Else
    f.WriteLine vbTab & "MagIf_engine status off"
    f.WriteLine vbTab & "system message ""The Python engine is not ready: " & reason & ". Press 0. Install Python Engine (about 5 minutes). Stored storms and the shipped look-back play without it."""
End If
f.Close
' replace, never delete-then-write: a reader that arrives early gets the previous answer, not nothing
If fso.FileExists(dest) Then fso.DeleteFile dest, True
fso.MoveFile tmp, dest
'''

VBS_FILES = {
    VBS_DETECT: ("is the Python engine installed? (writes engine-status.ds)", _VBS_DETECT_BODY),
    VBS_LIVE_START: ("start the live daemon (Scripts\\kickoff_live_daemon.py start)",
                     '\nRunPy "kickoff_live_daemon.py", "start"\n'),
    VBS_LIVE_STOP: ("ask the live daemon to stop (Scripts\\kickoff_live_daemon.py stop)",
                    '\nRunPy "kickoff_live_daemon.py", "stop"\n'),
    VBS_LOOKBACK: ("rebuild the 48 h look-back (Scripts\\kickoff_lookback_rebuild.py)",
                   '\nRunPy "kickoff_lookback_rebuild.py", ""\n'),
}


def write_vbs(out_dir: Path | None = None) -> list[Path]:
    out_dir = Path(out_dir) if out_dir else paths.SCRIPTS_DIR
    written = []
    for name, (what, body) in VBS_FILES.items():
        text = (f"' {name}  -  GENERATED by Engine\\tools\\write_shims.py: {what}\n"
                "' STATIC and self-locating: the package is found from this file's own path, so it\n"
                "' names no drive and no machine. Started by `system execute` from the panel's .ds.\n"
                + _VBS_COMMON + body)
        if re.search(r"\b[A-Za-z]:\\", text):
            raise ValueError(f"{name}: a drive letter in a static launcher")
        dest = out_dir / name
        tmp = dest.with_suffix(".vbs.tmp")
        with open(tmp, "w", encoding="ascii", newline="\r\n") as fh:
            fh.write(text)
        os.replace(tmp, dest)
        written.append(dest)
    return written


def write_engine_status_default(out_dir: Path | None = None) -> Path:
    """engine-status.ds as it should SHIP: off. detect_engine.vbs rewrites it before every read, so
    this answers only when that launcher did not run - and on an installed dome it does not EXIST until
    Install Python Engine has run (Digistar's packager drops .vbs files; mag_setup.py writes them). So
    the message says how to get the engine, and the safe answer 'no engine' leaves the dome alone."""
    out_dir = Path(out_dir) if out_dir else paths.SCRIPTS_DIR
    lines = _head(ENGINE_STATUS, "default answer (off) until detect_engine.vbs has checked this system")
    lines += ["0:00"] + flag_decl("engine") + [
        f"\t{flag('engine')} status off",
        '\tsystem message "The Python engine is not installed on this system yet: press 0. Install Python Engine '
        '(a few minutes, asks first). Stored storms and the shipped look-back play without it."',
        ""]
    return _write(out_dir / ENGINE_STATUS, lines)


# ---------------------------------------------------------------------------------------
# Toggle and layer scripts, in .ds instead of JavaScript.
#
# WHY. The panel's 14 toggle buttons use ObjectToggleOnOffCommand, which names a scene OBJECT,
# not a file - so the buttons themselves tell the .dsz packager nothing. The paths live in each
# toggle's onCommand/offCommand, and until now those strings were BUILT AT RUNTIME inside
# MagnetoToggles.js ('event-' + EVENT_IDS[k] + '-play.ds'). That single concatenation was the
# only place the event shims were named anywhere in the package.
#
# House precedent for doing this in .ds is the OPiS panels, e.g.
# OPiS-Inner\Scripts\OurPlaceInSpaceSetupRev_08-1-26.ds:
#     DESI_toggle is toggleClass
#     DESI_toggle onCommand  "Script play .\DESI_dr1.ds"
#
# TWO RULES THIS DEPENDS ON, both from the Class/Scripting Reference and both load-bearing:
#  1. `X is <class>` on an EXISTING object is IGNORED - "Digistar ignores the command, and
#     Digistar does not set the command's attributes to the default values" (sr_creating_objects).
#     That is the only existence-safe primitive .ds has, and it is what makes this file safe to
#     replay mid-show. It also registers the name at parse time, so the lines below resolve.
#  2. Writing `status` DIRECTLY never runs the commands; only `on`/`off`/`toggle` do
#     (cr_toggleclass). So status sync stays side-effect free.
# Note what is deliberately ABSENT: any `status` line. A freshly created toggleClass defaults to
# off, and re-running this file must NOT darken a lit panel mid-show.
LAYER_TOGGLES = {
    "FieldLines": "MagFieldLines_toggle", "WindParticles": "MagSolarWind_toggle",
    "FieldPulses": "MagFieldPulses_toggle", "Shell": "MagShells_toggle",
    "AuroraOval": "MagAuroraOval_toggle", "RingCurrent": "MagRingCurrent_toggle",
    "Plasmasphere": "MagPlasmasphere_toggle", "IMFLines": "MagIMF_toggle",
    "HUD": "MagReadout_toggle",
}
# Layers the event player raises at ACTION start. 'Shell' is NOT here: whether the shells start
# visible is a property of the PACKAGE (its manifest's shells_hidden), so it is set per event.
PLAYER_RAISES = ("FieldLines", "WindParticles", "FieldPulses", "AuroraOval",
                 "RingCurrent", "Plasmasphere", "IMFLines", "HUD")
HOLDER_INTEN = {"FieldLines": 92, "WindParticles": 92, "AuroraOval": 92, "FieldPulses": 92,
                "RingCurrent": 30, "Plasmasphere": 55, "IMFLines": 45}
HUD_INTEN = 70
HUD_KEY = ("READOUT: Wind = solar wind speed at L1, km/s|Bz = north-south IMF, nT: negative = "
           "southward = the storm driver|n = proton density per cc|Kp = global geomagnetic "
           "index 0-9|then time UT, spacecraft, LIVE data age or REPLAY")
DS_COMMAND_STRING_MAX = 256      # cr_commandstring.html
# The text after `|` in `js play file.js | <call>`. Measured on the dome 2026-10-04 (Solar Interior):
# "JavaScript parameters length exceeds maximum allowed of 259 characters".
JS_PARAM_MAX = 259


# ---- .ds-native state: flags, declare-first and branches (2026-10-04) --------------------------
# Solar Interior's pattern, dome-probed on 2026-10-04 and used there to retire four JS files. A FLAG
# is a permanent toggleClass that no panel button is bound to: one bit a .ds can branch on.
# `F toggle` runs offCommand when status is on and onCommand when it is off (cr_toggleclass.html), so
# branch() writes the status back as the FIRST command of each string and the flag ends where it
# started. resetStatus on = "the status attribute turns off when a permanent ... toggleClass object
# resets", which a plain `system reset` does too - so a reset from ANY package darkens the flags that
# describe what it deleted (the event, the live objects, the markers). The proxies are permanent
# emptyClass objects that survive a reset with their values, so their flag keeps its status.
# The MagIf_ prefix keeps every flag clear of make_dsz's unbound-toggle guard (Mag\w+_toggle).
FLAGS = {  # key -> (object, resetStatus)
    "event":   ("MagIf_event", True),      # a storm or the look-back is on the dome (holders exist)
    "live":    ("MagIf_live", True),       # the live loader is running (its holders exist)
    "rig":     ("MagIf_rig", True),        # the ten camera markers exist
    "proxies": ("MagIf_proxies", False),   # the four style proxies exist and carry values
    "engine":  ("MagIf_engine", True),     # the Python engine is installed (detect_engine.vbs)
    "drift":   ("MagIf_drift", True),      # mirrors `navigation drift` - the Xbox X button TOGGLES it
}
# Flags that are real toggles rather than branch bits: their on/offCommand is defined once in
# toggles-define.ds, and a branch() on them would overwrite it.
NOT_BRANCHABLE = {"drift"}
LOADER_STOP = "\tjs stop MagnetoLiveLoader"
LOADER_ASK_STOP = "\tjs message MagnetoLiveLoader>stop"


def flag(key: str) -> str:
    return FLAGS[key][0]


def flag_decl(*keys: str) -> list[str]:
    """Declare-first. `X is toggleClass type permanent` on an existing X is ignored and leaves its
    attributes alone (sr_creating_objects.html), so this is safe to repeat - and it makes the name
    known when Digistar PARSES the script, straight after a reset too."""
    out = []
    for k in keys:
        n, reset = FLAGS[k]
        out += [f"\t{n} is toggleClass type permanent", f"\t{n} resetStatus {'on' if reset else 'off'}"]
    return out


def branch(key: str, if_on=(), if_off=()) -> list[str]:
    """if/else on a flag: run `if_on` when its status is on, `if_off` when it is off.

    Each side is a backtick-separated command string (cr_toggleclass.html: "can contain ... multiple
    commands"), so nothing in it may contain a double quote - a system message goes in its own
    script and is `script play`ed. The commands run as a command-string script a moment AFTER the
    toggle, which is why a script must never branch twice on the same flag in one cue."""
    if key in NOT_BRANCHABLE:
        raise ValueError(f"{flag(key)} is a real toggle; a branch would overwrite its commands")
    n = flag(key)
    on_cmds = "`".join([f"{n} status on"] + list(if_on))
    off_cmds = "`".join([f"{n} status off"] + list(if_off))
    for c in (on_cmds, off_cmds):
        if '"' in c:
            raise ValueError(f"double quote inside a command string: {c}")
        if len(c) > DS_COMMAND_STRING_MAX:
            raise ValueError(f"{len(c)} > {DS_COMMAND_STRING_MAX} characters: {c}")
    return [f'\t{n} offCommand "{on_cmds}"', f'\t{n} onCommand "{off_cmds}"', f"\t{n} toggle"]


def _after_reset_flags() -> list[str]:
    """What a `system reset` deleted, said explicitly in the cue AFTER it. resetStatus already does
    this; writing it costs three lines and means no shim depends on WHEN the host applies it."""
    return [f"\t{flag('rig')} status off", f"\t{flag('event')} status off",
            f"\t{flag('live')} status off",
            f"\t{flag('drift')} status on"]          # _reset_and_instanav_lines turned drift on


def _sp(fname: str) -> str:
    """`script play <Scripts>\\fname` with the absolute token - the only form a STORED command may
    use (make_dsz stored-command guard), and a literal path the .dsz packager follows."""
    return f"script play {_tok(paths.SCRIPTS_DIR / fname)}"


def _js(fname: str, call: str) -> str:
    if len(call) > JS_PARAM_MAX:
        raise ValueError(f"{len(call)} > {JS_PARAM_MAX} characters after the |: {call}")
    return f"js play {_tok(paths.SCRIPTS_DIR / fname)} | {call}"


def _exec_vbs(path: Path) -> str:
    return f"system execute {_tok(path)}"


_CMD_ATTR = re.compile(r'^\s*(\S+)\s+(onCommand|offCommand)\s+"(.*)"\s*$')
_CUE = re.compile(r"^(?:\d+:\d+(?::\d+)?(?:\.\d+)?|\d+(?:\.\d+)?|\+\d+(?:\.\d+)?)\s*$")


def lint_ds(name: str, lines: list[str]) -> None:
    """The rules every generated .ds must pass, enforced at write time so a bad file is never written.

      * a stored command string is <= 256 characters and contains no double quote
      * the call after `js play x.js |` is <= 259 characters
      * a script never branches (`F toggle`) on the same flag twice in one cue
      * every flag whose status a script writes or toggles is DECLARED earlier in that script
      * no `fadeStopReset` (the Users Guide: never from a script)
      * a script that runs `system reset` writes the rig/event/live flags in a LATER cue
    """
    names = {v[0] for v in FLAGS.values()}
    declared: set[str] = set()
    cue_toggles: set[str] = set()
    cue_no = 0
    reset_cue = None
    flag_writes_after_reset: set[str] = set()
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if _CUE.match(s):
            cue_no += 1
            cue_toggles = set()
            continue
        where = f"{name} line {i + 1}"
        if "fadestopreset" in s.lower():
            raise ValueError(f"{where}: fadeStopReset from a script ({s})")
        m = _CMD_ATTR.match(raw)
        if m:
            body = m.group(3)
            if '"' in body or len(body) > DS_COMMAND_STRING_MAX:
                raise ValueError(f"{where}: bad command string ({len(body)} chars): {body}")
        if s.startswith("js play") and "|" in s:
            call = s.split("|", 1)[1].strip()
            if len(call) > JS_PARAM_MAX:
                raise ValueError(f"{where}: js parameter {len(call)} > {JS_PARAM_MAX}: {call}")
        tok = s.split()
        if len(tok) >= 3 and tok[1] == "is" and tok[0] in names:
            declared.add(tok[0])
        if tok and tok[0] in names and len(tok) >= 2 and tok[1] in ("status", "toggle", "offCommand",
                                                                     "onCommand", "on", "off"):
            if tok[0] not in declared:
                raise ValueError(f"{where}: {tok[0]} used before it is declared")
            if tok[1] == "toggle":
                if tok[0] in cue_toggles:
                    raise ValueError(f"{where}: second branch on {tok[0]} in one cue")
                cue_toggles.add(tok[0])
            if reset_cue is not None and cue_no > reset_cue and tok[1] == "status":
                flag_writes_after_reset.add(tok[0])
        if s == "system reset":
            reset_cue = cue_no
            flag_writes_after_reset = set()
    if reset_cue is not None:
        need = {flag("rig"), flag("event"), flag("live")}
        if not need <= flag_writes_after_reset:
            raise ValueError(f"{name}: `system reset` without writing {sorted(need - flag_writes_after_reset)} "
                             "in a later cue")


def _all_toggle_objects() -> list[str]:
    """Every toggle object toggles-define.ds creates, in definition order."""
    names = [f"MagEvent_{presets.event_key(eid)}_toggle" for eid in events.EVENTS]
    names += ["MagLookback_toggle", "MagLiveFeed_toggle"]
    names += list(LAYER_TOGGLES.values())
    names += ["MagNativeAurora_toggle", "MagTextbook_toggle"]
    return names


def _status_script(name: str, what: str, wanted: dict[str, bool], why: str) -> list[str]:
    r"""A status-sync script: declare every toggle it touches, then set it.

    The declaration lines are NOT decoration. A .ds resolves every object name at PARSE time and
    a single unknown name refuses the WHOLE file ("Cannot play script ... because of errors",
    observed 6/6 in this box's logs) - and Fade-Stop-Reset CLEARS the name table
    (sr_the_name_table.html). So a shim played after an FSR but before Panel Setup would be
    refused outright. `X is toggleClass type permanent` is ignored for an object that already
    exists (sr_creating_objects.html) and registers the name when it does not, which makes this
    file safe in both worlds.

    Writing `status` directly never fires onCommand/offCommand (cr_toggleclass.html) - that is
    what lets these files keep the panel truthful without re-triggering anything.
    """
    lines = _head(name, what)
    lines += ["# " + why, "",
              "# declared first so this file parses even straight after a Fade-Stop-Reset,",
              "# which clears the name table - see write_shims._status_script().",
              "", "0:00"]
    for obj in _all_toggle_objects():
        if obj in wanted:
            lines.append(f"\t{obj} is toggleClass type permanent")
    for obj in _all_toggle_objects():
        if obj in wanted:
            lines.append(f"\t{obj} status {'on' if wanted[obj] else 'off'}")
    return lines


def _event_toggle_name(event_id: str) -> str:
    if str(event_id).startswith(events.LOOKBACK_PREFIX):
        return "MagLookback_toggle"
    return f"MagEvent_{presets.event_key(event_id)}_toggle"


def _shells_hidden(event_id: str) -> bool:
    """Straight from the manifest the player reads, so the button cannot disagree with the dome."""
    man = manifest_path(event_id)
    if not man.exists():
        return False
    try:
        return bool(json.loads(man.read_text(encoding="utf-8")).get("shells_hidden"))
    except (OSError, ValueError):
        return False


def write_toggles_status(out_dir: Path | None = None) -> list[Path]:
    """The per-event / live status-sync scripts that replace MagnetoToggles.js.

    Every argument the JavaScript took at runtime - which event, whether it has a native-aurora
    companion, whether its shells start up - is known HERE, at shim-write time. So the tri-state
    logic collapses into literal `status on|off` lines, which is both simpler and the reason the
    shim paths stop being JavaScript strings.
    """
    out_dir = Path(out_dir) if out_dir else paths.SCRIPTS_DIR
    written = []
    all_events = [_event_toggle_name(e) for e in events.EVENTS] + ["MagLookback_toggle"]
    layer_objs = [LAYER_TOGGLES[k] for k in PLAYER_RAISES if k in LAYER_TOGGLES]

    ids = list(events.EVENTS)
    lb = current_lookback_id()
    if lb:
        ids.append(lb)

    for eid in ids:
        key = presets.event_key(eid)
        me = _event_toggle_name(eid)
        aurora = (scripts_dir_for(eid) / dsds.DS_NATIVE_AURORA_FMT.format(eid)).exists()
        shells_up = not _shells_hidden(eid)

        on: dict[str, bool] = {o: False for o in all_events}
        on[me] = True
        on["MagLiveFeed_toggle"] = False
        for o in layer_objs:
            on[o] = True
        on[LAYER_TOGGLES["Shell"]] = shells_up
        on["MagNativeAurora_toggle"] = aurora
        written.append(_write(out_dir / f"toggles-{key}-on.ds", _status_script(
            f"toggles-{key}-on.ds", f"light the panel for {presets.event_label(eid)}",
            on,
            f"Shells start {'UP' if shells_up else 'DOWN'} for this package (its manifest's "
            f"shells_hidden), and it {'has' if aurora else 'has no'} native-aurora companion. "
            "A layer toggle that does not match the dome is worse than no toggle at all.")))

        off = {me: False, "MagNativeAurora_toggle": False}
        for o in layer_objs:
            off[o] = False
        off[LAYER_TOGGLES["Shell"]] = False
        written.append(_write(out_dir / f"toggles-{key}-off.ds", _status_script(
            f"toggles-{key}-off.ds", f"darken the panel after {presets.event_label(eid)} stops",
            off, "Nothing is playing, so every layer this event raised goes dark too.")))

    everything = {o: False for o in _all_toggle_objects() if o != "MagTextbook_toggle"}
    written.append(_write(out_dir / "toggles-all-off.ds", _status_script(
        "toggles-all-off.ds", "darken every event, look-back, live and layer toggle",
        everything,
        "A manual reset for the panel (no shim plays it since clear-events.ds retired on "
        "2026-10-04). Textbook Field is left alone: it is Digistar's own static model.")))

    live_on = {o: False for o in all_events}
    live_on["MagLiveFeed_toggle"] = True
    for o in layer_objs:
        live_on[o] = True
    live_on[LAYER_TOGGLES["Shell"]] = True
    live_on["MagNativeAurora_toggle"] = False        # the live feed drives no native aurora
    written.append(_write(out_dir / "toggles-live-on.ds", _status_script(
        "toggles-live-on.ds", "light the panel for the live feed",
        live_on, "The live feed draws its shells, unlike Gannon.")))

    live_off = {"MagLiveFeed_toggle": False, "MagNativeAurora_toggle": False}
    for o in layer_objs:
        live_off[o] = False
    live_off[LAYER_TOGGLES["Shell"]] = False
    written.append(_write(out_dir / "toggles-live-off.ds", _status_script(
        "toggles-live-off.ds", "darken the panel when the live feed stops",
        live_off,
        "Clearing the whole layer row matters here: leaving it lit over an empty dome was a "
        "real bug on 2026-09-16.")))
    return written


def _layer_script_names(layer: str) -> tuple[str, str]:
    if layer == "Shell":
        return "shells-on.ds", "shells-off.ds"
    return f"layer-{layer}-on.ds", f"layer-{layer}-off.ds"


# The mesh layers' brightness is their HOLDER COLOUR (blendMode add; the crossfade slots carry
# explicit intensities, which REPLACE a holder intensity), faded with MagnetoShells.js's own easing
# and durations: setShells() 1 s, setMesh() 2 s, both `25% 25%`. Lines and dots fade INTENSITY,
# which their frame objects inherit (MagnetoStyle.js layer(): 1 s up, 2 s down).
MESH_FADE_S = {"Shell": 1, "RingCurrent": 2, "Plasmasphere": 2}
MESH_EASE = "25% 25%"
SHELL_PARTS = ("MagnetopauseShell", "BowShockShell")


def _holder(source: str, part: str) -> str:
    return dsds.event_holder(part) if source == "event" else dsds.live_holder(part)


def layer_commands(layer: str, on: bool, source: str) -> list[str]:
    """What one layer button sends to ONE source's holders (source 'event' or 'live')."""
    if layer == "HUD":
        hud = "MagnetoEventHUD" if source == "event" else "MagnetoLiveHUD"
        return [f"{hud} intensity {HUD_INTEN if on else 0} duration 1"]
    if layer in MESH_FADE_S:
        out = []
        for part in (SHELL_PARTS if layer == "Shell" else (layer,)):
            r, g, b = dsds.holder_color(part) if on else (0, 0, 0)
            out.append(f"{_holder(source, part)} color {r:g} {g:g} {b:g} "
                       f"duration {MESH_FADE_S[layer]:g} {MESH_EASE}")
        return out
    inten = HOLDER_INTEN.get(layer, 92) if on else 0
    return [f"{_holder(source, layer)} intensity {inten:g} duration {1 if on else 2}"]


def write_layer_scripts(out_dir: Path | None = None) -> list[Path]:
    r"""One tiny .ds per layer per direction - the layer toggles' on/offCommand since 2026-10-04.

    MagnetoStyle.js layer() existed to name only holders that EXIST (a .ds line naming a missing
    object logs an error; 3,394 of them in one 2026-09-21 session). The MagIf_event / MagIf_live
    flags say which holders exist, so each script branches on them and names nothing else: no
    JavaScript, and still no error when only one source is up.
    """
    out_dir = Path(out_dir) if out_dir else paths.SCRIPTS_DIR
    written = []
    for layer in LAYER_TOGGLES:
        for name, on in zip(_layer_script_names(layer), (True, False)):
            if layer in MESH_FADE_S:
                why = ("brightness is the HOLDER COLOUR (preset x 1 or x 0): the crossfade slots carry "
                       "explicit intensities, and an explicit child intensity replaces the holder's")
            elif layer == "HUD":
                why = "raising the readout also posts the presenter's key to its six segments"
            else:
                why = "children inherit intensity from their holder, so one line fades every frame object"
            lines = _head(name, f"{'raise' if on else 'lower'} the {layer} layer on whichever source is up")
            lines += ["# " + why + ".",
                      "# Each branch names a source's holders only while that source's flag says they exist.",
                      "", "0:00"]
            lines += flag_decl("event", "live")
            lines += branch("event", if_on=layer_commands(layer, on, "event"))
            lines += branch("live", if_on=layer_commands(layer, on, "live"))
            if layer == "HUD" and on:
                lines.append(f'\tsystem message "{HUD_KEY}"')      # <= 255 chars, | is a line break
            written.append(_write(out_dir / name, lines + [""]))

    for name, body in (
        ("native-aurora-on.ds",
         ["\tEarth auroraDaylightElevation 90 89", "\tEarth auroraIntensity 60 duration 3"]),
        ("native-aurora-off.ds", ["\tEarth auroraIntensity 0 duration 3"]),
    ):
        lines = _head(name, "Digistar's own planet aurora on the Earth object")
        lines += ["# auroraDaylightElevation 90 89 keeps the oval visible from space: the default",
                  "# 2 -18 fades it out in daylight. Earth always exists, so this cannot miss.",
                  "", "0:00"] + body
        written.append(_write(out_dir / name, lines))
    return written


def write_toggles_define(out_dir: Path | None = None) -> Path:
    r"""toggles-define.ds - every toggleClass object, with its paths LITERAL, and the MagIf_ flags.

    This is the ONLY definition of the toggles' commands. Until 2026-10-04 MagnetoToggles.js
    ensure() rewrote them at run time from its own table, so an edit here could be silently undone
    by the next storm press (the 2026-09-21 .ds layer toggles were rolled back for exactly that).
    It is also the file that makes the storms visible to the packager.
    """
    out_dir = Path(out_dir) if out_dir else paths.SCRIPTS_DIR

    defs: list[tuple[str, str, str]] = []
    for eid in events.EVENTS:
        on_name, off_name = event_shim_names(eid)
        defs.append((f"MagEvent_{presets.event_key(eid)}_toggle", _sp(on_name), _sp(off_name)))
    defs.append(("MagLookback_toggle", _sp("lookback-latest-on.ds"), _sp("lookback-latest-off.ds")))
    defs.append(("MagLiveFeed_toggle", _sp("live-play.ds"), _sp("live-remove.ds")))
    for layer, obj in LAYER_TOGGLES.items():
        on_name, off_name = _layer_script_names(layer)
        defs.append((obj, _sp(on_name), _sp(off_name)))
    defs.append(("MagNativeAurora_toggle", _sp("native-aurora-on.ds"), _sp("native-aurora-off.ds")))
    defs.append(("MagTextbook_toggle", "EarthMagnetosphere on duration 2",
                 "EarthMagnetosphere off duration 2"))
    # The Xbox X button (xboxReset.ds binds `xbox0 command 10 "MagIf_drift toggle"`): a REAL toggle,
    # unlike the branch flags. Every script that writes `navigation drift` writes its status too.
    defs.append((flag("drift"), "navigation drift on", "navigation drift off"))

    lines = _head("toggles-define.ds", "every toggleClass object behind a panel button, and the MagIf_ flags")
    lines += [
        "# Safe to replay at any time: `X is toggleClass` on an existing object is IGNORED and",
        "# does not reset its attributes (sr_creating_objects.html), and NOTHING here writes",
        "# `status` - so re-running this mid-show cannot darken a lit button or flip a flag.",
        "",
        "0:00",
    ]
    for name, on, off in defs:
        for s, which in ((on, "onCommand"), (off, "offCommand")):
            if len(s) > DS_COMMAND_STRING_MAX:
                raise ValueError(
                    f"{name} {which} is {len(s)} chars; the host caps a commandString at "
                    f"{DS_COMMAND_STRING_MAX} (cr_commandstring.html): {s}")
        lines += [f"\t{name} is toggleClass type permanent",
                  f"\t{name} resetStatus on",
                  f'\t{name} onCommand "{on}"',
                  f'\t{name} offCommand "{off}"']
    lines += ["", "\t# the state flags (write_shims.FLAGS): declared and given their resetStatus, no commands"]
    lines += flag_decl(*[k for k in FLAGS if k != "drift"])
    return _write(out_dir / "toggles-define.ds", lines + [""])


# The packager's model declarations (`<key>-models-NofM.ds`, 2026-09-20 .. 2026-10-15) are RETIRED: they existed
# because a very long script was thought to choke the packager, but its IL has no length cap (a 28,000-line
# player costs ~11 s) and it does per-REFERENCE work for every model line, so naming all ~19,000 models a
# second time only doubled that work. The players name every model; tools\native_pack_check.py proves it.
# The glob stays so a leftover file is easy to find and delete.
MODEL_DECL_GLOB = "*-models-*of*.ds"


PACKAGE_MANIFEST_NAME = "package-manifest.ds"


def _manifest_targets() -> list[Path]:
    """Every .ds the packager must reach, in the order a reader would want them.

    Built from the SAME helpers the shim writers use - rendered_ids(), event_shim_names(),
    scripts_dir_for(), the dsexport name formats - so it cannot drift from what was actually
    written. test_manifest_lists_exactly_what_the_shim_writers_wrote pins that.
    """
    S = paths.SCRIPTS_DIR
    out: list[Path] = []

    # 1. the panel-facing shims. They are named literally in toggles-define.ds now; until
    #    2026-10-04 they existed only as a JavaScript string concatenation in MagnetoToggles.js,
    #    which is why this file was written - ~20 GB of Renders\ was invisible to the packager.
    for eid in events.EVENTS:
        on_name, off_name = event_shim_names(eid)
        out += [S / on_name, S / off_name]
    lb = current_lookback_id()
    if lb:
        on_name, off_name = event_shim_names(lb)
        out += [S / on_name, S / off_name]
    out += [S / n for n in LIVE_SCRIPTS]

    # 1b. the panel-behaviour scripts. The status files are already reachable transitively (a
    #     shim plays one literally), but the layer / shells / native-aurora files are named ONLY
    #     inside a quoted `onCommand "script play ..."` in toggles-define.ds - and whether the
    #     scanner reads a path inside a quoted attribute value is exactly what we have not been
    #     able to verify. Listing them costs nothing and removes the dependency on that answer.
    out.append(S / "toggles-define.ds")
    out.append(S / CLEANUP_NAME)
    out.append(S / PYTHON_FILES_DS)      # -> the flat .py files and MagnetoEngine.zip (py play)
    out += sorted(S.glob("toggles-*-on.ds")) + sorted(S.glob("toggles-*-off.ds"))
    out += sorted(S.glob("layer-*.ds")) + sorted(S.glob("shells-*.ds"))
    out += sorted(S.glob("native-aurora-*.ds"))

    # 2. the camera scripts behind the view buttons (build_views.ps1). The _nav bodies, rig_create.ds
    #    and rig_aim.ds name the markers or make them, so since 2026-10-07 they are reached ONLY
    #    through quoted onCommand/offCommand strings (a literal `script play` is parsed with its
    #    caller, before the markers exist - PARSE RULE in build_views.ps1). Listing them here is what
    #    keeps them reachable by a literal-token scan.
    out += sorted(S.glob("view_*_go.ds")) + sorted(S.glob("view_*_nav.ds"))
    out += sorted(S.glob("rig_create*.ds")) + sorted(S.glob("rig_aim*.ds"))

    # 3. every rendered package's own scripts. Reaching the player is what matters: it carries
    #    a literal `model ..\Models\<file>` operand for every model the render ships.
    for eid in rendered_ids():
        sd = scripts_dir_for(eid)
        # NOT event-<id>-on.ds, the teardown (magneto-event-<id>-off.ds) or live-restore.ds: no
        # shim plays them since 2026-10-04, and each pulled a render-written JS file
        # (MagnetoPlayer-<id>.js, MagnetoLive.js) into the package.
        # Nor magneto-status.ds (2026-10-15): nothing plays it, and every render has one under the
        # same name, so Digistar's packager shipped six renamed copies for nothing.
        for name in (dsds.DS_EVENT_PLAYER_FMT.format(eid),
                     f"event-{eid}-off.ds",
                     dsds.DS_NATIVE_AURORA_FMT.format(eid),
                     dsds.DS_NATIVE_AURORA_OFF_FMT.format(eid)):
            p = sd / name
            if p.exists():
                out.append(p)

    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def write_package_manifest(out_dir: Path | None = None) -> Path:
    r"""package-manifest.ds - make every shipped asset visible to Digistar's content packager.

    WHY THIS FILE EXISTS
    --------------------
    Digistar's packager (Library > Share Item via Content Package) seeds its scan at the panel
    and follows paths it recognises by KEYWORD (`script play|include`, `<object> play|model|path`,
    `js play`, the quoted command of any attribute ending in "command", ...) through .dscp, .ds
    and .js files (Digistar 2026.0.3.3, read from its IL on 2026-10-15). Several things this
    package plays are named only inside quoted onCommand strings or through a MagIf_ flag branch
    (whose first command is a `status` write, so the packager never sees the path after the back
    tick), and the camera bodies only through rig flags. This file names every one of them
    literally, so the package does not depend on how deep those chains are followed.

    HOW IT WORKS
    ------------
    Every target is a literal `script play <token>` operand AFTER `script end`: the host stops
    executing at `script end` (verified on the dome 2026-09-17: nothing fired), while the
    packager reads every line to the end of the file. A `#` comment line is NOT followed (its IL
    skips `^\s*($|;|#)`), which is why the commented copy this file used to carry is gone.
    Panel Setup plays this file, which is how the packager reaches it.

    THE PACKAGER REWRITES what it finds, in every staged .ds (relative: `.\x.ds`,
    `..\Models\<stem>\<file>`) and in the panel and .js files (absolute, at the new install
    path) - including inside stored onCommand strings. A relative stored command set by a .ds
    WORKS (dome-probed 2026-10-15), so the toggles survive it.

    The Share dialog's Advanced view lists what the scan found (Cancel writes nothing).
    Scripts\native_pack_check.py emulates the scan and fails closed before a share.
    """
    out_dir = Path(out_dir) if out_dir else paths.SCRIPTS_DIR
    targets = _manifest_targets()
    lines = _head(PACKAGE_MANIFEST_NAME,
                  "every shipped .ds, named literally, so Digistar's packager can find them")
    lines += [
        "# Playing this does nothing, and it is verified: on 2026-09-17 this file was played on",
        "# the dome, no shim fired, and package-manifest.lis lists nothing past `script end`.",
        "# Digistar's packager reads every line after `script end` (it skips # comment lines).",
        r"# See the docstring in Engine\tools\write_shims.py.",
        "",
        "0:00",
        '	system message "Live Magnetosphere package manifest - nothing to play"',
        "	script end",
        "",
        f"# ---- {len(targets)} files, after `script end` " + "-" * 40,
    ]
    for p in targets:
        lines.append(f"	script play {_tok(p)}")
    return _write(out_dir / PACKAGE_MANIFEST_NAME, lines)


def write_panel_setup(out_dir: Path | None = None) -> Path:
    r"""MagnetoPanelSetup.ds - the panel's "1. Panel Setup" button (hand-written until 2026-10-04).

    Idempotent and safe at any time: nothing here writes a panel toggle's status, the proxies are
    made only when their flag says they never were (so Setup can never put the defaults back over
    the operator's sliders), and the rig is created only when its flag says it is missing.
    """
    out_dir = Path(out_dir) if out_dir else paths.SCRIPTS_DIR
    lines = _head("MagnetoPanelSetup.ds", "arm the Live Magnetosphere panel (idempotent, safe any time)")
    lines += [
        "#   1. toggles-define.ds: the toggle object behind every button, and the MagIf_ flags",
        "#   2. the four emptyClass proxies the Style sliders bind to (MagnetoStyle.js ensureProxies,",
        "#      which sets MagIf_proxies) - only while that flag is off",
        "#   3. the Sun-fixed camera markers (rig_ensure.ds: created if MagIf_rig is off, re-aimed)",
        "#   4. the completion watcher of a render that is still running (kickoff_watch.js resume())",
        "# The toggle objects, the flags and the proxies are permanent and survive a Fade-Stop-Reset;",
        "# the markers and the watcher do not, which is why this is the button to press after one.",
        "",
        "0:00",
        f"\t{_sp('toggles-define.ds')}",
    ]
    lines += flag_decl("proxies")
    lines += ["", "+0.2"]
    lines += branch("proxies", if_off=[_js("MagnetoStyle.js", "ensureProxies()")])
    lines += [f"\t{_sp('rig_ensure.ds')}",
              "", "+1.0",
              "\t# MagnetoStyleWatch.js retired 2026-09-16: stop one left by an older session or package",
              "\tjs stop MagnetoStyleWatch",
              f"\t{_js('kickoff_watch.js', 'resume()')}",
              '\tsystem message "Live Magnetosphere panel ready|toggles, style proxies and camera rig armed"',
              "",
              "# The line below exists for the .dsz PACKAGER, not for the dome. package-manifest.ds names",
              "# every shipped .ds with a literal path; playing it does nothing (it starts with `script end`).",
              "+0.1",
              f"\t{_sp(PACKAGE_MANIFEST_NAME)}",
              ""]
    return _write(out_dir / "MagnetoPanelSetup.ds", lines)


CLEANUP_NAME = "MagnetoCleanup.ds"
# MagnetoStyle.js KNOBS (hand-authored): the permanent emptyClass objects the Style sliders bind to.
# test_cleanup pins this list to that file and to build_panel.SLIDERS.
STYLE_PROXIES = ("magnetoWindDot", "magnetoPulseDot", "magnetoFieldWidth", "magnetoImfWidth")
# Every JavaScript that loops, or looped in an older build an installed dome may still run, plus
# the two one-shot fan-outs. `js stop` is a HARD stop (digistar-js): no shutdown() command can land
# after the reset, which is why the loader is not asked with `js message ...>stop` here.
CLEANUP_JS = ("MagnetoLiveLoader", "kickoff_watch", "MagnetoStyleWatch", "MagnetoLive", "kickoff",
              "MagnetoToggles", "MagnetoStyle", "MagnetoShells")
# Look-back ids that shipped in a .dsz. An older install on another dome may be playing one of
# these; `script stop` on a name that is not running only warns ("not in active list").
SHIPPED_LOOKBACK_IDS = ("lookback-20260915-20260917", "lookback-20260927-20260929",
                        "lookback-20261005-20261007")
XBOX_X_BUTTON = 10      # xboxReset.ds binds Xbox X (command 10) to `MagIf_drift toggle`
CLEANUP_START_MSG = "Live Magnetosphere clean-up: stopping the live feed, players and scripts"
CLEANUP_DONE_MSG = ("Live Magnetosphere cleaned up|live feed, players, scripts and panel objects "
                    "removed|press Panel Setup to use the panel again")


def _all_lookback_ids() -> list[str]:
    """Every look-back id this package has carried: the shipped ones, the current and previous
    render, and any rebuilt flat in Scripts\\ on an installed dome."""
    ids = set(SHIPPED_LOOKBACK_IDS)
    root = paths.render_root("lookback")
    for sd in (root / "Scripts", root / "ScriptsPrevious", paths.SCRIPTS_DIR):
        for p in glob.glob(str(sd / "event-lookback-*-on.ds")):
            m = re.match(r"event-(lookback-\d{8}-\d{8})-on\.ds$", os.path.basename(p))
            if m:
                ids.add(m.group(1))
    return sorted(ids)


def cleanup_objects() -> list[tuple[str, str]]:
    """(name, class) of every PERMANENT object this package makes - what survives a reset and a
    Fade-Stop-Reset, so only Clean Up removes it: the panel toggles, the MagIf_ flags, the proxies."""
    out = [(n, "toggleClass") for n in _all_toggle_objects()]
    out += [(FLAGS[k][0], "toggleClass") for k in FLAGS if FLAGS[k][0] not in {n for n, _ in out}]
    out += [(n, "emptyClass") for n in STYLE_PROXIES]
    return out


def write_cleanup(out_dir: Path | None = None) -> Path:
    r"""MagnetoCleanup.ds - the panel's Clean Up button (David, 2026-10-12: "clean up any variables /
    digistar objects / running js scripts that might exist when I'm done with it").

    The Remove buttons' standalone reset, plus everything that reset and a Fade-Stop-Reset leave
    behind: the live daemon (outside Digistar), the looping JavaScript, the players, and the
    permanent objects. `delete` does not run a toggle's on/offCommand (cr_toggleclass.html), and
    Panel Setup recreates every deleted object (`X is <class>` on a deleted name re-creates it,
    sr_deleting_an_object.html). Every name is declared first, because one unknown name refuses the
    whole file (_status_script) - so it runs where Panel Setup never ran and straight after a
    Fade-Stop-Reset. Safe to press twice.
    """
    out_dir = Path(out_dir) if out_dir else paths.SCRIPTS_DIR
    perm = cleanup_objects()
    lines = _head(CLEANUP_NAME, "Clean Up: remove everything Live Magnetosphere left on this host")
    lines += [
        "# 1. the live daemon (pythonw, outside Digistar) is asked to exit; a render job is not touched",
        "# 2. the looping JavaScript, hard-stopped so nothing lands after the reset",
        "# 3. every storm / look-back player, stopped BEFORE the reset deletes its objects",
        "# 4. `system reset` + the Earth insta-nav every Remove button ends with (scene objects, markers)",
        f"# 5. the {len(perm)} permanent objects: panel toggles, MagIf_ flags, style proxies",
        "# 6. Xbox X, which xboxReset.ds wired to MagIf_drift, re-pointed at `navigation drift on`",
        "# Press Panel Setup to use the panel again.",
        "",
        "0:00",
        f"\teye color black duration {FADE_OUT_S:g}",
        f'\tsystem message "{CLEANUP_START_MSG}"',
        "\t# declared first so every line below parses, whatever state the dome is in",
    ]
    lines += [f"\t{n} is {cls} type permanent" for n, cls in perm]
    lines += ["\txbox0 is xBoxClass",
              f"\t{_exec_vbs(paths.SCRIPTS_DIR / VBS_LIVE_STOP)}"]
    lines += [f"\tjs stop {j}" for j in CLEANUP_JS]
    lines.append("\t# \"not in active list\" warnings for players that are not running are expected")
    for eid in list(events.EVENTS) + _all_lookback_ids():
        for fmt in (dsds.DS_EVENT_PLAYER_FMT, dsds.DS_EVENT_TEARDOWN_FMT,
                    dsds.DS_NATIVE_AURORA_FMT, dsds.DS_NATIVE_AURORA_OFF_FMT):
            lines.append(f"\tscript stop {fmt.format(eid)[:-3]}")
    lines += ["\t# what Native Aurora and Textbook Field switch on, faded in the dark",
              f"\tEarth auroraIntensity 0 duration {FADE_OUT_S:g}",
              f"\tEarthMagnetosphere off duration {FADE_OUT_S:g}",
              "", f"+{FADE_OUT_S + 0.1:g}"]
    lines += _reset_and_instanav_lines(None)
    lines += ["", f"+{GAP_NAV_S:g}", "\tnavigation skipAnimation"]
    lines += [f"\t{flag(k)} status off" for k in ("rig", "event", "live")]  # truthful if a delete fails
    lines += [f"\t{n} delete" for n, _ in perm]
    lines += [f'\txbox0 command {XBOX_X_BUTTON} "navigation drift on"',
              "", "+0.5",
              f"\teye color white duration {FADE_IN_S:g}",
              f'\tsystem message "{CLEANUP_DONE_MSG}"',
              ""]
    return _write(out_dir / CLEANUP_NAME, lines)


def write_all(out_dir: Path | None = None) -> list[Path]:
    written = []
    for eid in events.EVENTS:
        written += write_event_shims(eid, out_dir)
    written += write_lookback_shims(out_dir)
    written += write_live_shims(out_dir)
    written += write_vbs(out_dir)
    written.append(write_engine_status_default(out_dir))
    # the live feed's dome-side loader comes straight from dsexport. MagnetoLive.js and
    # MagnetoShells.js are no longer copied here (2026-10-04): nothing on the panel calls them.
    out = Path(out_dir) if out_dir else paths.SCRIPTS_DIR
    written.append(dsds.write_live_loader_js(out))
    written += write_layer_scripts(out_dir)
    written.append(write_toggles_define(out_dir))
    written += write_toggles_status(out_dir)
    written.append(write_panel_setup(out_dir))
    written.append(write_cleanup(out_dir))
    written.append(write_python_files_ds(out_dir))
    # LAST, so it lists the shims this run just wrote rather than the previous run's
    written.append(write_package_manifest(out_dir))
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--event", metavar="ID")
    g.add_argument("--lookback", action="store_true")
    ap.add_argument("--out-dir", default=None, help="default: <package>\\Scripts")
    args = ap.parse_args(argv)
    out = Path(args.out_dir) if args.out_dir else None
    if args.all:
        written = write_all(out)
    elif args.event:
        written = write_event_shims(args.event, out)
    else:
        written = write_lookback_shims(out)
    for p in written:
        print(p)
    print(f"rendered now: {', '.join(rendered_ids()) or '(nothing)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
