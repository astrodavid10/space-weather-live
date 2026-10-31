r"""Digistar .ds writers for Build A: live status banner, event player, overrides.

Cloned from the VERIFIED players, not invented here - the same provenance rule
the Rubin sibling (rubin/ds_writer.py) follows, and where possible the same
helpers and constant names so the two builds read as one house style:

  the reference solar player sunspots-Bfield-daily.py (see CLAUDE.md)
      write_animation_ds()      -> the 0:00 PREP / +step ACTION structure, the
                                   emptyClass holder at intensity 0 faded up on
                                   the first visible frame, the first-frame
                                   prototype with every later frame
                                   'is <prototype>' + 'intensity inherited',
                                   'morph 100 duration <step>' on PARAMETRIC
                                   frames with 'morph 0' as the resting state,
                                   the '+0.1' pacing cue every 25 loads, and
                                   the one-shot 'system message' banner.
      write_animation_off_ds()  -> the teardown: fade the holder over 3 s,
                                   switch every step object off, wait 3.1 s,
                                   delete the holder.
  the reference solar player solarwind-daily.py (see CLAUDE.md)
      write_animation_ds()      -> the parcel-frame animation cadence and
                                   'morph 100 duration <step>' on parametric
                                   DOTS. NOTE: that script loads its DOTS .vlc
                                   through lineModelClass, and Build A does NOT
                                   copy that - see the dotModelClass note in
                                   _prototype_lines. A DOTS file in a
                                   lineModelClass cooks to zero geometry.
      write_series_ds()         -> the precedent for stepping SEVERAL layers
                                   together under one holder, which is exactly
                                   what a magnetosphere frame is (boundaries +
                                   field lines + aurora move as one).
      write_animation_ds()      -> ALSO the 'scene date' precedent: a PREP pin
                                   at the first frame's epoch and an ACTION
                                   tween to the last frame's epoch, BOTH
                                   emitted COMMENTED OUT because 'scene date'
                                   drives the GLOBAL dome clock, not just the
                                   one object that wants it. Build A clones
                                   that exactly (see write_event_player).

Deploy layout (house convention: Models\ is a sibling of Scripts\)::

    <ShowFolder>\
        Models\    Magnetopause.vla BowShock.vla FieldLines.vlc AuroraOval.vlc
                   AuroraCurtains.vlc WindParticles_NNNN.vlc L1Marker.vlc
                   EarthFrameCheck.vla live_manifest.json current_state.json
                   (event renders add the frame index: Magnetopause_0000.vla ...)
        Scripts\   magneto-status.ds  MagnetoLive.js  (loader: MagnetoLiveLoader.js)
                   magneto-event-<id>.ds / -off.ds
                   event-<id>-on.ds / event-<id>-off.ds / live-restore.ds

Every model reference is emitted FLAT as '..\Models\<file>'. The Rubin sibling
had to flag sub-directories inside a model path as NEEDS-DOME-VERIFY; Build A
sidesteps that entirely by keeping Models\ flat and putting the frame index in
the filename instead.

Token provenance
----------------
COPIED (verified on the live install by the solar builds, and re-used by the
Rubin sibling): emptyClass, lineModelClass, model, intensity <n>, intensity 0,
intensity inherited, 'intensity <n> duration <s>', 'morph 0',
'morph 100 duration <s>', 'is <prototype>', '<holder> add <child>', '<obj> off',
'<obj> delete', DIIMorph off, roundness 100, blend on, width 0.25 (dots) /
0.4 (lines), useWidth off, gap 1, color 100 100 100, system message "a|b|c",
the 0:00 / +N cue grammar with tab-indented commands, the +0.1 pacing cue every
25 loads, holder intensity 92, 0.5 s fade in, 3.0 s fade out, 3.1 s wait before
delete.

CONFIRMED AGAINST THE INSTALL (Digistar 2026.0.3.3 Users Guide / Class
Reference / Library scripts, plus the CmdEcho and cooked-asset logs of the
2026-08-12 Gannon run - see docs/HANDOFF_DOME.md):
    * '<Earth> add <obj>' is the Library's own idiom for an Earth-attached
      overlay (ControlPanel Eclipseopedia, FDC/CustomPlanets graticules), so
      the parent line is now EMITTED, not commented out. An unparented holder
      renders nothing at all, which is what the Gannon run actually did.
    * 'standardSpace on' is documented on both model classes as making an
      ancestor's attitude and scale not affect the object: Earth's position
      without Earth's rotation, which is exactly what a J2000 bake needs.
    * dotModelClass for 'filecontent DOTS', lineModelClass for LINES, and
      never each other's attributes (see _prototype_lines).
    * DIIMorph (not DIIStyleMorph) is the attribute name in the Class
      Reference, and 'off' selects the D3 equation - the one that reads V rows
      as absolute targets, which is what this build writes.
    * relative model paths ('..\Models\<file>') resolve correctly.
    * light years, not metres: see magneto/geo.py's units note.

NEEDS-DOME-VERIFY (no precedent in any verified script; each one is ALSO
emitted into the .ds itself with the same marker, so the operator finds it
without reading this file):
    1. the holder's ORIENTATION when the render used frame 'gsm'. GSM's +X
       must track the real Sun, which neither a plain Earth child (spins with
       the planet) nor standardSpace (WCS axes, nose at the vernal equinox)
       provides - something has to drive the holder's attitude per epoch.
       The 'j2000' frame exists precisely so this question does not arise, and
       it is the default; this stays a documented gap for the GSM path only.
    2. running one .ds from another. The presenter override scripts would like
       to say "now run the event player"; no verified script chains scripts, so
       that line is emitted COMMENTED OUT and the dome key is documented to
       call both scripts instead.
    3. re-reading a model file whose bytes changed on disk while the object
       still exists (the live-holder contract). The solar players only ever
       load a file once. This is the single most important thing for the live
       loader author to verify, and write_live_loader_notes() is entirely about
       it - comments only, no executable tokens.
    4. 'scene date'. Cloned from the solar precedent and emitted COMMENTED OUT
       for the same reason: it moves the GLOBAL dome clock. Build A wants it
       for a different payoff - with the magnetosphere sun-fixed, tweening the
       scene date across the event window is what makes the ROTATING EARTH
       TEXTURE line up with the aurora sweeping underneath it.

Everything emitted is ASCII and contains NO semicolons (a hard parse error in
the .vla/.vlc grammar this project also writes; whether .ds tolerates them is
not demonstrated by any verified script, so the prose simply avoids them).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from pathlib import Path
from .mesh import MESH_FORMAT as _MESH_EXT

from livecore.state import data_age_str

log = logging.getLogger(__name__)

# --- tokens COPIED verbatim from the verified solar players -------------------
DS_HOLDER_CLASS = "emptyClass"
DS_LINE_CLASS = "lineModelClass"       # for 'set filecontent LINES' files
DS_DOT_CLASS = "dotModelClass"         # for 'set filecontent DOTS' files
DS_MODEL_CLASS = DS_LINE_CLASS         # back-compat alias for the LINES case
# phase 4 (2026-09-05): translucent boundary shells - meshes in effectModelClass with the
# angle-based-intensity effect (the E&S EarthMagShell look), dome-verified with an OBJ.
DS_MESH_CLASS = "effectModelClass"
# Shell LOOK presets (2026-09-14), modelled on Digistar's shipped EarthMagShell
# (color 5 10 30 / intensity 60 / abiThickness 99: a Fresnel rim over a near-black
# base). Brightness lives in the HOLDER colour: the frame objects leave 'color'
# INHERITED (dome-probed 2026-09-14) and under blendMode add colour magnitude IS
# brightness (holder color 0 0 0 rendered nothing). Holder INTENSITY is inert for
# the shells because the crossfade writes explicit slot intensities every step.
# abiThickness is the OPAQUE band from the edge inward (dome A/B 2026-09-14: 99 fills
# the surface, 94 leaves only a faint rim). The bow shock's tail cone is ~76 R_E
# wide, so most camera positions sit INSIDE it and see its wall everywhere - it
# gets a thinner band and a dimmer colour than the magnetopause in every preset.
DS_SHELL_PRESETS = {
    "P1": {"abi": {"MagnetopauseShell": 98, "BowShockShell": 95},
           "MagnetopauseShell": (12, 30, 36), "BowShockShell": (16, 11, 5),
           "note": "rim - fuller magnetopause band, shock a thin veil"},
    "P2": {"abi": {"MagnetopauseShell": 96, "BowShockShell": 94},
           "MagnetopauseShell": (14, 34, 40), "BowShockShell": (22, 15, 6),
           "note": "show (default) - glowing teardrop, shock a faint amber veil"},
    "P3": {"abi": {"MagnetopauseShell": 94, "BowShockShell": 92},
           "MagnetopauseShell": (10, 24, 28), "BowShockShell": (10, 7, 3),
           "note": "veil - both shells barely there, lines and wind carry the scene"},
}
DS_SHELL_PRESET = "P2"
# The plasma populations became effectModelClass surfaces on 2026-09-20 (David:
# "swapped to effect model class featuring glb files"). They are not boundary
# shells, so they get their own look rather than a shell preset entry:
#
#   colour  = each layer's own dot hue, scaled down to a holder colour. Under
#             blendMode "add" the holder COLOUR is the brightness (an explicit
#             child intensity would replace the holder's), so this is where the
#             dome-tuned weight from 2026-09-16 now lives - the ring current at
#             roughly a third of the plasmasphere, because at equal weight it
#             read as a solid orange disc wider than the field lines.
#   abi     = 98: nearly filled. A plasma population is a volume, not a rim; at
#             94 (the bow shock's value) both layers nearly vanished at 18 R_E.
DS_MESH_LOOK = {
    "Plasmasphere": {"color": (22, 25, 36), "abi": 98},   # dots were (0.62, 0.70, 1.00)
    "RingCurrent":  {"color": (34, 15, 8),  "abi": 98},   # dots were (1.00, 0.45, 0.25)
}
DS_SHELL_COLOR_DEFAULT = (14, 34, 40)
# 3-slot ping-pong crossfade (meshes cannot morph): slot k%3 fades up, (k-1)%3
# fades down, (k+1)%3 - idle since it faded out at cue k-1 - is re-pointed at
# file k+1, a whole step of preload. Re-pointing 'model' on a live
# effectModelClass object loads the new file in 0.02 s (dome-probed 2026-09-14).
# 'cut' keeps the old hard switch with the same three slots.
DS_SHELL_SLOTS = 3
DS_SHELL_SLOT_TAGS = ("A", "B", "C")
DS_SHELL_FADE_FRAC = 0.9
# "cut" (David 2026-09-16): the incoming slot goes to 100 and the outgoing to 0 on the
# same cue with no duration, so two shells are never lit at once. The "crossfade"
# alternative sums two additive rims and only looks right while the surface is nearly
# stationary between steps - during a storm it pulses once per step.
DS_SHELL_TRANSITION = "cut"
DS_EVENT_SHELL_SLOT_FMT = "MagnetoEvent{0}{1}"      # MagnetoEventMagnetopauseShellA
DS_SHELLS_JS_NAME = "MagnetoShells.js"
# The dome-visible READOUT (2026-09-14): a textClass object fixed on the dome, front and
# a little up, re-texted every field step from the manifest's "hud" strings (the honesty
# law - values, time, spacecraft, data age - on the DOME, not only in the operator UI).
# textClass tokens per the Class Reference: textScale, color, position, 'scene add X fixed'
# (fixed = does not move with the eye), text. NEEDS-DOME-VERIFY placement + legibility.
# Layout (David 2026-09-14): ONE line along the base of the dome, centred on the fisheye's
# bottom, as SIX textClass segments under one emptyClass so the items follow the dome's
# curvature. Each segment is centred in a fixed-width slot; slot widths are in CHARACTERS,
# converted with DS_HUD_DEG_PER_CHAR (dome-measured: a 40-character ruler at textScale 1,
# el 1.25 spanned ~48 deg of azimuth). textClass always faces the camera at a fixed size.
DS_HUD_EVENT = "MagnetoEventHUD"
DS_HUD_LIVE = "MagnetoLiveHUD"
DS_HUD_CLASS = "textClass"
DS_HUD_EL_DEG = 1.25
DS_HUD_DIST_M = 10.0
DS_HUD_SCALE = 1.0
DS_HUD_COLOR = (75, 82, 90)
DS_HUD_INTEN = 70
DS_HUD_SEGMENTS = 6
DS_HUD_DEG_PER_CHAR = 1.2
DS_HUD_GAP_DEG = 2.0
DS_HUD_SLOT_CHARS_EVENT = (14, 11, 10, 6, 19, 13)     # "OMNI - REPLAY"
DS_HUD_SLOT_CHARS_LIVE = (14, 11, 10, 6, 19, 25)      # "CACHED 1h05m old - SOLAR1"
# Dots (wind parcels, pulses, aurora) blend NORMAL so the far side is hidden by the Earth
# (David 2026-09-14: the additive oval showed through the planet). Dome-checked: intensity
# fades still dim normal-blended dots. Lines stay additive (glow).
DS_DOT_BLEND = "normal"
# Presenter talking points: the manifest's data-derived key_moments line first, then the
# registry's curated notes (magneto.events EventSpec.talk), one system message each, early
# in the playback so the presenter has them while the storm builds.
DS_TALK_FIRST_S = 10.0
DS_TALK_EVERY_S = 12.0
DS_FILE_ATTR = "model"
DS_EARTH = "Earth"                     # Library precedent: '<Earth> add <obj>'
DS_MODEL_REL_PREFIX = "..\\Models\\"
DS_HOLDER_INTEN = 92
# Show-look decision (David, 2026-08-12): the boundary surfaces are context,
# not the subject - they START dim and the presenter brings them up if wanted.
# Any layer not named here starts at DS_HOLDER_INTEN. Callers can override
# per render with write_event_player(..., intensities={"BowShock": 25}).
# The shell holders sit at 100: their slot objects carry explicit intensities
# (crossfade) so the holder value is inert - the shells' brightness is the
# holder COLOUR (holder_color / MagnetoShells.js setShells).
# The ring current and plasmasphere are DENSE populations close to the Earth,
# so at the show default (92) they swamp everything: at the Gannon shock the
# ring current read as a solid orange disc wider than the field lines and hid
# the plasmasphere inside it (dome-checked 2026-09-16). Tuned on the dome to a
# diffuse torus with the plasmasphere shell visible inside it.
DS_HOLDER_INTEN_BY_LAYER = {"MagnetopauseShell": 100, "BowShockShell": 100,
                            "RingCurrent": 30, "Plasmasphere": 55,
                            # The IMF bundle covers a lot of sky, so it has to sit
                            # behind the magnetosphere it is draping over, not in
                            # front of it (dome-checked on the live feed).
                            "IMFLines": 45}
# Show-look colour per asset type: shell holders get their preset colour from
# holder_color(); every other layer keeps its file colours.
DS_HOLDER_COLOR_BY_LAYER: dict = {}
DS_HOLDER_FADE_SEC = 0.5
DS_HOLDER_OUT_SEC = 3.0
DS_TEARDOWN_WAIT_SEC = 3.1
DS_LOADS_PER_PACE = 25
# Rolling window (phase 2/4): frames behind the active one are deleted this
# many cues later, and the timeline starts this long after the last PREP line.
# The lag is TIME-based as well as cue-based (2026-09-14): a delete that lands
# on a frame whose cooked model is still being allocated logs
# 'Array allocation failed ... OMERROR_OBJECT_NOT_FOUND' (seen on wind frame
# 0260 during a normal first play). The wind cues every 0.2 s, so 6 cues was
# only 1.2 s of margin; every layer now waits at least DS_DELETE_LAG_MIN_SEC.
DS_DELETE_LAG_MIN_CUES = 6
# 8 s, not 3 (dome 2026-09-14): the FIRST play of a fresh render COOKS every .vlc as
# it loads (~0.75 s of loader work per 0.8 s step, aurora steps more), so the load
# queue drifts seconds behind the cue clock and a 3 s lag still lost one wind frame
# (WindParticles0200, OMERROR_OBJECT_NOT_FOUND). 40 extra live wind objects is cheap.
DS_DELETE_LAG_MIN_SEC = 8.0
DS_DELETE_LAG = DS_DELETE_LAG_MIN_CUES          # back-compat name (the field layers' value)
DS_PREP_SETTLE_SEC = 1.5


def delete_lag(cue_s: float) -> int:
    """Cues to wait before deleting a hidden frame: max(6, ceil(3 s / cue))."""
    try:
        cue = float(cue_s)
    except (TypeError, ValueError):
        return DS_DELETE_LAG_MIN_CUES
    if cue <= 0:
        return DS_DELETE_LAG_MIN_CUES
    return max(DS_DELETE_LAG_MIN_CUES, int(math.ceil(DS_DELETE_LAG_MIN_SEC / cue)))
DS_PACE_SEC = 0.1
DS_DOT_WIDTH = 0.25                    # solarwind DOTS prototype
# Line width now that useWidth is ON (it was ignored before): the install's
# FDC reference arcs run width 1.7; 1.0 is a visible-but-not-chunky start for
# 200 overlapping field lines. Dome-tunable per object. NEEDS-DOME-VERIFY.
DS_LINE_WIDTH = 1.0
DS_DOT_SCALE = 1.0                     # dotModelClass dotScale (dome-tunable)
# phase 5: the field-line pulses are a few hundred beads that must read as beads,
# not as the 10k-parcel haze - bigger sprites for that one layer.
# The ring current and plasmasphere are diffuse populations, not
# individual particles: bigger, softer dots read better than a fine spray.
DS_DOT_SCALE_BY_LAYER = {"FieldPulses": 2.5, "RingCurrent": 2.0,
                         "Plasmasphere": 1.6}

# --- object names invented for this build (the naming PATTERN is copied) ------
DS_LIVE_HOLDER_FMT = "MagnetoLive{0}Holder"    # MagnetoLiveMagnetopauseHolder
DS_LIVE_OBJ_FMT = "MagnetoLive{0}"             # MagnetoLiveFieldLines, ...
DS_EVENT_HOLDER_FMT = "MagnetoEvent{0}Holder"  # MagnetoEventBowShockHolder
DS_EVENT_OBJ_FMT = "MagnetoEvent{0}{1:04d}"    # MagnetoEventFieldLines0003

# ONE HOLDER PER ASSET TYPE. Every layer - each boundary surface, the field
# lines, each half of the aurora, the L1 marker, the parcel stream - gets its
# own emptyClass holder, so the presenter can hide, dim or restyle any single
# asset type from the dome without touching the others and without knowing how
# many frame objects it has. The holders are siblings, each parented to the
# Earth by the same frame contract - no holder nests inside another, so nothing
# here assumes an unverified emptyClass-inside-emptyClass inheritance.
# Group operations (fade the whole overlay) are emitted as one line per holder.
# --- are the boundary shells DRAWN? -------------------------------------------
# The master switch for the two translucent boundary surfaces. It was False for a few
# hours on 2026-09-16 while their physics was questioned; back on the same day, because
# the review (plan Stream E) does not need them off the dome to happen.
#
# Keep it: turning the shells off across every stored package, the look-back and the live
# feed is one constant plus a re-emit (tools/retime_event.py --root <pkg> --step-seconds
# <the value it already has>), never a re-render, because the .obj files are always
# exported regardless.
#
# Whether a package starts with its shells VISIBLE is a different question and a different
# mechanism - see "shells_hidden" in write_event_player().
DS_DRAW_SHELLS = True


def drawn_layers(layers):
    """Drop the mesh (shell) rows while DS_DRAW_SHELLS is off.

    `layers` is the 4-tuple form used by both the manifest-driven event writers
    and the static LIVE_LAYERS table: (name, files_or_file, width, kind).
    """
    if DS_DRAW_SHELLS:
        return list(layers)
    return [row for row in layers if str(row[3]).lower() != "mesh"]


DS_EVENT_HOLDER_LAYERS = ("MagnetopauseShell", "BowShockShell", "FieldLines",
                          "AuroraOval", "WindParticles", "FieldPulses",
                          "RingCurrent", "Plasmasphere", "IMFLines")
# Manifests written before the per-asset split said 'field' or 'wind' here.
DS_LEGACY_HOLDER_KEYS = ("field", "wind")
DS_STATUS_NAME = "magneto-status.ds"
DS_EVENT_PLAYER_FMT = "magneto-event-{0}.ds"
DS_EVENT_TEARDOWN_FMT = "magneto-event-{0}-off.ds"
DS_NATIVE_AURORA_FMT = "magneto-native-aurora-{0}.ds"
DS_NATIVE_AURORA_OFF_FMT = "magneto-native-aurora-{0}-off.ds"

# --- Digistar's NATIVE planet aurora, driven from the same Kp series ----------
# On this install (2026.0.3.3) auroraClass is DEPRECATED - planet auroras are
# planetClass attributes on the Earth object (Class Reference). The Kp mapping
# below approximates the same equatorward expansion zhang_paxton draws:
# equatorward edge ~ 66 - 2*Kp deg magnetic latitude, band width growing with
# activity. All three numbers are dome-tunable aesthetics, not physics.
DS_AURORA_INTEN_BASE = 25.0      # auroraIntensity percent at Kp 0
DS_AURORA_INTEN_PER_KP = 8.0     # + per Kp step (capped at 100)
DS_AURORA_EDGE_DEG = 66.0        # equatorward edge at Kp 0 (deg mag lat)
DS_AURORA_EDGE_PER_KP = 2.0      # equatorward motion per Kp step
DS_AURORA_WIDTH_DEG = 4.0        # band width at Kp 0
DS_AURORA_WIDTH_PER_KP = 0.8     # widening per Kp step
DS_LIVE_MANIFEST_NAME = "live_manifest.json"

VERIFY = "# NEEDS-DOME-VERIFY"

# Layers a live cycle publishes, in load order, with the .vlc/.vla file that
# carries each and the prototype width its precedent uses. The live loader and
# the event player both walk this table, so adding a layer is a one-line change.
LIVE_LAYERS = (
    ("MagnetopauseShell", f"MagnetopauseShell.{_MESH_EXT}", 0.0, "mesh"),
    ("BowShockShell", f"BowShockShell.{_MESH_EXT}", 0.0, "mesh"),
    ("FieldLines", "FieldLines.vlc", DS_LINE_WIDTH, "lines"),
    ("FieldPulses", "FieldPulses.vlc", DS_DOT_WIDTH, "dots"),
    ("AuroraOval", "AuroraOval.vlc", DS_DOT_WIDTH, "dots"),
    ("WindParticles", "WindParticles_0000.vlc", DS_DOT_WIDTH, "dots"),
    ("RingCurrent", f"RingCurrent.{_MESH_EXT}", 0.0, "mesh"),
    ("Plasmasphere", f"Plasmasphere.{_MESH_EXT}", 0.0, "mesh"),
    ("IMFLines", "IMFLines.vlc", DS_LINE_WIDTH, "lines"),
)


# --- small shared helpers (same shapes as rubin/ds_writer.py) -----------------

# Digistar's .dsz packager puts every packaged model in its own folder named after the file -
# Models\<name>\<name>.<ext> - the shape of every packaged house package on this box (BlackHoles,
# JWST, OPiS, and the Library's own Galaxy\Blackhole_Shell\Blackhole_Shell.X). A render that
# SHIPS inside a package is therefore laid out that way at source, so the round trip is a no-op
# rather than a rewrite we would have to predict.
#
# DEFAULT FLIPPED TO TRUE, 2026-09-20 (David: "make sure that the file structure is the same
# for each one"). It used to be False on the reasoning that the look-back and the live feed are
# rebuilt on the target rather than shipped, so they gained nothing from the extra directory.
# That reasoning died when the look-back started shipping inside the same .dsz as the storms:
# the package then carried two layouts, and - worse - a look-back REBUILD silently reverted the
# layout to flat, so "they all match" was true only until the next rebuild. Uniform by default
# is the only version of that promise a rebuild cannot quietly undo.
#
# The live feed is the deliberate exception and is not governed by this flag: its
# Models\c000042\<Layer>.<ext> per-CYCLE directories are the turnover mechanism, not a layout
# choice (see publish_cycle).
DS_MODEL_CONVENTION_DIRS = True


def _model_ref(name, prefix: str = DS_MODEL_REL_PREFIX,
               convention_dirs: bool | None = None) -> str:
    r"""'FieldLines.vlc' -> '..\Models\FieldLines.vlc'.

    With the packaging convention on, the same name becomes
    '..\Models\FieldLines\FieldLines.vlc' - its own folder, named after the file.
    """
    n = str(name).replace("/", "\\").lstrip("\\")
    use = DS_MODEL_CONVENTION_DIRS if convention_dirs is None else convention_dirs
    if use:
        return f"{prefix}{n.rsplit('.', 1)[0]}\\{n}"
    return prefix + n


def _obj_token(name) -> str:
    """'Aurora Oval' -> 'AuroraOval'. Object names are ASCII alphanumerics."""
    return "".join(ch for ch in str(name) if ch.isalnum())


def event_holder(layer_name) -> str:
    """Holder object for ONE asset type: 'BowShock' -> MagnetoEventBowShockHolder.

    The layer's own name is the key, so adding a layer adds its holder with no
    other edit. Legacy manifests that say 'field'/'wind' are resolved by the
    caller against the layer name before they reach here.
    """
    return DS_EVENT_HOLDER_FMT.format(_obj_token(layer_name))


def live_holder(layer_name) -> str:
    """Live-side counterpart of event_holder() - same one-per-asset-type rule."""
    return DS_LIVE_HOLDER_FMT.format(_obj_token(layer_name))


def holder_intensity(layer_key, overrides=None) -> float:
    """Starting intensity for one asset-type holder.

    Resolution order: per-render override (CLI --holder-intensity), then the
    show-look table DS_HOLDER_INTEN_BY_LAYER, then the global default. The key
    is the LAYER name ('BowShock'), the same key event_holder()/live_holder()
    take - legacy 'field'/'wind' values never reach here (_resolve_holder).
    """
    key = _obj_token(layer_key)
    if overrides and key in overrides:
        return float(overrides[key])
    return float(DS_HOLDER_INTEN_BY_LAYER.get(key, DS_HOLDER_INTEN))


def _resolve_holder(layer_name, manifest_value=None) -> str:
    """Resolve a manifest 'holder' field to an asset-type key.

    A manifest written before the per-asset split carries 'field' or 'wind';
    both mean "this layer's own holder" now, so the layer name wins.
    """
    key = str(manifest_value or "").strip()
    if not key or key in DS_LEGACY_HOLDER_KEYS:
        return str(layer_name)
    return key


def _write(out_ds, lines) -> Path:
    out_ds = Path(out_ds)
    out_ds.parent.mkdir(parents=True, exist_ok=True)
    with open(out_ds, "w", encoding="ascii", newline="\n") as fh:
        fh.writelines(lines)
    return out_ds


# 'js play <file> | fn(<params>)': the host caps <params> at 259 characters and DROPS the
# line (listing: 'JavaScript parameters length exceeds maximum allowed of 259 characters',
# dome 2026-09-14). Every generated call that takes a name list goes through js_name_calls.
DS_JS_PARAM_MAX = 230


def js_name_calls(js_ref: str, fn: str, names, *extra) -> list[str]:
    """One or more `\tjs play <js_ref> | fn('a,b,c'[, extra...])\n` lines, each under the cap."""
    tail = "".join(f", {x}" for x in extra)
    out, chunk = [], []

    def flush():
        if chunk:
            out.append(f"\tjs play {js_ref} | {fn}('{','.join(chunk)}'{tail})\n")
            chunk.clear()

    for n in names:
        n = str(n)
        if chunk and len(",".join(chunk + [n])) + len(tail) + 4 > DS_JS_PARAM_MAX:
            flush()
        chunk.append(n)
    flush()
    return out


def _ascii(text) -> str:
    """Banner-safe text: ASCII, no semicolons, no double quotes, single line."""
    s = " ".join(str(text).split())
    s = s.replace('"', "'").replace(";", ",")
    return s.encode("ascii", "replace").decode("ascii")


def _holder_parent_lines(holder: str, parent=None, frame: str = "j2000",
                         brief: bool = False) -> list[str]:
    r"""Parent the holder to the Earth, per the export frame.

    All three frames are Earth-CENTRED, so the holder always takes the Earth's
    position - '<Earth> add <holder>', the same shape as the solar players'
    '<Sun> add <holder>' and of the Library's own 'Earth add <overlay>'
    (Eclipseopedia, FDC/CustomPlanets graticules).

    What differs is whether the holder inherits the Earth's ROTATION:

      j2000  NO. The models are baked in geocentric equatorial J2000, which is
             the Digistar WCS triad, so 'standardSpace on' - documented as
             making an ancestor's attitude and scale not affect the object - is
             exactly right, and no attitude is needed at all.
      ecef   YES. Earth-fixed geometry is supposed to ride the planet. Plain
             'add', no standardSpace. (EarthFrameCheck.vla lives here.)
      gsm    +X must stay on the real Sun, which neither of the above gives:
             a WCS-aligned holder aims the nose at the vernal equinox. Render
             in the j2000 frame instead unless something else is driving the
             holder's attitude - this branch stays a documented gap.

    `brief` is for a second holder in the same script - the full note is
    already a dozen lines up.
    """
    p = parent or DS_EARTH
    frame = str(frame).lower()
    if frame == "gsm":
        if brief:
            return [
                f"\t{VERIFY}  same orientation gap as the holder above.\n",
                f"\t{p} add {holder}\n",
            ]
        return [
            f"\t{p} add {holder}\n",
            f"\t{VERIFY}  ORIENTATION. This render is in the GSM frame: +X of\n",
            "\t# every model points at the Sun. Parented to the Earth it will\n",
            "\t# either spin with the planet or (with standardSpace on) sit in\n",
            "\t# WCS axes and aim the magnetopause nose at the vernal equinox.\n",
            "\t# Neither is right. Something must drive this holder's attitude\n",
            "\t# per epoch - or re-render with --frame j2000, which needs no\n",
            "\t# attitude at all. Sanity check either way: the nose must sit\n",
            "\t# between the Earth and the Sun for the whole event.\n",
        ]
    if frame == "ecef":
        if brief:
            return [f"\t{p} add {holder}\n"]
        return [
            f"\t{p} add {holder}\n",
            "\t# ECEF render: Earth-FIXED, so this holder is a plain child of\n",
            "\t# the Earth and SHOULD ride its rotation - do not set\n",
            "\t# standardSpace here. Load EarthFrameCheck.vla to confirm: if\n",
            "\t# its equator ring lies on the equator and its cross sits on\n",
            "\t# north Alabama, the frame is right.\n",
        ]
    # j2000 - the default
    if brief:
        return [
            f"\t{p} add {holder}\n",
            f"\t{holder} standardSpace on\n",
        ]
    return [
        f"\t{p} add {holder}\n",
        f"\t{holder} standardSpace on\n",
        "\t# The models are baked in geocentric equatorial J2000 - the same\n",
        "\t# axes as the Digistar WCS - so the holder needs the Earth's\n",
        "\t# POSITION but not its ROTATION. That is what standardSpace does:\n",
        "\t# an ancestor's attitude and scale no longer affect this object, so\n",
        "\t# no attitude of our own is needed and the structure stays put\n",
        "\t# while the planet turns underneath it.\n",
        "\t# Set standardSpace when the object is FIRST placed in the graph\n",
        "\t# (as here) - toggling it later moves the object.\n",
        "\t# Sanity check on the dome: the magnetopause nose sits between the\n",
        "\t# Earth and the Sun and stays there for the whole event, and the\n",
        "\t# aurora sweeps under it instead of dragging it around.\n",
    ]


def _scene_datetime(ts_iso) -> str:
    """ISO-8601 Z epoch -> a Digistar 'scene date' value, 'YYYY-MM-DD HH:MM:SS'.

    Single formatting point for the PREP pin and the ACTION tween, exactly as
    _ds_scene_datetime() is in sunspots-Bfield-daily.py.
    """
    s = str(ts_iso).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:                                     # pragma: no cover
        return str(ts_iso)


def _prototype_lines(obj: str, width: float, color: bool = False,
                     kind: str = "lines", layer: str | None = None,
                     preset: str | None = None) -> list[str]:
    """The prototype's model style block, per FILE KIND.

    THE CLASS MUST MATCH 'set filecontent'. Digistar's lineModelClass asks its
    loader for line topology and dotModelClass asks for dots, and the mismatch
    is silent: a DOTS .vlc loaded into a lineModelClass cooks to ZERO geometry
    (verified on the install - the cooked-asset log for a 10,000-parcel
    WindParticles frame was 56 bytes with no geometry block, while the LINES
    layers beside it cooked 1,768-46,800 vertices). So dots go to dotModelClass,
    lines to lineModelClass, and neither borrows the other's attributes:
    width/useWidth/gap/roundness/blend exist only on lineModelClass, and
    dotScale/autoScale only on dotModelClass.

    color=True adds 'color 100 100 100' - a neutral white multiplier over the
    file's own per-vertex colours. The sunspots LINES prototype sets it and the
    solarwind DOTS prototype does not, so each kind follows its own precedent
    rather than averaging the two.
    """
    # depthCue defaults ON for BOTH model classes (Class Reference) and acts
    # whenever the SCENE has depth-cueing enabled - the .vla header's own
    # 'set depthcue 0' does not govern the class attribute. These overlays are
    # physically ~4e-8 ly across, so scene depth-cueing tuned for scene-scale
    # content dims them to nothing; switch it off explicitly on every
    # prototype (found on the dome, 2026-08-13: field lines invisible except
    # up close).
    if str(kind).lower() == "mesh":
        # effectModelClass has no morph/DIIMorph/depthCue. NO 'color' line: the
        # object inherits its holder's colour, and that colour is the shell's
        # brightness (see DS_SHELL_PRESETS). Slot intensities are written by
        # the crossfade cues, never inherited.
        abi = shell_abi(str(layer or ""), preset)
        return [
            f"\t{obj} is {DS_MESH_CLASS}\n",
            f"\t{obj} effectMode \"angleBasedIntensity\"\n",
            f"\t{obj} abiMode \"fadeCenter\"\n",
            f"\t{obj} abiThickness {abi:g}\n",
            f"\t{obj} blendMode \"add\"\n",
            f"\t{obj} cullMode \"none\"\n",
            f"\t{obj} tessellation 0\n",
            # OBJ vertices are read as METRES (dome-measured 2026-09-05: a
            # light-year file rendered nanometre-sized); the file is written in
            # light years like the .vla layers, so one model unit = 1 ly.
            f"\t{obj} scale 1 ly\n",
        ]
    if str(kind).lower() == "dots":
        out = [
            f"\t{obj} is {DS_DOT_CLASS}\n",
            f"\t{obj} DIIMorph off\n",
            f"\t{obj} morph 0\n",
            f"\t{obj} depthCue off\n",
            f"\t{obj} blendMode \"{DS_DOT_BLEND}\"\n",
            f"\t{obj} dotScale {DS_DOT_SCALE_BY_LAYER.get(str(layer or ''), DS_DOT_SCALE):g}\n",
        ]
        if color:
            out.append(f"\t{obj} color 100 100 100\n")
        return out
    # useWidth ON, against the solar-player precedent (it used off): with
    # useWidth off the Class Reference says lines draw as 1-px hardware lines,
    # width is IGNORED and there is no antialiasing - a hairline reads fine on
    # a dome-filling Sun but disappears on a magnetosphere seen from outside.
    # The install's own always-visible reference arcs (FDC Hour Angle scripts)
    # run useWidth on / width 1.7; width here is dome-tunable per holder.
    out = [
        f"\t{obj} is {DS_LINE_CLASS}\n",
        f"\t{obj} DIIMorph off\n",
        f"\t{obj} morph 0\n",
        f"\t{obj} depthCue off\n",
        f"\t{obj} roundness 100\n",
        f"\t{obj} blend on\n",
        f"\t{obj} width {width:g}\n",
        f"\t{obj} useWidth on\n",
        f"\t{obj} gap 1\n",
    ]
    if color:
        out.append(f"\t{obj} color 100 100 100\n")
    return out


# --- the status banner ----------------------------------------------------------

def status_text(state: dict, now=None, note: str | None = None) -> str:
    """The dome readout string for one state.

    'Solar wind: SOLAR1 | as of 17:32 UT (1m ago) | Kp 3.67 | LIVE'

    Age comes from livecore.state.data_age_str, so a stalling feed reads
    honestly at any staleness. EVENT tier appends the event title; `note`
    appends the daemon's own qualifier (e.g. 'EVENT ARMED' past 3 h of cache).

    When no `now` is given, an EVENT frame is read against its OWN epoch rather
    than the wall clock - the same rule magneto.events.state_age_readout
    already applies, and for the same reason: a 2024 replay measured against
    today's clock would read '17000h ago', which is true and useless. A
    step-held stretch of an event then reads exactly as a stalling live feed
    would, which is the entire point of keeping age_s honest through playback.
    """
    if now is None and state.get("event_id"):
        now = datetime.fromisoformat(state["epoch"].replace("Z", "+00:00"))
    fields = [
        f"Solar wind: {state['source']}",
        data_age_str(state["age_s"], now=now),
        f"Kp {float(state['kp']['value']):g}",
        str(state["tier"]),
    ]
    if state.get("event_id"):
        fields.append(event_title(state["event_id"]) or str(state["event_id"]))
    if note:
        fields.append(str(note))
    return " | ".join(_ascii(f) for f in fields)


def event_title(event_id: str) -> str | None:
    """Registry title for an event id, or None when it is not registered."""
    try:
        from magneto.events import EVENTS
        spec = EVENTS.get(event_id)
        if spec is None:
            return None
        # look-back ids are registered as plain strings - never str.title
        title = spec.get("title") if isinstance(spec, dict) else getattr(spec, "title", None)
        return title if isinstance(title, str) else None
    except Exception:                                      # pragma: no cover
        return None


def write_status_ds(state: dict, path, now=None, note: str | None = None) -> Path:
    """Write the one-line status banner .ds for the current state.

    Deliberately the thinnest script in the build: one 0:00 cue, one verified
    'system message' token, no objects and no state of its own. The daemon
    rewrites it EVERY cycle - including cycles where nothing was re-rendered -
    so the age on the dome keeps ticking even when the geometry does not move.
    Re-running it is always safe.

    Digistar renders '|' inside a system message as a line break, so the
    fields land as separate lines of one banner.
    """
    text = status_text(state, now=now, note=note)
    L = [
        "# magneto-status.ds  -  generated by dsexport/ds.py\n",
        "# The live solar-wind readout. Rewritten every daemon cycle (even when\n",
        "# no geometry was re-rendered) so the age keeps climbing honestly.\n",
        f"# epoch {state['epoch']}  generated {state['generated']}  "
        f"tier {state['tier']}  age {int(state['age_s'])} s\n",
        "# '|' is a line break inside a system message (verified in the solar "
        "players).\n",
        "\n",
        "0:00\n",
        f'\tsystem message "{text}"\n',
    ]
    return _write(path, L)


# --- the shells: presets, slots, the panel knob ---------------------------------

def shell_look(name=None) -> dict:
    """The shell look preset (colour per shell + abiThickness); None -> DS_SHELL_PRESET."""
    key = str(name or DS_SHELL_PRESET)
    if key not in DS_SHELL_PRESETS:
        raise ValueError(f"unknown shell preset {key!r}; known: {sorted(DS_SHELL_PRESETS)}")
    return dict(DS_SHELL_PRESETS[key], name=key)


def shell_abi(layer_name, preset=None) -> float:
    """abiThickness for one shell layer under a preset (int or per-layer dict)."""
    key = _obj_token(layer_name)
    if key in DS_MESH_LOOK:
        return float(DS_MESH_LOOK[key]["abi"])
    abi = shell_look(preset)["abi"]
    if isinstance(abi, dict):
        return float(abi.get(key, next(iter(abi.values()))))
    return float(abi)


def holder_color(layer_key, preset=None):
    """Holder colour for one asset type, or None.

    Shell holders carry the preset colour - their slot objects leave 'color'
    INHERITED, so under additive blending this IS the shell's brightness (the
    panel's MagnetoShells.js scales it). Other layers keep their file colours.
    """
    key = _obj_token(layer_key)
    if key in ("MagnetopauseShell", "BowShockShell"):
        return tuple(shell_look(preset).get(key, DS_SHELL_COLOR_DEFAULT))
    if key in DS_MESH_LOOK:
        return tuple(DS_MESH_LOOK[key]["color"])
    return DS_HOLDER_COLOR_BY_LAYER.get(key)


def shell_slot(layer_name, i: int) -> str:
    """'MagnetopauseShell', 0 -> MagnetoEventMagnetopauseShellA."""
    return DS_EVENT_SHELL_SLOT_FMT.format(_obj_token(layer_name),
                                          DS_SHELL_SLOT_TAGS[i % DS_SHELL_SLOTS])


def hud_slot_azimuths(slot_chars=DS_HUD_SLOT_CHARS_EVENT, deg_per_char: float = DS_HUD_DEG_PER_CHAR,
                      gap_deg: float = DS_HUD_GAP_DEG) -> list[float]:
    """Slot centres (deg of azimuth, symmetric about the front) for fixed-width segments."""
    widths = [float(c) * deg_per_char for c in slot_chars]
    total = sum(widths) + gap_deg * (len(widths) - 1)
    x, out = -total / 2.0, []
    for w in widths:
        out.append(round(x + w / 2.0, 2))
        x += w + gap_deg
    return out


def hud_object_names(name: str) -> list[str]:
    """The readout's objects: the emptyClass parent + its segment texts (delete children first)."""
    return [f"{name}{i}" for i in range(DS_HUD_SEGMENTS)] + [name]


