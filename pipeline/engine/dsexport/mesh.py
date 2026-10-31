r"""Wavefront OBJ writer for the translucent boundary SHELLS (phase 4, 2026-09-05).

The magnetopause and bow shock are drawn as .vla ring/meridian wireframes (the
honest geometry, they morph between steps). The shells are the beauty layer on
top: the SAME axisymmetric (x, rho) profile a boundary mesh already computes
(magneto.model.boundaries._surface_of_revolution), written as a closed surface
of revolution for Digistar's effectModelClass - angle-based-intensity shading,
the look of the E&S EarthMagShell, but from THIS state's Shue / Farris-Russell
surface.

Dome facts this writer is built on (measured 2026-09-04/05, see the plan file):
  * effectModelClass loads .obj (cooked to TRIANGLELIST; 1200-vertex sphere ->
    1108 verts / 6912 indices), renders 'abiMode fadeCenter' as a glowing rim,
    and fades with 'intensity ... duration'.
  * Coordinates are LIGHT YEARS in the export frame, exactly like the .vla
    layers, so the frame objects sit under the same 'Earth add <holder>' +
    'standardSpace on' holders - but OBJ vertices are read as METRES, so the
    player puts 'scale 1 ly' on the mesh prototype (measured). 9 significant figures
    are float32-exact (dsexport.vla.fmt_coord_sig).
  * Meshes cannot morph, so the player switches shell frames per step
    ('intensity inherited' / 'intensity 0'); an explicit child intensity
    REPLACES the holder's, so no per-object crossfade - the holder toggle on
    the panel must keep working.

Topology: N_THETA stations x N_PHI azimuths. Station 0 is the nose (rho 0):
one apex vertex + a triangle fan; the remaining stations are rings joined by
quads (split into two triangles - Digistar's loader triangulates anyway, and
triangles keep every face planar). The tail end is left OPEN (the physical
tail is open too). Normals are the analytic surface normal of the profile,
rotated with the ring, pointing OUTWARD; cullMode is 'none' in the player so
winding cannot hide the surface.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from magneto import geo
from magneto.model import N_PHI, N_THETA

# same azimuth grid as magneto.model.boundaries.PHI_RAD (not imported: boundaries imports us)
PHI_RAD = np.linspace(0.0, 2.0 * math.pi, N_PHI, endpoint=False)

from .vla import fmt_coord_sig

# Rounded tail cap (shells only): N_CAP extra rings on a quarter ellipse whose
# axial semi-axis is CAP_LENGTH_FACTOR x the end radius, then an apex.
N_CAP = 5
CAP_LENGTH_FACTOR = 0.6


def _profile_normals(x: np.ndarray, rho: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Outward unit normal (n_x, n_rho) of the revolved profile at each station.

    Tangent t = (dx, drho) along the profile (nose -> tail); the outward normal
    in the (x, rho) half-plane is (drho, -dx) normalised - for a surface that
    closes toward -x this points away from the axis and toward +x at the nose.
    Central differences inside, one-sided at the ends.
    """
    dx = np.gradient(np.asarray(x, dtype=float))
    dr = np.gradient(np.asarray(rho, dtype=float))
    nx, nr = dr, -dx
    norm = np.hypot(nx, nr)
    norm[norm == 0] = 1.0
    nx, nr = nx / norm, nr / norm
    # nose: the profile derivative is degenerate (rho 0); the normal is +x.
    nx[0], nr[0] = 1.0, 0.0
    return nx, nr


def _lattice_normals(verts_grid: np.ndarray) -> np.ndarray:
    """Outward unit normals of a (n_station, N_PHI, 3) vertex grid, by cross product.

    `_profile_normals` above rotates a 2-D gradient about the axis, which is only
    valid for a surface of revolution. A 3-D magnetopause (Lin's cusp
    indentations, its tilt response) is not one, and the failure mode is the
    quiet kind: Digistar's angle-based-intensity shading is driven entirely by
    the normal, so wrong normals make the shell look wrong rather than raise.

    Central differences along the station axis (one-sided at the ends) and
    PERIODIC differences in azimuth, then du x dv.

    The sign is resolved ONCE for the whole grid, at the widest station where the
    true normal is most strongly radial and the test is best conditioned, then
    applied everywhere. Per-vertex sign tests are tempting and wrong: near the
    nose the normal is nearly axial, the radial test is ill-conditioned, and
    flipping a few vertices there would put creases in the shading.
    """
    g = np.asarray(verts_grid, dtype=float)
    n_st = g.shape[0]
    du = np.empty_like(g)
    du[1:-1] = g[2:] - g[:-2]
    du[0] = g[1] - g[0]
    du[-1] = g[-1] - g[-2]
    if n_st == 1:
        du[:] = np.array([1.0, 0.0, 0.0])
    dv = np.roll(g, -1, axis=1) - np.roll(g, 1, axis=1)      # azimuth wraps

    n = np.cross(du, dv)
    ln = np.linalg.norm(n, axis=2, keepdims=True)
    ln[ln == 0.0] = 1.0
    n = n / ln

    rho = np.hypot(g[..., 1], g[..., 2])
    i_wide = int(np.argmax(rho.mean(axis=1)))                # best-conditioned row
    ref = np.stack([np.zeros_like(g[i_wide, :, 0]), g[i_wide, :, 1], g[i_wide, :, 2]], axis=1)
    ref_len = np.linalg.norm(ref, axis=1, keepdims=True)
    ref_len[ref_len == 0.0] = 1.0
    if float(np.sum(np.einsum("ij,ij->i", n[i_wide], ref / ref_len))) < 0.0:
        n = -n
    return n


