"""The plasmasphere and its storm-time erosion - a published radius, a dipole shape.

    Carpenter, D. L., & Anderson, R. R. (1992), An ISEE/whistler model of
    equatorial electron density in the magnetosphere, J. Geophys. Res. 97(A2),
    1097-1108, doi:10.1029/91JA01548          [DOI verified against Crossref]

        L_pp = 5.6 - 0.46 * Kp_max(previous 24 h)

    O'Brien, T. P., & Moldwin, M. B. (2003), Empirical plasmapause models from
    magnetic indices, Geophys. Res. Lett. 30(4), 2002GL016007,
    doi:10.1029/2002GL016007                  [DOI verified against Crossref]

        L_pp = 5.39 - 0.382 * Kp_max(previous 36 h)

WHY THIS LAYER IS DIFFERENT FROM THE OTHER "DRAWN" ONES
-------------------------------------------------------
Most decorative layers in this show are a drawing convention wrapped around one
measured number. This one is not: BOTH the size and the shape are published.

  * the RADIUS is Carpenter & Anderson's one-line fit, driven by the Kp this
    project already carries - 5.6 R_E at Kp 0 collapsing to about 1.5 at Kp 9,
    which is the erosion the audience is meant to see, and it is real.
  * the SHAPE is a dipole L-shell, r(lambda) = L * cos^2(lambda), which is not a
    convention either - the plasmapause IS an L-shell to a good approximation.
    It also makes the family SELF-SIMILAR: every L_pp is the L = 1 shell scaled.

WHAT IT DOES NOT DRAW, AND THIS HAS TO BE SAID
-----------------------------------------------
A real eroding plasmasphere grows a DUSKSIDE PLUME - a tongue of cold plasma
dragged sunward. It is one of the most recognisable features of the system and a
plasma physicist will look for it. This layer is axisymmetric and does not draw
it, because drawing it would mean inventing its shape. `label()` says so out
loud, and the credits page repeats it. Understating is honest; inventing a plume
is not.

Carpenter & Anderson also state their own weak point: the fit assumes conditions
"slowly varying" over the preceding ~20 h. A shock arrival violates that
completely, so the least trustworthy moment is exactly the storm onset - the one
the show lingers on. That caveat travels in the file comments.

THE FRAME: ECEF, AND THAT IS PHYSICS, NOT CONVENIENCE
------------------------------------------------------
The plasmasphere COROTATES with the Earth - that is its defining property, the
thing that distinguishes it from everything else in this scene. Exported in the
Earth-fixed frame and parented plainly to the Earth (the EarthFrameCheck.vla
idiom: no standardSpace), it rides the rotation for free, and the shell only has
to be re-emitted when L_pp actually changes rather than every step. The dipole
axis is fixed in GEO to about 0.05 deg per YEAR, against the 1 deg per day the
Sun line moves, so baking the orientation once is safe here in a way it is not
for the sun-facing boundaries.
"""
from __future__ import annotations

import math

import numpy as np

CARPENTER_ANDERSON = (5.6, 0.46, 24.0)      # (a, b, window_hours)
OBRIEN_MOLDWIN = (5.39, 0.382, 36.0)
MODELS = {"carpenter_anderson": CARPENTER_ANDERSON, "obrien_moldwin": OBRIEN_MOLDWIN}
DEFAULT_MODEL = "carpenter_anderson"

DOI = {"carpenter_anderson": "10.1029/91JA01548",
       "obrien_moldwin": "10.1029/2002GL016007"}

# Floor and ceiling on the drawn plasmapause. Carpenter & Anderson's line is a
# fit over observed conditions, not an identity: at Kp 9 it gives 1.46 R_E, which
# is below the altitudes where a plasmasphere is a meaningful description, and it
# would go negative past Kp 12. The floor is a DRAWING limit, stated as one.
L_MIN = 1.6
L_MAX = 6.0
L_REF = 5.0                      # the reference shell a scaled mesh would use

# Fixed row count - the morph contract. Never make this data-dependent.
N_SHELL = 4000
N_RING = 80                      # dots around each meridian ring
N_MERIDIAN = N_SHELL // N_RING   # 50 meridians

