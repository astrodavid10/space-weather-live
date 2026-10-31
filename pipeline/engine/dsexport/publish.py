r"""Build-into-staging, promote-on-success publisher for a live Digistar root.

Semantics replicated from the verified daily/animation build
(the reference solar players' CLAUDE.md, sections "Idempotency & directory
rotation" and "Build-into-staging, promote-on-success"), because the failure
mode they defend against is exactly ours: the `.ds` player has NO
current->previous fallback, so a build that dies half-way must never be able to
leave a torn `Models\` on the dome.

Layout under an output root::

    <root>/
        Models/            live content (.vla / .vlc / .json) + .run_stamp
        ModelsPrevious/    exactly one archived generation - the rollback
        Scripts/           the .ds players
        ScriptsPrevious/
        .staging/          this run's build; Models/ + Scripts/ + .staging_stamp
        state/             daemon-owned, NOT published (current_state.json,
                           override.json) - it must stay readable across a
                           promote, so it lives outside the swapped dirs

The rules, in the order they matter:

1. **Everything is built under `.staging/`.** `build_fn(models_dir,
   scripts_dir)` only ever sees staging paths. If it raises, the exception
   propagates and live + Previous are untouched - the dome keeps playing the
   last good run.
2. **Staging is keyed by the run stamp** (`.staging/.staging_stamp`). A
   same-stamp restart keeps the partial tree - the frames a dead event build
   already wrote are still there to inspect, and a build_fn that wants to skip
   finished work can look for them; any other stamp's leftovers are wiped on
   entry, so an abandoned build never contaminates the next one. (Build A's
   own event render does not yet skip finished frames: the file writes are
   the cheap half, and the T96 trace it would still have to redo is the
   expensive one.)
3. **Rotation is conditional, not every run.** `Models -> ModelsPrevious`
   fires only when the live `.run_stamp` differs from this run's stamp. A
   same-stamp re-promote overwrites live in place and KEEPS the existing
   rollback - re-running the same cycle must not burn the one real generation
   of history.
4. **Promotion is `os.replace` per directory** (an atomic rename inside one
   filesystem - staging is a sibling of live by construction).
5. **`.run_stamp` is written last**, into the live `Models/`, after both
   directories are in place. A crash anywhere before that leaves no stamp (or
   the old one), which is exactly the state that forces a rotation next run.

Known limit, stated rather than papered over: `Models` and `Scripts` are two
separate renames, so there is a sub-millisecond window in which the dome could
see a new `Models` beside an old `Scripts`. Making that atomic needs a
symlinked run-root, which the Digistar content share does not support. The
window is closed in practice by the stable-filename contract (see dsexport/ds
`write_live_loader_notes`): an old player addressing the new Models finds the
same filenames it always did.

This module also writes `EarthFrameCheck.vla` into every published `Models/`
(tiny, constant, epoch-independent) so a coordinate-frame question on the dome
is always one file load away from an answer.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

STAGING_DIRNAME = ".staging"
MODELS_DIRNAME = "Models"
SCRIPTS_DIRNAME = "Scripts"
MODELS_PREV_DIRNAME = "ModelsPrevious"
SCRIPTS_PREV_DIRNAME = "ScriptsPrevious"
STATE_DIRNAME = "state"                  # daemon-owned, never published
RUN_STAMP_NAME = ".run_stamp"
STAGING_STAMP_NAME = ".staging_stamp"
DS_MODEL_REL_PREFIX = "..\\Models\\"
DS_MODEL_PREV_PREFIX = "..\\" + MODELS_PREV_DIRNAME + "\\"

FRAME_CHECK_NAME = "EarthFrameCheck.vla"

# Huntsville AL - the dome's own site, the one point on the globe every
# operator can check by eye.
HUNTSVILLE_LAT_DEG = 34.7304
HUNTSVILLE_LON_DEG = -86.5861
HUNTSVILLE_H_M = 200.0
MARKER_ARM_M = 40_000.0        # +-40 km cross arms: visible from orbit distance


# --- primitives ---------------------------------------------------------------

def write_into(path, bytes_or_text) -> Path:
    """Atomically write bytes or text to `path` via a sibling .tmp + os.replace.

    The one write primitive this layer uses: a reader (Digistar, the JS loader,
    another process) either sees the whole previous file or the whole new one,
    never a half-written body. Text is encoded ASCII with LF endings - the
    house convention for every .ds/.vla/.vlc this project emits.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (bytes_or_text if isinstance(bytes_or_text, (bytes, bytearray))
            else str(bytes_or_text).encode("ascii"))
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return path


def read_stamp(root, dirname: str = MODELS_DIRNAME) -> str | None:
    """The `.run_stamp` of a live run directory, or None when it has none."""
    try:
        return (Path(root) / dirname / RUN_STAMP_NAME).read_text(
            encoding="ascii").strip()
    except OSError:
        return None