def lattice_vertices(x: np.ndarray, rho: np.ndarray,
                     cap: bool = False) -> tuple[np.ndarray, np.ndarray, list]:
    """surface_vertices for a FULL (N_THETA, N_PHI) lattice - radius varies with azimuth.

    Same vertex numbering, same triangle list and the same `cap` behaviour as the
    axisymmetric path, so an OBJ built here is interchangeable with one built
    there: only the positions and normals differ. That is what lets the Lin
    magnetopause drop in without touching the player, the manifest, the cook, or
    a single count test.

    `x` and `rho` are both (N_THETA, N_PHI) in GSM R_E. Station 0 must be the
    single nose point (Lin's r(theta=0) is genuinely azimuth-independent, which
    is asserted rather than assumed).
    """
    x = np.asarray(x, dtype=float)
    rho = np.asarray(rho, dtype=float)
    if x.shape != (N_THETA, N_PHI) or rho.shape != (N_THETA, N_PHI):
        raise ValueError(f"lattice must be ({N_THETA}, {N_PHI}), "
                         f"got {x.shape} / {rho.shape}")
    nose_spread = float(np.ptp(x[0])) + float(np.ptp(rho[0]))
    if nose_spread > 1e-6:
        raise ValueError(f"station 0 is not a single point: spread {nose_spread:.3e} R_E. "
                         "The nose apex is one vertex shared by every meridian.")

    cos_p, sin_p = np.cos(PHI_RAD), np.sin(PHI_RAD)
    xs = [x]
    rs = [rho]
    # The cap is a quarter-ellipse per azimuth, its axial length set by the MEAN
    # end radius so every meridian reaches the tail apex at the same x.
    cap_len = CAP_LENGTH_FACTOR * float(np.mean(rho[-1]))
    if cap:
        for j in range(1, N_CAP + 1):
            ph = 0.5 * math.pi * j / (N_CAP + 1)
            xs.append((x[-1] - cap_len * math.sin(ph))[None, :])
            rs.append((rho[-1] * math.cos(ph))[None, :])
    xg = np.concatenate(xs, axis=0)
    rg = np.concatenate(rs, axis=0)
    n_st = xg.shape[0]

    grid = np.stack([xg, rg * cos_p[None, :], rg * sin_p[None, :]], axis=2)
    norms_grid = _lattice_normals(grid)

    verts = [np.array([float(x[0, 0]), 0.0, 0.0])]
    norms = [np.array([1.0, 0.0, 0.0])]
    for i in range(1, n_st):
        for j in range(N_PHI):
            verts.append(grid[i, j])
            norms.append(norms_grid[i, j])
    if cap:
        verts.append(np.array([float(np.mean(x[-1])) - cap_len, 0.0, 0.0]))
        norms.append(np.array([-1.0, 0.0, 0.0]))
    verts = np.vstack(verts)
    norms = np.vstack(norms)

    def vid(i: int, j: int) -> int:
        return 1 + (i - 1) * N_PHI + (j % N_PHI)

    tris: list[tuple[int, int, int]] = []
    for j in range(N_PHI):                                  # nose fan
        tris.append((0, vid(1, j), vid(1, j + 1)))
    for i in range(1, n_st - 1):                            # ring bands
        for j in range(N_PHI):
            a, b = vid(i, j), vid(i, j + 1)
            c, d = vid(i + 1, j), vid(i + 1, j + 1)
            tris.append((a, c, d))
            tris.append((a, d, b))
    if cap:                                                 # tail fan
        apex = len(verts) - 1
        for j in range(N_PHI):
            tris.append((apex, vid(n_st - 1, j + 1), vid(n_st - 1, j)))
    return verts, norms, tris


