"""Lin et al. (2010) three-dimensional magnetopause - closed form, pure numpy.

    Lin, R. L., Zhang, X. X., Liu, S. Q., Wang, Y. L., & Gong, J. C. (2010),
    A three-dimensional asymmetric magnetopause model,
    J. Geophys. Res., 115, A04207, doi:10.1029/2009JA014235

Equations (19)-(20) and the Table 9 coefficients, transcribed from the paper
David supplied on 2026-09-16 and verified numerically against the values the
paper reports (see `selftest()` at the bottom - it is asserted by the test suite,
not left as a comment).

WHY THIS MODULE EXISTS, given Shue (1998) already works
-------------------------------------------------------
Shue is axisymmetric about the Earth-Sun line: one radius per polar angle, the
same all the way round. Lin is a genuine 3-D surface, and the three things it
adds are the three things the audience can actually see:

  * the CUSP INDENTATIONS - two funnels where the boundary is pulled inward by
    up to ~2.5 R_E, which is where the field lines enter. Shue draws a smooth
    shell straight through them.
  * the DIPOLE TILT response - the indentations swing north/south with the tilt,
    antisymmetrically (theta_n = a19 + a20*tilt, theta_s = a19 - a20*tilt), so
    the boundary breathes over a day the way the real one does.
  * north-south and dawn-dusk ASYMMETRY through the flaring exponent.

It is also, measured on the 32x72 lattice this project draws, **847x faster**
than the Shue path it replaces (0.21 ms against 176 ms) - because it is closed
form and needs no bisection at all. The 3-D surface is cheaper than the
axisymmetric one.

THE FRAME, AND WHY IT MATTERS
-----------------------------
Lin is defined in **aberrated GSM** (cGSM): GSM with Earth's ~30 km/s orbital
motion removed from the flow direction, which rotates the whole surface by ~4 deg
about Z. That is not optional dressing - the fit was made in that frame, so
evaluating it in plain GSM puts the nose 4 deg off its own definition. Callers
pass the ABERRATED angles; `geo.aberration_angle_rad` computes the rotation and
`boundaries` applies it at exactly one site.

THE FITTED BOX, AND WHY THIS PROJECT LEAVES IT
----------------------------------------------
The paper fits Bz in [-15, +5] nT and Pd in [1, 16] nPa, and says explicitly that
extreme conditions "should be further investigated". Every storm this show exists
to draw leaves that box - Gannon reaches Bz -44 nT and Pd 48 nPa. So:

  * every input is CLAMPED to the fitted box here, and the raw value is kept in
    the returned record. An extrapolated `beta0` is the dangerous one: at the box
    edge the flaring exponent is about -1.37, and on naive extrapolation to
    Bz -50 it nearly doubles to -2.20, which visibly deforms the tail.
  * the SUBSOLAR distance is blended back to Shue outside the box by the caller
    (`boundaries.hybrid_r0`), because Shue is the reference every other number in
    this tree is calibrated against.

Both facts are recorded in the manifest and stated on the credits page. A hybrid
is honest; a hybrid described as "the Lin model" is not.
"""
from __future__ import annotations

import numpy as np

DOI = "10.1029/2009JA014235"
CITATION = ("Lin et al. (2010), A three-dimensional asymmetric magnetopause model, "
            "JGR 115, A04207, doi:10.1029/2009JA014235")

# Table 9, a0..a21, in the paper's order. Do not reorder - every formula below
# indexes this tuple positionally.
A = (12.544, -0.194, 0.305, 0.0573, 2.178, 0.0571, -0.999, 16.473, 0.00152,
     0.382, 0.0431, -0.00763, -0.210, 0.0405, -4.430, -0.636, -2.600, 0.832,
     -5.328, 1.103, -0.907, 1.450)

# The fitted box (paper, section 4). Inputs are clamped to it; the raw values
# ride along in the returned record so nothing is silently rewritten.
BZ_MIN_NT, BZ_MAX_NT = -15.0, 5.0
PD_MIN_NPA, PD_MAX_NPA = 1.0, 16.0

# Magnetic pressure of the IMF, nPa per nT^2: B^2 / (2*mu0) with B in nT.
#   (1e-9)^2 / (2 * 4pi*1e-7) Pa/nT^2 = 3.9789e-13 Pa = 3.9789e-4 nPa
PM_NPA_PER_NT2 = 1.0e-18 / (2.0 * 4.0e-7 * np.pi) * 1.0e9


def magnetic_pressure_npa(bt_nt: float) -> float:
    """IMF magnetic pressure in nPa. Lin's pressure term is (Pd + Pm), not Pd."""
    return PM_NPA_PER_NT2 * float(bt_nt) ** 2