def hud_split(text, slot_chars=None) -> list[str]:
    """A manifest hud string -> exactly DS_HUD_SEGMENTS cleaned segments.

    The segments sit in FIXED-WIDTH slots (hud_slot_azimuths): a segment longer
    than its slot runs into its neighbour and the readout reads as overlapping
    text. Clamping here is the only guard, because the slot centres are baked
    into the objects' positions at PREP and never move.
    """
    segs = [_ascii(s) for s in str(text).split("|")]
    segs = segs[:DS_HUD_SEGMENTS] + [""] * (DS_HUD_SEGMENTS - len(segs))
    if slot_chars:
        segs = [s if len(s) <= n else s[: max(1, n - 1)].rstrip() + "."
                for s, n in zip(segs, slot_chars)]
    return segs


def _hud_prep_lines(name: str, text: str, slot_chars=DS_HUD_SLOT_CHARS_EVENT) -> list[str]:
    """PREP for the dome readout: an emptyClass fixed on the dome + six centred textClass
    segments along its base (el DS_HUD_EL_DEG), one per readout item."""
    segs = hud_split(text, slot_chars)
    out = [
        f"\t# Dome readout: values, time, spacecraft and data age ON the dome (honesty law),\n",
        f"\t# six centred segments along the base of the dome under one parent.\n",
        f"\t{name} is emptyClass\n",
        f"\t{name} intensity 0\n",
        f"\tscene add {name} fixed\n",
    ]
    for i, az in enumerate(hud_slot_azimuths(slot_chars)):
        c = f"{name}{i}"
        out += [
            f"\t{c} is {DS_HUD_CLASS}\n",
            f"\t{c} resolution \"high\"\n",
            f"\t{c} origin \"center\"\n",
            f"\t{c} alignment \"center\"\n",
            f"\t{c} textScale {DS_HUD_SCALE:g} {DS_HUD_SCALE:g}\n",
            f"\t{c} color {DS_HUD_COLOR[0]:g} {DS_HUD_COLOR[1]:g} {DS_HUD_COLOR[2]:g}\n",
            f"\t{c} position spherical {az:g} {DS_HUD_EL_DEG:g} {DS_HUD_DIST_M:g}\n",
            f"\t{c} intensity inherited\n",
            f"\t{name} add {c}\n",
            f"\t{c} text \"{segs[i]}\"\n",
        ]
    return out