def surface_vertices(x: np.ndarray, rho: np.ndarray,
                     cap: bool = False) -> tuple[np.ndarray, np.ndarray, list]:
    """Vertices (GSM R_E), outward normals (GSM) and triangle index triples.

    Vertex 0 is the nose apex; vertex 1 + (i-1)*N_PHI + j is station i
    (1..N_THETA-1, then the N_CAP cap rings when `cap`), azimuth j; with `cap`
    the last vertex is the tail apex. Indices are 0-based here; the OBJ writer
    adds 1.

    `cap` closes the tail end with a quarter-ellipse of N_CAP rings (axial
    length CAP_LENGTH_FACTOR x end radius) - visual closure for the shell, the
    profile itself is untouched.
    """
    x = np.asarray(x, dtype=float)
    rho = np.asarray(rho, dtype=float)
    # A 2-D profile is a lattice: radius varies with azimuth (Lin's cusps). It
    # takes a different code path entirely, so the axisymmetric one below is
    # bit-for-bit what it always was - byte-identity of every existing render is
    # a property of the dispatch, not something re-measured after each edit.
    if x.ndim == 2 or rho.ndim == 2:
        return lattice_vertices(x, rho, cap=cap)
    if x.shape != (N_THETA,) or rho.shape != (N_THETA,):
        raise ValueError(f"profile must be ({N_THETA},), got {x.shape} / {rho.shape}")
    nx, nr = _profile_normals(x, rho)
    xs, rs, nxs, nrs = list(x), list(rho), list(nx), list(nr)
    cap_len = CAP_LENGTH_FACTOR * float(rho[-1])
    if cap:
        for j in range(1, N_CAP + 1):
            ph = 0.5 * math.pi * j / (N_CAP + 1)
            xs.append(float(x[-1]) - cap_len * math.sin(ph))
            rs.append(float(rho[-1]) * math.cos(ph))
            # outward normal of the ellipse (x_end - L sin ph, rho_end cos ph)
            n = np.array([-float(rho[-1]) * math.sin(ph), cap_len * math.cos(ph)])
            n /= max(np.linalg.norm(n), 1e-12)
            nxs.append(n[0])
            nrs.append(n[1])
    n_st = len(xs)
    cos_p, sin_p = np.cos(PHI_RAD), np.sin(PHI_RAD)

    verts = [np.array([xs[0], 0.0, 0.0])]
    norms = [np.array([1.0, 0.0, 0.0])]
    for i in range(1, n_st):
        for j in range(N_PHI):
            verts.append(np.array([xs[i], rs[i] * cos_p[j], rs[i] * sin_p[j]]))
            norms.append(np.array([nxs[i], nrs[i] * cos_p[j], nrs[i] * sin_p[j]]))
    if cap:
        verts.append(np.array([float(x[-1]) - cap_len, 0.0, 0.0]))
        norms.append(np.array([-1.0, 0.0, 0.0]))
    verts = np.vstack(verts)
    norms = np.vstack(norms)

    def vid(i: int, j: int) -> int:
        return 1 + (i - 1) * N_PHI + (j % N_PHI)

    tris: list[tuple[int, int, int]] = []
    for j in range(N_PHI):                                  # nose fan
        tris.append((0, vid(1, j), vid(1, j + 1)))
    for i in range(1, n_st - 1):                            # ring bands
        for j in range(N_PHI):
            a, b = vid(i, j), vid(i, j + 1)
            c, d = vid(i + 1, j), vid(i + 1, j + 1)
            tris.append((a, c, d))
            tris.append((a, d, b))
    if cap:                                                 # tail fan
        apex = len(verts) - 1
        for j in range(N_PHI):
            tris.append((apex, vid(n_st - 1, j + 1), vid(n_st - 1, j)))
    return verts, norms, tris


# Digistar imports .obj through a Y-UP pipeline. Measured on the dome 2026-09-17 with a box
# probe: a vertex written at file (0, d, 0) is DRAWN where file (0, 0, d) is, i.e.
#     drawn = (x, -z, y)   for a file vertex (x, y, z)
# .obj carries no axis metadata to prevent this, and every other layer in this show is
# .vla/.vlc, which Digistar takes literally. The shells are the only .obj here, so they were
# the only layer drawn ROTATED against the field lines - and because the rotation is fixed in
# J2000 while the tail points anti-sunward, the visible error follows the SEASON: 10.7 deg on
# the 2026 look-back (reads as fine) but 66.3 deg on Gannon in May 2024 (reads as badly wrong).
# That is why it looked like a storm-specific physics fault for weeks. The exported files were
# correct throughout; only the rendering was rotated.
#
# Pre-rotate here so the importer's rotation lands back on the frame we meant:
#     drawn = (X, -Z, Y)  with  (X, Y, Z) = (x, z, -y)  =  (x, y, z)
OBJ_YUP_PRECOMPENSATE = True
OBJ_YUP_NOTE = ("yup-precompensated: vertices written (x, z, -y) so Digistar's Y-up .obj "
                "import draws them in the export frame - see dsexport/mesh.py")