def archive_run_dir(parent, current_name: str, previous_name: str) -> bool:
    """Rotate <parent>/<current> -> <parent>/<previous>, keeping 2 generations.

    Deletes any existing previous directory first, then renames. No-op (False)
    when the current directory does not exist yet - the first run.
    """
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    current, previous = parent / current_name, parent / previous_name
    if not current.exists():
        return False
    if previous.exists():
        shutil.rmtree(previous, ignore_errors=True)
    os.replace(current, previous)
    return True


def publish_run_dir(src, dst) -> None:
    """Swap a freshly built staging directory into its live location."""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    os.replace(src, dst)


def repoint_archived_ds(scripts_prev_dir) -> int:
    """Rewrite '..\\Models\\' -> '..\\ModelsPrevious\\' in the archived players.

    Model references inside a .ds are relative (`..\\Models\\<file>`), so once
    rotation has moved a player into ScriptsPrevious/ its paths resolve to the
    CURRENT run. For the live loop that happens to be harmless (filenames are
    stable by contract), but for an event frame series the archived player
    would address a different window's frames. Repointing makes rollback
    literally "run ScriptsPrevious\\<player>.ds" with no folder shuffling.
    """
    scripts_prev_dir = Path(scripts_prev_dir)
    n = 0
    try:
        for ds in sorted(scripts_prev_dir.glob("*.ds")):
            text = ds.read_text(encoding="ascii", errors="replace")
            if DS_MODEL_REL_PREFIX not in text:
                continue
            ds.write_text(text.replace(DS_MODEL_REL_PREFIX,
                                       DS_MODEL_PREV_PREFIX), encoding="ascii")
            n += 1
    except Exception as exc:                                # pragma: no cover
        log.warning("could not repoint archived .ds (%s); roll back by renaming "
                    "%s -> %s instead", exc, MODELS_PREV_DIRNAME, MODELS_DIRNAME)
    return n


def carry_forward_files(src_dir, dst_dir, skip=(RUN_STAMP_NAME,)) -> int:
    """Fill a staging dir's GAPS from the live dir (hard link, else copy).

    The live loop re-bakes expensive layers on their own cadence (wind every
    5 min, aurora only on a new OVATION observation) while publishing a whole
    new Models/ every render. Without this the un-rebaked layers would vanish
    from the dome on the next promote.

    Only names the build did not write are linked (existing files are skipped),
    and publish() calls this AFTER the build for exactly that reason - see the
    comment there. Nothing is ever modified through a link: every later write
    creates a new name and inode, so the archived generation keeps its bytes.
    """
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    if not src_dir.is_dir():
        return 0
    dst_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src_dir.iterdir()):
        if not p.is_file() or p.name in skip:
            continue
        dst = dst_dir / p.name
        if dst.exists():
            continue
        try:
            os.link(p, dst)
        except OSError:
            shutil.copy2(p, dst)
        n += 1
    return n


# --- the frame check ------------------------------------------------------------

