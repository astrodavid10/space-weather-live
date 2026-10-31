"""Shared constants + the single geopack-recalc gateway for Build A geometry.

geopack keeps its epoch state (rotation matrices, IGRF coefficients, dipole
tilt) in module-level globals and is NOT thread-safe. Two rules follow, both
enforced structurally here:

1. All model code runs on one thread.
2. geopack.recalc() is called ONLY through recalc_for_state()/recalc_for_epoch()
   below, so the epoch used for physics and the epoch used for GSM->ECEF frame
   baking can never drift apart within one export.

The mesh-topology constants live here because parametric (morphing) Digistar
exports pair rows positionally between two epochs: any consumer that builds a
boundary mesh or seed table must share these numbers or morphs tear apart.
"""
from __future__ import annotations

import math
from datetime import datetime

# Boundary-SHELL topology (2026-09-14: the wireframe rings are gone; these
# stations x azimuths build the effectModelClass OBJ shells). 52 x 72 = 5 deg
# facets. Dome probes showed Digistar's 'tessellation' attribute does NOT
# smooth a silhouette, so the mesh itself carries the roundness. Fixed so the
# two shells always share one topology.
N_THETA = 52                      # profile stations nose -> tail edge (incl. nose)
N_PHI = 72                        # azimuth samples per ring
THETA_MAX_RAD = math.radians(165.0)   # last flank angle from +X_GSM

# --- tail shape v2 (2026-09-05) ----------------------------------------------
# Shue-1998 and the Slavin-Holzer conic are fits to dayside / near-flank
# crossings and pure extrapolations down-tail: Shue flares without bound as
# theta -> 180 deg and the conic opens to a 30.5 deg half-angle (86 R_E wide
# at x = -60). Past a blend station each drawn surface follows the observed
# distant tail instead - the magnetopause relaxes to a near-cylinder of
# MP_TAIL_RADIUS_FACTOR x standoff (ISEE-3 distant tail, 25-30 R_E), the bow
# shock bends from the conic's slope into a BS_TAIL_SLOPE Mach cone - both C1
# at the blend through an exponential slope relaxation. The station count is
# still N_THETA, so the ring index stays the morph key.
THETA_BLEND_RAD = math.radians(120.0)   # magnetopause: Shue rays to here
N_HEAD = 32                             # stations on the Shue / conic head (incl. nose)
X_TAIL_RE = -200.0                      # drawn tail end of BOTH surfaces (GSM x, R_E)
MP_TAIL_RADIUS_FACTOR = 2.5             # asymptotic tail radius / Shue standoff
BS_BLEND_X_RE = 0.0                     # bow shock: conic to the terminator plane
BS_TAIL_SLOPE = math.tan(math.radians(8.0))   # distant-shock flare, d rho / d(-x)
BS_BLEND_LAMBDA_RE = 15.0               # slope relaxation scale (R_E)

# Bisection bracket/tolerance for boundary radii (R_E).
#
# The top of the bracket is not a modelling choice, it is a SEARCH bound: the
# Shue (1998) magnetopause is star-shaped about Earth, so every ray from the
# geocentre crosses it exactly once and the bisection only needs a bracket
# wide enough to contain that crossing. At 60 R_E it was not: the quiet-time
# Shue surface is already ~113 R_E at the mesh's last flank angle (165 deg),
# so the last four rings of the drawn magnetopause were sitting ON the bound -
# a spherical cap welded onto the tail. 200 R_E clears the drawn mesh at every
# angle it uses for quiet AND storm conditions (measured: 113 R_E quiet,
# 125 R_E at Pdyn 8 nPa), and still bounds the search where the Shue radius
# genuinely diverges, at theta -> 180 deg, which the mesh never reaches.
#
# Nothing here bounds the BOW SHOCK mesh any more: the Slavin & Holzer conic
# is a hyperbola whose asymptote sits at theta = 149.5 deg, so no ray-and-radius
# parameterization can describe its tail at ANY bracket. The drawn shock is
# parameterized by x station instead (boundaries.bow_shock_mesh); the bracket
# still clamps boundaries.bow_shock_radius(), which survives only as the
# theta-keyed lookup the wind's obstacle table interpolates.
R_BISECT_LO_RE = 3.0
R_BISECT_HI_RE = 200.0
BISECT_TOL_RE = 1e-3


def epoch_to_ut(epoch_iso: str) -> float:
    """ISO-8601 'Z' epoch string -> unix UTC seconds (geopack.recalc input)."""
    return datetime.fromisoformat(epoch_iso.replace("Z", "+00:00")).timestamp()