def _shell_prep_lines(name, files, holder: str, model_prefix: str,
                      preset=None) -> list[str]:
    """PREP for one shell layer: DS_SHELL_SLOTS fixed slot objects under its holder.

    Slot A is the prototype (full effectModelClass style, file 0), B a clone
    preloaded with file 1, C an empty clone. The ACTION cues only ever fade
    slots and re-point 'model', so the object count is fixed at three: the
    panel can address the slots by name and no rolling window is needed.
    """
    a = shell_slot(name, 0)
    out = _prototype_lines(a, 0.0, kind="mesh", layer=name, preset=preset)
    out += [f"\t{a} {DS_FILE_ATTR} {_model_ref(files[0], model_prefix)}\n",
            f"\t{a} intensity 0\n", f"\t{holder} add {a}\n"]
    for i in range(1, DS_SHELL_SLOTS):
        s = shell_slot(name, i)
        out.append(f"\t{s} is {a}\n")
        if i == 1 and len(files) > 1:
            out.append(f"\t{s} {DS_FILE_ATTR} {_model_ref(files[1], model_prefix)}\n")
        out += [f"\t{s} intensity 0\n", f"\t{holder} add {s}\n"]
    return out


def _shell_lib_prep_lines(name, sh: dict, holder: str, model_prefix: str,
                          preset=None) -> list[str]:
    """PREP for a LIBRARY shell layer: two slots, slot A on the first shape at the first
    step's scale, slot B an idle clone (the player scales A every step and only swaps
    to B when the shape bin changes - magneto.model.shell_library)."""
    files, bins, scales = sh["files"], sh["bin"], sh["scale"]
    a, b = shell_slot(name, 0), shell_slot(name, 1)
    out = ["\t# Shell LIBRARY: reference shape(s) scaled per step - smooth, no per-step OBJs.\n"]
    out += _prototype_lines(a, 0.0, kind="mesh", layer=name, preset=preset)
    out += [f"\t{a} {DS_FILE_ATTR} {_model_ref(files[int(bins[0])], model_prefix)}\n",
            f"\t{a} scale {float(scales[0]):.6g} ly\n",
            f"\t{a} intensity 100\n", f"\t{holder} add {a}\n",
            f"\t{b} is {a}\n", f"\t{b} intensity 0\n", f"\t{holder} add {b}\n"]
    return out


def _shell_lib_step_lines(name, sh: dict, k: int, times, model_prefix: str,
                          slot_state: dict) -> list[str]:
    """ACTION cue k for a LIBRARY shell: tween the active slot's scale to step k+1's
    standoff over the hold; when the shape bin changes at k+1, point the idle slot at
    the new shape (snapped to the current scale) and crossfade, then it is the active one."""
    bins, scales, files = sh["bin"], sh["scale"], sh["files"]
    n = len(scales)
    if k + 1 >= n:
        return []
    cue = float(times[k + 1] - times[k])
    active = int(slot_state.get("active", 0))
    A, I = shell_slot(name, active), shell_slot(name, 1 - active)
    target = f"scale {float(scales[k + 1]):.6g} ly duration {_fmt_t(cue)}"
    if int(bins[k + 1]) != int(bins[k]):
        fade = _fmt_t(DS_SHELL_FADE_FRAC * cue)
        slot_state["active"] = 1 - active
        return [f"\t{I} {DS_FILE_ATTR} {_model_ref(files[int(bins[k + 1])], model_prefix)}\n",
                f"\t{I} scale {float(scales[k]):.6g} ly\n",
                f"\t{I} intensity 100 duration {fade}\n",
                f"\t{A} intensity 0 duration {fade}\n",
                f"\t{I} {target}\n", f"\t{A} {target}\n"]
    return [f"\t{A} {target}\n"]


def _shell_step_lines(name, files, k: int, times, step: float,
                      model_prefix: str) -> list[str]:
    """ACTION cue k for one shell layer: the 3-slot ping-pong (DS_SHELL_TRANSITION).

    Slot k%3 fades up over DS_SHELL_FADE_FRAC of the hold, slot (k-1)%3 fades
    down, and slot (k+1)%3 - which finished fading out at cue k-1 - is
    re-pointed at file k+1 so it has a whole hold to load. Cue 0 only shows
    slot A (slot B was preloaded with file 1 in PREP). 'cut' mode writes the
    same cues without durations.
    """
    n = len(files)
    cue = (times[k + 1] - times[k]) if k + 1 < n else step
    fade = DS_SHELL_FADE_FRAC * float(cue) if DS_SHELL_TRANSITION == "crossfade" else 0.0
    dur = f" duration {_fmt_t(fade)}" if fade > 0 else ""
    out = [f"\t{shell_slot(name, k)} intensity 100{dur}\n"]
    if k >= 1:
        out.append(f"\t{shell_slot(name, k - 1)} intensity 0{dur}\n")
    if k >= 1 and k + 1 < n:
        out.append(f"\t{shell_slot(name, k + 1)} {DS_FILE_ATTR} "
                   f"{_model_ref(files[k + 1], model_prefix)}\n")
    return out