def write_earth_frame_check(models_dir) -> Path:
    r"""Write EarthFrameCheck.vla: equator + Greenwich meridian + Huntsville.

    Constant and epoch-independent (ECEF is Earth-fixed), so it is written by
    every publish and costs nothing. It answers exactly one question, the one
    that silently ruins a magnetosphere show: is this holder in the Earth's
    rotating frame, the right way round?

      * equator ring     - 1 deg steps on the WGS84 ellipsoid at h = 0
      * Greenwich merid. - lon 0, lat -90..90, 1 deg steps
      * Huntsville       - a 3-segment ECEF-axis cross whose intersection is
                           exactly geodetic (34.7304 N, 86.5861 W, h = 200 m)

    Load it beside the magnetosphere layers: if the ring lies on the equator
    and the cross sits on north Alabama, the holder's parent frame is right and
    every other ECEF-meter file in Models/ is placed correctly. If the ring is
    tilted or the cross is in the ocean, the holder is parented wrong (or not
    parented at all) - fix that before trusting any field line.
    """
    from magneto import geo            # local: keeps dsexport importable alone

    from .vla import fmt_coord, write_lines_vla

    models_dir = Path(models_dir)
    # ECEF metres -> light years, because 'set coordsys RIGHT' IS light years.
    # The frame check is the one file that must be right before anything else
    # is trusted, so it goes through the same unit constant as every layer.
    k = geo.M_TO_LY

    def ring(lons, lats, h=0.0, opacity=1.0):
        x, y, z = geo.geodetic_to_ecef(lons, lats, h)
        return [[float(a) * k, float(b) * k, float(c) * k, opacity]
                for a, b, c in zip(x, y, z)]

    lons = list(range(0, 361))
    equator = ring(lons, [0.0] * len(lons), 0.0, 1.0)
    lats = list(range(-90, 91))
    greenwich = ring([0.0] * len(lats), lats, 0.0, 0.7)

    hx, hy, hz = geo.geodetic_to_ecef(HUNTSVILLE_LON_DEG, HUNTSVILLE_LAT_DEG,
                                      HUNTSVILLE_H_M)
    cx, cy, cz = float(hx) * k, float(hy) * k, float(hz) * k
    a = MARKER_ARM_M * k
    cross = [
        [[cx - a, cy, cz, 1.0], [cx + a, cy, cz, 1.0]],
        [[cx, cy - a, cz, 1.0], [cx, cy + a, cz, 1.0]],
        [[cx, cy, cz - a, 1.0], [cx, cy, cz + a, 1.0]],
    ]

    comments = [
        "coordinate frame check - constant, epoch independent, ECEF frame",
        f"units LIGHT YEARS (1 R_E = {geo.RE_LY:.5e} ly) - coordsys RIGHT is ly",
        "holder: Earth add <holder> and do NOT set standardSpace - this file is "
        "the one that is supposed to ride the planet's rotation",
        f"equator ring {len(equator)} vertices at 1 deg, h 0 on WGS84; "
        f"Greenwich meridian {len(greenwich)} vertices lat -90..90 at lon 0",
        f"site marker: 3-segment ECEF-axis cross, arms +-{MARKER_ARM_M / 1000:g} km, "
        f"intersection at geodetic {HUNTSVILLE_LAT_DEG:g} N "
        f"{abs(HUNTSVILLE_LON_DEG):g} W h {HUNTSVILLE_H_M:g} m (Huntsville AL)",
        "if the ring is not on the equator or the cross is not on north Alabama, "
        "the holder is parented to the wrong frame - fix that before trusting "
        "any other file in this directory",
        "positions via magneto.geo.geodetic_to_ecef (WGS84 ellipsoid, never a sphere)",
    ]
    path = models_dir / FRAME_CHECK_NAME
    rows = write_lines_vla(path, [equator, greenwich] + cross,
                           intensity="EXPLICIT", comments=comments,
                           coord_fmt=fmt_coord)
    log.debug("frame check: %d vertex rows -> %s", rows, path)
    return path


# --- the publisher ----------------------------------------------------------------

def publish(root, build_fn, stamp, carry_forward: bool = False,
            repoint_previous: bool = True, frame_check: bool = True) -> dict:
    """Build under <root>/.staging and promote it atomically on success.

    build_fn(models_dir, scripts_dir) does the work and may return anything;
    whatever it returns comes back under "build". It must write ONLY inside the
    two directories it is handed.

    stamp is this run's identity - the live daemon uses the state's epoch
    minute, an event render uses the event id plus its window. Rotation fires
    only when the live `.run_stamp` differs from it (rule 3 in the module
    docstring), so re-publishing the same stamp is idempotent and preserves the
    rollback.

    Returns {root, stamp, rotated, carried, models, scripts, build, elapsed_s}.
    Raises whatever build_fn raises, with live + Previous untouched.
    """
    t0 = time.time()
    root = Path(root)
    staging = root / STAGING_DIRNAME
    st_models, st_scripts = staging / MODELS_DIRNAME, staging / SCRIPTS_DIRNAME
    staging_stamp = staging / STAGING_STAMP_NAME

    # Keyed staging: keep a same-stamp partial build (resume), wipe anything else.
    if staging.exists():
        try:
            prior = staging_stamp.read_text(encoding="ascii").strip()
        except OSError:
            prior = None
        if prior != stamp:
            log.info("dropping stale staging (stamp %r != %r)", prior, stamp)
            shutil.rmtree(staging, ignore_errors=True)
    st_models.mkdir(parents=True, exist_ok=True)
    st_scripts.mkdir(parents=True, exist_ok=True)
    write_into(staging_stamp, stamp)

    built = build_fn(st_models, st_scripts)      # may raise: staging survives
    if frame_check:
        write_earth_frame_check(st_models)

    # Gap-fill AFTER the build, never before: carried files may be hard links
    # to the live copies, and the .vla/.vlc writers open their target with "w"
    # (no tmp+replace). Filling gaps afterwards means a link is only ever made
    # for a name this build did NOT write, so nothing is mutated in place and
    # the rollback generation can never be edited out from under itself.
    carried = 0
    if carry_forward:
        carried = carry_forward_files(root / MODELS_DIRNAME, st_models)
        carried += carry_forward_files(root / SCRIPTS_DIRNAME, st_scripts)

    # ---- promote: nothing above here can have touched live or Previous ----
    live_models = root / MODELS_DIRNAME
    rotated = False
    if live_models.exists() and read_stamp(root) != stamp:
        rotated = archive_run_dir(root, MODELS_DIRNAME, MODELS_PREV_DIRNAME)
        archive_run_dir(root, SCRIPTS_DIRNAME, SCRIPTS_PREV_DIRNAME)
        if rotated and repoint_previous:
            repoint_archived_ds(root / SCRIPTS_PREV_DIRNAME)
    publish_run_dir(st_models, live_models)
    publish_run_dir(st_scripts, root / SCRIPTS_DIRNAME)
    write_into(live_models / RUN_STAMP_NAME, stamp)          # last, always
    shutil.rmtree(staging, ignore_errors=True)

    elapsed = time.time() - t0
    log.info("published %s stamp %s (%s, %d carried) in %.2f s", root, stamp,
             "rotated" if rotated else "in place", carried, elapsed)
    return {"root": root, "stamp": stamp, "rotated": rotated,
            "carried": carried, "models": live_models,
            "scripts": root / SCRIPTS_DIRNAME, "build": built,
            "elapsed_s": elapsed}


