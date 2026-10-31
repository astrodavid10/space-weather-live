"""The ring current: a MEASURED strength wrapped around a DRAWN shape.

    Dessler, A. J., & Parker, E. N. (1959), Hydromagnetic theory of geomagnetic
    storms, J. Geophys. Res. 64(12), 2239-2252, doi:10.1029/JZ064i012p02239
    Sckopke, N. (1966), A general relation between the energy of trapped
    particles and the disturbance field near the Earth, J. Geophys. Res. 71(13),
    3125-3130, doi:10.1029/JZ071i013p03125

        Delta_B = -mu0 * E / (2 pi B0 R_E^3)   ->   E = 4.02e13 J per nT of depression

BE PRECISE ABOUT WHAT IS MEASURED HERE - THIS LAYER IS EASY TO OVERSELL
------------------------------------------------------------------------
The Dessler-Parker-Sckopke relation is exact and first-principles: the ring
current's TOTAL ENERGY follows from the measured SYM-H depression and nothing
else. Gannon's -498 nT is about 2.0e16 J, which is a real number about the real
storm, and it is what drives this layer's brightness.

DPS says nothing whatever about WHERE that energy sits. The radius, the
thickness and the inward march of the peak as a storm deepens are a DRAWING,
anchored on what the literature reports qualitatively (a quiet peak near L 4-5,
an intense-storm peak near L 3) and interpolated. Every anchor is printed below
in the same spirit as zhang_paxton's oval anchors, `label()` says "climatology,
not a measurement" out loud, and the credits page repeats it.

If a published radial profile is wanted later, the honest upgrade is to
transcribe a statistical current-density map and tag it NEEDS-VERIFY the way
ts05.py tags its W coefficients - NOT to smooth this curve until it looks
authoritative.

The driver is nullable and it is a DIFFERENT measurement per event kind:
`omni_symh` (1-minute SYM/H) for the curated storms, `kyoto` Dst quick-look for
the look-back, a model forecast for the live feed. The source name travels into
the label, because plan.peak_caption already refuses to call one by the other's
name and this layer must not undo that. With no Dst at all the layer emits
nothing - the _render_l1 rule: absent is honest, invented is not.
"""
from __future__ import annotations

import math

import numpy as np

# --- the measured part ----------------------------------------------------------
# 2 pi B0 R_E^3 / mu0 with B0 = 3.11e-5 T, R_E = 6.3712e6 m, mu0 = 4 pi x 1e-7.
B0_T = 3.11e-5
RE_M = 6.3712e6
MU0 = 4.0e-7 * math.pi
DPS_J_PER_NT = 2.0 * math.pi * B0_T * RE_M ** 3 / MU0 * 1e-9      # ~4.02e13 J/nT

DOI_DPS = "10.1029/JZ064i012p02239"
DOI_SCKOPKE = "10.1029/JZ071i013p03125"


def depression_nt(symh_nt: float) -> float:
    """The DEPRESSION only: max(0, -SYM-H).

    SYM-H goes POSITIVE during a storm's initial phase - the sudden impulse when
    the shock compresses the magnetosphere - and a positive excursion is
    compression of the dayside field, NOT a ring current. Feeding |SYM-H| to the
    DPS relation would report a large ring current at the exact moment the storm
    has not built one yet, which is both wrong and the most visible moment in the
    replay. Caught by the smoke render on 2026-09-16: Gannon at 16:00 UT reads
    +14 nT and was being drawn as 5.6e14 J of trapped particles.

    (The full treatment also removes the magnetopause-current contribution,
    SYM-H* = SYM-H - b*sqrt(Pdyn) + c. That is a further correction this does not
    make, and the file comments say so.)
    """
    return max(0.0, -float(symh_nt))


def energy_j(symh_nt: float) -> float:
    """Total ring-current energy in joules from the SYM-H depression.

    Derived, not fitted. A -100 nT storm carries ~4e15 J; Gannon's -498 nT is
    ~2.0e16 J, comparable to a few hundred megatons. Zero when SYM-H is positive.
    """
    return depression_nt(symh_nt) * DPS_J_PER_NT


# --- the drawn part -------------------------------------------------------------
# (SYM-H nT, peak L). NOT A PUBLISHED FIT. A monotone interpolation between the
# inward motion of the ring-current peak that the literature reports
# qualitatively (AMPTE/CCE, Van Allen Probes: quiet peak near L 4-5, intense
# storm peak near L 3). Printed here, in full, so nobody has to guess which
# numbers were chosen rather than measured.
RING_L_ANCHORS = ((0.0, 4.5), (-50.0, 4.0), (-150.0, 3.4), (-300.0, 2.9), (-500.0, 2.6))
RING_HALF_WIDTH_L = 0.9          # radial half-thickness, drawing convention
RING_HALF_HEIGHT_RE = 0.7        # vertical half-thickness, drawing convention