def obj_precompensate(v):
    """(x, y, z) -> (x, z, -y). Its own inverse composed with Digistar's import rotation.

    A pure rotation (determinant +1), so normals take the same transform - no
    inverse-transpose needed.
    """
    v = np.asarray(v, dtype=float)
    return np.stack([v[..., 0], v[..., 2], -v[..., 1]], axis=-1)


def write_obj_surface(path, mesh: dict, frame: str = geo.DEFAULT_FRAME,
                      comments=None, coord_fmt=fmt_coord_sig,
                      cap: bool = False) -> dict:
    """Write one boundary shell as an OBJ in export-frame light years.

    `mesh` is a magneto.model.boundaries mesh dict ('x', 'rho' in GSM R_E, plus
    'r' for the header). Positions go through geo.gsm_re_to_frame exactly like
    the .vla polylines; normals are rotated with the same frame matrix but not
    scaled. Returns {'path', 'vertices', 'faces', 'nose_re'}.
    """
    path = Path(path)
    frame = geo.check_frame(frame)
    verts_re, norms_gsm, tris = surface_vertices(mesh["x"], mesh["rho"], cap=cap)
    pos_ly = geo.gsm_re_to_frame(verts_re, frame)
    # rotate normals only (unit vectors): frame(unit) - frame(0)
    n_rot = geo.gsm_re_to_frame(norms_gsm, frame) - geo.gsm_re_to_frame(np.zeros((1, 3)), frame)
    n_len = np.linalg.norm(n_rot, axis=1, keepdims=True)
    n_len[n_len == 0] = 1.0
    n_rot = n_rot / n_len

    # Cancel Digistar's Y-up .obj import rotation (see OBJ_YUP_PRECOMPENSATE above).
    # Applied LAST, to both positions and normals, so everything above this line stays in
    # the honest export frame and only the bytes on disk carry the compensation.
    if OBJ_YUP_PRECOMPENSATE:
        pos_ly = obj_precompensate(pos_ly)
        n_rot = obj_precompensate(n_rot)

    lines = [f"# {path.name} - boundary shell for Digistar effectModelClass (dsexport/mesh.py)",
             f"# frame {frame.upper()}: " + {"j2000": "geocentric equatorial, the WCS axes - sun-fixed by epoch",
                                          "gsm": "+X sunward, sun-fixed",
                                          "ecef": "Earth-fixed, rides the rotation"}.get(frame, frame)
             + " - coordinates in LIGHT YEARS like the .vla layers (the player applies scale 1 ly: OBJ units are metres)",
             f"# {len(verts_re)} vertices, {len(tris)} triangles, "
             f"nose {float(np.asarray(mesh['r'])[0].ravel()[0]):.3f} R_E"
             + (f", tail closed by a {N_CAP}-ring rounded cap ({CAP_LENGTH_FACTOR:g} x end radius)"
                if cap else ", tail end OPEN")]
    if OBJ_YUP_PRECOMPENSATE:
        lines.append("# " + OBJ_YUP_NOTE)
    for c in (comments or []):
        lines.append("# " + str(c).replace("\n", " "))
    for p in pos_ly:
        lines.append(f"v {coord_fmt(p[0])} {coord_fmt(p[1])} {coord_fmt(p[2])}")
    for n in n_rot:
        lines.append(f"vn {n[0]:.5f} {n[1]:.5f} {n[2]:.5f}")
    for a, b, c in tris:
        lines.append(f"f {a+1}//{a+1} {b+1}//{b+1} {c+1}//{c+1}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return {"path": path, "vertices": int(len(verts_re)), "faces": int(len(tris)),
            # mesh['r'] is (N_THETA,) for a revolved profile and (N_THETA, N_PHI)
            # for a lattice; row 0 is the single nose point either way.
            "nose_re": float(np.asarray(mesh["r"])[0].ravel()[0]), "capped": bool(cap)}