def write_shells_js(scripts_dir, preset=None) -> Path:
    """Write MagnetoShells.js: setShells(pct, dur) - THE panel knob for the shells.

    Scales both shell holders' colour (event and live prefixes, existence-
    checked) to pct% of the preset: 0 = invisible under additive blending,
    100 = the preset, 200 = twice. Holder intensity is inert for the shells
    (their slot objects carry explicit intensities), so nothing else on the
    panel may touch shell objects.
    """
    sp = shell_look(preset)
    base = ", ".join(f"'{k}': [{sp[k][0]:g}, {sp[k][1]:g}, {sp[k][2]:g}]"
                     for k in ("MagnetopauseShell", "BowShockShell"))
    # Every mesh layer's preset colour, so the panel can fade ANY mesh layer by colour: the
    # ring current and plasmasphere toggles faded the holder's intensity and nothing moved
    # (David, 2026-09-22) - their crossfade slots carry explicit intensities exactly like the
    # shells, so colour is their brightness channel too.
    mesh = ", ".join([base] + [f"'{k}': [{v['color'][0]:g}, {v['color'][1]:g}, {v['color'][2]:g}]"
                              for k, v in DS_MESH_LOOK.items()])
    js = [
        "// {[2026.0.3.3]}",
        f"// {DS_SHELLS_JS_NAME}  -  generated by dsexport/ds.py (shell preset {sp['name']}: {sp['note']})",
        "// setShells(pct, dur): scale BOTH shell holders' colour to pct% of the preset. Colour is",
        "// the shells' brightness (additive blend, colour inherited by the slot objects); 0 hides",
        "// them, 100 is the preset, 200 twice it. Holder intensity is inert for the shells.",
        "// setMesh(layer, pct, dur): the same for ONE mesh layer by name - MagnetopauseShell,",
        "// BowShockShell, RingCurrent or Plasmasphere - with an eased fade (25% in / 25% out).",
        f"var BASE = {{{base}}};",
        f"var MESH = {{{mesh}}};",
        "var PREFIXES = ['MagnetoEvent', 'MagnetoLive'];",
        "function exists(n) { try { return Ds.GetObjectID(n) != -1; } catch (e) { return false; } }",
        "function cmd(s) { Ds.SendStringCommand(s); }",
        "function colourTo(h, c, f, d) {",
        "    cmd(h + ' color ' + (c[0] * f).toFixed(2) + ' ' + (c[1] * f).toFixed(2) + ' ' + (c[2] * f).toFixed(2) + ' duration ' + d + ' 25% 25%');",
        "}",
        "function setShells(pct, dur) {",
        "    var f = Math.max(0, Number(pct)) / 100.0, d = (dur == null) ? 1 : Number(dur), n = 0;",
        "    for (var p = 0; p < PREFIXES.length; p++) {",
        "        for (var k in BASE) {",
        "            var h = PREFIXES[p] + k + 'Holder';",
        "            if (!exists(h)) { continue; }",
        "            colourTo(h, BASE[k], f, d);",
        "            n++;",
        "        }",
        "    }",
        f"    print('{DS_SHELLS_JS_NAME}: set ' + n + ' shell holder(s) to ' + pct + '%');",
        "}",
        "function setMesh(layer, pct, dur) {",
        "    var c = MESH[layer];",
        f"    if (!c) {{ print('{DS_SHELLS_JS_NAME}: no mesh layer ' + layer); return; }}",
        "    var f = Math.max(0, Number(pct)) / 100.0, d = (dur == null) ? 2 : Number(dur), n = 0;",
        "    for (var p = 0; p < PREFIXES.length; p++) {",
        "        var h = PREFIXES[p] + layer + 'Holder';",
        "        if (!exists(h)) { continue; }",
        "        colourTo(h, c, f, d);",
        "        n++;",
        "    }",
        f"    print('{DS_SHELLS_JS_NAME}: ' + layer + ' to ' + pct + '% on ' + n + ' holder(s)');",
        "}",
    ]
    return _write(Path(scripts_dir) / DS_SHELLS_JS_NAME, [ln + "\n" for ln in js])


# --- the curated-event frame-series player ---------------------------------------

def _series_layers(frames_manifest: dict):
    """[(layer_name, [file, ...], width, kind)] from a render_event() manifest.

    `kind` ('dots' | 'lines') decides the MODEL CLASS, not just the prototype
    width - see _prototype_lines. It comes straight from the render manifest,
    which takes it from the file's own 'set filecontent'.
    """
    out = []
    for layer in frames_manifest.get("layers", []):
        files = list(layer.get("files", []))
        if not files:
            continue
        k = str(layer.get("kind", "")).lower()
        kind = "dots" if k == "dots" else ("mesh" if k == "mesh" else "lines")
        width = DS_DOT_WIDTH if kind == "dots" else DS_LINE_WIDTH
        out.append((str(layer["name"]), files, width, kind))
    # The manifest still LISTS the shells (they are still rendered, and the .obj
    # files are still there) - they are dropped here, at the one place the player,
    # the teardown and the teardown JS all read, so a package needs no edit to stop
    # drawing them and no re-render to start again.
    return drawn_layers(out)


def _layer_times(layer: dict, n_files: int, step_seconds: float) -> list[float]:
    """Dome times (s) at which each file of one layer becomes the active frame.

    A layer may carry its own 'times' (seconds) or 'steps' (field-step indices)
    because the layers no longer share one cadence:

      * boundaries / field lines / L1 - one file per field step
      * the aurora - one file per Kp CHANGE (the oval is zhang_paxton(Kp) and
        Kp is 3-hourly, so emitting it every step would be 200 identical 9 MB
        files); its frames therefore hold for many steps and morph slowly
      * the wind - SEVERAL files per field step, because the parcels curve
        around the magnetopause and a coarse morph would cut the corner

    With neither key it falls back to the historical one-file-per-step.
    """
    times = layer.get("times")
    if not times:
        steps = layer.get("steps")
        times = ([float(s) * float(step_seconds) for s in steps] if steps
                 else [k * float(step_seconds) for k in range(n_files)])
    times = [float(t) for t in times][:n_files]
    # A short 'times' list would index-error below and take the whole player
    # down over a bookkeeping slip, so pad it at the layer's own cadence.
    if len(times) < n_files:
        cad = (times[-1] - times[-2]) if len(times) > 1 else float(step_seconds)
        last = times[-1] if times else 0.0
        times += [last + cad * (i + 1) for i in range(n_files - len(times))]
    return times


def _fmt_t(v: float) -> str:
    """Cue delta / duration: compact, no trailing zeros, never in exponent form."""
    return f"{float(v):.4f}".rstrip("0").rstrip(".") or "0"


def _write_event_player(frames_manifest: dict, out_ds, step_seconds: float,
                       model_prefix: str = DS_MODEL_REL_PREFIX,
                       parent=None, frame: str = "j2000",
                       intensities: dict | None = None,
                       preload_window: int | None = None,
                       shell_preset: str | None = None) -> Path:
    r"""Emit the .ds that plays one curated event as a frame SERIES.

    frames_manifest is what magneto.render.render_event() returns: event_id,
    title, epochs, and a 'layers' list of {name, kind, files, holder, times}
    where files[k] is the model file for that layer's frame k (flat names in
    Models\) and times[k] is the dome second at which it becomes active.

    Structure is write_animation_ds()'s, extended exactly as solarwind-daily's
    write_series_ds() extends it - several layers stepped together - plus the
    two things a magnetosphere event needs that the solar animations did not:

    ONE HOLDER PER ASSET TYPE, TWO CADENCES. Every layer gets its own
    emptyClass holder with its own intensity (see DS_EVENT_HOLDER_LAYERS), so
    the presenter can act on one asset type - kill the parcel stream, dim the
    bow shock, hide the L1 marker - without touching the rest and without
    naming a single frame object. The cadences are still two: the field layers
    (boundaries, field lines, aurora, L1) step once per event step, while the
    parcel stream steps SEVERAL times per event step, because parcels curve
    around the magnetopause and a once-per-step morph would cut the chord
    straight through it. Their cues are interleaved on the one script timeline
    (a .ds has exactly one clock) but each layer only ever fires at its own
    times, and the aurora - which is a Kp oval and Kp is 3-hourly - fires only
    when Kp actually changed, with a long-duration morph in between.

    'scene date' (ACTIVE since 2026-09-04): the magnetosphere is exported
    SUN-FIXED, so pinning the scene date to the first epoch in PREP and
    tweening it to the last epoch in ACTION is what makes the rotating Earth
    texture, terminator, native aurora and Sun-fixed camera views line up with
    the frames. The teardown gives real time back ('scene date now').

    preload_window (None = preload every frame, the original behaviour): keep
    at most this many frame objects of each layer alive ahead of the playhead.
    Frame k+window is created and loaded at cue k, frame k-2 is deleted at cue
    k (frame 0 - the prototype the clones derive from - is never deleted).
    Bounds the model-load queue: a 96 h look-back with 2,301 wind files hit
    "Discarded LOAD_MODEL because model texture load queue is full" at ~1,127
    concurrent objects (2026-08-18 host log). The window also bounds memory.
    """
    layers = _series_layers(frames_manifest)
    if not layers:
        raise ValueError("frames_manifest has no layers with files")
    if all(len(files) == 0 for _n, files, _w, _k in layers):
        raise ValueError("frames_manifest layers have no frames")
    step = float(step_seconds)
    eid = str(frames_manifest.get("event_id", "unknown"))
    title = _ascii(frames_manifest.get("title") or eid)
    epochs = list(frames_manifest.get("epochs") or [])
    first_epoch = epochs[0] if epochs else "?"
    last_epoch = epochs[-1] if epochs else "?"
    step_min = frames_manifest.get("step_min")
    aurora_caption = _ascii(frames_manifest.get("aurora_label", ""))
    peak = frames_manifest.get("peak_caption")
    frame = str(frames_manifest.get("frame", frame)).lower()

    by_name = {str(lay["name"]): lay for lay in frames_manifest.get("layers", [])}
    # Per-package default visibility of the boundary shells. Gannon starts hidden (David
    # 2026-09-16) so the storm opens on the field lines; the look-back and the live feed
    # start visible.
    shells_hidden = bool(frames_manifest.get("shells_hidden"))
    shells_blk = frames_manifest.get("shells") or {}
    lib_rows = {str(lay["name"]) for lay in frames_manifest.get("layers", [])
                if lay.get("library") and str(lay["name"]) in shells_blk}
    plan = []   # (name, files, width, kind, holder_key, times, parametric)
    for name, files, width, kind in layers:
        lay = by_name.get(name, {})
        holder_key = _resolve_holder(name, lay.get("holder"))
        # a library shell row lists a few SHAPES, but its cues run every step
        n_times = len(shells_blk[name]["scale"]) if name in lib_rows else len(files)
        plan.append((name, files, width, kind, holder_key,
                     _layer_times(lay, n_times, step),
                     bool(lay.get("parametric", True))))
    used_holders = list(dict.fromkeys(q[4] for q in plan))
    # The field cadence is the step cadence: every layer except the parcel
    # stream advances once per event step, so the step labels follow the
    # longest non-wind layer.
    n_field = max((len(q[1]) for q in plan
                   if _obj_token(q[4]) != "WindParticles"), default=0)
    total_s = max((q[5][-1] for q in plan if q[5]), default=0.0)

    L: list[str] = []
    w = L.append
    w(f"# {Path(out_ds).name}  -  generated by dsexport/ds.py\n")
    w(f"# Plays the curated event {eid}: {title}\n")
    w(f"# {n_field} field step(s) at {step:g}s, {len(layers)} layer(s), "
      f"{len(used_holders)} holder(s), {total_s + step:g}s total.\n")
    w(f"# Simulated window {first_epoch} .. {last_epoch}"
      + (f" at {step_min:g} min per field step.\n" if step_min else ".\n"))
    for name, files, _width, kind, holder_key, times, _par in plan:
        cad = (times[1] - times[0]) if len(times) > 1 else step
        w(f"#   {name:<16} {len(files):>5} {kind:<5} "
          f"{event_holder(holder_key):<32} cadence {cad:g}s\n")
    w("# Frames are PARAMETRIC: each file's V rows hold the NEXT frame OF THAT\n")
    w("# LAYER, so 'morph 100 duration <that layer's own hold>' interpolates\n")
    w("# between them - which is why the aurora may morph over minutes while\n")
    w("# the wind morphs over a fraction of a second.\n")
    if any(q[3] == "mesh" for q in plan):
        sp = shell_look(shell_preset)
        w(f"# Shell layers (effectModelClass, preset {sp['name']}: {sp['note']}): meshes\n")
        w(f"# cannot morph, so each shell has {DS_SHELL_SLOTS} fixed slot objects that are\n")
        w(f"# {DS_SHELL_TRANSITION}d per step - the holder COLOUR is the shell's brightness\n")
        w(f"# ({DS_SHELLS_JS_NAME} setShells(pct) is the panel's knob).\n")
    if not DS_DRAW_SHELLS:
        w("# BOUNDARY SHELLS SUPPRESSED: the magnetopause and bow shock are not drawn in\n")
        w("# this build. The geometry is still computed and still exported - the .obj\n")
        w("# files are in Models and the manifest still lists them - so restoring them is\n")
        w("# a re-emit, not a re-render. The boundary drives the rest of the scene either\n")
        w("# way: the solar wind steers around it and the field lines are gated by it.\n")
    elif shells_hidden:
        w("# BOUNDARY SHELLS START HIDDEN in this package: their holders are set to\n")
        w("# colour 0 0 0 in PREP (a shell's brightness is its holder colour, and under\n")
        w("# additive blending black renders nothing). The panel's Shells toggle raises\n")
        w("# them with MagnetoShells.js setShells(100). Nothing else differs from a\n")
        w("# package whose shells start visible.\n")
    w(f"# Export frame: {frame.upper()}"
      + ("  (+X sunward, sun-fixed - see the holder note below)\n"
         if frame == "gsm" else
         "  (geocentric equatorial, the WCS axes - sun-fixed by epoch)\n"
         if frame == "j2000" else "  (Earth-fixed, rides the rotation)\n"))
    w("# Coordinates are LIGHT YEARS - that is what 'set coordsys RIGHT' means.\n")
    w("# Nothing here scales the models.\n")
    w("# Structure cloned from sunspots-Bfield-daily.py write_animation_ds()\n")
    w("# and solarwind-daily.py write_series_ds() (several layers stepped\n")
    w("# together), with one holder per asset type instead of one for all.\n")
    w(f"# Models are referenced flat as '{model_prefix}<file>' "
      "(Models\\ beside Scripts\\).\n")
    if aurora_caption:
        w(f"# Aurora layer provenance: {aurora_caption}\n")
    # Post-render provenance (e.g. a retime) travels with the manifest and is
    # restated here, so the script itself says how it differs from the render.
    for note in frames_manifest.get("provenance_notes") or []:
        w(f"# {_ascii(str(note))}\n")
    w("\n")

    # -- PREP -----------------------------------------------------------------
    w("0:00\n")
    # The clock is pinned FIRST, before any object exists. standardSpace is applied when a
    # holder is first placed in the graph, so a holder created under today's date and then
    # jumped decades would be placed against the wrong Earth (audited 2026-09-15).
    L.extend(_scene_date_prep_lines(first_epoch, last_epoch, total_s))
    w("\t# ONE emptyClass holder PER ASSET TYPE. Each holder owns every frame of\n")
    w("\t# exactly one layer, so from the dome you can hide, dim or restyle any\n")
    w("\t# single asset type - drop the parcel stream, take the bow shock down\n")
    w("\t# to 30, leave the aurora alone - by touching ONE object, whatever the\n")
    w("\t# frame count. They are siblings, not nested: each is parented to the\n")
    w("\t# Earth in its own right, so no holder inherits another's intensity.\n")
    w("\t# To act on the whole overlay, act on all of them (this script and the\n")
    w("\t# teardown both emit one line per holder).\n")
    for i, key in enumerate(used_holders):
        holder = event_holder(key)
        w(f"\t{holder} is {DS_HOLDER_CLASS}\n")
        L.extend(_holder_parent_lines(holder, parent, frame, brief=bool(i)))
        w(f"\t{holder} intensity 0\n")
        rgb = holder_color(key, shell_preset)
        if rgb:
            # A shell's brightness knob is its holder COLOUR, not an intensity: the three
            # crossfade slots each carry an explicit intensity, and an explicit child
            # intensity replaces the holder's, so `holder intensity 0` leaves a shell
            # drawing (that was the "duplicate magnetosheath" bug). Under blendMode "add",
            # colour 0 0 0 renders nothing - so a package whose shells start hidden writes
            # black here and the panel's Shells toggle brings them up with
            # MagnetoShells.js setShells(100), which restores this same preset colour.
            if shells_hidden:
                w(f"\t# shells start HIDDEN in this package - press Shells to raise them\n")
                w(f"\t{holder} color 0 0 0\n")
            else:
                w(f"\t{holder} color {rgb[0]:g} {rgb[1]:g} {rgb[2]:g}\n")
    w("\t# Per-asset intensity: start HIDDEN (0), fade up on the first ACTION\n")
    w("\t# frame. Every frame object uses 'intensity inherited', so the holder\n")
    w("\t# of its asset type is the single place that layer's brightness is set.\n")
    hud = [str(h) for h in (frames_manifest.get("hud") or [])]
    if hud:
        L.extend(_hud_prep_lines(DS_HUD_EVENT, hud[0]))
    w("\n")
    # system message payloads are hard-capped at 255 characters on the
    # install (dome-verified 2026-08-14). Panes are dropped from the tail -
    # aurora caption first, then the peaks - before anything essential.
    banner_parts = [f"{title}|{first_epoch} to {last_epoch}"]
    if peak:
        banner_parts.append(_ascii(peak))
    if aurora_caption:
        banner_parts.append(aurora_caption)
    while len("|".join(banner_parts)) > 255 and len(banner_parts) > 1:
        banner_parts.pop()
    w(f'\tsystem message "{"|".join(banner_parts)}"\n')
    w("\n")
    w("\t# Pre-load every frame of every layer as a hidden child. The FIRST\n")
    w("\t# frame of each layer is that layer's prototype: it declares the model\n")
    w("\t# class and style, later frames are 'is <prototype>' and set only\n")
    w("\t# their own file.\n")
    loads = 0
    win = int(preload_window) if preload_window else None
    if win is not None and win < 3:
        raise ValueError("preload_window must be >= 3 (or None for all frames)")
    # Preload in DISPLAY-TIME order across layers (frame 0 of every layer,
    # then frame 1 ...), not layer by layer. Digistar loads models in the
    # order declared, and layer-by-layer put the wind's first frame behind
    # every other layer's window (~15 s of field-line loads at window 60) so
    # the parcels popped in late - dome-measured 2026-09-05. Frame 0 of a
    # layer is its prototype and always precedes that layer's clones because
    # a layer's times increase.
    for name, files, _width, kind, holder_key, _times, _par in plan:
        if kind == "mesh" and name in lib_rows:
            L.extend(_shell_lib_prep_lines(name, shells_blk[name], event_holder(holder_key),
                                           model_prefix, shell_preset))
            loads += 1
        elif kind == "mesh":
            # Shell layers: three fixed slot objects, no clones, no rolling window.
            L.extend(_shell_prep_lines(name, files, event_holder(holder_key),
                                       model_prefix, shell_preset))
            loads += min(2, len(files))
    queue = []
    for li, (name, files, _width, _kind, _holder_key, times, _par) in enumerate(plan):
        if _kind == "mesh":
            continue
        n_pre = len(files) if win is None else min(len(files), win)
        for k in range(n_pre):
            queue.append((times[k] if k < len(times) else k * step, li, k))
    queue.sort()
    protos: dict[int, str] = {}
    for _t, li, k in queue:
        name, files, width, kind, holder_key, _times, _par = plan[li]
        holder = event_holder(holder_key)
        obj = DS_EVENT_OBJ_FMT.format(name, k)
        if li not in protos:
            L.extend(_prototype_lines(obj, width, kind=kind, layer=name))
            protos[li] = obj
        else:
            w(f"\t{obj} is {protos[li]}\n")
        w(f"\t{obj} {DS_FILE_ATTR} {_model_ref(files[k], model_prefix)}\n")
        w(f"\t{obj} intensity 0\n")
        w(f"\t{holder} add {obj}\n")
        loads += 1
        # Advance 0.1 s every 25 loads so Digistar finishes that batch
        # before the next is declared (keeps the loader from overrunning).
        if loads % DS_LOADS_PER_PACE == 0:
            w(f"+{DS_PACE_SEC:g}\n")
    w("\t# Give Digistar a moment to land the first frames before the timeline\n")
    w("\t# starts - the rest of the preload keeps streaming in display order.\n")
    w("\n")
    w(f"+{DS_PREP_SETTLE_SEC:g}\n")
    w("\n")

    # -- ACTION ---------------------------------------------------------------
    # One merged timeline: every layer contributes its own cue times, and the
    # script walks the union in order. A .ds has exactly one clock, so "two
    # cadences" means interleaved cues, not two scripts.
    cues: dict[float, list[str]] = {}
    labels: dict[float, str] = {}

    def at(t: float) -> list[str]:
        return cues.setdefault(round(float(t), 6), [])

    for name, files, _width, _kind, holder_key, times, parametric in plan:
        if _kind == "mesh" and name in lib_rows:
            slot_state = {"active": 0}
            for k in range(len(times)):
                at(times[k]).extend(_shell_lib_step_lines(name, shells_blk[name], k, times,
                                                          model_prefix, slot_state))
            continue
        if _kind == "mesh":
            for k in range(len(files)):
                at(times[k]).extend(_shell_step_lines(name, files, k, times, step,
                                                      model_prefix))
            continue
        holder = event_holder(holder_key)
        proto = DS_EVENT_OBJ_FMT.format(name, 0)
        lag = delete_lag((times[1] - times[0]) if len(times) > 1 else step)
        for k in range(len(files)):
            t = times[k]
            obj = DS_EVENT_OBJ_FMT.format(name, k)
            block = at(t)
            if win is not None and k + win < len(files):
                # rolling window: declare + load frame k+win now, well ahead
                # of its cue; the prototype (frame 0) stays alive as the
                # template every later clone derives from.
                j = k + win
                objj = DS_EVENT_OBJ_FMT.format(name, j)
                block.append(f"\t{objj} is {proto}\n")
                block.append(f"\t{objj} {DS_FILE_ATTR} {_model_ref(files[j], model_prefix)}\n")
                block.append(f"\t{objj} intensity 0\n")
                block.append(f"\t{holder} add {objj}\n")
            if parametric:
                # SNAP to the start pose while still hidden. A clone copies the
                # prototype's CURRENT morph at creation - and the prototype's own
                # tween ended at 100 long ago - so without this every frame was
                # born at its END pose and the tween below was a no-op: static
                # holds and a cut at every swap (dome-measured 2026-09-14).
                block.append(f"\t{obj} morph 0\n")
            block.append(f"\t{obj} intensity inherited\n")
            if parametric and k < len(files) - 1:
                # Morph over THIS layer's own hold, not the field step: the
                # aurora holds for a whole Kp interval, the wind for a quarter
                # step. The last file of a layer is NON_PARAMETRIC, so no cue -
                # and a layer that is never parametric (the single-dot L1
                # marker) gets no morph cue at all.
                block.append(f"\t{obj} morph 100 duration "
                             f"{_fmt_t(times[k + 1] - t)}\n")
            if k:
                block.append(
                    f"\t{DS_EVENT_OBJ_FMT.format(name, k - 1)} intensity 0\n")
            if win is not None and k - lag >= 1:
                # frame k-lag finished its morph cues ago and is hidden; release
                # it (never frame 0, the prototype). The lag keeps a delete from
                # landing on a frame whose LOAD_MODEL is still queued right after
                # PREP (OMERROR_OBJECT_NOT_FOUND, dome-measured 2026-09-05) and
                # is at least DS_DELETE_LAG_MIN_SEC of wall time per layer.
                block.append(f"\t{DS_EVENT_OBJ_FMT.format(name, k - lag)} delete\n")
    # the readout re-texts only the SEGMENTS that changed (PREP set step 0's)
    prev_segs = hud_split(hud[0], DS_HUD_SLOT_CHARS_EVENT) if hud else None
    for k in range(1, min(n_field, len(hud))):
        segs = hud_split(hud[k], DS_HUD_SLOT_CHARS_EVENT)
        for i, s in enumerate(segs):
            if s != prev_segs[i]:
                at(k * step).append(f"\t{DS_HUD_EVENT}{i} text \"{s}\"\n")
        prev_segs = segs
    # presenter talking points (David 2026-09-14): KEY MOMENTS (data) then WATCH / PHYSICS /
    # IMPACT (registry), spaced so each can be read; never past the end of the playback
    talk = [str(m).strip() for m in ([frames_manifest.get("key_moments") or ""]
                                     + list(frames_manifest.get("talking_points") or []))]
    for j, m in enumerate(t for t in talk if t):
        t_msg = DS_TALK_FIRST_S + j * DS_TALK_EVERY_S
        if t_msg >= total_s:
            break
        at(t_msg).append(f"\tsystem message \"{_ascii(m)[:255]}\"\n")
    for k, ep in enumerate(epochs[:n_field]):
        labels.setdefault(round(k * step, 6), f"\t# --- step {k:04d}  {ep} ---\n")

    order = sorted(cues)
    w("\t# ACTION: the merged cue timeline. Each layer shows its frame k, morphs\n")
    w("\t# it into frame k+1 over that layer's OWN hold, and hides frame k-1.\n")
    L.extend(_scene_date_action_lines(last_epoch, total_s))
    for i, t in enumerate(order):
        if t in labels:
            w(labels[t])
        L.extend(cues[t])
        if i == 0:
            for key in used_holders:
                w(f"\t{event_holder(key)} intensity "
                  f"{holder_intensity(key, intensities):g} "
                  f"duration {DS_HOLDER_FADE_SEC:g}\n")
            if hud:
                w(f"\t{DS_HUD_EVENT} intensity {DS_HUD_INTEN:g} duration {DS_HOLDER_FADE_SEC:g}\n")
        w("\n")
        delta = (order[i + 1] - t) if i + 1 < len(order) else step
        w(f"+{_fmt_t(delta)}\n")
    w("\n")
    w(f"\t# Holds the last frame. Run {DS_EVENT_TEARDOWN_FMT.format(eid)} to clear,\n")
    w("\t# or live-restore.ds to clear it and bring the live layer back.\n")
    if win is not None:
        w(f"\t# Rolling window: at most {win} frame objects per layer were alive at once.\n")

    out = _write(out_ds, L)
    log.info("event player: %s, %d layers, %d cues, %d model loads, %.3gs "
             "total -> %s", eid, len(layers), len(order), loads, total_s, out)
    return out