N_RING = 6000                    # fixed row count - the morph contract
N_AZIMUTH = 120
N_SECTION = N_RING // N_AZIMUTH  # 50 points around each cross-section

# Brightness reference: the depression at which the layer reaches full opacity.
SYMH_FULL_NT = 300.0
OPACITY_MIN = 0.10


def peak_l(symh_nt: float) -> float:
    """L of the ring current's peak. CLIMATOLOGY - see RING_L_ANCHORS."""
    xs = [a[0] for a in RING_L_ANCHORS][::-1]     # ascending SYM-H (most negative first)
    ys = [a[1] for a in RING_L_ANCHORS][::-1]
    # -depression, so a positive SYM-H reads as quiet rather than running off the
    # end of the table.
    return float(np.interp(-depression_nt(symh_nt), xs, ys))


def intensity(symh_nt: float) -> float:
    """0..1 brightness from the measured depression, i.e. from DPS energy.

    Linear in |SYM-H|, which is linear in the total energy, so the brightness IS
    proportional to the physical quantity rather than to an arbitrary curve.
    """
    f = depression_nt(symh_nt) / SYMH_FULL_NT
    return float(min(1.0, max(OPACITY_MIN, f)))


def torus_points_sm(symh_nt: float, n_azimuth: int = N_AZIMUTH,
                    n_section: int = N_SECTION) -> np.ndarray:
    """(n_azimuth * n_section, 3) points on the drawn torus, in SM R_E.

    Built in SM (dipole) coordinates because the ring current is organised by the
    dipole, not by the Sun line; the caller rotates to GSM and then to the export
    frame. The count is FIXED so consecutive frames morph row by row.

    The torus is axisymmetric, so its corotation is invisible and no
    Earth-fixed frame is needed - unlike the plasmasphere, whose shell this
    deliberately does not share a code path with.
    """
    l0 = peak_l(symh_nt)
    phi = np.linspace(0.0, 2.0 * math.pi, n_azimuth, endpoint=False)[:, None]
    tau = np.linspace(0.0, 2.0 * math.pi, n_section, endpoint=False)[None, :]
    rho = l0 + RING_HALF_WIDTH_L * np.cos(tau)
    z = RING_HALF_HEIGHT_RE * np.sin(tau)
    x = rho * np.cos(phi)
    y = rho * np.sin(phi)
    zz = np.repeat(z, n_azimuth, axis=0)
    return np.stack([x, y, zz], axis=-1).reshape(-1, 3)


def torus_points_gsm(symh_nt: float) -> np.ndarray:
    """The drawn torus in GSM R_E at the recalc'd epoch (dipole tilt applied)."""
    from .. import geo as geo_mod
    return torus_points_sm(symh_nt) @ geo_mod.sm_to_gsm_matrix().T


def source_name(source: str | None) -> str:
    return {"omni_symh": "OMNI 1-minute SYM/H",
            "kyoto": "Kyoto Dst quick-look",
            "geospace_pred": "SWPC Geospace predicted Dst"}.get(
                str(source), str(source or "unknown index"))


def label(symh_nt: float, source: str | None = None) -> str:
    """The honest one-liner: energy measured, geometry drawn."""
    return (f"Ring current - total energy {energy_j(symh_nt):.2e} J from "
            f"{source_name(source)} {float(symh_nt):.0f} nT via "
            f"Dessler-Parker-Sckopke - radial position and thickness are "
            f"climatology, not a measurement")


def comments(symh_nt: float, source: str | None = None) -> list:
    """File-header provenance. ASCII, no semicolons."""
    return [
        label(symh_nt, source),
        f"MEASURED: total energy {energy_j(symh_nt):.3e} J = |{float(symh_nt):.1f} nT| x "
        f"{DPS_J_PER_NT:.3e} J/nT (Dessler & Parker 1959 doi:{DOI_DPS}, "
        f"Sckopke 1966 doi:{DOI_SCKOPKE}) - brightness is proportional to this",
        f"DRAWN: peak at L {peak_l(symh_nt):.2f}, half-width {RING_HALF_WIDTH_L:g} L, "
        f"half-height {RING_HALF_HEIGHT_RE:g} R_E. The inward march of the peak is a "
        f"monotone interpolation of anchors {RING_L_ANCHORS} - the literature reports "
        f"this qualitatively, it is NOT a published fit, and the thickness is a "
        f"drawing convention",
        f"index source {source_name(source)} - live, look-back and storm replays use "
        f"three different measurements and the label always names which",
        "only the DEPRESSION drives this layer: a positive SYM-H is the initial-phase "
        "compression of the dayside field, not trapped particles, so it draws a quiet "
        "ring current. The magnetopause-current correction (SYM-H* = SYM-H - b sqrt(Pdyn) "
        "+ c) is NOT applied, so the energy here is a slight overestimate during the "
        "high-pressure part of a storm",
        f"{N_AZIMUTH} azimuths x {N_SECTION} cross-section points = {N_RING} dots, "
        f"fixed every frame (the morph contract)",
    ]