# ---------------------------------------------------------------- binary glTF
# Digistar accepts .glb for every surface class, effectModelClass included, and a
# shell costs 24.5% of what the same geometry costs as .obj: 146,000 B against
# 595,385 B, measured on a real Gannon frame. The saving is pure encoding - .obj
# writes 9-significant-figure ASCII, .glb writes the float32 the cook keeps anyway
# (DXGI_FORMAT_R32G32B32_FLOAT), so nothing the GPU would have retained is lost.
#
# Dome-verified 2026-09-20, each against a control so the reading means something:
#   renders identically  obj vs glb 43.95 dB against a 43.99 dB same-format control
#   orientation carries  the (x, z, -y) pre-compensation applies unchanged - glTF is
#                        Y-up like .obj and Digistar treats the two the same way
#   re-point cadence     312 steps at 0.8 s, motion band identical to .obj
#                        (min 21.59, max 30.41, spread 8.82 dB either way)
#
# Two traps worth keeping written down:
#   - The Users Guide's 3D Model Formats table says ".gltf .gltb". `.gltb` is
#     REJECTED ("'.gltb' is not a supported model file type"). Use .glb.
#   - The JSON chunk must be padded with SPACES and the BIN chunk with zeros.
#     NUL-padded JSON still loads in Digistar but no conforming parser will read it
#     back, which is how a verifier that should have caught a bad write instead
#     failed on 2,551 good ones.
MESH_FORMAT = "glb"                       # "glb" or "obj"; write_surface dispatches
GLB_JSON_PAD = b" "
GLB_BIN_PAD = bytes([0])


def _pad4(b: bytes, fill: bytes) -> bytes:
    return b + fill * ((4 - len(b) % 4) % 4)


# --- general structured lattices (plasmasphere, ring current) ----------------
# The boundary shells come from an (x, rho) PROFILE revolved about the Sun-Earth
# line, which is why write_obj_surface takes a profile. The plasmasphere and the
# ring current are not surfaces of revolution about that axis - they are already
# structured (u, v) lattices in their own model code:
#
#     plasmasphere  shell_points_sm  -> (N_MERIDIAN=50, N_RING=80)   dipole L-shell
#     ring current  torus_points_sm  -> (N_AZIMUTH=120, N_SECTION=50) torus
#
# so meshing them needs faces and normals over an existing grid, not new geometry.
# Both keep their FIXED row counts, which is what lets the layer stay on the morph
# contract's topology rules even though a mesh cuts rather than morphs.


def lattice_faces(n_u: int, n_v: int, wrap_u: bool = True, wrap_v: bool = False):
    """Triangles for a structured (n_u, n_v) grid; vertex index = u * n_v + v.

    wrap_u/wrap_v close the surface in that direction. The plasmasphere wraps in
    longitude only (it pinches to a point at each pole, so the v ends stay open);
    the torus wraps in both.
    """
    u_max = n_u if wrap_u else n_u - 1
    v_max = n_v if wrap_v else n_v - 1
    tris = np.empty((u_max * v_max * 2, 3), dtype=np.int64)
    k = 0
    for u in range(u_max):
        u1 = (u + 1) % n_u
        for v in range(v_max):
            v1 = (v + 1) % n_v
            a, b, c, d = u * n_v + v, u1 * n_v + v, u1 * n_v + v1, u * n_v + v1
            tris[k] = (a, b, c); tris[k + 1] = (a, c, d); k += 2
    return tris[:k]


def lattice_normals(grid: np.ndarray) -> np.ndarray:
    """Per-vertex normals from the grid's own tangents, pointed outward.

    np.gradient gives central differences inside and one-sided at the edges, which
    is what keeps the poles usable where the plasmasphere shell pinches. A normal
    that ends up pointing inward is flipped, because angle-based-intensity shading
    reads the normal and a sign error there looks like a lighting bug rather than
    an error.
    """
    n = np.cross(np.gradient(grid, axis=0), np.gradient(grid, axis=1))
    ln = np.linalg.norm(n, axis=-1, keepdims=True)
    ln[ln == 0] = 1.0
    n = n / ln
    r = grid / np.maximum(np.linalg.norm(grid, axis=-1, keepdims=True), 1e-30)
    n[(n * r).sum(-1) < 0] *= -1.0
    return n


