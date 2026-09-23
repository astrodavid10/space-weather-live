// =====================================================================
// Morphing dot layers: WindParticles and AuroraOval
// =====================================================================
// Soft round points, additive. Size is in EARTH RADII projected to pixels
// (distance from abs(mv.z): WWT's view space has +z in front -- sol footgun),
// clamped so a wind parcel never vanishes far away or floods the screen close up.
// colour = color0 * lum (lum = density for wind, oval intensity for aurora).

import { AdditiveBlending, BufferAttribute, BufferGeometry, Float32BufferAttribute, Points, ShaderMaterial } from "three";

import type { LayerData } from "../../data/bundle";
import { MORPH_GLSL, VertexMorph, boxUniforms, pxPerUnit } from "../morph";

const VERT = /* glsl */ `
  precision highp float;
  ${MORPH_GLSL}
  attribute float aAlphaA; attribute float aAlphaB;
  attribute float aLumA; attribute float aLumB;
  attribute vec3 aColor;
  uniform float uSizeRe; uniform float uPxPerUnit; uniform float uMinPx; uniform float uMaxPx; uniform float uScale;
  uniform float uGain;
  varying vec3 vColor; varying float vAlpha;
  void main() {
    vec4 mv = modelViewMatrix * vec4(morphPos(), 1.0);
    gl_Position = projectionMatrix * mv;
    // Earth radii -> scene units is folded into modelViewMatrix; measure it once.
    float unit = length((modelViewMatrix * vec4(1.0, 0.0, 0.0, 0.0)).xyz);
    float px = uSizeRe * unit * uPxPerUnit / max(abs(mv.z), 1e-12);
    gl_PointSize = clamp(px, uMinPx, uMaxPx) * uScale;
    vAlpha = mix(aAlphaA, aAlphaB, uMix);
    vColor = aColor * mix(aLumA, aLumB, uMix) * uGain;
  }
`;

const FRAG = /* glsl */ `
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

export interface MorphPoints {
  object3d: Points;
  update(frameT: number): void;
  setRes(w: number, h: number, dpr: number): void;
  setScale(s: number): void;
  setVisible(v: boolean): void;
  dispose(): void;
}

export function createMorphPoints(L: LayerData, opts: {
  sizeRe: number; minPx: number; maxPx: number; gain?: number; opacity?: number; renderOrder: number;
}): MorphPoints {
  const geo = new BufferGeometry();
  const morph = new VertexMorph(L, geo);
  geo.setAttribute("aColor", new BufferAttribute(L.color0, 3, true));
  if (!L.lum) {
    const ones = new Uint8Array(L.meta.count).fill(255);
    geo.setAttribute("aLumA", new BufferAttribute(ones, 1, true));
    geo.setAttribute("aLumB", new BufferAttribute(ones, 1, true));
  }
  const uniforms = {
    ...boxUniforms(L),
    uSizeRe: { value: opts.sizeRe }, uPxPerUnit: { value: 1000 },
    uMinPx: { value: opts.minPx }, uMaxPx: { value: opts.maxPx }, uScale: { value: 1 },
    uGain: { value: opts.gain ?? 1 }, uOpacity: { value: opts.opacity ?? 1 },
  };
  const mat = new ShaderMaterial({
    vertexShader: VERT, fragmentShader: FRAG, uniforms,
    transparent: true, depthWrite: false, depthTest: true, blending: AdditiveBlending,
  });
  const pts = new Points(geo, mat);
  pts.frustumCulled = false;
  pts.renderOrder = opts.renderOrder;
  let dprScale = 1;
  return {
    object3d: pts,
    update(frameT) { uniforms.uMix.value = morph.update(frameT); },
    setRes(_w, h, dpr) { uniforms.uPxPerUnit.value = pxPerUnit(h); dprScale = dpr; uniforms.uMinPx.value = opts.minPx * dpr; uniforms.uMaxPx.value = opts.maxPx * dpr; },
    setScale(s) { uniforms.uScale.value = s; void dprScale; },
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
}): StaticPoints {
  const positions = new Float32Array(capacity * 3);
  const colors = new Float32Array(capacity * 3);
  const alphas = new Float32Array(capacity);
  const geo = new BufferGeometry();
  const pa = new Float32BufferAttribute(positions, 3);
  const ca = new Float32BufferAttribute(colors, 3);
  const aa = new Float32BufferAttribute(alphas, 1);
  geo.setAttribute("position", pa);
  geo.setAttribute("aColor", ca);
  geo.setAttribute("aAlpha", aa);
  geo.setDrawRange(0, 0);
  const uniforms = {
    uSizeRe: { value: opts.sizeRe }, uPxPerUnit: { value: 1000 },
    uMinPx: { value: opts.minPx }, uMaxPx: { value: opts.maxPx }, uScale: { value: 1 },
    uOpacity: { value: opts.opacity ?? 1 },
  };
  const mat = new ShaderMaterial({
    vertexShader: VERT_STATIC, fragmentShader: FRAG, uniforms,
    transparent: true, depthWrite: false, depthTest: true, blending: AdditiveBlending,
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
