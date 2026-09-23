/* eslint-disable @typescript-eslint/naming-convention -- L = one bundle layer, as in the bundle README */
// =====================================================================
// Keyframe morph shared by every bundle layer
// =====================================================================
// The bundle ships uint16 positions per keyframe, quantised to a per-layer box.
// Two keyframes (A = floor, B = next) are bound as NORMALIZED Uint16 attributes
// -- the GPU sees 0..1 -- and the vertex shader does
//
//     p = uLo + mix(aPosA, aPosB, uMix) * (uHi - uLo)          (Earth radii)
//
// which is the bundle README's dequantise + mix() in one line. Alpha is mixed the
// same way and MULTIPLIED into the output: that is what makes a crossfaded line
// vanish instead of sweeping (bundle README, rule 2).
//
// A swap (the playhead crossing a keyframe) binds new SUBARRAY VIEWS of the one
// decoded ArrayBuffer; nothing is copied on the CPU for per-vertex layers.

import { BufferAttribute, BufferGeometry, Vector3 } from "three";

import type { LayerData } from "../data/bundle";

export interface Pair {
  a: number;
  b: number;
  mix: number;
}

/**
 * The (a, b, mix) a layer should show at global keyframe position `frameT`.
 * `map` is the layer's keyframe_map: its own keyframe i sits at global index
 * map[i]. Identity for every layer except the aurora (sparse, Kp-driven).
 */
export function pairFor(map: number[], frameT: number): Pair {
  const n = map.length;
  if (n === 1 || frameT <= map[0]) { return { a: 0, b: Math.min(1, n - 1), mix: 0 }; }
  if (frameT >= map[n - 1]) { return { a: n - 1, b: n - 1, mix: 0 }; }
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (map[mid] <= frameT) { lo = mid; } else { hi = mid; }
  }
  const span = map[hi] - map[lo];
  return { a: lo, b: hi, mix: span > 0 ? (frameT - map[lo]) / span : 0 };
}

/** GLSL: dequantise + mix. Needs aPosA, aPosB, uLo, uHi, uMix. */
export const MORPH_GLSL = /* glsl */ `
  attribute vec3 aPosA;
  attribute vec3 aPosB;
  uniform vec3 uLo;
  uniform vec3 uHi;
  uniform float uMix;
  vec3 morphPos() { return uLo + mix(aPosA, aPosB, uMix) * (uHi - uLo); }
`;

export function boxUniforms(L: LayerData): { uLo: { value: Vector3 }; uHi: { value: Vector3 }; uMix: { value: number } } {
  return {
    uLo: { value: new Vector3(...L.meta.pos_lo) },
    uHi: { value: new Vector3(...L.meta.pos_hi) },
    uMix: { value: 0 },
  };
}

/**
 * Per-vertex keyframe binding for Points and Meshes: one vertex per bundle
 * vertex, so A/B are plain subarrays.
 */
export class VertexMorph {
  readonly L: LayerData;
  private boundA = -1;
  private boundB = -1;

  constructor(L: LayerData, readonly geometry: BufferGeometry) {
    this.L = L;
    const n = L.meta.count;
    // Placeholders so the program links before the first bind.
    geometry.setAttribute("position", new BufferAttribute(new Float32Array(n * 3), 3));
    this.bind(0, Math.min(1, L.meta.keyframes - 1));
  }

  bind(a: number, b: number): void {
    if (a === this.boundA && b === this.boundB) { return; }
    const { L, geometry } = this;
    const n = L.meta.count;
    geometry.setAttribute("aPosA", new BufferAttribute(L.pos.subarray(a * n * 3, (a + 1) * n * 3), 3, true));
    geometry.setAttribute("aPosB", new BufferAttribute(L.pos.subarray(b * n * 3, (b + 1) * n * 3), 3, true));
    geometry.setAttribute("aAlphaA", new BufferAttribute(L.alpha.subarray(a * n, (a + 1) * n), 1, true));
    geometry.setAttribute("aAlphaB", new BufferAttribute(L.alpha.subarray(b * n, (b + 1) * n), 1, true));
    if (L.lum) {
      geometry.setAttribute("aLumA", new BufferAttribute(L.lum.subarray(a * n, (a + 1) * n), 1, true));
      geometry.setAttribute("aLumB", new BufferAttribute(L.lum.subarray(b * n, (b + 1) * n), 1, true));
    }
    this.boundA = a;
    this.boundB = b;
  }

  /** Bind the right pair for `frameT` and return the mix. */
  update(frameT: number): number {
    const p = pairFor(this.L.meta.keyframe_map, frameT);
    this.bind(p.a, p.b);
    return p.mix;
  }
}

/** Projection scale for point sizes: pixels per unit at view distance 1 (FOV pi/4 vertical). */
export function pxPerUnit(bufferHeight: number): number {
  return bufferHeight / (2 * Math.tan(Math.PI / 8));
}