def write_lattice_surface(path, grid_ly: np.ndarray, *, wrap_u=True, wrap_v=False,
                          comments=None, extras=None):
    """Write a structured (n_u, n_v, 3) lattice, already in export-frame LIGHT YEARS,
    as a surface in MESH_FORMAT. Applies the same Y-up pre-compensation as the shells.
    """
    grid_ly = np.asarray(grid_ly, dtype=float)
    n_u, n_v, _ = grid_ly.shape
    tris = lattice_faces(n_u, n_v, wrap_u, wrap_v)
    norms = lattice_normals(grid_ly)
    V = grid_ly.reshape(-1, 3)
    N = norms.reshape(-1, 3)
    if OBJ_YUP_PRECOMPENSATE:
        V = obj_precompensate(V)
        N = obj_precompensate(N)
    ex = {"lattice": f"{n_u} x {n_v}", "wrap_u": wrap_u, "wrap_v": wrap_v}
    ex.update(extras or {})
    return _write_mesh(Path(path), V, N, tris, comments=comments, extras=ex)


def _write_mesh(path: Path, V, N, tris, comments=None, extras=None) -> dict:
    """Shared tail for the lattice writers: emit MESH_FORMAT from raw arrays."""
    import json as _json
    import struct as _struct

    V = np.ascontiguousarray(V, dtype=np.float32)
    N = np.ascontiguousarray(N, dtype=np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)

    if MESH_FORMAT == "obj":
        path = path.with_suffix(".obj")
        out = [f"# {path.name} - structured lattice surface (dsexport/mesh.py)"]
        if OBJ_YUP_PRECOMPENSATE:
            out.append("# " + OBJ_YUP_NOTE)
        for k, v in (extras or {}).items():
            out.append(f"# {k}: {v}")
        for c in (comments or []):
            out.append("# " + str(c).replace("\n", " "))
        for p in V:
            out.append(f"v {fmt_coord_sig(p[0])} {fmt_coord_sig(p[1])} {fmt_coord_sig(p[2])}")
        for n in N:
            out.append(f"vn {n[0]:.5f} {n[1]:.5f} {n[2]:.5f}")
        for a, b, c in tris:
            out.append(f"f {a+1}//{a+1} {b+1}//{b+1} {c+1}//{c+1}")
        path.write_text("\n".join(out) + "\n", encoding="ascii")
        return {"path": path, "vertices": int(len(V)), "faces": int(len(tris))}

    path = path.with_suffix(".glb")
    dt, comp = (np.uint16, 5123) if len(V) < 65536 else (np.uint32, 5125)
    I = np.ascontiguousarray(tris, dtype=dt)
    bv, bn, bi = V.tobytes(), N.tobytes(), _pad4(I.tobytes(), GLB_BIN_PAD)
    blob = _pad4(bv + bn + bi, GLB_BIN_PAD)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(bv), "target": 34962},
             {"buffer": 0, "byteOffset": len(bv), "byteLength": len(bn), "target": 34962},
             {"buffer": 0, "byteOffset": len(bv) + len(bn), "byteLength": len(bi),
              "target": 34963}]
    acc = [{"bufferView": 0, "componentType": 5126, "count": int(len(V)), "type": "VEC3",
            "min": V.min(0).tolist(), "max": V.max(0).tolist()},
           {"bufferView": 1, "componentType": 5126, "count": int(len(N)), "type": "VEC3"},
           {"bufferView": 2, "componentType": comp, "count": int(I.size), "type": "SCALAR"}]
    ex = dict(extras or {})
    ex.update({"vertices": int(len(V)), "triangles": int(len(I))})
    if OBJ_YUP_PRECOMPENSATE:
        ex["yup"] = OBJ_YUP_NOTE
    if comments:
        ex["notes"] = [str(c).replace("\n", " ") for c in comments]
    g = {"asset": {"version": "2.0", "generator": "dsexport/mesh.py", "extras": ex},
         "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1},
                                     "indices": 2}]}],
         "buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": acc}
    js = _pad4(_json.dumps(g, separators=(",", ":")).encode("utf-8"), GLB_JSON_PAD)
    path.write_bytes(_struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
                     + _struct.pack("<II", len(js), 0x4E4F534A) + js
                     + _struct.pack("<II", len(blob), 0x004E4942) + blob)
    return {"path": path, "vertices": int(len(V)), "faces": int(len(I))}