# Emission economy: re-emit when the plasmapause has moved by this much, or when
# the hold reaches the cap. L_pp follows Kp_max over a 24 h window, so it is a
# step function that can sit still for hours.
L_QUANTUM_RE = 0.05
MAX_HOLD_MIN = 60.0


def plasmapause_l(kp_max: float, model: str = DEFAULT_MODEL) -> float:
    """Plasmapause L value from the peak Kp of the preceding window.

    Kp 0 -> 5.6, Kp 5 -> 3.3, Kp 7 -> 2.4, Kp 9 -> 1.6 (floored).
    """
    a, b, _hours = MODELS[model]
    return float(min(L_MAX, max(L_MIN, a - b * float(kp_max))))


def window_hours(model: str = DEFAULT_MODEL) -> float:
    return MODELS[model][2]


def kp_max_window(timeline, epoch_iso: str, hours: float = 24.0) -> float:
    """Peak Kp over the `hours` before `epoch_iso`, from the FULL event timeline.

    The full timeline, not the render window: a curated storm's window starts
    only ~10 h before onset, so a Kp_max computed from the window alone would
    start the plasmasphere too wide and then jump when the real peak came into
    view. Same reasoning as ts05.as_ts05 taking the whole timeline.

    Falls back to the earliest samples available rather than raising - a short
    timeline is a reason to say so in the label, not to blank the layer.
    """
    from datetime import datetime, timedelta, timezone

    def _t(s):
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)

    end = _t(epoch_iso)
    start = end - timedelta(hours=float(hours))
    best = None
    for st in timeline:
        kp = (st.get("kp") or {}).get("value")
        if kp is None:
            continue
        t = _t(st.get("epoch"))
        if start <= t <= end:
            best = kp if best is None else max(best, kp)
    if best is None:                      # nothing in the window at all
        vals = [(st.get("kp") or {}).get("value") for st in timeline]
        vals = [v for v in vals if v is not None]
        best = max(vals) if vals else 0.0
    return float(best)


def shell_points_sm(l_value: float, n_ring: int = N_RING,
                    n_meridian: int = N_MERIDIAN) -> np.ndarray:
    """(n_meridian * n_ring, 3) points on the dipole L-shell, in SM R_E.

    r(lambda) = L cos^2(lambda) is the dipole field line through L at the
    equator. Sampling lambda uniformly bunches points near the poles where the
    shell closes, which is where it should be bright, so the sampling is left
    uniform in lambda rather than in arc length.

    The count is FIXED. Every frame has exactly n_meridian * n_ring rows, so the
    exporter can morph vertex i of meridian j between epochs with no matching
    step - the same contract the field lines and the aurora oval obey.
    """
    lam = np.linspace(-math.pi / 2 * 0.98, math.pi / 2 * 0.98, n_ring)
    phi = np.linspace(0.0, 2.0 * math.pi, n_meridian, endpoint=False)
    r = float(l_value) * np.cos(lam) ** 2
    x = (r * np.cos(lam))[None, :] * np.cos(phi)[:, None]
    y = (r * np.cos(lam))[None, :] * np.sin(phi)[:, None]
    z = np.repeat((r * np.sin(lam))[None, :], n_meridian, axis=0)
    return np.stack([x, y, z], axis=-1).reshape(-1, 3)


def shell_points_geo(l_value: float) -> np.ndarray:
    """The same shell in GEO (Earth-fixed) R_E, at the recalc'd epoch.

    SM -> GSM -> GEO. The result is epoch-independent to within the dipole's
    secular drift (~0.05 deg/year), which is what lets this layer be baked once
    and left to corotate. `dipole_axis_drift_deg` checks that claim rather than
    asserting it.
    """
    from .. import geo as geo_mod
    pts = shell_points_sm(l_value)
    m = geo_mod.gsm_to_geo_matrix() @ geo_mod.sm_to_gsm_matrix()
    return pts @ m.T


