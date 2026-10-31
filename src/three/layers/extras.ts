/* eslint-disable @typescript-eslint/naming-convention -- dipole maths uses the textbook symbols (L-shell, N) */
// =====================================================================
// Client-side layers: Earth occluder, field-line pulses, textbook dipole
// =====================================================================

import {
  BufferGeometry, Float32BufferAttribute, Group, LineBasicMaterial, LineSegments, Mesh,
  MeshBasicMaterial, SphereGeometry,
} from "three";

import type { LayerData } from "../../data/bundle";
import { pairFor } from "../morph";
import { StaticPoints, createStaticPoints } from "./points";

// ---------------------------------------------------------------------
// Earth occluder
// ---------------------------------------------------------------------
// WWT's depth buffer is cleared before our pass (three-wwt/setupThreeWWT.ts:
// its values are engine-internal), so WWT's Earth cannot hide our far-side
// geometry. This sphere writes depth and nothing else, at r = 0.995 R_E so the
// aurora at 1.017 and the field-line footpoints at 1.02 always win.

export function createEarthOccluder(): Mesh {
  const m = new Mesh(
    new SphereGeometry(0.995, 64, 32),
    new MeshBasicMaterial({ colorWrite: false, depthWrite: true, depthTest: true }),
  );
  m.renderOrder = -10;
  m.frustumCulled = false;
  return m;
}

// ---------------------------------------------------------------------
// Field pulses
// ---------------------------------------------------------------------
// Beads gliding along +B on the traced lines -- the dome's FieldPulses layer,
// which the web bundle deliberately does not ship (README: "a pulse is a bead
// gliding along +B on a field line you already have"). The engine's line vertex
// index runs ALONG -B (magneto/model/fieldpulses.py), so beads move toward
// DECREASING index. Their speed is illustrative (a steady ~14 s per traverse),
// not a physical drift, and the layer hint says only that they show direction.
//
// ARC-LENGTH, NOT VERTEX INDEX. The bundle's decimated lines are NOT evenly
// spaced: Gannon desktop runs a repeating 1-1-2 pattern (0.23, 0.23, 0.46 R_E),
// so a bead stepping a constant number of VERTICES per second doubled its speed
// on every third segment -- roughly once a second, a rhythmic lurch that read as
// a dropped-frame stutter (David 2026-10-31). Beads now sit at a constant
// FRACTION OF ARC LENGTH, located separately on keyframe A and keyframe B and
// then mixed, so the motion is continuous across keyframe swaps too. Cumulative
// arc length is built lazily, once per keyframe (110 x 6720 floats on desktop).

const BEADS_PER_LINE = 4;
const TRAVERSE_S = 14;

export interface Pulses {
  points: StaticPoints;
  update(frameT: number, nowS: number): void;
  setVisible(v: boolean): void;
  dispose(): void;
}