def recalc_for_epoch(epoch_iso: str) -> float:
    """THE one geopack.recalc call site in Build A. Returns dipole tilt (rad).

    Default solar-wind velocity is kept so geopack's GSW frame stays exactly
    GSM - all our physics and frame math is plain GSM.

    Also records the epoch with magneto.geo: the J2000 export frame needs the
    date for its precession term, and geopack does not expose the time it was
    last recalc'd for. Recording it here - the single gateway - is what keeps
    the physics epoch and the frame-baking epoch from ever drifting apart.
    """
    from geopack import geopack as gp

    from ..geo import note_epoch_ut

    ut = epoch_to_ut(epoch_iso)
    note_epoch_ut(ut)
    return gp.recalc(ut)


def recalc_for_state(state: dict) -> float:
    """recalc geopack for a state object's measurement epoch (see STATE_SCHEMA)."""
    return recalc_for_epoch(state["epoch"])


# --- WHAT PHYSICS BUILT THIS ----------------------------------------------------
# Bumped whenever the DRAWN GEOMETRY's physics changes - a different magnetopause
# model, a different bow-shock construction, a different coordinate convention.
# It is NOT a code version: a refactor that leaves the numbers alone does not bump it.
#
#   1  Shue et al. (1998) magnetopause, axisymmetric about GSM +X, evaluated by
#      bisection on geopack's own inside/outside flag; bow shock an INDEPENDENT
#      Slavin & Holzer conic anchored only at the nose. Everything built before
#      2026-09-16.
#
# A render whose manifest carries no "physics" key is version 1 BY DEFINITION -
# which is exactly how every pre-existing package identifies itself without
# needing to be touched.
# 2 (2026-09-16): Lin et al. 2010 3-D magnetopause on a hybrid subsolar
# distance, evaluated in aberrated GSM, with the bow shock following it.
# 3 (2026-09-16): solar-wind parcels now cross a real Rankine-Hugoniot shock,
# and two index-driven layers joined the export - the ring current (energy from
# SYM-H via Dessler-Parker-Sckopke) and the plasmasphere (plasmapause radius from
# Carpenter & Anderson 1992). The wind change moves every parcel, so this is a
# geometry change and has to bump the version even though the boundaries did not.
# 4 (2026-09-16): interplanetary field lines draping over the magnetosphere, with
# Kan & Lee (1979) reconnection recolouring where the merging rate is non-zero.
# 5 (2026-09-20): open field lines are clipped against the DRAWN magnetopause
# instead of the analytic one. The analytic radius flares without bound as theta
# approaches pi while the drawn tail is relaxed to a near-cylinder, so the old clip
# tested a boundary up to 21x looser than the visible shell and let open lines run
# outside it down the tail. This MOVES TRACED VERTICES, so it is a geometry change
# and must bump the version even though no model was swapped: a package built
# before it and one built after it draw different field lines from the same data,
# which is exactly what this number exists to tell apart.
# 6 (2026-09-21): the clip's boundary became the drawn surface RESOLVED IN AZIMUTH.
# v5 clipped against a theta profile of the drawn shell, but at the flank that shell's
# radius varies by more than a factor of ten with azimuth, so one radius per theta is
# the wrong boundary for almost every direction. Measured on Jan 2026: open field lines
# sitting at 36.6 R_E where the boundary in their own direction is 13.1 R_E - 4.45x
# outside the visible shell. Re-traced with the 2-D table the same epochs give 1.20x.
# Moves traced vertices, so it is a geometry change and bumps the version.
# 7 (2026-09-21): a field line whose SEED sits outside the drawn boundary is now cut to
# its longest inside run instead of being left unclipped. Tail seeds sit at ~31 R_E where
# a crushed storm's boundary is ~30, so a seed 4% outside - essentially on the surface -
# was disabling the clip for that whole line, which then ran to 3.5x the boundary. Two
# lines per epoch failed the alignment gate for all of Gannon.
PHYSICS_VERSION = 7
PHYSICS_TAG = "lin2010-hybrid-aberrated-shocked-imf-drawnclip2d-seedsafe"