def _scene_date_prep_lines(first_epoch, last_epoch, total_s) -> list[str]:
    """The ACTIVE 'scene date' pin (dome decision 2026-09-04; was commented out).

    The magnetosphere is exported SUN-FIXED at each frame's own epoch, so the
    dome clock has to run across the event window for the Earth texture, the
    terminator, Digistar's native aurora and the Sun-fixed camera views
    (LiveMagnetosphere\\Scripts\\view_*.ds) to agree with the frames. Yes, it
    moves the GLOBAL dome clock - that is the point here; the teardown and
    live-restore put real time back with 'scene date now'.
    """
    start = _scene_datetime(first_epoch)
    end = _scene_datetime(last_epoch)
    return [
        "\t# SCENE DATE PIN (active). The frames are baked SUN-FIXED at their own\n",
        "\t# epochs, so the dome clock runs across the event window: Earth texture,\n",
        "\t# terminator, native aurora and the Sun-fixed camera views all follow the\n",
        "\t# frames. Pinned BEFORE any object exists so nothing jumps - swept in ACTION.\n",
        "\t# The teardown restores real time with 'scene date now'.\n",
        f"\tscene date {start}\n",
        f"\t# (window {start} -> {end} over {float(total_s):g} dome seconds)\n",
        "\n",
    ]


def _scene_date_action_lines(last_epoch, total_s) -> list[str]:
    """The ACTIVE 'scene date' tween that matches the PREP pin.

    'scene date <when> duration <s>' is the dome-verified form (the Rubin
    players use it: 'scene date 2026-9-25 23:42:22 dur 28 3 3').
    """
    end = _scene_datetime(last_epoch)
    if float(total_s) > 0:
        cue = f"\tscene date {end} duration {float(total_s):g}\n"
    else:                                                  # pragma: no cover
        cue = f"\tscene date {end}\n"
    return [
        "\t# Sweeps the scene clock from the PREP pin to the LAST frame's epoch\n",
        "\t# over the whole playback, so every frame's Earth orientation matches\n",
        "\t# its own epoch and the aurora stays on the right continents.\n",
        cue,
        "\n",
    ]


def player_js_name(eid) -> str:
    """Per-event helper JS written next to the player (see write_player_js)."""
    return f"MagnetoPlayer-{eid}.js"



def write_event_player(frames_manifest: dict, out_ds, step_seconds: float, **kw):
    r"""Thin wrapper that pins the model layout for the duration of the write.

    A render that SHIPS inside a .dsz lays its models out the way the packager will
    (Models\<name>\<name>.<ext>); one that is rebuilt on the target system stays flat. The
    manifest records which, so re-emitting an older package cannot silently flip its references
    to a layout its files are not in - which would break every model line at once, silently.

    AN ABSENT KEY MEANS FLAT, never the module default. Every render comes off the writers
    flat; the convention layout only ever arrives via tools/rename_models.py or
    tools/normalize_model_layout.py, and both of those SET the key. So "no key" is always a
    tree that has not been converted. Falling back to the module default instead cost a whole
    Gannon render on 2026-09-20: the default had just been flipped to True, the fresh
    manifest carried no key, and the player named Models\FieldLines_0000\FieldLines_0000.vlc
    over a flat tree - 3,483 model lines wrong at once, with --validate green because every
    file was perfect and nothing checked the references.
    """
    global DS_MODEL_CONVENTION_DIRS
    saved = DS_MODEL_CONVENTION_DIRS
    DS_MODEL_CONVENTION_DIRS = bool(frames_manifest.get("model_convention_dirs", False))
    try:
        return _write_event_player(frames_manifest, out_ds, step_seconds, **kw)
    finally:
        DS_MODEL_CONVENTION_DIRS = saved


def write_player_js(frames_manifest: dict, scripts_dir) -> Path:
    """Write MagnetoPlayer-<id>.js: existence-checked object teardown for the event.

    The player may keep only a rolling window of frame objects alive (see
    write_event_player preload_window), so a .ds teardown cannot know which
    frame objects exist - and Digistar validates object names when it PARSES a
    script, dropping every line that names a missing object ("name is not
    known" in the .lis) and reporting "had N errors". JavaScript can ask
    (Ds.GetObjectID(name) != -1), so the deletes live here. A .ds resolves
    'js play .\\<file>.js' relative to itself (dome-verified 2026-09-04), which
    is what lets this travel through the staging/promote rotation.
    """
    layers = _series_layers(frames_manifest)
    eid = str(frames_manifest.get("event_id", "unknown"))
    rows = []
    for name, files, _w, kind in layers:
        if kind == "mesh":
            names = ", ".join(f"'{shell_slot(name, i)}'" for i in range(DS_SHELL_SLOTS))
            rows.append(f"    {{ names: [{names}] }},")
        else:
            rows.append(f"    {{ prefix: '{DS_EVENT_OBJ_FMT.format(name, 0)[:-4]}', n: {len(files)} }},")
    if frames_manifest.get("hud"):
        names = ", ".join(f"'{n}'" for n in hud_object_names(DS_HUD_EVENT))
        rows.append(f"    {{ names: [{names}] }},")
    js = [
        "// {[2026.0.3.3]}",
        f"// {player_js_name(eid)}  -  generated by dsexport/ds.py for {eid}",
        "// teardownEvent(): delete every frame object of this event that exists (the player keeps",
        "// only a rolling window of them alive, so a .ds cannot list them without parse errors).",
        "var LAYERS = [",
    ] + rows + [
        "];",
        "function exists(n) { try { return Ds.GetObjectID(n) != -1; } catch (e) { return false; } }",
        "function pad4(k) { var s = '000' + k; return s.substring(s.length - 4); }",
        "function teardownEvent() {",
        "    var gone = 0;",
        "    for (var i = 0; i < LAYERS.length; i++) {",
        "        if (LAYERS[i].names) {",
        "            for (var s = 0; s < LAYERS[i].names.length; s++) {",
        "                if (exists(LAYERS[i].names[s])) { Ds.SendStringCommand(LAYERS[i].names[s] + ' delete'); gone++; }",
        "            }",
        "            continue;",
        "        }",
        "        for (var k = 0; k < LAYERS[i].n; k++) {",
        "            var name = LAYERS[i].prefix + pad4(k);",
        "            if (exists(name)) { Ds.SendStringCommand(name + ' delete'); gone++; }",
        "        }",
        "    }",
        f"    print('{player_js_name(eid)}: deleted ' + gone + ' frame object(s)');",
        "}",
    ]
    return _write(Path(scripts_dir) / player_js_name(eid), [ln + "\n" for ln in js])


def write_event_teardown(frames_manifest: dict, out_ds) -> Path:
    """Emit the companion 'off' player for an event frame series.

    Order (dome-verified grammar, 2026-09-04): stop the player script first so
    no frame cue fires at an object being deleted (the teardown race seen in
    the 2026-08-18 host log), fade every holder to 0 over 3 s, wait 3.1 s,
    delete the frame objects that exist (through the per-event JS - see
    write_player_js), delete the holders, and give the dome its real-time
    clock back ('scene date now', since the player pinned the scene date).
    Object names mirror write_event_player() so the two stay in sync.
    """
    layers = _series_layers(frames_manifest)
    by_name = {str(lay["name"]): lay for lay in frames_manifest.get("layers", [])}
    used_holders = list(dict.fromkeys(
        _resolve_holder(name, by_name.get(name, {}).get("holder"))
        for name, _f, _w, _k in layers))
    eid = str(frames_manifest.get("event_id", "unknown"))
    total = sum(len(f) for _n, f, _w, _k in layers)
    player_stem = DS_EVENT_PLAYER_FMT.format(eid)[:-3]
    L: list[str] = []
    w = L.append
    w(f"# {Path(out_ds).name}  -  generated by dsexport/ds.py\n")
    w(f"# Tears down the event overlay built by "
      f"{DS_EVENT_PLAYER_FMT.format(eid)}: stop the player, fade the holder(s)\n")
    w(f"# out over {DS_HOLDER_OUT_SEC:g}s, wait {DS_TEARDOWN_WAIT_SEC:g}s, delete the "
      f"frame objects that exist ({player_js_name(eid)}),\n")
    w("# delete the holder(s), restore the real-time clock.\n")
    w(f"# {total} frame object(s) across {len(layers)} layer(s) and "
      f"{len(used_holders)} holder(s).\n")
    w("\n")
    w("0:00\n")
    w("\t# Stop the player first: a frame cue firing at a deleted object was the\n")
    w("\t# HOSTERROR_INVALID_OBJECT race in the 2026-08-18 host log.\n")
    w(f"\tscript stop {player_stem}\n")
    mesh_holders = {_resolve_holder(name, by_name.get(name, {}).get("holder"))
                    for name, _f, _w, kind in layers if str(kind).lower() == "mesh"}
    for key in used_holders:
        w(f"\t{event_holder(key)} intensity 0 duration {DS_HOLDER_OUT_SEC:g}\n")
        if key in mesh_holders:
            # A mesh layer's brightness is its holder COLOUR: the two crossfade slots carry
            # explicit intensities, and an explicit child intensity replaces the holder's, so
            # the line above never reaches them and the shells, ring current and plasmasphere
            # used to vanish at the delete instead of fading (David, 2026-09-22).
            w(f"\t{event_holder(key)} color 0 0 0 duration {DS_HOLDER_OUT_SEC:g}\n")
    has_hud = bool(frames_manifest.get("hud"))
    if has_hud:
        w(f"\t{DS_HUD_EVENT} intensity 0 duration {DS_HOLDER_OUT_SEC:g}\n")
    w(f"+{DS_TEARDOWN_WAIT_SEC:g}\n")
    w("\t# Frame objects: only the ones that exist (rolling-window player).\n")
    w(f"\tjs play .\\{player_js_name(eid)} | teardownEvent()\n")
    for key in used_holders:
        w(f"\t{event_holder(key)} delete\n")
    if has_hud:
        for hname in hud_object_names(DS_HUD_EVENT):
            w(f"\t{hname} delete\n")
    w("\t# The player pinned and swept the scene date - give real time back.\n")
    w("\tscene date now\n")
    out = _write(out_ds, L)
    log.info("event teardown: %s, %d frame objects x %d layers -> %s",
             eid, total, len(layers), out)
    return out


