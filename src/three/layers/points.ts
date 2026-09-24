// =====================================================================
// Morphing dot layers: WindParticles and AuroraOval
// =====================================================================
// Size is in EARTH RADII projected to pixels (distance from abs(mv.z): WWT's
// view space has +z in front -- sol footgun), clamped so a dot never vanishes
// far away or floods the screen close up. colour = color0 * lum (lum = density
// for wind, oval intensity for aurora).
//
// Two looks:
//  * "glow"  -- soft additive disc (the aurora).
//  * "solid" -- small crisp disc with NORMAL blending and an alpha floor, so a
//               parcel reads as an opaque grain of wind rather than a faint
//               haze (the wind; David 2026-09-24: "smaller dots, more opaque").
//
// RECYCLE CUT (wind only). The engine keeps a fixed pool of parcels: one that
// leaves down-tail is relaunched upstream. The dome crossfades that at its own
// 10-minute step, but the web keyframes are 10-30 minutes apart, so between two
// keyframes a recycled parcel was linearly interpolated from the tail all the
// way back past Earth to L1 -- the visible "snap back toward the Sun". Real
// parcels only ever move ANTI-sunward (upstream, through the sheath, down the
// cusp), so a sunward step between keyframes means a relaunch: the shader holds
// the parcel at its old place fading out, then at its new place fading in,
// instead of flying it back through the scene.

import {
  AdditiveBlending, BufferAttribute, BufferGeometry, NormalBlending, Points,
  ShaderMaterial, Vector3,
} from "three";

import type { LayerData } from "../../data/bundle";
import { VertexMorph, boxUniforms, pxPerUnit } from "../morph";

const VERT = /* glsl */ `
  precision highp float;
  attribute vec3 aPosA; attribute vec3 aPosB;
  attribute float aAlphaA; attribute float aAlphaB;
  attribute float aLumA; attribute float aLumB;
  attribute vec3 aColor;
  uniform vec3 uLo; uniform vec3 uHi; uniform float uMix;
  uniform float uSizeRe; uniform float uPxPerUnit; uniform float uMinPx; uniform float uMaxPx; uniform float uScale;
  uniform float uGain;
  uniform vec3 uAxis;          // unit sunward axis, equatorial (the magnetopause nose)
  uniform float uCut;          // 1 = detect recycles (wind)
  uniform float uCutSunwardRe; // a step this far SUNWARD is a relaunch
  varying vec3 vColor; varying float vAlpha;
  void main() {
    vec3 pA = uLo + aPosA * (uHi - uLo);
    vec3 pB = uLo + aPosB * (uHi - uLo);
    vec3 p = mix(pA, pB, uMix);
    float cut = 1.0;
    if (uCut > 0.5 && dot(pB - pA, uAxis) > uCutSunwardRe) {
      p = uMix < 0.5 ? pA : pB;
      cut = abs(1.0 - 2.0 * uMix);
    }
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    // Earth radii -> scene units is folded into modelViewMatrix; measure it once.
    float unit = length((modelViewMatrix * vec4(1.0, 0.0, 0.0, 0.0)).xyz);
    float px = uSizeRe * unit * uPxPerUnit / max(abs(mv.z), 1e-12);
    gl_PointSize = clamp(px, uMinPx, uMaxPx) * uScale;
    vAlpha = mix(aAlphaA, aAlphaB, uMix) * cut;
    vColor = aColor * mix(aLumA, aLumB, uMix) * uGain;
  }
`;

const FRAG_GLOW = /* glsl */ `
  precision highp float;
  uniform float uOpacity;
  varying vec3 vColor; varying float vAlpha;
  void main() {
    vec2 d = gl_PointCoord - 0.5;
    float r2 = dot(d, d);
    if (r2 > 0.25) { discard; }
    float a = vAlpha * uOpacity * (1.0 - smoothstep(0.08, 0.25, r2));
    if (a < 0.003) { discard; }
    gl_FragColor = vec4(vColor * a, a);
  }
`;

// Crisp disc, normal blending. uAlphaFloor lifts faint parcels (the bundle's
// alpha encodes density) without resurrecting HIDDEN ones: alpha ~0 stays 0.
const FRAG_SOLID = /* glsl */ `
  precision highp float;
  uniform float uOpacity; uniform float uAlphaFloor;
  varying vec3 vColor; varying float vAlpha;
  void main() {
    vec2 d = gl_PointCoord - 0.5;
    float r2 = dot(d, d);
    if (r2 > 0.25) { discard; }
    float edge = 1.0 - smoothstep(0.16, 0.25, r2);
    float shown = smoothstep(0.0, 0.06, vAlpha);
    float a = shown * mix(uAlphaFloor, 1.0, vAlpha) * uOpacity * edge;
    if (a < 0.01) { discard; }
    gl_FragColor = vec4(min(vColor, vec3(1.0)), a);
  }
`;

export interface MorphPoints {
  object3d: Points;
  update(frameT: number): void;
  setRes(w: number, h: number, dpr: number): void;
  setScale(s: number): void;
  setVisible(v: boolean): void;
  dispose(): void;
}

export interface MorphPointOptions {
  sizeRe: number; minPx: number; maxPx: number; gain?: number; opacity?: number; renderOrder: number;
  look?: "glow" | "solid";
  alphaFloor?: number;
  /** Shared sunward axis (equatorial, unit); required for the recycle cut. */
  axis?: Vector3;
  recycleCut?: boolean;
}

