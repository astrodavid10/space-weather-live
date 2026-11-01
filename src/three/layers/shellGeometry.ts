/* eslint-disable @typescript-eslint/naming-convention -- L = one bundle layer; N, M, R, F = vertex count, ring size, ring count, faces */
// =====================================================================
// Smooth shell geometry: per-keyframe positions + vertex normals, refined
// =====================================================================
// The shells used to light themselves with screen-space derivatives
// (cross(dFdx, dFdy) of the view position). That is one flat normal per
// triangle -- every facet of a 36-around mesh showed -- and it collapses when
// the camera flies through a surface: neighbouring pixels quantise to the same
// view position, the cross product is zero, normalize() returns NaN and the
// GPU paints the whole near triangle white.
//
// So each keyframe is expanded on the CPU, once, into Float32 positions and
// area-weighted VERTEX normals, and the shader mixes A/B normals exactly as it
// mixes positions. The magnetopause and bow shock have the engine's
// apex-and-rings topology (vertex 0 the subsolar apex, R rings of M, last
// vertex the tail cap; decimate_shell keeps it), which is a regular grid, so
// they are also REFINED with centripetal Catmull-Rom splines -- 3x around, 2x along -- for
// round silhouettes. Other meshes (ring current, plasmasphere) keep their
// vertices and get smooth normals, welded across duplicate seam vertices.
//
// Keyframes are expanded lazily into a few recycled slots: playback moves
// forward one keyframe at a time, and the next one is expanded while the browser
// is idle, so a swap during playback is normally just a re-bind.

import { BufferAttribute, BufferGeometry } from "three";

import type { LayerData } from "../../data/bundle";
import { pairFor } from "../morph";

const AROUND = 3;   // refinement factor around each ring
const ALONG = 2;    // ... and between rings
const SLOTS = 4;

interface Grid { M: number; R: number }

/** Apex + R rings of M + cap, with only grid-neighbour edges -- or null. */
export function ringGrid(L: LayerData): Grid | null {
  const F = L.faces;
  const N = L.meta.count;
  if (!F) { return null; }
  let M = 0;
  for (let f = 0; f < F.length; f += 3) {
    if (F[f] === 0 || F[f + 1] === 0 || F[f + 2] === 0) { M++; }
  }
  if (M < 3 || (N - 2) % M !== 0) { return null; }
  const R = (N - 2) / M;
  if (R < 2 || F.length / 3 !== 2 * M * R) { return null; }
  const ring = (v: number): number => (v === 0 ? -1 : v === N - 1 ? R : Math.floor((v - 1) / M));
  const col = (v: number): number => (v - 1) % M;
  for (let f = 0; f < F.length; f += 3) {
    for (let e = 0; e < 3; e++) {
      const a = F[f + e], b = F[f + ((e + 1) % 3)];
      const ra = ring(a), rb = ring(b);
      if (ra < 0 || rb < 0 || ra === R || rb === R) { continue; }
      const dc = Math.abs(col(a) - col(b));
      if (Math.abs(ra - rb) > 1 || Math.min(dc, M - dc) > 1) { return null; }
    }
  }
  return { M, R };
}

/** sqrt of the xyz distance between two points: the centripetal knot step. */
function knot(a: Float32Array, i: number, b: Float32Array, j: number): number {
  const d = Math.hypot(b[j] - a[i], b[j + 1] - a[i + 1], b[j + 2] - a[i + 2]);
  return Math.max(Math.sqrt(d), 1e-4);
}

/**
 * CENTRIPETAL Catmull-Rom (Barry-Goldman) between p1 and p2, written to
 * out[o..o+c); knots from xyz only. Not uniform: the shells' rings are 0.6 R_E
 * apart on the dayside and 10 R_E apart down-tail, and the uniform spline
 * overshoots at that jump -- the surface folds back on itself, and a fold seen
 * edge-on is a bright rim line across the shell (measured at x = 0 on Gannon).
 */