def _native_aurora_attrs(kp: float) -> tuple[float, float, float]:
    """(auroraIntensity %, ovalMin deg, ovalMax deg) for one Kp value."""
    kp = max(0.0, min(9.0, float(kp)))
    inten = min(100.0, DS_AURORA_INTEN_BASE + DS_AURORA_INTEN_PER_KP * kp)
    lo = DS_AURORA_EDGE_DEG - DS_AURORA_EDGE_PER_KP * kp
    hi = min(80.0, lo + DS_AURORA_WIDTH_DEG + DS_AURORA_WIDTH_PER_KP * kp)
    return round(inten, 1), round(lo, 1), round(hi, 1)


def write_native_aurora_ds(frames_manifest: dict, out_ds,
                           off_ds=None) -> list[Path]:
    """Emit the OPTIONAL Digistar-native-aurora companion for an event.

    A SEPARATE script from the event player, on purpose: every token in here
    is dome-unverified (auroraClass is deprecated on this install; planet
    auroras are planetClass attributes on the Earth object per the Class
    Reference), so a bad token can never take the main player down. It drives
    Earth's own aurora from the SAME Kp series the AuroraOval frames were
    built from - one cue per aurora emission step, tweened over that step's
    own hold - so the two ovals agree by construction.

    Needs frames_manifest keys 'aurora_steps' (state indices) and 'aurora_kp'
    (Kp per emission); returns [] without writing when either is absent (old
    manifests). Also writes the companion off script (auroraIntensity 0).
    """
    steps = list(frames_manifest.get("aurora_steps") or [])
    kps = list(frames_manifest.get("aurora_kp") or [])
    if not steps or len(steps) != len(kps):
        return []
    step_seconds = float(frames_manifest.get("step_seconds", 1.0))
    # The aurora layer's own dome times, exactly as the player computes them.
    by_name = {str(lay["name"]): lay
               for lay in frames_manifest.get("layers", [])}
    times = _layer_times(by_name.get("AuroraOval", {"steps": steps}),
                         len(steps), step_seconds)
    eid = str(frames_manifest.get("event_id", "unknown"))
    epochs = list(frames_manifest.get("epochs") or [])

    L: list[str] = []
    w = L.append
    w(f"# {Path(out_ds).name}  -  generated by dsexport/ds.py\n")
    w(f"# OPTIONAL companion to {DS_EVENT_PLAYER_FMT.format(eid)}: drives the\n")
    w("# EARTH OBJECT'S OWN aurora (the deprecated-auroraClass replacement:\n")
    w("# planet auroras are planetClass attributes on Earth) from the same Kp\n")
    w("# series the AuroraOval frames were built from. Start it TOGETHER with\n")
    w("# the event player - both timelines are cut from the same manifest.\n")
    w("#\n")
    w("# HONESTY: ovalMinMax is a STATISTICAL magnetic-latitude band, not the\n")
    w("# OVATION-topology AuroraOval this build exports. Running both puts two\n")
    w("# differently-drawn ovals on the dome - they are driven from the SAME Kp\n")
    w("# here so they agree by construction, but if only one is wanted, run\n")
    w("# only one (hide the AuroraOval layer's holder, or skip this script).\n")
    w("# The Kp mapping is an aesthetic approximation of the modeled oval's\n")
    w("# expansion (equatorward edge ~ 66 - 2*Kp), not an observation.\n")
    w("#\n")
    w(f"{VERIFY}  EVERY Earth attribute in this file. The tokens come from the\n")
    w("# Class Reference of this install but none has run on the dome yet -\n")
    w("# including whether ovalMinMax accepts a 'duration' tween.\n")
    w("\n")
    w("0:00\n")
    w("\t# Space view: the docs name 90 89 as the value that keeps the whole\n")
    w("\t# oval visible from orbit (the default 2 -18 fades it in daylight).\n")
    w("\tEarth auroraDaylightElevation 90 89\n")
    w("\tEarth auroraIntensity 0\n")
    if epochs:
        w(f"\t# Kp series from {epochs[0]} .. {epochs[-1]}, "
          f"{len(steps)} emission step(s).\n")
    w("\n")
    for j, (k, kp) in enumerate(zip(steps, kps)):
        inten, lo, hi = _native_aurora_attrs(kp)
        hold = (times[j + 1] - times[j]) if j + 1 < len(times) \
            else step_seconds
        ep = epochs[k] if k < len(epochs) else "?"
        w(f"\t# --- aurora step {j:04d}  Kp {kp:g}  {ep} ---\n")
        w(f"\tEarth ovalMinMax {lo:g} {hi:g}"
          + (f" duration {_fmt_t(hold)}\n" if j else "\n"))
        w(f"\tEarth auroraIntensity {inten:g}"
          + (f" duration {_fmt_t(hold)}\n" if j
             else f" duration {DS_HOLDER_FADE_SEC:g}\n"))
        w("\n")
        if j + 1 < len(times):
            w(f"+{_fmt_t(times[j + 1] - times[j])}\n")
    w(f"\t# Holds the last oval. Run {DS_NATIVE_AURORA_OFF_FMT.format(eid)}\n")
    w("\t# to switch the native aurora off.\n")
    out = _write(out_ds, L)

    F: list[str] = []
    off_path = Path(off_ds) if off_ds is not None \
        else Path(out_ds).with_name(DS_NATIVE_AURORA_OFF_FMT.format(eid))
    F.append(f"# {off_path.name}  -  generated by dsexport/ds.py\n")
    F.append(f"# Switches off the native aurora driven by {Path(out_ds).name}.\n")
    F.append(f"{VERIFY}  Earth attribute tokens, as in the on script.\n")
    F.append("\n0:00\n")
    F.append(f"\tEarth auroraIntensity 0 duration {DS_HOLDER_OUT_SEC:g}\n")
    off = _write(off_path, F)
    log.info("native aurora: %s, %d Kp step(s) -> %s + %s",
             eid, len(steps), out, off)
    return [out, off]


# --- presenter override scripts ---------------------------------------------------

def _override_note_lines(root_hint: str = "state\\override.json") -> list[str]:
    """The shared explanation of how a dome key reaches the daemon."""
    return [
        "# The live daemon polls <root>\\" + root_hint + " once per cycle.\n",
        '#   {"force": "EVENT:<event id>"}   play that curated event\n',
        '#   {"force": "LIVE"}               pin the live feed (no auto switch)\n',
        '#   {"force": null}                 release, back to automatic\n',
        "# A dome key should WRITE that file (one small JSON, written to a .tmp\n",
        "# and renamed) and then run this script. The daemon picks the change up\n",
        "# on its next cycle - within cycle_s seconds, 60 by default.\n",
    ]


DS_LIVE_JS_NAME = "MagnetoLive.js"


def write_live_js(scripts_dir) -> Path:
    """Write MagnetoLive.js: existence-checked helpers for the live holders.

    The live holders (MagnetoLive<Layer>Holder) exist only after the live daemon
    has published; a .ds that names them when they do not exist loses those
    lines at parse time and the host reports "had 6 errors" on every play (the
    2026-08-18 log). JavaScript can check first.
    """
    # The readout rides with the layers: an event and the live feed must never both
    # draw a readout (two data ages on the dome at once, overlapping text at the base).
    live_names = [live_holder(name) for name, _f, _w, _k in LIVE_LAYERS] + [DS_HUD_LIVE]
    live_inten = {live_holder(name): holder_intensity(name) for name, _f, _w, _k in LIVE_LAYERS}
    live_inten[DS_HUD_LIVE] = DS_HUD_INTEN
    live = ", ".join(f"'{n}'" for n in live_names)
    inten = ", ".join(f"'{n}': {live_inten[n]:g}" for n in live_names)
    # The shell SLOT objects. Fading their holder does NOTHING to them: the
    # crossfade gives each slot an explicit intensity, and an explicit child
    # intensity replaces the holder's. Without these in hideLive(), running the
    # live feed and then playing a stored event draws BOTH magnetospheres - which
    # is exactly what "duplicate magnetosheath and shock" looked like on the dome
    # (2026-09-16: holder at 0, MagnetoLiveMagnetopauseShellB still at 100).
    shell_slots = [f"{live_holder(name)[:-len('Holder')]}{tag}"
                   for name, _f, _w, kind in LIVE_LAYERS if kind == "mesh"
                   for tag in ("A", "B")]
    slots = ", ".join(f"'{n}'" for n in shell_slots)
    # Every EVENT-side object any package may have built: the per-layer holders (their
    # children are the frame objects and the prototype), the mesh crossfade slots and the
    # readout. One list, so teardownEvents() needs no per-event knowledge.
    event_holders = [event_holder(k) for k in DS_EVENT_HOLDER_LAYERS]
    event_slots = [DS_EVENT_SHELL_SLOT_FMT.format(_obj_token(name), tag)
                   for name, _f, _w, kind in LIVE_LAYERS if kind == "mesh"
                   for tag in ("A", "B", "C")]
    ev_holders = ", ".join(f"'{n}'" for n in event_holders)
    ev_slots = ", ".join(f"'{n}'" for n in event_slots)
    ev_hud = ", ".join(f"'{n}'" for n in hud_object_names(DS_HUD_EVENT))
    js = [
        "// {[2026.0.3.3]}",
        f"// {DS_LIVE_JS_NAME}  -  generated by dsexport/ds.py",
        "// Existence-checked helpers: a .ds line naming a missing object is dropped at parse",
        "// time and counted as an error; these functions only touch objects that exist.",
        f"var LIVE = [{live}];",
        f"var LIVE_INTEN = {{{inten}}};",
        "// Shell crossfade slots: they carry an EXPLICIT intensity, so fading their",
        "// holder leaves them drawing. hideLive() has to name them.",
        f"var LIVE_SHELL_SLOTS = [{slots}];",
        "function exists(n) { try { return Ds.GetObjectID(n) != -1; } catch (e) { return false; } }",
        "function cmd(s) { Ds.SendStringCommand(s); }",
        "// fade every named object that exists: fade('A,B,C', 0, 3)",
        "function fade(namesCsv, inten, dur) {",
        "    var names = namesCsv.split(','), n = 0;",
        "    for (var i = 0; i < names.length; i++) { var x = names[i].replace(/^\\s+|\\s+$/g, ''); if (x && exists(x)) { cmd(x + ' intensity ' + inten + ' duration ' + dur); n++; } }",
        "    return n;",
        "}",
        "function deleteIfExists(namesCsv) {",
        "    var names = namesCsv.split(','), n = 0;",
        "    for (var i = 0; i < names.length; i++) { var x = names[i].replace(/^\\s+|\\s+$/g, ''); if (x && exists(x)) { cmd(x + ' delete'); n++; } }",
        "    return n;",
        "}",
        "function hideLive() {",
        f"    var n = fade(LIVE.join(','), 0, {DS_HOLDER_OUT_SEC:g});",
        "    // The shells need their own line: an explicit child intensity replaces",
        "    // the holder's, so the fade above never reaches them and the live",
        "    // magnetopause and bow shock stay on the dome behind a stored event.",
        f"    var m = fade(LIVE_SHELL_SLOTS.join(','), 0, {DS_HOLDER_OUT_SEC:g});",
        f"    print('{DS_LIVE_JS_NAME}: hid ' + n + ' holder(s) and ' + m + ' shell slot(s)');",
        "}",
        "function showLive() {",
        "    var n = 0;",
        f"    for (var i = 0; i < LIVE.length; i++) {{ if (exists(LIVE[i])) {{ cmd(LIVE[i] + ' intensity ' + LIVE_INTEN[LIVE[i]] + ' duration {DS_HOLDER_FADE_SEC:g}'); n++; }} }}",
        f"    print('{DS_LIVE_JS_NAME}: raised ' + n + ' live holder(s)');",
        "}",
        "// ---- one source on the dome at a time (2026-09-21) ----",
        "// Ds.GetObjectChildNames() has returned an array here, but list attributes have come",
        "// back as strings elsewhere - accept both.",
        "function namesOf(v) {",
        "    if (v === null || v === undefined) { return []; }",
        "    if (typeof v === 'string') { var parts = v.split(/[\\s,]+/), out = []; for (var i = 0; i < parts.length; i++) { if (parts[i]) { out.push(parts[i]); } } return out; }",
        "    return v;",
        "}",
        "// Is the live loader (MagnetoLiveLoader.js) running? Running scripts are the 'js' object's",
        "// children; their 'script' attribute names the file (the loader's own alreadyRunning idiom).",
        "function loaderRunning() {",
        "    try {",
        "        var kids = namesOf(Ds.GetObjectChildNames('js'));",
        "        for (var i = 0; i < kids.length; i++) {",
        "            var s = ''; try { s = String(Ds.GetObjectAttr(kids[i], 'script')); } catch (e) { }",
        f"            if (s.indexOf('{DS_LIVE_LOADER_JS_NAME[:-3]}') >= 0) {{ return true; }}",
        "        }",
        "    } catch (e) { }",
        "    return false;",
        "}",
        "// An event START calls this first. The loader's own shutdown() fades and DELETES every",
        "// live object and holder, so nothing live can re-light under the storm (its turnOver()",
        "// gave the mesh slots an explicit intensity 100 one cycle after hideLive()'s fade - the",
        "// live shells at TODAY's Sun direction over a 2024 storm, 2026-09-21). The daemon keeps",
        "// polling; pressing Live Feed brings the layers back within one poll.",
        "function stopLoader() {",
        f"    if (loaderRunning()) {{ cmd('js message {DS_LIVE_LOADER_JS_NAME[:-3]}>stop'); print('{DS_LIVE_JS_NAME}: loader asked to stop'); return 1; }}",
        f"    print('{DS_LIVE_JS_NAME}: loader not running');",
        "    return 0;",
        "}",
        "// Delete every EVENT-side object whatever package built it. All storms share one",
        "// MagnetoEvent<Layer>NNNN namespace, so six per-event teardownEvent() calls racing over",
        "// it logged ~1,880 'object does not exist' errors per Clear Events; one holder-driven",
        "// pass has nothing to race. Frame objects (and the prototype) are holder children.",
        f"var EVENT_HOLDERS = [{ev_holders}];",
        f"var EVENT_SLOTS = [{ev_slots}];",
        f"var EVENT_HUD = [{ev_hud}];",
        "function teardownEvents() {",
        "    var n = 0, i, j;",
        "    for (i = 0; i < EVENT_HOLDERS.length; i++) {",
        "        var h = EVENT_HOLDERS[i];",
        "        if (!exists(h)) { continue; }",
        "        var kids = [];",
        "        try { kids = namesOf(Ds.GetObjectChildNames(h)); } catch (e) { kids = []; }",
        "        for (j = 0; j < kids.length; j++) { if (exists(kids[j])) { cmd(kids[j] + ' delete'); n++; } }",
        "        cmd(h + ' delete'); n++;",
        "    }",
        "    for (i = 0; i < EVENT_SLOTS.length; i++) { if (exists(EVENT_SLOTS[i])) { cmd(EVENT_SLOTS[i] + ' delete'); n++; } }",
        "    for (i = 0; i < EVENT_HUD.length; i++) { if (exists(EVENT_HUD[i])) { cmd(EVENT_HUD[i] + ' delete'); n++; } }",
        f"    print('{DS_LIVE_JS_NAME}: teardownEvents deleted ' + n + ' object(s)');",
        "    return n;",
        "}",
        "// A state probe for the bridge and the click-through log: what is REALLY on the dome.",
        "// extraCsv names further objects (the panel toggles) whose 'status' is read back.",
        "function report(extraCsv) {",
        "    var out = ['loader ' + (loaderRunning() ? 'RUNNING' : 'stopped')], i;",
        "    for (i = 0; i < LIVE.length; i++) { if (exists(LIVE[i])) { var iv = ''; try { iv = String(Ds.GetObjectAttr(LIVE[i], 'intensity')); } catch (e) { } out.push(LIVE[i] + ' intensity ' + iv); } }",
        "    for (i = 0; i < LIVE_SHELL_SLOTS.length; i++) { if (exists(LIVE_SHELL_SLOTS[i])) { var sv = ''; try { sv = String(Ds.GetObjectAttr(LIVE_SHELL_SLOTS[i], 'intensity')); } catch (e) { } out.push(LIVE_SHELL_SLOTS[i] + ' intensity ' + sv); } }",
        "    for (i = 0; i < EVENT_HOLDERS.length; i++) { if (exists(EVENT_HOLDERS[i])) { out.push(EVENT_HOLDERS[i] + ' present'); } }",
        "    var extra = extraCsv ? String(extraCsv).split(',') : [];",
        "    for (i = 0; i < extra.length; i++) { var x = extra[i].replace(/^\\s+|\\s+$/g, ''); if (x && exists(x)) { var st = ''; try { st = String(Ds.GetObjectAttr(x, 'status')); } catch (e) { } out.push(x + ' status ' + st); } }",
        f"    for (i = 0; i < out.length; i++) {{ print('{DS_LIVE_JS_NAME}: report ' + out[i]); }}",
        "    return out.length;",
        "}",
    ]
    return _write(Path(scripts_dir) / DS_LIVE_JS_NAME, [ln + "\n" for ln in js])