def physics_stamp() -> dict:
    """The physics provenance block. Goes in THREE places and must stay in all three:

      1. every exported file's own comments, via state_comments() below
      2. the render manifest ("physics"), event and live
      3. the plan's opts_digest - so a --resume can never splice frames built by
         one physics into a render built by another

    Third one is the load-bearing one. Without it a resumed build silently mixes
    two magnetopause models in one package and nothing reports it.
    """
    return {
        "physics_version": PHYSICS_VERSION,
        "tag": PHYSICS_TAG,
        "magnetopause": ("Lin et al. 2010 (doi:10.1029/2009JA014235) 3-D surface - cusp "
                         "indentations, dipole-tilt and dawn-dusk asymmetry - on a subsolar "
                         "distance blended C2 to Shue et al. 1998 outside Lin's fitted box "
                         "(Bz -15..+5 nT, Pd 1..16 nPa), where Lin's inputs are clamped"),
        "bow_shock": ("Slavin & Holzer 1981 conic, Farris & Russell 1994 standoff, ratio "
                      "bounded at BS_RATIO_MAX, radius scaled per azimuth by the "
                      "magnetopause's own shape so the sheath cannot invert"),
        "aberration": ("applied to the magnetopause evaluation and the wind's upstream "
                       "direction only - Tsyganenko field lines stay in plain GSM, the "
                       "frame they are fitted in"),
        # WHICH boundary a traced line is cut against, which is a geometry fact about
        # the drawn picture and not a modelling detail: the analytic surface and the
        # one on the dome are the same only on the dayside.
        "field_line_clip": ("open lines cut at the DRAWN magnetopause, looked up in 2-D "
                            "(theta, phi) on the export lattice itself, so the clip and "
                            "the exported shell are the same surface by construction - "
                            "a theta-only profile is the wrong boundary at the flank, "
                            "where the radius varies more than tenfold with azimuth. "
                            "Closed lines are NOT cut (both feet on the Earth is what "
                            "defines them) and any that sit outside are counted as a "
                            "real field-model/boundary-model disagreement"),
        "mesh": {"n_theta": N_THETA, "n_phi": N_PHI, "n_head": N_HEAD},
        # How the boundary .obj files are written. Digistar imports .obj through a Y-up
        # pipeline - a file vertex (x, y, z) is DRAWN at (x, -z, y) - so the shells are
        # pre-rotated (x, z, -y) to cancel it. Recorded here because this is in opts_digest:
        # without it a --resume could splice pre-fix shells into a post-fix render, and the
        # package would carry two different boundary orientations with nothing reporting it.
        "obj_frame": ("yup-precompensated: shell .obj vertices and normals written (x, z, -y) "
                      "so Digistar's Y-up import draws them in the export frame"),
        # Shue is star-shaped about Earth and its family is SELF-SIMILAR: every
        # standoff is one reference surface scaled, which is the only reason the
        # shell library (one OBJ, a scale tween per step) can exist. A magnetopause
        # whose SHAPE depends on dipole tilt is not self-similar, and the library
        # would silently draw the wrong surface rather than fail. shell_library and
        # tools/reshell_event.py both refuse when this is False.
        # Lin's SHAPE depends on dipole tilt, so no scaled shape library can
        # represent it - see shell_library.require_self_similar.
        "self_similar_shells": False,
        "wind_shock": ("Rankine-Hugoniot jump at the fast-magnetosonic Mach number, "
                       "obliquity-weighted and faded out past the Slavin & Holzer "
                       "asymptote where the conic is clamped rather than computed"),
        "extra_layers": ("RingCurrent (energy measured from Dst/SYM-H via "
                         "Dessler-Parker-Sckopke, radial geometry is climatology), "
                         "Plasmasphere (plasmapause from Carpenter & Anderson 1992 "
                         "doi:10.1029/91JA01548, axisymmetric - no duskside plume), "
                         "IMFLines (drape convected through the wind's own flow field, "
                         "reconnection marked where Kan & Lee 1979 doi:10.1029/GL006i007p00577 "
                         "gives a non-zero merging rate - quasi-steady, no induction equation)"),
    }


def physics_comment_lines() -> list[str]:
    """The provenance line every exported file carries. ASCII, no semicolons."""
    st = physics_stamp()
    return [f"physics v{st['physics_version']} ({st['tag']}): "
            f"magnetopause {st['magnetopause']}",
            f"physics: bow shock {st['bow_shock']} - aberration {st['aberration']}"]


def state_comments(state: dict, extra=(), frame=None) -> list[str]:
    """Standard provenance comments every exported file must carry.

    Records epoch, tier, model, the driving values AND the export frame, so a
    stray .vla/.vlc on the dome can always be traced back to the exact
    conditions that shaped it and to the frame its holder has to be set up in.
    `frame` defaults to geo.DEFAULT_FRAME ('gsm'); pass it explicitly from every
    exporter so the file can never claim a frame it was not baked in.
    """
    from ..geo import DEFAULT_FRAME, frame_comments

    sw = state["sw"]
    model = state["model"]
    parmod = model.get("parmod")
    parmod_txt = ("none" if parmod is None
                  else " ".join(f"{float(v):g}" for v in parmod))
    lines = [
        f"epoch {state['epoch']} tier {state['tier']} source {state['source']}",
        f"model {model['external']} iopt {model['iopt']} parmod {parmod_txt}",
        (f"driving pdyn {sw['pdyn_npa']:.3f} nPa bz {sw['bz_gsm']:.2f} nT "
         f"bt {sw['bt']:.2f} nT n {sw['density']:.2f} cm-3 "
         f"v {sw['speed']:.1f} km/s T {sw['temperature']:.0f} K "
         f"kp {state['kp']['value']:g}"),
    ]
    lines.extend(frame_comments(DEFAULT_FRAME if frame is None else frame))
    # Every exported file names the physics that shaped it - one edit here reaches
    # boundaries, field lines, pulses, aurora and wind, so no exporter can forget.
    lines.extend(physics_comment_lines())
    lines.extend(extra)
    return lines