export function createPulses(L: LayerData): Pulses {
  const starts = L.lineStarts as Uint32Array;
  const lengths = L.lineLengths as Uint32Array;
  const nLines = starts.length;
  const cap = nLines * BEADS_PER_LINE;
  // Near-white beads tinted by the line's hue, NORMAL-blended on top of the
  // additive lines: an additive bead the colour of its own line vanished into it
  // (David 2026-09-24: "the field pulses are not visible at all").
  const pts = createStaticPoints(cap, { sizeRe: 0.32, minPx: 3.2, maxPx: 7.5, renderOrder: 45, look: "solid", alphaFloor: 0.75 });
  const { pos_lo: lo, pos_hi: hi, count: n, keyframes: nK } = L.meta;
  const lum = L.lum as Uint8Array;
  const sx = (hi[0] - lo[0]) / 65535, sy = (hi[1] - lo[1]) / 65535, sz = (hi[2] - lo[2]) / 65535;

  /** Normalised cumulative arc length (0 at a line's first vertex, 1 at its
   *  last) for every vertex of every keyframe, filled on first use. */
  const cum = new Float32Array(nK * n);
  const built = new Uint8Array(nK);
  function ensureArc(k: number): void {
    if (built[k]) { return; }
    built[k] = 1;
    const P = L.pos;
    for (let li = 0; li < nLines; li++) {
      const s = starts[li], len = lengths[li];
      if (len < 1) { continue; }
      const o = k * n + s;
      cum[o] = 0;
      let acc = 0;
      for (let j = 1; j < len; j++) {
        const b = (o + j) * 3;
        const dx = (P[b] - P[b - 3]) * sx, dy = (P[b + 1] - P[b - 2]) * sy, dz = (P[b + 2] - P[b - 1]) * sz;
        acc += Math.sqrt(dx * dx + dy * dy + dz * dz);
        cum[o + j] = acc;
      }
      // A degenerate (zero-length) line falls back to even vertex spacing.
      for (let j = 1; j < len; j++) { cum[o + j] = acc > 0 ? cum[o + j] / acc : j / (len - 1); }
    }
  }

  /** Point at arc fraction f on line (s, len) of keyframe k -> out; returns the
   *  vertex index i (segment i -> i+1) and writes the in-segment t to seg[0]. */
  const seg = [0];
  function along(k: number, s: number, len: number, f: number, out: number[]): number {
    const o = k * n + s;
    // Largest j in [0, len-2] with cum[o+j] <= f.
    let a = 0, z = len - 1;
    while (z - a > 1) {
      const mid = (a + z) >> 1;
      if (cum[o + mid] <= f) { a = mid; } else { z = mid; }
    }
    const c0 = cum[o + a], c1 = cum[o + a + 1];
    const t = c1 > c0 ? Math.min(1, Math.max(0, (f - c0) / (c1 - c0))) : 0;
    const b = (o + a) * 3;
    const P = L.pos;
    out[0] = lo[0] + (P[b] + (P[b + 3] - P[b]) * t) * sx;
    out[1] = lo[1] + (P[b + 1] + (P[b + 4] - P[b + 1]) * t) * sy;
    out[2] = lo[2] + (P[b + 2] + (P[b + 5] - P[b + 2]) * t) * sz;
    seg[0] = t;
    return s + a;
  }
  const pa = [0, 0, 0], pb = [0, 0, 0];

  return {
    points: pts,
    update(frameT, nowS) {
      const p = pairFor(L.meta.keyframe_map, frameT);
      ensureArc(p.a); ensureArc(p.b);
      const phase = 1 - ((nowS / TRAVERSE_S) % 1);
      const m = p.mix;
      let c = 0;
      for (let li = 0; li < nLines; li++) {
        const s = starts[li], len = lengths[li];
        if (len < 2) { continue; }
        for (let bead = 0; bead < BEADS_PER_LINE; bead++) {
          const f = (phase + bead / BEADS_PER_LINE) % 1;
          const ia = along(p.a, s, len, f, pa);
          const ta = seg[0];
          const ib = along(p.b, s, len, f, pb);
          const tb = seg[0];
          for (let d = 0; d < 3; d++) { pts.positions[c * 3 + d] = pa[d] + (pb[d] - pa[d]) * m; }
          // Alpha and luminance interpolated along the segment too, so a bead
          // never flickers as it crosses a vertex.
          const alA = (L.alpha[p.a * n + ia] + (L.alpha[p.a * n + ia + 1] - L.alpha[p.a * n + ia]) * ta) / 255;
          const alB = (L.alpha[p.b * n + ib] + (L.alpha[p.b * n + ib + 1] - L.alpha[p.b * n + ib]) * tb) / 255;
          const lA = (lum[p.a * n + ia] + (lum[p.a * n + ia + 1] - lum[p.a * n + ia]) * ta) / 255;
          const lB = (lum[p.b * n + ib] + (lum[p.b * n + ib + 1] - lum[p.b * n + ib]) * tb) / 255;
          const l = 0.75 + 0.25 * (lA + (lB - lA) * m);
          const ic = (ta < 0.5 ? ia : ia + 1) * 3;
          for (let d = 0; d < 3; d++) {
            const hue = L.color0[ic + d] / 255;
            pts.colors[c * 3 + d] = Math.min(1, (0.45 * hue + 0.55) * l);
          }
          // A bead on a hidden (crossfading) line hides with it.
          pts.alphas[c] = alA + (alB - alA) * m;
          c++;
        }
      }
      pts.commit(c);
    },
    setVisible(v) { pts.setVisible(v); },
    dispose() { pts.dispose(); },
  };
}

