r"""Shell shape LIBRARY for the effectModelClass boundaries (David, 2026-09-14).

The per-step shells (one OBJ per 10-minute step, crossfaded through three slots) are
replaced by a handful of REFERENCE shapes that the player SCALES every step:

  * the bow shock is ONE shape - the Slavin & Holzer conic has a fixed eccentricity, so
    every standoff is the same surface at a different size
  * the magnetopause is a FEW shapes - Shue's flaring exponent alpha changes with Bz and
    pressure (the tail radius moves ~30 % across a storm), so the library holds one shape
    per ALPHA_BIN of alpha and the player crossfades between shapes only when the bin
    changes (rare) while scaling continuously

Every library mesh is built at the reference standoff R_REF_RE in GSM axes (nose along
+X_GSM) and frame-baked once, at the render's MIDDLE epoch: the surfaces are
axisymmetric about the Earth-Sun line, and that line drifts ~1 deg/day, so a two-day
storm is at most ~1 deg off at its ends - invisible on a soft shell, and it buys
smooth 'scale' tweens instead of cuts. The player writes 'scale <standoff/R_REF> ly'
per step (the OBJ units are light years at R_REF, so the multiplier is dimensionless).

Per-step numbers come from the same functions the per-step export used - the geopack
Shue bisection for the magnetopause nose, Farris & Russell for the shock standoff - so
gate 5 (last closed field line vs the drawn magnetopause) still holds at the nose.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from .. import geo
from . import recalc_for_state
from .boundaries import (boundary_mesh, bow_shock_mesh_at, bow_shock_standoff,
                         magnetopause_radius)

log = logging.getLogger(__name__)

R_REF_RE = 10.0                 # reference standoff every library mesh is built at
ALPHA_BIN = 0.02                # magnetopause flaring bin width
ALPHA_MIN, ALPHA_MAX = 0.30, 1.30
BOW_SHOCK_LIB_NAME = "BowShockShell_lib.obj"
MP_LIB_FMT = "MagnetopauseShell_{tag}.obj"
SHELL_NAMES = ("MagnetopauseShell", "BowShockShell")


def shue_alpha(state: dict) -> float:
    """Shue et al. (1998) flaring exponent alpha(Bz, Pdyn), clipped to a sane range.

    geopack's shuetal_mgnp uses the same formula, so the analytic library shape for a
    bin matches the bisected per-step nose the export measures.
    """
    sw = state["sw"]
    bz = float(sw["bz_gsm"])
    pd = max(float(sw.get("pdyn_npa", 0.0)), 1e-3)
    a = (0.58 - 0.007 * bz) * (1.0 + 0.024 * math.log(pd))
    return float(min(max(a, ALPHA_MIN), ALPHA_MAX))


def alpha_bin(alpha: float) -> float:
    return round(round(float(alpha) / ALPHA_BIN) * ALPHA_BIN, 2)


def bin_tag(alpha_binned: float) -> str:
    """'a0p58' - a filename-safe tag for a binned alpha."""
    return f"a{alpha_binned:.2f}".replace(".", "p")


def magnetopause_library_mesh(alpha: float, r0: float = R_REF_RE) -> dict:
    """Shue surface of revolution at standoff r0 with flaring alpha (analytic head,
    the shared v2 tail blend), GSM axes."""
    def radius(theta, _state):
        th = np.asarray(theta, dtype=float)
        return float(r0) * (2.0 / (1.0 + np.cos(th))) ** float(alpha)
    mesh = boundary_mesh(radius, None)
    mesh["alpha"] = float(alpha)
    return mesh


def bow_shock_library_mesh(standoff: float = R_REF_RE) -> dict:
    return bow_shock_mesh_at(float(standoff))


def per_step_shell_params(states: list[dict]) -> dict:
    """Per-step standoffs (R_E) and flaring for every state - pure driving-value geometry."""
    mp, bs, alphas = [], [], []
    for s in states:
        mp.append(float(magnetopause_radius(0.0, s)))
        bs.append(float(bow_shock_standoff(s)))
        alphas.append(shue_alpha(s))
    bins = sorted({alpha_bin(a) for a in alphas})
    return {"mp_standoff_re": mp, "bs_standoff_re": bs, "alpha": alphas,
            "alpha_bins": bins, "mp_bin_index": [bins.index(alpha_bin(a)) for a in alphas]}


def require_self_similar(what: str) -> None:
    """Refuse to build a scaled shape library against a non-self-similar boundary.

    The library's entire premise is that every standoff is ONE reference surface
    times a scalar. That is true of Shue (star-shaped, axisymmetric about the
    Earth-Sun line) and false the moment the magnetopause shape depends on dipole
    tilt, which sweeps +-34 deg a day. The dangerous part is that it would not
    raise - it would happily scale the wrong surface and put a plausible, wrong
    boundary on the dome. So it raises here instead, loudly, at build time.
    """
    from . import physics_stamp
    st = physics_stamp()
    if not st.get("self_similar_shells", False):
        raise RuntimeError(
            f"{what} needs a self-similar magnetopause family, but physics "
            f"v{st['physics_version']} ({st['tag']}) is not one: "
            f"{st['magnetopause']}. Render with shell_mode='step' (one mesh per "
            f"step) - that path is the default and is what the live daemon uses.")


def export_shell_library(states: list[dict], out_dir, frame=geo.DEFAULT_FRAME,
                         coord_digits: int = geo.DEFAULT_COORD_DIGITS,
                         orient_state: dict | None = None) -> dict:
    """Write the library OBJs into out_dir and return the manifest's 'shells' block.

    Frame-baked at orient_state (default: the middle state) through the model gateway;
    geopack is left on that epoch.
    """
    from dsexport.mesh import write_obj_surface
    from dsexport.vla import coord_formatter

    require_self_similar("the shell library")
    out_dir = Path(out_dir)
    frame = geo.check_frame(frame)
    params = per_step_shell_params(states)
    mid = orient_state or states[len(states) // 2]
    recalc_for_state(mid)                       # the one rotation every library mesh uses
    cfmt = coord_formatter(coord_digits)
    common = [f"shell LIBRARY mesh at reference standoff {R_REF_RE:g} R_E - the player scales "
              f"it per step (scale <standoff/{R_REF_RE:g}> ly)",
              f"orientation baked at epoch {mid.get('epoch')} (axisymmetric about the "
              f"Earth-Sun line, which drifts ~1 deg/day)"]
    mp_files = []
    for a in params["alpha_bins"]:
        mesh = magnetopause_library_mesh(a)
        name = MP_LIB_FMT.format(tag=bin_tag(a))
        write_obj_surface(out_dir / name, mesh, frame,
                          comments=[f"Shue magnetopause, flaring alpha {a:.2f}"] + common
                          + list(geo.coord_comments(coord_digits)),
                          cap=True, coord_fmt=cfmt)
        mp_files.append(name)
    bs_mesh = bow_shock_library_mesh()
    write_obj_surface(out_dir / BOW_SHOCK_LIB_NAME, bs_mesh, frame,
                      comments=["Slavin & Holzer bow shock (fixed eccentricity - one shape)"]
                      + common + list(geo.coord_comments(coord_digits)),
                      cap=True, coord_fmt=cfmt)
    n = len(states)
    shells = {
        "mode": "library",
        "r_ref_re": R_REF_RE,
        "alpha_bin": ALPHA_BIN,
        "orientation_epoch": mid.get("epoch"),
        "MagnetopauseShell": {
            "files": mp_files,
            "bin": list(params["mp_bin_index"]),
            "scale": [round(r / R_REF_RE, 6) for r in params["mp_standoff_re"]],
            "standoff_re": [round(r, 4) for r in params["mp_standoff_re"]],
            "alpha": [round(a, 4) for a in params["alpha"]],
            "alpha_bins": list(params["alpha_bins"]),
        },
        "BowShockShell": {
            "files": [BOW_SHOCK_LIB_NAME],
            "bin": [0] * n,
            "scale": [round(r / R_REF_RE, 6) for r in params["bs_standoff_re"]],
            "standoff_re": [round(r, 4) for r in params["bs_standoff_re"]],
        },
    }
    log.info("shell library: %d magnetopause shape(s) (alpha bins %s) + 1 bow shock -> %s",
             len(mp_files), params["alpha_bins"], out_dir)
    return shells


def library_layer_rows(shells: dict, times: list[float]) -> list[dict]:
    """The manifest layers[] rows for the two shells in library mode (player order)."""
    rows = []
    for name in SHELL_NAMES:
        blk = shells[name]
        rows.append({"name": name, "kind": "mesh", "holder": name,
                     "files": list(blk["files"]), "parametric": False,
                     "library": True, "times": [float(t) for t in times]})
    return rows