def write_glb_surface(path, mesh: dict, frame: str = geo.DEFAULT_FRAME,
                      comments=None, coord_fmt=None, cap: bool = False) -> dict:
    """The same surface as write_obj_surface, written as binary glTF 2.0.

    `coord_fmt` is accepted and ignored: a .glb stores float32 directly, so there is
    no text precision to pick. --coord-digits still governs the .vla/.vlc layers; it
    simply has nothing to control here.
    """
    import json as _json
    import struct as _struct

    path = Path(path)
    frame = geo.check_frame(frame)
    verts_re, norms_gsm, tris = surface_vertices(mesh["x"], mesh["rho"], cap=cap)
    pos_ly = geo.gsm_re_to_frame(verts_re, frame)
    n_rot = geo.gsm_re_to_frame(norms_gsm, frame) - geo.gsm_re_to_frame(np.zeros((1, 3)), frame)
    n_len = np.linalg.norm(n_rot, axis=1, keepdims=True)
    n_len[n_len == 0] = 1.0
    n_rot = n_rot / n_len
    if OBJ_YUP_PRECOMPENSATE:
        pos_ly = obj_precompensate(pos_ly)
        n_rot = obj_precompensate(n_rot)

    V = np.ascontiguousarray(pos_ly, dtype=np.float32)
    N = np.ascontiguousarray(n_rot, dtype=np.float32)
    dtype, comp = (np.uint16, 5123) if len(V) < 65536 else (np.uint32, 5125)
    I = np.ascontiguousarray(np.asarray(tris), dtype=dtype)

    bv, bn, bi = V.tobytes(), N.tobytes(), _pad4(I.tobytes(), GLB_BIN_PAD)
    blob = _pad4(bv + bn + bi, GLB_BIN_PAD)
    views = [{"buffer": 0, "byteOffset": 0, "byteLength": len(bv), "target": 34962},
             {"buffer": 0, "byteOffset": len(bv), "byteLength": len(bn), "target": 34962},
             {"buffer": 0, "byteOffset": len(bv) + len(bn), "byteLength": len(bi),
              "target": 34963}]
    acc = [{"bufferView": 0, "componentType": 5126, "count": int(len(V)), "type": "VEC3",
            "min": V.min(0).tolist(), "max": V.max(0).tolist()},
           {"bufferView": 1, "componentType": 5126, "count": int(len(N)), "type": "VEC3"},
           {"bufferView": 2, "componentType": comp, "count": int(I.size), "type": "SCALAR"}]
    # The provenance the .obj carries in header comments has nowhere to live in a
    # binary container, so it goes to asset.extras - readable by any glTF parser,
    # which is what those comments were for.
    extras = {"frame": frame.upper(),
              "units": "light years (the player applies scale 1 ly)",
              "vertices": int(len(V)), "triangles": int(len(I)),
              "nose_re": float(np.asarray(mesh["r"])[0].ravel()[0]),
              "tail": f"closed by a {N_CAP}-ring rounded cap" if cap else "OPEN"}
    if OBJ_YUP_PRECOMPENSATE:
        extras["yup"] = OBJ_YUP_NOTE
    if comments:
        extras["notes"] = [str(c).replace("\n", " ") for c in comments]

    g = {"asset": {"version": "2.0", "generator": "dsexport/mesh.py", "extras": extras},
         "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1},
                                     "indices": 2}]}],
         "buffers": [{"byteLength": len(blob)}], "bufferViews": views, "accessors": acc}
    js = _pad4(_json.dumps(g, separators=(",", ":")).encode("utf-8"), GLB_JSON_PAD)
    glb = (_struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(blob))
           + _struct.pack("<II", len(js), 0x4E4F534A) + js
           + _struct.pack("<II", len(blob), 0x004E4942) + blob)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(glb)
    return {"path": path, "vertices": int(len(V)), "faces": int(len(I)),
            "nose_re": extras["nose_re"], "capped": bool(cap)}


def write_surface(path, mesh: dict, **kw) -> dict:
    """Write a shell in MESH_FORMAT. `path` may carry either extension - the
    constant decides, so no caller has to know which format is current."""
    path = Path(path)
    if MESH_FORMAT == "obj":
        return write_obj_surface(path.with_suffix(".obj"), mesh, **kw)
    return write_glb_surface(path.with_suffix(".glb"), mesh, **kw)


