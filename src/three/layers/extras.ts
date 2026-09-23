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

const BEADS_PER_LINE = 6;
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
  const pts = createStaticPoints(cap, { sizeRe: 0.22, minPx: 2.2, maxPx: 7, renderOrder: 45 });
  const { pos_lo: lo, pos_hi: hi, count: n } = L.meta;
  const lum = L.lum as Uint8Array;

  function vert(k: number, i: number, out: number[]): void {
    const b = (k * n + i) * 3;
    out[0] = lo[0] + (L.pos[b] / 65535) * (hi[0] - lo[0]);
    out[1] = lo[1] + (L.pos[b + 1] / 65535) * (hi[1] - lo[1]);
    out[2] = lo[2] + (L.pos[b + 2] / 65535) * (hi[2] - lo[2]);
  }
  const a0 = [0, 0, 0], a1 = [0, 0, 0], b0 = [0, 0, 0], b1 = [0, 0, 0];

  return {
    points: pts,
    update(frameT, nowS) {
      const p = pairFor(L.meta.keyframe_map, frameT);
      const phase = 1 - ((nowS / TRAVERSE_S) % 1);
      let c = 0;
      for (let li = 0; li < nLines; li++) {
        const s = starts[li], len = lengths[li];
        if (len < 2) { continue; }
        for (let bead = 0; bead < BEADS_PER_LINE; bead++) {
          const f = (phase + bead / BEADS_PER_LINE) % 1;
          const x = f * (len - 1);
          const j = Math.min(len - 2, Math.floor(x));
          const t = x - j;
          const i = s + j;
          vert(p.a, i, a0); vert(p.a, i + 1, a1); vert(p.b, i, b0); vert(p.b, i + 1, b1);
          const m = p.mix;
          for (let d = 0; d < 3; d++) {
            const va = a0[d] + (a1[d] - a0[d]) * t;
            const vb = b0[d] + (b1[d] - b0[d]) * t;
            pts.positions[c * 3 + d] = va + (vb - va) * m;
          }
          const alA = L.alpha[p.a * n + i] / 255, alB = L.alpha[p.b * n + i] / 255;
          const lA = lum[p.a * n + i] / 255, lB = lum[p.b * n + i] / 255;
          const l = Math.min(1, (lA + (lB - lA) * m) * 1.3 + 0.25);
          for (let d = 0; d < 3; d++) {
            pts.colors[c * 3 + d] = Math.min(1, (L.color0[i * 3 + d] / 255) * l * 1.3);
          }
          pts.alphas[c] = (alA + (alB - alA) * m) * 0.95;
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