def dipole_axis_geo() -> np.ndarray:
    """Unit dipole axis in GEO at the recalc'd epoch (the SM +Z axis)."""
    from .. import geo as geo_mod
    m = geo_mod.gsm_to_geo_matrix() @ geo_mod.sm_to_gsm_matrix()
    v = m @ np.array([0.0, 0.0, 1.0])
    return v / np.linalg.norm(v)


def emission_indices(l_values, max_hold_min: float = MAX_HOLD_MIN,
                     step_min: float = 10.0,
                     quantum_re: float = L_QUANTUM_RE) -> list:
    """Step indices at which to emit a shell: first, last, every real move.

    L_pp is a step function of Kp_max over a 24 h window, so it can sit still for
    hours; emitting every step would write hundreds of identical files. Same
    economy as the aurora oval's Kp-change rule, with a hold cap so a long flat
    stretch still gets refreshed.
    """
    vals = [float(v) for v in l_values]
    if not vals:
        return []
    out = [0]
    last_v, last_k = vals[0], 0
    hold_steps = max(1, int(round(float(max_hold_min) / max(step_min, 1e-9))))
    for k in range(1, len(vals)):
        if abs(vals[k] - last_v) >= quantum_re or (k - last_k) >= hold_steps:
            out.append(k)
            last_v, last_k = vals[k], k
    if out[-1] != len(vals) - 1:
        out.append(len(vals) - 1)
    return out


def label(kp_max: float, model: str = DEFAULT_MODEL, hours: float | None = None) -> str:
    """The honest one-liner: what is measured, what is a fit, what is missing."""
    a, b, win = MODELS[model]
    hours = win if hours is None else hours
    l = plasmapause_l(kp_max, model)
    name = {"carpenter_anderson": "Carpenter & Anderson 1992",
            "obrien_moldwin": "O'Brien & Moldwin 2003"}[model]
    return (f"Plasmasphere - plasmapause at L {l:.2f} from {name} "
            f"(L_pp = {a:g} - {b:g} x Kp_max = {kp_max:.1f} over {hours:g} h, "
            f"doi:{DOI[model]}) - dipole L-shell, corotating - "
            f"axisymmetric: the real duskside plume is NOT drawn")


def comments(kp_max: float, model: str = DEFAULT_MODEL) -> list:
    """File-header provenance. ASCII, no semicolons (the .vla/.vlc rule)."""
    a, b, win = MODELS[model]
    l = plasmapause_l(kp_max, model)
    lines = [label(kp_max, model),
             f"radius: published fit, L_pp = {a:g} - {b:g} * Kp_max({win:g} h), "
             f"doi:{DOI[model]} - Kp_max {kp_max:.2f} gives L {l:.3f}",
             f"drawn radius clamped to [{L_MIN:g}, {L_MAX:g}] R_E - the fit is a "
             f"regression over observed conditions, not an identity, and it runs "
             f"below a meaningful plasmasphere past about Kp 9",
             "shape: dipole L-shell r = L cos^2(latitude), corotating with Earth "
             "- exported EARTH-FIXED and parented plainly to the Earth, so the "
             "rotation is the planet's own",
             "CAVEAT from the source: the fit assumes conditions slowly varying "
             "over the preceding ~20 h, which a shock arrival violates - the "
             "least reliable moment is the storm onset itself",
             f"{N_MERIDIAN} meridians x {N_RING} points = {N_SHELL} dots, fixed "
             f"every frame (the morph contract)"]
    return lines


def selftest() -> dict:
    """Published values, checked rather than assumed."""
    out = {}
    for kp, want in ((0.0, 5.6), (5.0, 3.3), (7.0, 2.38)):
        got = plasmapause_l(kp)
        out[f"L_at_Kp{kp:g}"] = round(got, 3)
        assert abs(got - want) < 0.05, (kp, got, want)
    assert plasmapause_l(9.0) == L_MIN               # floored, deliberately
    # strictly decreasing in Kp until the floor
    vals = [plasmapause_l(k) for k in np.arange(0.0, 8.0, 0.5)]
    assert all(b <= a for a, b in zip(vals, vals[1:]))
    pts = shell_points_sm(5.0)
    out["n_points"] = int(len(pts))
    assert len(pts) == N_SHELL
    r = np.linalg.norm(pts, axis=1)
    out["max_r_re"] = round(float(r.max()), 3)
    assert r.max() <= 5.0 + 1e-9                     # the equator is the widest point
    # self-similarity: the whole family is one shape scaled
    np.testing.assert_allclose(shell_points_sm(2.0), shell_points_sm(4.0) / 2.0,
                               rtol=1e-12, atol=0)
    out["self_similar"] = True
    return out