def validate_glb(path) -> tuple[bool, list[str]]:
    """Structural check for a binary glTF, the counterpart of validate_obj.

    Reads the container rather than trusting it: magic and version, the declared
    total length against the real file size, both chunk tags, JSON that parses
    STRICTLY (no stripping - NUL padding must fail here, that is the point), the
    accessors' element counts against the buffer views that back them, and every
    triangle index inside the vertex range. Also rejects a non-finite position,
    which is the one corruption that renders as geometry instead of an error.
    """
    import json as _json
    import struct as _struct

    path = Path(path)
    errors: list[str] = []
    try:
        b = path.read_bytes()
    except OSError as e:
        return False, [f"unreadable: {e}"]
    if len(b) < 20:
        return False, [f"too short for a glTF container ({len(b)} bytes)"]
    magic, ver, total = _struct.unpack("<III", b[:12])
    if magic != 0x46546C67:
        errors.append("not a glTF binary (bad magic)")
    if ver != 2:
        errors.append(f"glTF version {ver}, expected 2")
    if total != len(b):
        errors.append(f"header says {total} bytes, file is {len(b)}")
    if errors:
        return False, errors

    jlen, jtag = _struct.unpack("<II", b[12:20])
    if jtag != 0x4E4F534A:
        return False, ["first chunk is not JSON"]
    try:
        g = _json.loads(b[20:20 + jlen].decode("utf-8"))
    except Exception as e:                                  # noqa: BLE001
        return False, [f"JSON chunk does not parse: {e} "
                       "(NUL padding instead of spaces will land here)"]
    off = 20 + jlen
    if len(b) < off + 8:
        return False, ["no BIN chunk"]
    blen, btag = _struct.unpack("<II", b[off:off + 8])
    if btag != 0x004E4942:
        errors.append("second chunk is not BIN")
    blob = off + 8

    accs, views = g.get("accessors", []), g.get("bufferViews", [])
    if len(accs) < 2:
        errors.append(f"expected at least POSITION and indices, got {len(accs)} accessors")
        return not errors, errors
    pos, idx = accs[0], accs[-1]
    n_v, n_i = int(pos.get("count", 0)), int(idx.get("count", 0))
    if n_v == 0 or n_i == 0:
        errors.append(f"empty mesh: {n_v} vertices, {n_i} indices")
    if n_i % 3:
        errors.append(f"{n_i} indices is not a whole number of triangles")
    for k, a in enumerate(accs):
        v = views[a["bufferView"]]
        width = {5126: 4, 5125: 4, 5123: 2}.get(a["componentType"], 0)
        comps = {"VEC3": 3, "SCALAR": 1}.get(a["type"], 0)
        need = int(a["count"]) * width * comps
        if need > int(v["byteLength"]):
            errors.append(f"accessor {k} needs {need} B, its view holds {v['byteLength']}")
        if blob + int(v["byteOffset"]) + int(v["byteLength"]) > len(b):
            errors.append(f"accessor {k} runs past the end of the file")
    if errors:
        return False, errors

    vv = views[pos["bufferView"]]
    P = np.frombuffer(b, np.float32, count=n_v * 3, offset=blob + int(vv["byteOffset"]))
    if not np.all(np.isfinite(P)):
        errors.append("non-finite vertex position")
    iv = views[idx["bufferView"]]
    dt = np.uint16 if idx["componentType"] == 5123 else np.uint32
    I = np.frombuffer(b, dt, count=n_i, offset=blob + int(iv["byteOffset"]))
    if I.size and int(I.max()) >= n_v:
        errors.append(f"triangle index {int(I.max())} out of range for {n_v} vertices")
    return not errors, errors


def validate_obj(path) -> tuple[bool, list[str]]:
    """Cheap structural check: counts, index bounds, ASCII, no NaN."""
    errors: list[str] = []
    n_v = n_vn = n_f = 0
    text = Path(path).read_text(encoding="ascii", errors="strict")
    for ln in text.splitlines():
        if ln.startswith("v "):
            n_v += 1
            if "nan" in ln.lower() or "inf" in ln.lower():
                errors.append(f"non-finite vertex: {ln}")
        elif ln.startswith("vn "):
            n_vn += 1
        elif ln.startswith("f "):
            n_f += 1
            for tok in ln.split()[1:]:
                vi = int(tok.split("/")[0])
                if not (1 <= vi <= max(n_v, 1)):
                    errors.append(f"face index {vi} out of range (v {n_v}): {ln}")
                    break
    if n_v == 0 or n_f == 0:
        errors.append(f"empty mesh (v {n_v}, f {n_f})")
    if n_vn and n_vn != n_v:
        errors.append(f"normal count {n_vn} != vertex count {n_v}")
    # Digistar imports .obj Y-up, so an uncompensated shell is DRAWN rotated against every
    # .vla layer in the show - by an amount that depends on the date, which is why it read as
    # an intermittent physics fault for weeks instead of as a constant bug. Every render runs
    # this check, so a new code path that writes an .obj without the compensation fails here
    # rather than on the dome.
    if OBJ_YUP_PRECOMPENSATE and "yup-precompensated" not in text:
        errors.append("not yup-precompensated: this .obj will be DRAWN rotated against the "
                      "\u002evla layers - see dsexport/mesh.py OBJ_YUP_PRECOMPENSATE, and "
                      "tools/fix_obj_frame.py to convert an existing render")
    return (not errors), errors
