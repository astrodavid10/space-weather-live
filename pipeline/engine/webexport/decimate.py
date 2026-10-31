"""Decimation. Every function here is a STRIDE, never a resample.

That matters: a resampled vertex is a new number that has to be trusted, while a strided
one is bit-identical to a vertex the physics actually produced. `test_decimated_shell_is_a_subset`
asserts exactly that, so a future "improvement" that smooths or averages will fail the suite.

The shells are a structured lattice and dsexport/mesh.py::surface_vertices documents the
numbering: vertex 0 is the nose apex, vertex 1 + (i-1)*n_phi + j is ring i / azimuth j, and
with a cap the last vertex is the tail apex. So striding rings and azimuths is exact, and the
triangle list is rebuilt from the reduced grid rather than filtered from the old one.
"""
from __future__ import annotations

import numpy as np


def stride_points(n: int, keep: int) -> np.ndarray:
    """Indices of `keep` points spread evenly through n. Deterministic, endpoints included."""
    if keep >= n:
        return np.arange(n, dtype=np.int32)
    return np.unique(np.linspace(0, n - 1, keep).round().astype(np.int32))


def decimate_lines(frame, keep_lines: int, keep_verts: int):
    """Subsample polylines and their vertices.

    Returns (idx, starts, lengths): idx indexes the original arrays, starts/lengths describe
    the reduced polylines. Both endpoints of every kept line survive, so a field line never
    appears to stop short of the ionosphere or the magnetopause clip.
    """
    starts = frame.starts
    if starts.size == 0:
        idx = stride_points(frame.n, keep_lines * keep_verts)
        return idx, np.zeros(1, dtype=np.int32), np.array([idx.size], dtype=np.int32)

    lengths = frame.line_lengths()
    take = stride_points(starts.size, keep_lines)
    out, new_starts, new_lengths = [], [], []
    for li in take:
        s, ln = int(starts[li]), int(lengths[li])
        v = stride_points(ln, keep_verts) + s
        new_starts.append(len(out))
        new_lengths.append(v.size)
        out.append(v)
    return (np.concatenate(out).astype(np.int32),
            np.asarray(new_starts, dtype=np.int32),
            np.asarray(new_lengths, dtype=np.int32))


def shell_grid(n_verts: int, n_phi: int) -> tuple[int, bool]:
    """(n_rings, capped) for a shell of n_verts vertices at n_phi azimuths."""
    for capped in (True, False):
        body = n_verts - (2 if capped else 1)
        if body > 0 and body % n_phi == 0:
            return body // n_phi, capped
    raise ValueError(f"{n_verts} vertices is not a lattice at n_phi={n_phi}")


def decimate_shell(n_verts: int, n_phi: int, ring_stride: int, phi_stride: int):
    """Strided shell.

    Returns (idx, faces): idx indexes the original vertex array; faces are 0-based triangles
    into the REDUCED array, rebuilt for the coarser grid.
    """
    n_rings, capped = shell_grid(n_verts, n_phi)
    rings = np.arange(0, n_rings, ring_stride, dtype=np.int32)
    if rings[-1] != n_rings - 1:
        rings = np.append(rings, n_rings - 1)          # always keep the tail-most ring
    phis = np.arange(0, n_phi, phi_stride, dtype=np.int32)
    nr, npz = rings.size, phis.size

    idx = [np.int32(0)]                                 # nose apex
    for i in rings:
        idx.append(1 + i * n_phi + phis)
    if capped:
        idx.append(np.int32(n_verts - 1))               # tail apex
    idx = np.concatenate([np.atleast_1d(a) for a in idx]).astype(np.int32)

    def vid(ri, pj):                                    # into the REDUCED array
        return 1 + ri * npz + (pj % npz)

    faces = []
    for j in range(npz):                                # nose fan
        faces.append((0, vid(0, j), vid(0, j + 1)))
    for i in range(nr - 1):                             # quads between rings
        for j in range(npz):
            a, b = vid(i, j), vid(i, j + 1)
            c, d = vid(i + 1, j), vid(i + 1, j + 1)
            faces.append((a, c, d))
            faces.append((a, d, b))
    if capped:
        tail = 1 + nr * npz
        for j in range(npz):                            # tail fan
            faces.append((tail, vid(nr - 1, j + 1), vid(nr - 1, j)))
    return idx, np.asarray(faces, dtype=np.int32)


def _strided_axis(n: int, stride: int, wrap: bool) -> np.ndarray:
    """Indices kept along one lattice axis. An OPEN axis always keeps its last row (the
    plasmasphere's polar pinch); a wrapped axis does not need to, because the last kept
    row joins back to the first."""
    keep = np.arange(0, n, max(1, int(stride)), dtype=np.int32)
    if not wrap and keep[-1] != n - 1:
        keep = np.append(keep, np.int32(n - 1))
    return keep


def decimate_lattice(n_u: int, n_v: int, wrap_u: bool, wrap_v: bool,
                     stride_u: int, stride_v: int):
    """Strided structured (u, v) lattice - the ring current (torus, wraps both ways) and
    the plasmasphere (wraps in longitude only).

    Mirrors dsexport/mesh.py::lattice_faces exactly: vertex index = u * n_v + v, and each
    cell is the two triangles (a, b, c), (a, c, d) with a = (u, v), b = (u+1, v),
    c = (u+1, v+1), d = (u, v+1), so the winding (and therefore the face normal a web
    renderer derives) is the one the dome sees.

    Returns (idx, faces): idx indexes the ORIGINAL vertex array; faces are 0-based
    triangles into the REDUCED array.
    """
    us = _strided_axis(n_u, stride_u, wrap_u)
    vs = _strided_axis(n_v, stride_v, wrap_v)
    nu, nv = us.size, vs.size
    idx = (us[:, None] * n_v + vs[None, :]).ravel().astype(np.int32)

    u_max = nu if wrap_u else nu - 1
    v_max = nv if wrap_v else nv - 1
    if u_max <= 0 or v_max <= 0:
        return idx, np.zeros((0, 3), dtype=np.int32)
    u = np.arange(u_max, dtype=np.int32)[:, None]
    v = np.arange(v_max, dtype=np.int32)[None, :]
    u1 = (u + 1) % nu
    v1 = (v + 1) % nv
    a = u * nv + v
    b = u1 * nv + v
    c = u1 * nv + v1
    d = u * nv + v1
    faces = np.stack([np.stack([a, b, c], axis=-1),
                      np.stack([a, c, d], axis=-1)], axis=2).reshape(-1, 3)
    return idx, np.ascontiguousarray(faces, dtype=np.int32)


def pick_keyframes(times, epochs, n_keep: int, fine_windows=()):
    """Choose keyframe indices: dense inside fine_windows, sparse elsewhere.

    `fine_windows` are (start_s, end_s) in playback seconds - typically the shock arrival,
    the Bz minimum and the SYM-H floor, which `key_moments` in the render manifest already
    names, so the schedule is derived from the data rather than hand-picked.
    """
    times = np.asarray(times, dtype=float)
    n = times.size
    if n_keep >= n:
        return np.arange(n, dtype=np.int32)

    fine = np.zeros(n, dtype=bool)
    for a, b in fine_windows:
        fine |= (times >= a) & (times <= b)

    # Give the fine windows every other frame, then spread what is left over the remainder.
    fine_idx = np.flatnonzero(fine)[::2]
    budget = max(n_keep - fine_idx.size, 2)
    coarse_idx = stride_points(n, budget)
    return np.unique(np.concatenate([fine_idx, coarse_idx])).astype(np.int32)