# --- the live feed's dome-side loader (Stream C6, 2026-09-14) ---------------------

DS_LIVE_LOADER_JS_NAME = "MagnetoLiveLoader.js"
DS_LIVE_TICK_S = 0.25          # wind frame step on the dome
DS_LIVE_POLL_S = 5.0           # manifest poll
DS_LIVE_MORPH_S = 45.0         # lines/dots glide from the previous pose to the new one
DS_LIVE_SHELL_FADE_S = 8.0     # shells crossfade between two slots
DS_LIVE_BANNER_EVERY_S = 300.0
DS_LIVE_MAX_MISSES = 3         # manifest unreadable this many polls -> STALLED on the HUD
DS_LIVE_DELETE_LAG_S = 4.0     # an old object is deleted this long after it was hidden
DS_LIVE_STALL_S = 135.0        # manifest not refreshed for this long -> STALLED (2 cycles + margin)


def write_live_loader_js(scripts_dir, layers=None, preset=None) -> Path:
    r"""Write MagnetoLiveLoader.js - the executable half of the live-holder contract.

    The daemon publishes every render into its own Renders\live\Models\c<cycle>\
    directory and merges a per-layer map into live_manifest.json (dsexport.publish
    .publish_cycle + magneto.daemon). This script, played from the PACKAGE
    Scripts\ folder ('js play ...MagnetoLiveLoader.js | run()'), polls that
    manifest and, when a layer's cycle changes, loads the new file(s) into a NEW
    object and swaps:

      lines/dots  new object at morph 0 (= the previous pose, the parametric D
                  rows) replaces the old one instantly, then morphs to 100 over
                  DS_LIVE_MORPH_S - the "morph 100 duration 60" contract
      shells      two slots per surface, crossfaded over DS_LIVE_SHELL_FADE_S
      wind        the new cycle's frames are declared, then stepped one per
                  DS_LIVE_TICK_S (morph 0 -> 100 per frame, the event idiom)

    Every object is a child of MagnetoLive<Layer>Holder (created here, 'Earth add'
    + standardSpace on - the j2000 contract), so the panel's layer toggles, style
    sliders and MagnetoShells.js reach the live layers exactly as they reach an
    event. MagnetoLiveHUD carries the manifest's 'hud' string (data age on the
    dome); STALLED / STOPPED are written there when the feed is not alive.
    'js message MagnetoLiveLoader>stop' (or js stop) ends the loop and clears
    every live object. Paths are derived from the script's own location - no
    drive letters, no tokens.
    """
    # Default is the DRAWN set: with DS_DRAW_SHELLS off the loader never declares a
    # shell slot, so the live feed carries the same layers as the stored packages.
    layers = LIVE_LAYERS if layers is None else layers
    layers = drawn_layers(layers)
    rows = []
    for name, filename, _w, kind in layers:
        rows.append(f"    {{ name: '{name}', kind: '{kind}', file: '{filename}' }}")
    inten = ", ".join(f"'{name}': {holder_intensity(name):g}" for name, _f, _w, _k in layers)
    colors = []
    for name, _f, _w, kind in layers:
        rgb = holder_color(name, preset) if kind == "mesh" else None
        if rgb:
            colors.append(f"'{name}': '{rgb[0]:g} {rgb[1]:g} {rgb[2]:g}'")
    abi = ", ".join(f"'{name}': {shell_abi(name, preset):g}" for name, _f, _w, k in layers if k == "mesh")
    dot = ", ".join(f"'{name}': {DS_DOT_SCALE_BY_LAYER.get(name, DS_DOT_SCALE):g}"
                    for name, _f, _w, k in layers if k == "dots")
    js = f"""// {{[2026.0.3.3]}}
// {DS_LIVE_LOADER_JS_NAME}  -  generated by dsexport/ds.py (Stream C6)
// The dome-side half of the LIVE FEED. Polls <package>\\Renders\\live\\Models\\{DS_LIVE_MANIFEST_NAME}
// and turns each layer over to its new cycle directory when the daemon publishes one:
//   lines/dots: new object at morph 0 (previous pose) swaps in, morphs to 100 over {DS_LIVE_MORPH_S:g} s
//   shells:     two slots, crossfade {DS_LIVE_SHELL_FADE_S:g} s      wind: frames stepped every {DS_LIVE_TICK_S:g} s
// Start:  js play <pkg>\\Scripts\\{DS_LIVE_LOADER_JS_NAME} | run()      Stop: js message MagnetoLiveLoader>stop
// Per-cycle directories mean Digistar never re-reads a filename it already cooked.
var TICK_S = {DS_LIVE_TICK_S:g}, POLL_S = {DS_LIVE_POLL_S:g}, MORPH_S = {DS_LIVE_MORPH_S:g}, SHELL_FADE_S = {DS_LIVE_SHELL_FADE_S:g};
var BANNER_EVERY_S = {DS_LIVE_BANNER_EVERY_S:g}, MAX_MISSES = {DS_LIVE_MAX_MISSES}, DELETE_LAG_S = {DS_LIVE_DELETE_LAG_S:g};
var STALL_S = {DS_LIVE_STALL_S:g};
var LAYERS = [
{",".join(chr(10) + r for r in rows)}
];
var HOLDER_INTEN = {{{inten}}};
var HOLDER_COLOR = {{{", ".join(colors)}}};
var SHELL_ABI = {{{abi}}};
var DOT_SCALE = {{{dot}}};
var LINE_WIDTH = {DS_LINE_WIDTH:g};
var HUD = '{DS_HUD_LIVE}', HUD_INTEN = {DS_HUD_INTEN:g}, HUD_N = {DS_HUD_SEGMENTS};
var HUD_AZ = [{', '.join(f'{a:g}' for a in hud_slot_azimuths(DS_HUD_SLOT_CHARS_LIVE))}];
var PREFIX = 'MagnetoLive';

function scriptDir() {{ return Ds.GetCurrentScriptPath().replace(Ds.GetCurrentScriptName() + '.js', ''); }}
function pkgDir() {{ return scriptDir().replace(/Scripts\\\\$/, ''); }}
function liveModels() {{ return pkgDir() + 'Renders\\\\live\\\\Models\\\\'; }}
// The folder as the GPs are SENT it. GetCurrentScriptPath() is the HOST's drive path, and a GP
// resolves a drive path against its own disk, where the per-cycle folders never exist (David,
// 2026-10-12: on a real dome the live feed showed on the host only, the GPs could not find the
// models). Ds.UnresolvePath turns it back into $Content\\..., which a GP resolves locally or
// fetches from the host's CXContent share (Site\\NetConfig.xml ShareAddress) - the form the storms'
// relative ..\\Models paths already reach the GPs in. Host-side file reads keep liveModels().
var DOME_MODELS = null;
function domeModels() {{
    if (DOME_MODELS === null) {{
        var p = liveModels(), u = '';
        try {{ u = String(Ds.UnresolvePath(p) || ''); }} catch (e) {{ u = ''; }}
        if (u.charAt(0) != '$') {{
            var i = p.toLowerCase().indexOf('\\\\user\\\\');
            u = i > 0 ? '$Content' + p.substring(i) : p;
        }}
        DOME_MODELS = u;
        print('{DS_LIVE_LOADER_JS_NAME}: models are sent to the GPs as ' + DOME_MODELS);
    }}
    return DOME_MODELS;
}}
function exists(p) {{ try {{ return Ds.FileAccess(p, 0) == 0; }} catch (e) {{ return false; }} }}
function readAll(p) {{ try {{ return Ds.FileReadAllText(p, 'r'); }} catch (e) {{ return ''; }} }}
function obj(n) {{ try {{ return Ds.GetObjectID(n) != -1; }} catch (e) {{ return false; }} }}
function cmd(s) {{ Ds.SendStringCommand(s); }}
function now() {{ return new Date().getTime() / 1000.0; }}
// 'YYYY-MM-DDTHH:MM:SSZ' -> seconds since, or null when unparseable
function isoAge(iso) {{
    var m = /^(\\d{{4}})-(\\d{{2}})-(\\d{{2}})T(\\d{{2}}):(\\d{{2}}):(\\d{{2}})/.exec(String(iso || ''));
    if (!m) {{ return null; }}
    var t = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]), Number(m[6])) / 1000.0;
    return now() - t;
}}
function rel(s) {{
    s = Math.max(0, Math.round(s));
    if (s < 60) {{ return '<1m'; }}
    if (s < 3600) {{ return Math.floor(s / 60) + 'm'; }}
    var mm = Math.floor((s % 3600) / 60);
    return Math.floor(s / 3600) + 'h' + (mm < 10 ? '0' : '') + mm + 'm';
}}
function say(text) {{
    if (text.length > 255) {{ text = text.substring(0, 252) + '...'; }}
    cmd('system message "' + text.replace(/"/g, "'") + '"');
}}
function holder(name) {{ return PREFIX + name + 'Holder'; }}

function alreadyRunning() {{
    try {{
        Ds.Wait(0.1, 'system');
        var me = Ds.GetCurrentScriptName(), n = 0;
        var kids = Ds.GetObjectChildNames('js');
        for (var i = 0; i < kids.length; i++) {{
            var s = '';
            try {{ s = String(Ds.GetObjectAttr(kids[i], 'script')); }} catch (e) {{ }}
            if (s.indexOf(me) >= 0) {{ n++; }}
        }}
        return n > 1;
    }} catch (e) {{ return false; }}
}}

// ---- holders + HUD (the j2000 contract: Earth's position, not its rotation) ----
function ensureHolders() {{
    for (var i = 0; i < LAYERS.length; i++) {{
        var h = holder(LAYERS[i].name);
        if (obj(h)) {{ continue; }}
        cmd(h + ' is emptyClass');
        cmd('Earth add ' + h);
        cmd(h + ' standardSpace on');
        if (HOLDER_COLOR[LAYERS[i].name]) {{ cmd(h + ' color ' + HOLDER_COLOR[LAYERS[i].name]); }}
        cmd(h + ' intensity ' + HOLDER_INTEN[LAYERS[i].name]);
    }}
}}
// The readout: one emptyClass fixed on the dome + six centred textClass segments along its
// base (same layout as the event players, live slot widths). hud('a|b|c|d|e|f') re-texts
// only the segments that changed; hudTail(x) replaces the last one (feed status).
function ensureHud() {{
    // One readout at a time. An event player owns {DS_HUD_EVENT}; if it is still on the
    // dome its six segments sit at the EVENT slot azimuths, a few degrees off ours, and the
    // two lines interleave at the dome base (the "text overlaid on itself" report).
    for (var e = 0; e < HUD_N; e++) {{ if (obj('{DS_HUD_EVENT}' + e)) {{ cmd('{DS_HUD_EVENT}' + e + ' intensity 0'); }} }}
    if (obj('{DS_HUD_EVENT}')) {{ cmd('{DS_HUD_EVENT}' + ' intensity 0 duration 0.5'); }}
    lastSegs = [];          // a re-created readout must be re-texted from scratch
    if (obj(HUD)) {{ return; }}
    cmd(HUD + ' is emptyClass');
    cmd(HUD + ' intensity 0');
    cmd('scene add ' + HUD + ' fixed');
    for (var i = 0; i < HUD_N; i++) {{
        var c = HUD + i;
        cmd(c + ' is {DS_HUD_CLASS}');
        cmd(c + ' resolution "high"');
        cmd(c + ' origin "center"');
        cmd(c + ' alignment "center"');
        cmd(c + ' textScale {DS_HUD_SCALE:g} {DS_HUD_SCALE:g}');
        cmd(c + ' color {DS_HUD_COLOR[0]:g} {DS_HUD_COLOR[1]:g} {DS_HUD_COLOR[2]:g}');
        cmd(c + ' position spherical ' + HUD_AZ[i] + ' {DS_HUD_EL_DEG:g} {DS_HUD_DIST_M:g}');
        cmd(c + ' intensity inherited');
        cmd(HUD + ' add ' + c);
        cmd(c + ' text ""');
    }}
    cmd(HUD + (HUD_N - 1) + ' text "LIVE FEED starting"');
    cmd(HUD + ' intensity ' + HUD_INTEN + ' duration 1');
}}
var lastSegs = [];
var HUD_SLOT = [{', '.join(str(c) for c in DS_HUD_SLOT_CHARS_LIVE)}];
function hud(text) {{
    var segs = String(text).split('|');
    for (var i = 0; i < HUD_N; i++) {{
        var s = String(segs[i] || '').replace(/"/g, "'").replace(/;/g, ',').replace(/^\\s+|\\s+$/g, '');
        // fixed-width slots: a longer value would overlap the next segment
        if (s.length > HUD_SLOT[i]) {{ s = s.substring(0, Math.max(1, HUD_SLOT[i] - 1)) + '.'; }}
        if (s != lastSegs[i]) {{
            lastSegs[i] = s;
            if (obj(HUD + i)) {{ cmd(HUD + i + ' text "' + s + '"'); }}
        }}
    }}
}}
function hudTail(text) {{
    var segs = [];
    for (var i = 0; i < HUD_N - 1; i++) {{ segs.push(lastSegs[i] || ''); }}
    segs.push(text);
    hud(segs.join('|'));
}}

// ---- declaring objects: the same class blocks the event players emit ----
// The panel's Style Modifiers write one emptyClass proxy per knob (MagnetoStyle.js
// KNOBS). The loader re-DECLARES every object on each new cycle, so if it used the
// render's baked-in defaults here, the next cycle would quietly undo whatever the
// operator had just applied with Update Styles - about sixty seconds later, which reads
// as "the sliders do not work on the live feed". So each new object is born with the
// operator's current value, and the baked default is only the fallback.
//
// 'scale' is a COMPOUND attribute: JavaScript gets {{x, y, z, units}}, so read .x.
var STYLE_PROXY = {{ WindParticles: 'magnetoWindDot', FieldPulses: 'magnetoPulseDot',
                    FieldLines: 'magnetoFieldWidth', IMFLines: 'magnetoImfWidth' }};
function styleFor(layer, fallback) {{
    var p = STYLE_PROXY[layer];
    if (!p) {{ return fallback; }}
    try {{
        var raw = Ds.GetObjectAttr(p, 'scale');
        var v = (raw !== null && typeof raw === 'object') ? Number(raw.x) : Number(raw);
        return (isNaN(v) || v <= 0) ? fallback : v;
    }} catch (e) {{ return fallback; }}
}}

function declare(name, layer, kind, path) {{
    if (kind == 'mesh') {{
        cmd(name + ' is {DS_MESH_CLASS}');
        cmd(name + ' effectMode "angleBasedIntensity"');
        cmd(name + ' abiMode "fadeCenter"');
        cmd(name + ' abiThickness ' + SHELL_ABI[layer]);
        cmd(name + ' blendMode "add"');
        cmd(name + ' cullMode "none"');
        cmd(name + ' tessellation 0');
        cmd(name + ' scale 1 ly');
    }} else if (kind == 'dots') {{
        cmd(name + ' is {DS_DOT_CLASS}');
        cmd(name + ' DIIMorph off');
        cmd(name + ' morph 0');
        cmd(name + ' depthCue off');
        cmd(name + ' blendMode "{DS_DOT_BLEND}"');
        cmd(name + ' dotScale ' + styleFor(layer, DOT_SCALE[layer]));
    }} else {{
        cmd(name + ' is {DS_LINE_CLASS}');
        cmd(name + ' DIIMorph off');
        cmd(name + ' morph 0');
        cmd(name + ' depthCue off');
        cmd(name + ' roundness 100');
        cmd(name + ' blend on');
        cmd(name + ' width ' + styleFor(layer, LINE_WIDTH));
        cmd(name + ' useWidth on');
        cmd(name + ' gap 1');
    }}
    cmd(name + ' model ' + path);
    cmd(name + ' intensity 0');
    cmd(holder(layer) + ' add ' + name);
}}

// ---- state ----
var current = {{}};          // layer name -> {{ cycle, objs: [names], slot }}
var windSet = null;         // {{ names: [...], k }}
var pendingDelete = [];     // {{ name, at }}
var created = {{}};          // every object this loader declared
function laterDelete(name) {{ pendingDelete.push({{ name: name, at: now() + DELETE_LAG_S }}); }}
function processDeletes() {{
    var t = now(), keep = [];
    for (var i = 0; i < pendingDelete.length; i++) {{
        var d = pendingDelete[i];
        if (d.at <= t) {{ if (obj(d.name)) {{ cmd(d.name + ' delete'); }} delete created[d.name]; }}
        else {{ keep.push(d); }}
    }}
    pendingDelete = keep;
}}

function turnOver(layer, entry) {{
    var dir = domeModels() + entry.dir + '\\\\';      // every use below is a `model` command
    var files = entry.files || [layer.file];
    var cur = current[layer.name] || {{ cycle: -1, objs: [], slot: 0 }};
    if (layer.kind == 'mesh') {{
        var slots = [PREFIX + layer.name + 'A', PREFIX + layer.name + 'B'];
        var idle = slots[1 - cur.slot], active = cur.objs.length ? cur.objs[0] : null;
        if (!obj(idle)) {{ declare(idle, layer.name, 'mesh', dir + files[0]); created[idle] = 1; }}
        else {{ cmd(idle + ' model ' + dir + files[0]); }}
        Ds.Wait(0.5, 'system');
        cmd(idle + ' intensity 100 duration ' + SHELL_FADE_S);
        if (active) {{ cmd(active + ' intensity 0 duration ' + SHELL_FADE_S); }}
        current[layer.name] = {{ cycle: entry.cycle, objs: [idle], slot: 1 - cur.slot }};
    }} else if (layer.name == 'WindParticles') {{
        var names = [];
        for (var k = 0; k < files.length; k++) {{
            var n = PREFIX + 'WindParticlesC' + entry.cycle + '_' + k;
            declare(n, layer.name, 'dots', dir + files[k]);
            created[n] = 1;
            names.push(n);
        }}
        Ds.Wait(1.5, 'system');
        var old = windSet;
        windSet = {{ names: names, k: 0 }};
        if (old) {{ for (var j = 0; j < old.names.length; j++) {{ cmd(old.names[j] + ' intensity 0'); laterDelete(old.names[j]); }} }}
        current[layer.name] = {{ cycle: entry.cycle, objs: names, slot: 0 }};
    }} else {{
        var name = PREFIX + layer.name + 'C' + entry.cycle;
        declare(name, layer.name, layer.kind, dir + files[0]);
        created[name] = 1;
        Ds.Wait(0.8, 'system');
        cmd(name + ' morph 0');
        cmd(name + ' intensity inherited');
        cmd(name + ' morph 100 duration ' + MORPH_S);
        for (var m = 0; m < cur.objs.length; m++) {{ cmd(cur.objs[m] + ' intensity 0'); laterDelete(cur.objs[m]); }}
        current[layer.name] = {{ cycle: entry.cycle, objs: [name], slot: 0 }};
    }}
    print('{DS_LIVE_LOADER_JS_NAME}: ' + layer.name + ' -> cycle ' + entry.cycle + ' (' + entry.dir + ')');
}}

function stepWind() {{
    if (!windSet || !windSet.names.length) {{ return; }}
    var n = windSet.names.length, k = windSet.k;
    var cur = windSet.names[k], prev = windSet.names[(k + n - 1) % n];
    // Something else (a teardown, a Fade-Stop-Reset, a second loader) may have deleted the
    // frames: stepping ghosts at 4 Hz logged "Cannot set morph attribute for deleted object"
    // for minutes on 2026-09-21. Drop the set and wait for the next cycle instead.
    if (!obj(cur)) {{ windSet = null; print('{DS_LIVE_LOADER_JS_NAME}: wind frames gone - waiting for the next cycle'); return; }}
    cmd(cur + ' morph 0');
    cmd(cur + ' intensity inherited');
    cmd(cur + ' morph 100 duration ' + TICK_S);
    if (prev != cur) {{ cmd(prev + ' intensity 0'); }}
    windSet.k = (k + 1) % n;
}}

var misses = 0, lastBanner = 0, lastTier = '', stopped = false;
// A 'stopped' marker in the manifest is only OURS once this loader has seen the feed
// actually running. Before that it is the previous daemon's epitaph, still on disk
// because a restart cannot rewrite it before we first poll (the daemon's parent has
// to import the engine first). Reporting it made pressing Start announce
// 'stopped by operator' about four seconds later - 2026-09-16.
var sawNewCycle = false, startedAt = 0, STARTUP_GRACE_S = 120;
var pinned = null, lastStallSay = 0;
// The daemon's EVENT-tier fallback (stale cache -> a stored storm stands in) publishes
// scene_date so the dome clock follows the replay - the frames are baked sun-fixed at
// their own epochs, and a 2024 storm against today's Sun is a 122-degree lie. Released
// with 'scene date now' the moment the feed is live again, and on shutdown.
function pinSceneDate(m) {{
    var want = (m.mode == 'EVENT' && m.scene_date) ? String(m.scene_date) : null;
    if (want && want != pinned) {{ cmd('scene date ' + want + (pinned ? ' duration 55' : '')); pinned = want; }}
    else if (!want && pinned) {{ cmd('scene date now'); pinned = null; }}
}}
function poll() {{
    var text = readAll(liveModels() + '{DS_LIVE_MANIFEST_NAME}');
    var m = null;
    try {{ m = text ? JSON.parse(text) : null; }} catch (e) {{ m = null; }}
    if (!m || !m.layers) {{
        misses++;
        if (misses >= MAX_MISSES) {{ hudTail('LIVE FEED STALLED ' + Math.round(misses * POLL_S) + 's'); }}
        return;
    }}
    misses = 0;
    if (m.stopped) {{
        if (!sawNewCycle) {{
            // the previous run's marker: we have never seen this feed live, so this says
            // nothing about the start now in progress
            if (now() - startedAt > STARTUP_GRACE_S) {{ hudTail('LIVE FEED NOT STARTING'); }}
            return;
        }}
        if (!stopped) {{ stopped = true; hudTail('LIVE FEED STOPPED'); say('Live feed stopped: ' + (m.stopped_why || '')); }}
        return;
    }}
    stopped = false;
    for (var i = 0; i < LAYERS.length; i++) {{
        var e = m.layers[LAYERS[i].name];
        if (!e || !e.dir) {{ continue; }}
        var cur = current[LAYERS[i].name];
        if (!cur || cur.cycle != e.cycle) {{ turnOver(LAYERS[i], e); sawNewCycle = true; }}
    }}
    pinSceneDate(m);
    var t = now();
    // A manifest that is readable but no longer ADVANCING is a dead daemon (2026-09-21:
    // pid 20380 vanished mid-sleep; the dome kept saying 'LIVE 8m old'). The layers stay
    // up - never blank - but the readout must say so, with an age that keeps growing.
    var stall = isoAge(m.refreshed || m.generated);
    // ...but not in the first STARTUP_GRACE_S: a stale manifest then is the PREVIOUS run's, and
    // the daemon just started is still importing the engine. Without this, every Live Feed
    // press after more than STALL_S off said 'STALLED 3h15m - press off, then on' six seconds
    // in, and the daemon's first update landed 16 s later (dome, 2026-10-07).
    if (stall !== null && stall > STALL_S && now() - startedAt <= STARTUP_GRACE_S) {{
        hudTail('LIVE FEED STARTING');
        return;
    }}
    if (stall !== null && stall > STALL_S) {{
        hudTail('STALLED ' + rel(stall) + ' - CACHED ' + rel(Number(m.age_s || 0) + stall));
        if (t - lastStallSay > BANNER_EVERY_S) {{ say('Live feed STALLED: no daemon update for ' + rel(stall) + ' - press Live Space Weather Feed off, then on'); lastStallSay = t; }}
        return;
    }}
    if (m.hud) {{ hud(m.hud); }}
    if (m.banner && (t - lastBanner > BANNER_EVERY_S || m.tier != lastTier)) {{ say(m.banner); lastBanner = t; lastTier = m.tier; }}
}}

function shutdown() {{
    for (var i = 0; i < LAYERS.length; i++) {{ var h = holder(LAYERS[i].name); if (obj(h)) {{ cmd(h + ' intensity 0 duration 2'); }} }}
    if (obj(HUD)) {{ cmd(HUD + ' intensity 0 duration 2'); }}
    Ds.Wait(2.5, 'system');
    for (var n in created) {{ if (obj(n)) {{ cmd(n + ' delete'); }} }}
    for (var j = 0; j < LAYERS.length; j++) {{ var hh = holder(LAYERS[j].name); if (obj(hh)) {{ cmd(hh + ' delete'); }} }}
    for (var q = 0; q < HUD_N; q++) {{ if (obj(HUD + q)) {{ cmd(HUD + q + ' delete'); }} }}
    if (obj(HUD)) {{ cmd(HUD + ' delete'); }}
    if (pinned) {{ cmd('scene date now'); pinned = null; }}
    print('{DS_LIVE_LOADER_JS_NAME}: stopped, live objects cleared');
}}

function run() {{
    if (alreadyRunning()) {{ print('{DS_LIVE_LOADER_JS_NAME}: already running'); return; }}
    startedAt = now();
    sawNewCycle = false;
    ensureHolders();
    ensureHud();
    var pollTicks = Math.max(1, Math.round(POLL_S / TICK_S)), ticks = 0;
    while (true) {{
        var msg = '';
        try {{ msg = Ds.GetMessage(); }} catch (e) {{ msg = ''; }}
        if (msg == 'stop' || msg == 'exit') {{ break; }}
        // kickoff_watch.js's idiom: after a Fade-Stop-Reset every object is gone and every
        // command this loop would send is an error, so leave without shutdown().
        var fsr = false;
        try {{ fsr = (Ds.GetCommand() == 'fadestopreset'); }} catch (e) {{ fsr = false; }}
        if (fsr) {{ print('{DS_LIVE_LOADER_JS_NAME}: fadestopreset seen - exiting'); pinned = null; return; }}
        if (ticks % pollTicks == 0) {{ poll(); }}
        stepWind();
        processDeletes();
        Ds.Wait(TICK_S, 'system');
        ticks++;
    }}
    shutdown();
}}
"""
    return _write(Path(scripts_dir) / DS_LIVE_LOADER_JS_NAME,
                  [ln + "\n" for ln in js.splitlines()])