function cr(p0: Float32Array, i0: number, p1: Float32Array, i1: number, p2: Float32Array, i2: number,
  p3: Float32Array, i3: number, t: number, c: number, out: Float32Array, o: number): void {
  const t1 = knot(p0, i0, p1, i1), t2 = t1 + knot(p1, i1, p2, i2), t3 = t2 + knot(p2, i2, p3, i3);
  const u = t1 + t * (t2 - t1);
  // The pyramid A -> B -> C, folded into one weight per control point.
  const a10 = (t1 - u) / t1, a11 = u / t1;
  const a21 = (t2 - u) / (t2 - t1), a22 = (u - t1) / (t2 - t1);
  const a32 = (t3 - u) / (t3 - t2), a33 = (u - t2) / (t3 - t2);
  const b1a = (t2 - u) / t2, b1b = u / t2;
  const b2a = (t3 - u) / (t3 - t1), b2b = (u - t1) / (t3 - t1);
  const ca = (t2 - u) / (t2 - t1), cb = (u - t1) / (t2 - t1);
  const w0 = ca * b1a * a10;
  const w1 = ca * (b1a * a11 + b1b * a21) + cb * b2a * a21;
  const w2 = ca * b1b * a22 + cb * (b2a * a22 + b2b * a32);
  const w3 = cb * b2b * a33;
  for (let k = 0; k < c; k++) {
    out[o + k] = w0 * p0[i0 + k] + w1 * p1[i1 + k] + w2 * p2[i2 + k] + w3 * p3[i3 + k];
  }
}

interface Slot {
  key: number;
  used: number;
  pos: BufferAttribute;
  nrm: BufferAttribute;
  alpha: BufferAttribute;
}

/**
 * Drop-in for VertexMorph on shells: binds aPosA/B (R_E, Float32), aNrmA/B and
 * aAlphaA/B (0..1) for the keyframe pair at frameT, and owns the index.
 */
export class ShellMorph {
  readonly n: number;
  readonly refined: boolean;
  private readonly grid: Grid | null;
  private readonly index: Uint16Array | Uint32Array;
  /** Original vertex -> first vertex at the same quantised position (normal welding). */
  private readonly weld: Uint32Array | null;
  private readonly src: Float32Array;     // one keyframe, xyz + alpha per original vertex
  private readonly slots: Slot[] = [];
  private clock = 0;
  private pending = false;
  private boundA = -1;
  private boundB = -1;
  // Refinement scratch: rings refined around, xyz+alpha.
  private readonly ringsAround: Float32Array | null;

  constructor(readonly L: LayerData, readonly geometry: BufferGeometry) {
    this.grid = ringGrid(L);
    this.refined = this.grid !== null;
    const N = L.meta.count;
    this.src = new Float32Array(N * 4);
    if (this.grid) {
      const { M, R } = this.grid;
      const m = M * AROUND, r = (R - 1) * ALONG + 1;
      this.n = 2 + m * r;
      this.ringsAround = new Float32Array(R * m * 4);
      this.index = gridIndex(m, r, this.n);
      this.weld = null;
    } else {
      this.n = N;
      this.ringsAround = null;
      this.index = L.faces as Uint16Array | Uint32Array;
      this.weld = weldMap(L);
    }
    geometry.setIndex(new BufferAttribute(this.index, 1));
    // `position` only sizes the draw (three reads the count from it); never uploaded twice.
    geometry.setAttribute("position", new BufferAttribute(new Float32Array(this.n * 3), 3));
    this.bind(0, Math.min(1, L.meta.keyframes - 1));
  }

  update(frameT: number): number {
    const p = pairFor(this.L.meta.keyframe_map, frameT);
    this.bind(p.a, p.b);
    return p.mix;
  }

  private bind(a: number, b: number): void {
    if (a === this.boundA && b === this.boundB) { return; }
    const sa = this.slot(a, b, b), sb = this.slot(b, a, a);
    const g = this.geometry;
    g.setAttribute("aPosA", sa.pos); g.setAttribute("aPosB", sb.pos);
    g.setAttribute("aNrmA", sa.nrm); g.setAttribute("aNrmB", sb.nrm);
    g.setAttribute("aAlphaA", sa.alpha); g.setAttribute("aAlphaB", sb.alpha);
    this.boundA = a;
    this.boundB = b;
    this.prefetch(b + 1);
  }