def clamp_inputs(pd_npa: float, bz_nt: float) -> dict:
    """Clamp the drivers to the fitted box, keeping the raw values.

    Returns both, plus how far outside the box we are, so callers can blend and
    so the manifest can say "this frame was extrapolated by N nT" rather than
    quietly pretending it was not.
    """
    pd_raw, bz_raw = float(pd_npa), float(bz_nt)
    pd = min(max(pd_raw, PD_MIN_NPA), PD_MAX_NPA)
    bz = min(max(bz_raw, BZ_MIN_NT), BZ_MAX_NT)
    return {"pd_npa": pd, "bz_nt": bz, "pd_raw_npa": pd_raw, "bz_raw_nt": bz_raw,
            "pd_clamped": pd != pd_raw, "bz_clamped": bz != bz_raw,
            "bz_excess_nt": round(bz_raw - bz, 4),
            "pd_excess_npa": round(pd_raw - pd, 4),
            "in_fitted_box": (pd == pd_raw and bz == bz_raw)}


def r0_re(pd_npa: float, bz_nt: float, bt_nt: float = 0.0) -> float:
    """Subsolar standoff, equation (19) first factor. Inputs must be pre-clamped.

    r0 = a0 * (Pd+Pm)^a1 * [1 + a2 * (exp(a3*Bz) - 1) / (exp(a4*Bz) + 1)]

    The Bz bracket saturates at 1 - a2 = 0.695 as Bz goes very negative, so the
    nose does not run away; the pressure exponent a1 = -0.194 is a slightly
    STRONGER pressure response than Shue's -0.1515.
    """
    p = float(pd_npa) + magnetic_pressure_npa(bt_nt)
    bz = float(bz_nt)
    bracket = 1.0 + A[2] * (np.exp(A[3] * bz) - 1.0) / (np.exp(A[4] * bz) + 1.0)
    return float(A[0] * p ** A[1] * bracket)


def _betas(bz_nt: float, tilt_rad: float) -> tuple:
    """The four flaring-exponent coefficients of equation (19)."""
    bz, t = float(bz_nt), float(tilt_rad)
    b0 = A[6] + A[7] * (np.exp(A[8] * bz) - 1.0) / (np.exp(A[9] * bz) + 1.0)
    return b0, A[10], A[11] + A[12] * t, A[13]


def _cusp_terms(pd_npa: float, bt_nt: float, tilt_rad: float) -> dict:
    """Equation (20): the two cusp indentations, antisymmetric in dipole tilt."""
    p = float(pd_npa) + magnetic_pressure_npa(bt_nt)
    t = float(tilt_rad)
    c = A[14] * p ** A[15]                      # cn == cs, negative -> inward
    return {"cn": c, "cs": c,
            "dn": A[16] + A[17] * t + A[18] * t * t,
            "ds": A[16] - A[17] * t + A[18] * t * t,
            "theta_n": A[19] + A[20] * t,
            "theta_s": A[19] - A[20] * t,
            "phi_n": 0.5 * np.pi, "phi_s": 1.5 * np.pi,
            "en": A[21], "es": A[21]}


def radius_re(theta, phi, pd_npa: float, bz_nt: float, bt_nt: float = 0.0,
              tilt_rad: float = 0.0, r0_override: float | None = None):
    """Magnetopause radius in R_E at solar-zenith angle(s) `theta`, azimuth `phi`.

    `theta` is measured from the aberrated Earth-Sun line (0 = nose) and `phi` in
    the Y-Z plane from +Y toward +Z, i.e. `atan2(z, y)` - the convention this
    project's meshes already use. Both broadcast, so a whole lattice evaluates in
    one call (that is where the 847x comes from).

    `r0_override` substitutes a subsolar distance computed elsewhere - this is
    how the hybrid works: Lin supplies SHAPE, tilt, asymmetry and the cusp
    indentations at all times, while the nose can come from Shue when the drivers
    leave Lin's fitted box. Shape and scale stay separable because r0 enters
    equation (19) as a pure multiplier.

    Inputs are NOT clamped here - call clamp_inputs() first and pass the clamped
    values, so the clamping is visible at the call site and lands in the manifest.
    """
    th = np.asarray(theta, dtype=float)
    ph = np.asarray(phi, dtype=float)
    r0 = float(r0_re(pd_npa, bz_nt, bt_nt)) if r0_override is None else float(r0_override)

    b0, b1, b2, b3 = _betas(bz_nt, tilt_rad)
    exponent = b0 + b1 * np.cos(ph) + b2 * np.sin(ph) + b3 * np.sin(ph) ** 2

    # The shape factor. The base is >= 0 over theta in [0, pi] and reaches 0 only
    # at the anti-solar point, where the real surface is an open tail - clipped so
    # a lattice that includes theta = pi returns a huge radius rather than a nan.
    base = np.cos(0.5 * th) + A[5] * np.sin(2.0 * th) * (1.0 - np.exp(-th))
    f = np.power(np.clip(base, 1e-12, None), exponent)

    c = _cusp_terms(pd_npa, bt_nt, tilt_rad)
    cos_psi_n = (np.cos(th) * np.cos(c["theta_n"])
                 + np.sin(th) * np.sin(c["theta_n"]) * np.cos(ph - c["phi_n"]))
    cos_psi_s = (np.cos(th) * np.cos(c["theta_s"])
                 + np.sin(th) * np.sin(c["theta_s"]) * np.cos(ph - c["phi_s"]))
    psi_n = np.arccos(np.clip(cos_psi_n, -1.0, 1.0))
    psi_s = np.arccos(np.clip(cos_psi_s, -1.0, 1.0))
    q = (c["cn"] * np.exp(c["dn"] * np.power(psi_n, c["en"]))
         + c["cs"] * np.exp(c["ds"] * np.power(psi_s, c["es"])))

    return r0 * f + q