export function createMorphPoints(L: LayerData, opts: MorphPointOptions): MorphPoints {
  const geo = new BufferGeometry();
  const morph = new VertexMorph(L, geo);
  geo.setAttribute("aColor", new BufferAttribute(L.color0, 3, true));
  if (!L.lum) {
    const ones = new Uint8Array(L.meta.count).fill(255);
    geo.setAttribute("aLumA", new BufferAttribute(ones, 1, true));
    geo.setAttribute("aLumB", new BufferAttribute(ones, 1, true));
  }
  const solid = opts.look === "solid";
  const uniforms = {
    ...boxUniforms(L),
    uSizeRe: { value: opts.sizeRe }, uPxPerUnit: { value: 1000 },
    uMinPx: { value: opts.minPx }, uMaxPx: { value: opts.maxPx }, uScale: { value: 1 },
    uGain: { value: opts.gain ?? 1 }, uOpacity: { value: opts.opacity ?? 1 },
    uAlphaFloor: { value: opts.alphaFloor ?? 0 },
    uAxis: { value: opts.axis ?? new Vector3(1, 0, 0) },
    uCut: { value: opts.recycleCut && opts.axis ? 1 : 0 },
    uCutSunwardRe: { value: 10.0 },   // measured on Gannon: every sunward step is a >50 R_E relaunch
  };
  const mat = new ShaderMaterial({
    vertexShader: VERT, fragmentShader: solid ? FRAG_SOLID : FRAG_GLOW, uniforms,
    transparent: true, depthWrite: false, depthTest: true,
    blending: solid ? NormalBlending : AdditiveBlending,
  });
  const pts = new Points(geo, mat);
  pts.frustumCulled = false;
  pts.renderOrder = opts.renderOrder;
  return {
    object3d: pts,
    update(frameT) { uniforms.uMix.value = morph.update(frameT); },
    setRes(_w, h, dpr) {
      uniforms.uPxPerUnit.value = pxPerUnit(h);
      uniforms.uMinPx.value = opts.minPx * dpr;
      uniforms.uMaxPx.value = opts.maxPx * dpr;
    },
    setScale(s) { uniforms.uScale.value = s; },
    setVisible(v) { pts.visible = v; },
    dispose() { geo.dispose(); mat.dispose(); },
  };
}

// ---------------------------------------------------------------------
// Static (non-morphing) points: live OVATION oval, field-line pulse beads
// ---------------------------------------------------------------------

const VERT_STATIC = /* glsl */ `
  precision highp float;
  attribute vec3 aColor; attribute float aAlpha;
  uniform float uSizeRe; uniform float uPxPerUnit; uniform float uMinPx; uniform float uMaxPx; uniform float uScale;
  varying vec3 vColor; varying float vAlpha;
  void main() {
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
    float unit = length((modelViewMatrix * vec4(1.0, 0.0, 0.0, 0.0)).xyz);
    float px = uSizeRe * unit * uPxPerUnit / max(abs(mv.z), 1e-12);
    gl_PointSize = clamp(px, uMinPx, uMaxPx) * uScale;
    vColor = aColor; vAlpha = aAlpha;
  }
`;

export interface StaticPoints {
  object3d: Points;
  positions: Float32Array;
  colors: Float32Array;
  alphas: Float32Array;
  /** Call after editing the arrays; `count` <= capacity is drawn. */
  commit(count: number): void;
  setRes(w: number, h: number, dpr: number): void;
  setScale(s: number): void;
  setVisible(v: boolean): void;
  dispose(): void;
}

export function createStaticPoints(capacity: number, opts: {
  sizeRe: number; minPx: number; maxPx: number; opacity?: number; renderOrder: number;
  look?: "glow" | "solid"; alphaFloor?: number;
}): StaticPoints {
  const positions = new Float32Array(capacity * 3);
  const colors = new Float32Array(capacity * 3);
  const alphas = new Float32Array(capacity);
  const geo = new BufferGeometry();
  // BufferAttribute, NOT Float32BufferAttribute: the latter COPIES its array, so
  // writes to `positions` never reached the GPU and every bead (and the live
  // OVATION oval) drew at the origin with alpha 0.
  const pa = new BufferAttribute(positions, 3);
  const ca = new BufferAttribute(colors, 3);
  const aa = new BufferAttribute(alphas, 1);
  geo.setAttribute("position", pa);
  geo.setAttribute("aColor", ca);
  geo.setAttribute("aAlpha", aa);
  geo.setDrawRange(0, 0);
  const solid = opts.look === "solid";
  const uniforms = {
    uSizeRe: { value: opts.sizeRe }, uPxPerUnit: { value: 1000 },
    uMinPx: { value: opts.minPx }, uMaxPx: { value: opts.maxPx }, uScale: { value: 1 },
    uOpacity: { value: opts.opacity ?? 1 }, uAlphaFloor: { value: opts.alphaFloor ?? 0 },
  };
  const mat = new ShaderMaterial({
    vertexShader: VERT_STATIC, fragmentShader: solid ? FRAG_SOLID : FRAG_GLOW, uniforms,
    transparent: true, depthWrite: false, depthTest: true,
    blending: solid ? NormalBlending : AdditiveBlending,
  });
  const pts = new Points(geo, mat);
  pts.frustumCulled = false;
  pts.renderOrder = opts.renderOrder;
  return {
    object3d: pts, positions, colors, alphas,
    commit(count) {
      pa.needsUpdate = true; ca.needsUpdate = true; aa.needsUpdate = true;
      geo.setDrawRange(0, count);
    },
    setRes(_w, h, dpr) { uniforms.uPxPerUnit.value = pxPerUnit(h); uniforms.uMinPx.value = opts.minPx * dpr; uniforms.uMaxPx.value = opts.maxPx * dpr; },
    setScale(s) { uniforms.uScale.value = s; },
    setVisible(v) { pts.visible = v; },
    dispose() { geo.dispose(); mat.dispose(); },
  };
}