  /**
   * Expand keyframe k while the browser is idle, so forward playback finds it
   * ready at the next swap (an expansion is ~3 ms per refined shell on a desktop,
   * several times that on a phone -- a visible hitch if it lands in a frame).
   */
  private prefetch(k: number): void {
    if (this.pending || k >= this.L.meta.keyframes || this.slots.some((x) => x.key === k)) { return; }
    this.pending = true;
    const run = (): void => {
      this.pending = false;
      if (k !== this.boundA && k !== this.boundB) { this.slot(k, this.boundA, this.boundB); }
    };
    const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number };
    if (w.requestIdleCallback) { w.requestIdleCallback(run, { timeout: 250 }); } else { window.setTimeout(run, 0); }
  }

  /** The slot holding keyframe k, expanding it into the least recently used slot (never one holding k1 or k2). */
  private slot(k: number, k1: number, k2: number): Slot {
    const now = ++this.clock;
    let s = this.slots.find((x) => x.key === k);
    if (s) { s.used = now; return s; }
    if (this.slots.length < SLOTS) {
      s = {
        key: -1, used: 0,
        pos: new BufferAttribute(new Float32Array(this.n * 3), 3),
        nrm: new BufferAttribute(new Float32Array(this.n * 3), 3),
        alpha: new BufferAttribute(new Float32Array(this.n), 1),
      };
      this.slots.push(s);
    } else {
      s = this.slots.filter((x) => x.key !== k1 && x.key !== k2).reduce((p, q) => (q.used < p.used ? q : p));
    }
    this.expand(k, s.pos.array as Float32Array, s.nrm.array as Float32Array, s.alpha.array as Float32Array);
    s.pos.needsUpdate = true; s.nrm.needsUpdate = true; s.alpha.needsUpdate = true;
    s.key = k;
    s.used = now;
    return s;
  }

  private expand(k: number, pos: Float32Array, nrm: Float32Array, alpha: Float32Array): void {
    const { L, src } = this;
    const N = L.meta.count;
    const { pos_lo: lo, pos_hi: hi } = L.meta;
    const q = L.pos, al = L.alpha;
    const sx = (hi[0] - lo[0]) / 65535, sy = (hi[1] - lo[1]) / 65535, sz = (hi[2] - lo[2]) / 65535;
    for (let i = 0, b = k * N * 3, ab = k * N; i < N; i++, b += 3) {
      src[4 * i] = lo[0] + q[b] * sx;
      src[4 * i + 1] = lo[1] + q[b + 1] * sy;
      src[4 * i + 2] = lo[2] + q[b + 2] * sz;
      src[4 * i + 3] = al[ab + i] / 255;
    }
    if (this.grid) {
      this.refine(pos, alpha);
    } else {
      for (let i = 0; i < N; i++) {
        pos[3 * i] = src[4 * i]; pos[3 * i + 1] = src[4 * i + 1]; pos[3 * i + 2] = src[4 * i + 2];
        alpha[i] = src[4 * i + 3];
      }
    }
    vertexNormals(pos, this.index, nrm, this.weld);
  }

  /** Catmull-Rom the apex/ring grid: AROUND x per ring (cyclic), then ALONG x between rings. */
  private refine(pos: Float32Array, alpha: Float32Array): void {
    const { M, R } = this.grid as Grid;
    const src = this.src, ra = this.ringsAround as Float32Array;
    const m = M * AROUND, r = (R - 1) * ALONG + 1, n = this.n;
    // 1. around: ring j's original vertex v sits at refined column v*AROUND.
    for (let ring = 0; ring < R; ring++) {
      const base = 1 + ring * M;
      for (let c = 0; c < m; c++) {
        const v = Math.floor(c / AROUND), t = (c % AROUND) / AROUND;
        const at = (d: number): number => 4 * (base + ((v + d + M) % M));
        cr(src, at(-1), src, at(0), src, at(1), src, at(2), t, 4, ra, 4 * (ring * m + c));
      }
    }
    // 2. along each refined column. Before ring 0 the meridian runs through the
    //    apex; past the last ring it is extended straight (the cap is the tail's
    //    centre, not the next point down the tube).
    const tmp = new Float32Array(4);
    const write = (vi: number, from: Float32Array, o: number): void => {
      pos[3 * vi] = from[o]; pos[3 * vi + 1] = from[o + 1]; pos[3 * vi + 2] = from[o + 2];
      alpha[vi] = Math.min(1, Math.max(0, from[o + 3]));
    };
    write(0, src, 0);
    write(n - 1, src, 4 * (2 + M * R - 1));
    const ext = new Float32Array(4);
    for (let c = 0; c < m; c++) {
      const ringAt = (i: number): number => 4 * (i * m + c);
      for (let row = 0; row < r; row++) {
        const i = Math.floor(row / ALONG), t = (row % ALONG) / ALONG;
        const vi = 1 + row * m + c;
        if (t === 0) { write(vi, ra, ringAt(i)); continue; }
        const p0 = i === 0 ? src : ra, i0 = i === 0 ? 0 : ringAt(i - 1);
        let p3: Float32Array = ra, i3: number;
        if (i + 2 < R) {
          i3 = ringAt(i + 2);
        } else {
          for (let k = 0; k < 4; k++) { ext[k] = 2 * ra[ringAt(i + 1) + k] - ra[ringAt(i) + k]; }
          p3 = ext; i3 = 0;
        }
        cr(p0, i0, ra, ringAt(i), ra, ringAt(i + 1), p3, i3, t, 4, tmp, 0);
        write(vi, tmp, 0);
      }
    }
  }
}