def selftest() -> dict:
    out = {}
    # DPS constant, against the textbook value
    out["J_per_nT"] = f"{DPS_J_PER_NT:.3e}"
    assert abs(DPS_J_PER_NT - 4.02e13) / 4.02e13 < 0.02, DPS_J_PER_NT
    out["gannon_energy_J"] = f"{energy_j(-498.0):.3e}"
    assert 1.5e16 < energy_j(-498.0) < 2.5e16
    # a POSITIVE SYM-H is compression, not a ring current
    assert energy_j(14.0) == 0.0 and intensity(14.0) == OPACITY_MIN
    assert peak_l(14.0) == peak_l(0.0)

    # the drawn peak marches inward, monotonically, and stays in a sane band
    ls = [peak_l(v) for v in (0.0, -50.0, -150.0, -300.0, -500.0, -700.0)]
    out["peak_L"] = [round(v, 2) for v in ls]
    assert all(b <= a for a, b in zip(ls, ls[1:]))
    assert 2.0 <= min(ls) and max(ls) <= 5.0

    # brightness tracks the measured depression
    assert intensity(-10.0) < intensity(-150.0) < intensity(-400.0) == 1.0

    pts = torus_points_sm(-200.0)
    out["n_points"] = int(len(pts))
    assert len(pts) == N_RING
    rho = np.hypot(pts[:, 0], pts[:, 1])
    out["rho_range"] = [round(float(rho.min()), 2), round(float(rho.max()), 2)]
    assert rho.min() > 1.0, "the drawn torus must not intersect the Earth"
    assert abs(pts[:, 2]).max() <= RING_HALF_HEIGHT_RE + 1e-9
    return out


# --- export ---------------------------------------------------------------------

# Warm amber-red: the ring current is a hot ion population, and this must not read
# as another aurora (teal) or another field line (gold/blue/orange).
COLOUR = (1.0, 0.45, 0.25)


def ring_rows(state: dict, frame, coord_digits: int):
    """(N_RING, 7) export rows - xyz in the export frame, opacity, r, g, b."""
    import numpy as np
    from .. import geo as geo_mod

    symh = float((state.get("dst") or {})["value"])
    pts = torus_points_gsm(symh)
    xyz = geo_mod.gsm_re_to_frame(pts, frame)
    op = intensity(symh)
    rows = np.empty((len(xyz), 7))
    rows[:, :3] = xyz
    rows[:, 3] = op
    rows[:, 4:7] = COLOUR
    return rows


def export_ring_current(state: dict, prev_state, out_path, frame=None,
                        coord_digits: int = 9):
    """Write RingCurrent.vlc, morphing prev -> cur. None when there is no index.

    Returns None - writing NOTHING - when the state carries no Dst. That is the
    _render_l1 rule: a layer with no driver is absent, not invented. The manifest
    records the absence, and the dome simply has no ring current for that event.
    """
    from pathlib import Path

    from dsexport.mesh import write_lattice_surface
    from .. import geo as geo_mod
    from . import recalc_for_state, state_comments

    if (state.get("dst") or {}).get("value") is None:
        return None
    frame = geo_mod.check_frame(frame or geo_mod.DEFAULT_FRAME)
    out_path = Path(out_path)

    recalc_for_state(state)                 # the torus rides the dipole tilt
    symh = float(state["dst"]["value"])
    # (N_AZIMUTH, N_SECTION, 3) in the export frame. A SURFACE, not a point cloud:
    # effectModelClass has no morph, so prev_state no longer contributes a D row -
    # the layer cuts between steps on the shells' slot machinery instead.
    grid = geo_mod.gsm_re_to_frame(torus_points_gsm(symh), frame).reshape(
        N_AZIMUTH, N_SECTION, 3)

    src = (state.get("dst") or {}).get("source")
    lines = comments(symh, src)
    lines.append("effectModelClass surface: cuts between steps (a mesh cannot morph)")
    res = write_lattice_surface(out_path, grid, wrap_u=True, wrap_v=True,
                                comments=state_comments(state, lines, frame=frame),
                                extras={"layer": "RingCurrent", "frame": frame.upper(),
                                        "symh_nt": symh,
                                        "units": "light years (player applies scale 1 ly)"})
    out_path = res["path"]
    return {"path": out_path, "dots": res["vertices"], "frame": frame,
            "symh_nt": float(state["dst"]["value"]), "source": src,
            "energy_j": energy_j(float(state["dst"]["value"])),
            "peak_l": peak_l(float(state["dst"]["value"]))}


if __name__ == "__main__":
    import json
    print(json.dumps(selftest(), indent=1))
    for v, s in ((-25.0, "geospace_pred"), (-150.0, "kyoto"), (-498.0, "omni_symh")):
        print(f"\n{v:+.0f} nT: {label(v, s)}")