def write_override_scripts(events, scripts_dir,
                           model_prefix: str = DS_MODEL_REL_PREFIX,
                           holders=None) -> list[Path]:
    """Write event-<id>-on.ds / -off.ds per event, plus live-restore.ds and MagnetoLive.js.

    Deliberately THIN. Each script does only what a verified token can do:

      on      hide the live layer (through MagnetoLive.js, so nothing is named
              that may not exist) and announce the event; the panel plays the
              event player as its next command.
      off     fade the event overlay; the per-event teardown deletes it.
      restore fade + delete the event holders that exist, bring the live
              holders back up, restore the real-time clock.

    `events` is an iterable of event ids or of objects with an `.id`/`.title`
    (magneto.events.EventSpec works directly, as does EVENTS.values()).

    `holders` is the list of asset types the overlay actually built, so the
    'off' scripts name exactly the holders that exist. It defaults to every
    asset type this build knows - correct for the daemon, which writes these
    scripts for events it has not rendered yet.
    """
    scripts_dir = Path(scripts_dir)
    written: list[Path] = []
    # dedupe: two layers may share one holder (the shells), and the JS csv must name each once
    keys = list(dict.fromkeys(holders)) if holders else list(DS_EVENT_HOLDER_LAYERS)
    written.append(write_live_js(scripts_dir))

    for ev in events:
        eid = getattr(ev, "id", None) or str(ev)
        # `ev` may be a bare id string, whose .title is str.title - never that.
        title = getattr(ev, "title", None)
        if not isinstance(title, str):
            title = event_title(eid) or eid
        title = _ascii(title)
        player = DS_EVENT_PLAYER_FMT.format(eid)
        teardown = DS_EVENT_TEARDOWN_FMT.format(eid)
        holder_names = [event_holder(key) for key in keys] + hud_object_names(DS_HUD_EVENT)

        L: list[str] = []
        w = L.append
        w(f"# event-{eid}-on.ds  -  generated by dsexport/ds.py\n")
        w(f"# Presenter override: switch the dome from the live magnetosphere to\n")
        w(f"# the curated event {eid} ({title}).\n")
        w("#\n")
        L.extend(_override_note_lines())
        w("#\n")
        w("# The panel plays this file and then, 1 s later, the event player:\n")
        w(f"#     1. Scripts\\event-{eid}-on.ds      (this file - clean slate, hides live)\n")
        w(f"#     2. Scripts\\{player}   (the frame series)\n")
        w("# The 1 s gap lets the clean-slate deletes below land before the player\n")
        w("# declares the same object names again.\n")
        w(f"{VERIFY}  'js play .\\{DS_LIVE_JS_NAME}' relative to this script is verified from\n")
        w("# the panel package directory - confirm once from a published (rotated) tree.\n")
        w("\n")
        w("0:00\n")
        w("\t# Clean slate first. A teardown still running from a Remove pressed a few\n")
        w("\t# seconds ago would delete THIS event's freshly created objects ~7 s in\n")
        w("\t# (measured 2026-09-05), so stop it and the old player, then delete what\n")
        w("\t# they left - in JS, which names only objects that exist. 'script stop' on\n")
        w("\t# a script that is not running is a warning, not an error.\n")
        w(f"\tscript stop {Path(teardown).stem}\n")
        w(f"\tscript stop {Path(player).stem}\n")
        w(f"\tjs play .\\{player_js_name(eid)} | teardownEvent()\n")
        L.extend(js_name_calls(f".\\{DS_LIVE_JS_NAME}", 'deleteIfExists', holder_names))
        w("\n")
        w("\t# Fade the live layer out so the two never overlap. Done in JS so only\n")
        w("\t# holders that EXIST are named: a .ds line naming a missing object is\n")
        w("\t# dropped at parse time and counted as a script error.\n")
        w(f"\tjs play .\\{DS_LIVE_JS_NAME} | hideLive()\n")
        w("\n")
        w(f'\tsystem message "Curated event|{title}|Live layers off - press Live Space Weather Feed to return"\n')
        written.append(_write(scripts_dir / f"event-{eid}-on.ds", L))

        L = []
        w = L.append
        w(f"# event-{eid}-off.ds  -  generated by dsexport/ds.py\n")
        w(f"# Fades the {eid} overlay WITHOUT restoring the live layer.\n")
        w(f"# The panel plays Scripts\\{teardown} as well - that is the script that\n")
        w("# stops the player and deletes the pre-loaded frames.\n")
        w("# To go back to the live feed, press Live Feed on the panel (kickoff.js startLive()).\n")
        w("\n")
        w("# One line per asset-type holder. Comment out any line to leave that\n")
        w("# asset type up, or change 0 to a value to dim instead of hide.\n")
        w("\n")
        w("0:00\n")
        for key in keys:
            w(f"\t{event_holder(key)} intensity 0 "
              f"duration {DS_HOLDER_OUT_SEC:g}\n")
        written.append(_write(scripts_dir / f"event-{eid}-off.ds", L))

    L = []
    w = L.append
    w("# live-restore.ds  -  generated by dsexport/ds.py\n")
    w("# Presenter override: tear the event overlay down and bring the live\n")
    w("# magnetosphere back.\n")
    w("#\n")
    L.extend(_override_note_lines())
    w('# Write {"force": null} before running this, or the daemon will keep\n')
    w("# publishing event frames and the live layer will show a frozen state.\n")
    w("#\n")
    event_names = [event_holder(key) for key in keys] + hud_object_names(DS_HUD_EVENT)
    w("0:00\n")
    w("\t# Event overlay out: fade every event holder that exists (JS, so a holder\n")
    w("\t# that was never built is not named).\n")
    L.extend(js_name_calls(f".\\{DS_LIVE_JS_NAME}", 'fade', event_names, '0', f"{DS_HOLDER_OUT_SEC:g}"))
    w("\n")
    w(f"+{DS_TEARDOWN_WAIT_SEC:g}\n")
    w("\t# The frame objects belong to the per-event teardown, which knows how\n")
    w(f"\t# many were built ({DS_EVENT_TEARDOWN_FMT.format('<id>')}) - run it too if the memory matters.\n")
    L.extend(js_name_calls(f".\\{DS_LIVE_JS_NAME}", 'deleteIfExists', event_names))
    w("\t# Live layer back up (holders were only faded, never deleted).\n")
    w(f"\tjs play .\\{DS_LIVE_JS_NAME} | showLive()\n")
    w("\t# The event player pinned the scene date - back to real time.\n")
    w("\tscene date now\n")
    w("\n")
    w('\tsystem message "Live magnetosphere|Solar wind feed resumed"\n')
    written.append(_write(scripts_dir / "live-restore.ds", L))

    log.info("override scripts: %d files -> %s", len(written), scripts_dir)
    return written