/** Triangles of the refined grid: apex fan, quads between rows, cap fan. */
function gridIndex(m: number, r: number, n: number): Uint16Array | Uint32Array {
  const tris = 2 * m * r;
  const idx = n > 65535 ? new Uint32Array(tris * 3) : new Uint16Array(tris * 3);
  let o = 0;
  const v = (row: number, c: number): number => 1 + row * m + ((c + m) % m);
  for (let c = 0; c < m; c++) { idx[o++] = 0; idx[o++] = v(0, c); idx[o++] = v(0, c + 1); }
  for (let row = 0; row < r - 1; row++) {
    for (let c = 0; c < m; c++) {
      const a = v(row, c), b = v(row, c + 1), d = v(row + 1, c), e = v(row + 1, c + 1);
      idx[o++] = a; idx[o++] = d; idx[o++] = e;
      idx[o++] = a; idx[o++] = e; idx[o++] = b;
    }
  }
  for (let c = 0; c < m; c++) { idx[o++] = n - 1; idx[o++] = v(r - 1, c + 1); idx[o++] = v(r - 1, c); }
  return idx;
}

/** Vertices that share a quantised position in keyframe 0 (seams, pinches) share a normal. */
function weldMap(L: LayerData): Uint32Array | null {
  const N = L.meta.count;
  const seen = new Map<string, number>();
  const map = new Uint32Array(N);
  let dups = 0;
  for (let i = 0; i < N; i++) {
    const key = L.pos[3 * i] + "," + L.pos[3 * i + 1] + "," + L.pos[3 * i + 2];
    const first = seen.get(key);
    if (first === undefined) { seen.set(key, i); map[i] = i; } else { map[i] = first; dups++; }
  }
  return dups ? map : null;
}

/** Area-weighted vertex normals (unit; sign follows the winding, the shader uses abs). */
function vertexNormals(pos: Float32Array, idx: ArrayLike<number>, out: Float32Array, weld: Uint32Array | null): void {
  out.fill(0);
  const w = (i: number): number => (weld ? weld[i] : i);
  for (let f = 0; f < idx.length; f += 3) {
    const a = 3 * idx[f], b = 3 * idx[f + 1], c = 3 * idx[f + 2];
    const ux = pos[b] - pos[a], uy = pos[b + 1] - pos[a + 1], uz = pos[b + 2] - pos[a + 2];
    const vx = pos[c] - pos[a], vy = pos[c + 1] - pos[a + 1], vz = pos[c + 2] - pos[a + 2];
    const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
    const p = 3 * w(idx[f]), q = 3 * w(idx[f + 1]), s = 3 * w(idx[f + 2]);
    out[p] += nx; out[p + 1] += ny; out[p + 2] += nz;
    out[q] += nx; out[q + 1] += ny; out[q + 2] += nz;
    out[s] += nx; out[s + 1] += ny; out[s + 2] += nz;
  }
  const n = out.length / 3;
  for (let i = 0; i < n; i++) {
    const j = 3 * w(i);
    const x = out[j], y = out[j + 1], z = out[j + 2];
    const l = Math.hypot(x, y, z) || 1;
    out[3 * i] = x / l; out[3 * i + 1] = y / l; out[3 * i + 2] = z / l;
  }
}