// ---------------------------------------------------------------------
// Textbook dipole
// ---------------------------------------------------------------------
// The field a textbook draws: a tilted centred dipole, r = L cos^2(lambda), with
// no solar wind at all. It is built once in GEOGRAPHIC coordinates and turned by
// GMST each frame (the dipole corotates with Earth), so it can be compared with
// the traced Tsyganenko lines, which are what the measured wind actually makes.
// Pole: 80.7 N, 72.7 W -- the IGRF centred-dipole pole for the mid-2020s,
// the same constant the dome's zhang_paxton.py uses. Honesty label in the UI:
// "for comparison -- not the real field".

const POLE_LAT = 80.7;
const POLE_LON = -72.7;

export function createTextbookDipole(): { object3d: Group; setGmst(deg: number): void; setVisible(v: boolean): void; dispose(): void } {
  const plat = (POLE_LAT * Math.PI) / 180, plon = (POLE_LON * Math.PI) / 180;
  const zAxis = [Math.cos(plat) * Math.cos(plon), Math.cos(plat) * Math.sin(plon), Math.sin(plat)];
  // Orthonormal magnetic frame: x toward the pole's meridian, y = z x x.
  const ref = [0, 0, 1];
  let xAxis = [ref[1] * zAxis[2] - ref[2] * zAxis[1], ref[2] * zAxis[0] - ref[0] * zAxis[2], ref[0] * zAxis[1] - ref[1] * zAxis[0]];
  const xn = Math.hypot(xAxis[0], xAxis[1], xAxis[2]);
  xAxis = xAxis.map((v) => v / xn);
  const yAxis = [zAxis[1] * xAxis[2] - zAxis[2] * xAxis[1], zAxis[2] * xAxis[0] - zAxis[0] * xAxis[2], zAxis[0] * xAxis[1] - zAxis[1] * xAxis[0]];

  const verts: number[] = [];
  const Ls = [2, 3, 4.5, 6.5, 9];
  const N_MER = 12;
  for (let m = 0; m < N_MER; m++) {
    const phi = (m / N_MER) * 2 * Math.PI;
    for (const L of Ls) {
      const lamMax = Math.acos(Math.sqrt(1 / L));        // where r = 1
      const steps = 48;
      let prev: number[] | null = null;
      for (let k = 0; k <= steps; k++) {
        const lam = -lamMax + (2 * lamMax * k) / steps;
        const r = L * Math.cos(lam) ** 2;
        const u = r * Math.cos(lam) * Math.cos(phi), v = r * Math.cos(lam) * Math.sin(phi), w = r * Math.sin(lam);
        const p = [0, 1, 2].map((d) => u * xAxis[d] + v * yAxis[d] + w * zAxis[d]);
        if (prev) { verts.push(...prev, ...p); }
        prev = p;
      }
    }
  }
  const geo = new BufferGeometry();
  geo.setAttribute("position", new Float32BufferAttribute(verts, 3));
  const mat = new LineBasicMaterial({ color: 0xb8bfd6, transparent: true, opacity: 0.45, depthWrite: false });
  const lines = new LineSegments(geo, mat);
  lines.renderOrder = 32;
  lines.frustumCulled = false;
  const g = new Group();
  g.add(lines);
  return {
    object3d: g,
    setGmst(deg) { g.rotation.set(0, 0, (deg * Math.PI) / 180); },
    setVisible(v) { g.visible = v; },
    dispose() { geo.dispose(); mat.dispose(); },
  };
}