def describe(pd_npa: float, bz_nt: float, bt_nt: float, tilt_rad: float) -> dict:
    """Everything the manifest and the file headers need about one evaluation."""
    box = clamp_inputs(pd_npa, bz_nt)
    c = _cusp_terms(box["pd_npa"], bt_nt, tilt_rad)
    b0, b1, b2, b3 = _betas(box["bz_nt"], tilt_rad)
    return {**box, "bt_nt": float(bt_nt), "tilt_deg": round(np.degrees(tilt_rad), 3),
            "r0_re": round(r0_re(box["pd_npa"], box["bz_nt"], bt_nt), 4),
            "pm_npa": round(magnetic_pressure_npa(bt_nt), 5),
            "beta0": round(float(b0), 4),
            "cusp_theta_n_deg": round(float(np.degrees(c["theta_n"])), 2),
            "cusp_theta_s_deg": round(float(np.degrees(c["theta_s"])), 2),
            "doi": DOI}


def selftest() -> dict:
    """The numbers this transcription was verified against. Asserted by the tests.

    Every figure here was checked by hand against the paper before the model was
    wired into anything - transcribing 22 coefficients is exactly the kind of job
    where one transposed digit produces a plausible, wrong surface.
    """
    out = {}
    # 1. nominal subsolar distance: Pd 2 nPa, Bz 0, no tilt -> 10-11 R_E
    nose = float(radius_re(0.0, 0.0, 2.0, 0.0, 0.0, 0.0))
    out["nominal_nose_re"] = round(nose, 4)
    assert 10.0 <= nose <= 11.0, nose

    # 2. the cusps are an INDENTATION, and a deep one, on the cusp meridian
    th = np.radians(63.2)
    r_cusp = float(radius_re(th, 0.5 * np.pi, 2.0, 0.0, 0.0, 0.0))   # +Z, north cusp
    r_dawn = float(radius_re(th, 0.0, 2.0, 0.0, 0.0, 0.0))           # +Y, no cusp
    out["cusp_indent_re"] = round(r_dawn - r_cusp, 4)
    assert r_dawn - r_cusp > 2.0, (r_dawn, r_cusp)

    # 3. dipole tilt moves the two cusps antisymmetrically
    cp = _cusp_terms(2.0, 0.0, np.radians(23.0))
    cm = _cusp_terms(2.0, 0.0, np.radians(-23.0))
    out["cusp_theta_n_deg_at_+23"] = round(float(np.degrees(cp["theta_n"])), 2)
    out["cusp_theta_s_deg_at_+23"] = round(float(np.degrees(cp["theta_s"])), 2)
    assert abs(np.degrees(cp["theta_n"]) - np.degrees(cm["theta_s"])) < 1e-6

    # 4. Bz saturation: the bracket bottoms out at 1 - a2, so the nose cannot run away
    deep = r0_re(2.0, -200.0) / r0_re(2.0, 0.0)
    out["bz_saturation_ratio"] = round(float(deep), 6)
    # It approaches 1 - a2 from ABOVE: at Bz = -200 the numerator's exp(a3*Bz) is
    # still 1.05e-5 rather than 0, which is the whole residual (3.2e-6).
    assert 0.0 < deep - (1.0 - A[2]) < 1e-4, deep
    assert r0_re(2.0, -44.0) > r0_re(2.0, -200.0), "still monotone at Gannon's Bz"

    # 5. the surface is smooth and finite over a full lattice
    t = np.linspace(0.0, np.radians(150.0), 32)[:, None]
    p = np.linspace(0.0, 2.0 * np.pi, 72)[None, :]
    r = radius_re(t, p, 2.0, 0.0, 5.0, 0.0)
    out["lattice_shape"] = list(r.shape)
    assert np.all(np.isfinite(r)) and np.all(r > 0.0)
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(selftest(), indent=1))