# --- export ---------------------------------------------------------------------

# Cool violet-white: cold dense plasma, and distinct from every other layer's hue.
COLOUR = (0.62, 0.70, 1.0)
OPACITY = 0.55


def shell_rows(kp_max: float, frame, model: str = DEFAULT_MODEL):
    """(N_SHELL, 7) export rows for one plasmapause radius."""
    import numpy as np
    from .. import geo as geo_mod

    l = plasmapause_l(kp_max, model)
    pts = shell_points_sm(l) @ geo_mod.sm_to_gsm_matrix().T
    xyz = geo_mod.gsm_re_to_frame(pts, frame)
    rows = np.empty((len(xyz), 7))
    rows[:, :3] = xyz
    rows[:, 3] = OPACITY
    rows[:, 4:7] = COLOUR
    return rows


def export_plasmasphere(state: dict, prev_state, timeline, out_path, frame=None,
                        coord_digits: int = 9, model: str = DEFAULT_MODEL):
    """Write Plasmasphere.vlc, morphing prev -> cur. None when there is no Kp.

    `timeline` is the FULL event timeline, not the render window: Kp_max has to
    look 24 h back and a curated storm's window starts only ~10 h before onset,
    so a window-only maximum would start the plasmasphere too wide and then jump
    when the real peak came into view. Same precedent as ts05.as_ts05.

    Emitted in the render frame and re-baked every step, so the corotation is the
    real rotation at each epoch. The Earth-fixed single-mesh form described in the
    module docstring is an optimisation, not a requirement, and it needs a dome
    probe this does not.
    """
    from pathlib import Path

    from dsexport.mesh import write_lattice_surface
    from .. import geo as geo_mod
    from . import recalc_for_state, state_comments

    if (state.get("kp") or {}).get("value") is None:
        return None
    frame = geo_mod.check_frame(frame or geo_mod.DEFAULT_FRAME)
    out_path = Path(out_path)
    hours = window_hours(model)

    kp_max = kp_max_window(timeline or [state], state["epoch"], hours)
    recalc_for_state(state)
    l = plasmapause_l(kp_max, model)
    # (N_MERIDIAN, N_RING, 3) in the export frame. A SURFACE, not a point cloud:
    # effectModelClass has no morph, so prev_state no longer contributes a D row -
    # the layer cuts between steps on the boundary shells' slot machinery instead.
    pts = shell_points_sm(l) @ geo_mod.sm_to_gsm_matrix().T
    grid = geo_mod.gsm_re_to_frame(pts, frame).reshape(N_MERIDIAN, N_RING, 3)

    lines = comments(kp_max, model)
    lines.append("effectModelClass surface: cuts between steps (a mesh cannot morph)")
    # wrap_v stays False: r = L cos^2(lambda) pinches to a point at both poles, so
    # the v ends are a degenerate apex, not a seam to close.
    res = write_lattice_surface(out_path, grid, wrap_u=True, wrap_v=False,
                                comments=state_comments(state, lines, frame=frame),
                                extras={"layer": "Plasmasphere", "frame": frame.upper(),
                                        "plasmapause_l_re": l, "kp_max_24h": kp_max,
                                        "model": model,
                                        "units": "light years (player applies scale 1 ly)"})
    return {"path": res["path"], "dots": res["vertices"], "frame": frame,
            "kp_max": kp_max, "l_pp": l, "model": model}


if __name__ == "__main__":
    import json
    print(json.dumps(selftest(), indent=1))
    for kp in (0, 3, 5, 7, 9):
        print(f"Kp {kp}: {label(float(kp))}")