# --- the live feed: one directory per cycle ---------------------------------------

CYCLE_DIR_FMT = "c{0:06d}"
LIVE_KEEP_CYCLES = 3


def cycle_dirname(cycle) -> str:
    return CYCLE_DIR_FMT.format(int(cycle))


def list_cycle_dirs(models_dir) -> list[str]:
    """The c<NNNNNN> directories under a live Models/, oldest first."""
    models_dir = Path(models_dir)
    if not models_dir.is_dir():
        return []
    return sorted(p.name for p in models_dir.iterdir()
                  if p.is_dir() and re.fullmatch(r"c\d{6}", p.name))


def publish_cycle(root, build_fn, cycle, stamp=None, keep_cycles: int = LIVE_KEEP_CYCLES,
                  referenced=(), frame_check: bool = True) -> dict:
    """Build ONE live cycle into staging and promote it to <root>/Models/c<cycle>.

    The live feed's problem is not a torn tree (publish() solves that) but a
    dome that has already COOKED a filename: re-loading the same path may or
    may not re-read the bytes, and nothing in this house has verified it. So
    every cycle gets a directory of its own and the loader turns objects over
    to the new files - the question is never asked.

        <root>/Models/c000042/<Layer>.vlc ...   this cycle's renders
        <root>/Models/live_manifest.json        written by the CALLER after this
                                                returns (merged per-layer map)
        <root>/Scripts/                         live players/helpers, replaced
                                                file by file (atomic each)

    build_fn(models_dir, scripts_dir) builds into staging exactly as for publish().
    `referenced` names the cycle directories the caller's merged manifest still
    points at (un-rebaked layers live in older cycles); those are never pruned.
    Beyond them, only the newest `keep_cycles` directories survive.
    Returns {root, cycle, dir, models, scripts, build, pruned, elapsed_s}.
    """
    t0 = time.time()
    root = Path(root)
    models_root = root / MODELS_DIRNAME
    live_scripts = root / SCRIPTS_DIRNAME
    dirname = cycle_dirname(cycle)
    staging = root / STAGING_DIRNAME
    st_models, st_scripts = staging / dirname, staging / SCRIPTS_DIRNAME
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    st_models.mkdir(parents=True, exist_ok=True)
    st_scripts.mkdir(parents=True, exist_ok=True)

    built = build_fn(st_models, st_scripts)          # may raise: live untouched

    # ---- promote ----------------------------------------------------------
    models_root.mkdir(parents=True, exist_ok=True)
    live_scripts.mkdir(parents=True, exist_ok=True)
    target = models_root / dirname
    if target.exists():                               # same cycle re-run
        shutil.rmtree(target, ignore_errors=True)
    os.replace(st_models, target)
    for p in sorted(st_scripts.iterdir()):
        if p.is_file():
            write_into(live_scripts / p.name, p.read_bytes())
    if frame_check and not (models_root / FRAME_CHECK_NAME).exists():
        write_earth_frame_check(models_root)
    if stamp is not None:
        write_into(models_root / RUN_STAMP_NAME, str(stamp))
    shutil.rmtree(staging, ignore_errors=True)

    # ---- prune: keep what the manifest references + the newest N ----------
    keep = set(str(d) for d in referenced) | {dirname}
    dirs = list_cycle_dirs(models_root)
    keep |= set(dirs[-max(1, int(keep_cycles)):])
    pruned = []
    for d in dirs:
        if d not in keep:
            shutil.rmtree(models_root / d, ignore_errors=True)
            pruned.append(d)

    elapsed = time.time() - t0
    log.info("published live cycle %s -> %s (%d pruned) in %.2f s", cycle, target,
             len(pruned), elapsed)
    return {"root": root, "cycle": int(cycle), "dir": dirname, "models": target,
            "scripts": live_scripts, "build": built, "pruned": pruned,
            "elapsed_s": elapsed}
