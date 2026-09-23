/* eslint-disable @typescript-eslint/naming-convention -- A0/B0 = endpoint 0 in keyframes A/B; S = segment count; L = bundle layer */
// =====================================================================
// Screen-space quad lines with keyframe morph (FieldLines, IMFLines)
// =====================================================================
// gl.lineWidth is 1 px on almost every GPU, and the dome panel's "field line
// width" slider needs real width, so every polyline SEGMENT is an instanced quad
// expanded to uWidthPx in the vertex shader. Line2/LineMaterial would need the
// positions dequantised on the CPU every frame; this keeps the uint16 morph.
//
// Per instance (one segment i -> i+1 of one polyline): both endpoints in both
// keyframes (normalized uint16), both alphas and lums, and the endpoint colour.
// Segments never cross polyline boundaries (line_starts / line_lengths).
// Gathered on the CPU at each keyframe swap: ~5 k segments, microseconds.

import {
  AdditiveBlending, InstancedBufferAttribute, InstancedBufferGeometry, Float32BufferAttribute,
  Mesh, ShaderMaterial, Vector2,
} from "three";

import type { LayerData } from "../../data/bundle";
import { boxUniforms, pairFor } from "../morph";

const VERT = /* glsl */ `
  precision highp float;
  attribute vec2 corner;            // x: 0 = start, 1 = end; y: -1 / +1 side
  attribute vec3 aA0; attribute vec3 aB0;
  attribute vec3 aA1; attribute vec3 aB1;
  attribute vec2 aAlpha;            // (A, B), same for both ends of a segment's start vertex
  attribute vec2 aAlpha1;
  attribute vec2 aLum;
  attribute vec2 aLum1;
  attribute vec3 aColor;
  attribute vec3 aColor1;
  uniform vec3 uLo; uniform vec3 uHi; uniform float uMix;
  uniform vec2 uRes; uniform float uWidthPx; uniform float uGain;
  varying vec3 vColor; varying float vAlpha; varying float vEdge;
  vec3 deq(vec3 a, vec3 b) { return uLo + mix(a, b, uMix) * (uHi - uLo); }
  void main() {
    vec3 p0 = deq(aA0, aB0);
    vec3 p1 = deq(aA1, aB1);
    vec4 c0 = projectionMatrix * modelViewMatrix * vec4(p0, 1.0);
    vec4 c1 = projectionMatrix * modelViewMatrix * vec4(p1, 1.0);
    float end = corner.x;
    // WWT's projection is left-handed: w = +z_view, positive in front.
    if (c0.w <= 0.0 || c1.w <= 0.0) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); vAlpha = 0.0; return; }
    vec2 s0 = c0.xy / c0.w * uRes;
    vec2 s1 = c1.xy / c1.w * uRes;
    vec2 d = s1 - s0;
    float len = length(d);
    vec2 dir = len > 1e-6 ? d / len : vec2(1.0, 0.0);
    vec2 nrm = vec2(-dir.y, dir.x);
    vec4 c = end < 0.5 ? c0 : c1;
    c.xy += nrm * corner.y * uWidthPx / uRes * c.w;
    gl_Position = c;
    float a = end < 0.5 ? mix(aAlpha.x, aAlpha.y, uMix) : mix(aAlpha1.x, aAlpha1.y, uMix);
    float l = end < 0.5 ? mix(aLum.x, aLum.y, uMix) : mix(aLum1.x, aLum1.y, uMix);
    vColor = (end < 0.5 ? aColor : aColor1) * l * uGain;
    vAlpha = a;
    vEdge = corner.y;
  }
`;

const FRAG = /* glsl */ `
  precision highp float;
  uniform float uOpacity;
  varying vec3 vColor; varying float vAlpha; varying float vEdge;
  void main() {
    float soft = 1.0 - smoothstep(0.55, 1.0, abs(vEdge));
    float a = vAlpha * soft * uOpacity;
    if (a < 0.003) { discard; }
    gl_FragColor = vec4(vColor * a, a);
  }
`;

export interface QuadLines {
  object3d: Mesh;
  /** Segment index -> [line, vertexStart] for pulse beads. */
  update(frameT: number): void;
  setRes(w: number, h: number): void;
  setWidth(px: number): void;
  setVisible(v: boolean): void;
  dispose(): void;
}

export function createQuadLines(L: LayerData, opts: { widthPx: number; gain?: number; opacity?: number; renderOrder: number }): QuadLines {
  const n = L.meta.count;
  const starts = L.lineStarts as Uint32Array;
  const lengths = L.lineLengths as Uint32Array;
  // Segment endpoint vertex indices.
  const seg0: number[] = [];
  for (let li = 0; li < starts.length; li++) {
    for (let j = 0; j < lengths[li] - 1; j++) { seg0.push(starts[li] + j); }
  }
  const S = seg0.length;

  const geo = new InstancedBufferGeometry();
  geo.setAttribute("corner", new Float32BufferAttribute([0, -1, 0, 1, 1, -1, 1, 1], 2));
  geo.setIndex([0, 2, 1, 1, 2, 3]);
  geo.instanceCount = S;

  const A0 = new Uint16Array(S * 3), B0 = new Uint16Array(S * 3), A1 = new Uint16Array(S * 3), B1 = new Uint16Array(S * 3);
  const al0 = new Uint8Array(S * 2), al1 = new Uint8Array(S * 2), lu0 = new Uint8Array(S * 2), lu1 = new Uint8Array(S * 2);
  const col0 = new Uint8Array(S * 3), col1 = new Uint8Array(S * 3);
  for (let s = 0; s < S; s++) {
    const i = seg0[s];
    for (let c = 0; c < 3; c++) {
      col0[s * 3 + c] = L.color0[i * 3 + c];
      col1[s * 3 + c] = L.color0[(i + 1) * 3 + c];
    }
  }
  const mk = (arr: Uint16Array | Uint8Array, size: number) => {
    const a = new InstancedBufferAttribute(arr, size, true);
    return a;
  };
  const attrs = {
    aA0: mk(A0, 3), aB0: mk(B0, 3), aA1: mk(A1, 3), aB1: mk(B1, 3),
    aAlpha: mk(al0, 2), aAlpha1: mk(al1, 2), aLum: mk(lu0, 2), aLum1: mk(lu1, 2),
  };
  for (const [k, v] of Object.entries(attrs)) { geo.setAttribute(k, v); }
  geo.setAttribute("aColor", mk(col0, 3));
  geo.setAttribute("aColor1", mk(col1, 3));

  const lum = L.lum as Uint8Array;
  let boundA = -1, boundB = -1;
  function bind(a: number, b: number): void {
    if (a === boundA && b === boundB) { return; }
    const pa = a * n * 3, pb = b * n * 3, sa = a * n, sb = b * n;
    for (let s = 0; s < S; s++) {
      const i = seg0[s], j = i + 1;
      for (let c = 0; c < 3; c++) {
        A0[s * 3 + c] = L.pos[pa + i * 3 + c];
        B0[s * 3 + c] = L.pos[pb + i * 3 + c];
        A1[s * 3 + c] = L.pos[pa + j * 3 + c];
        B1[s * 3 + c] = L.pos[pb + j * 3 + c];
      }
      al0[s * 2] = L.alpha[sa + i]; al0[s * 2 + 1] = L.alpha[sb + i];
      al1[s * 2] = L.alpha[sa + j]; al1[s * 2 + 1] = L.alpha[sb + j];
      lu0[s * 2] = lum[sa + i]; lu0[s * 2 + 1] = lum[sb + i];
      lu1[s * 2] = lum[sa + j]; lu1[s * 2 + 1] = lum[sb + j];
    }
    for (const v of Object.values(attrs)) { v.needsUpdate = true; }
    boundA = a; boundB = b;
  }

  const uniforms = {
    ...boxUniforms(L),
    uRes: { value: new Vector2(1, 1) },
    uWidthPx: { value: opts.widthPx },
    uGain: { value: opts.gain ?? 1.0 },
    uOpacity: { value: opts.opacity ?? 1.0 },
  };
  const mat = new ShaderMaterial({
    vertexShader: VERT, fragmentShader: FRAG, uniforms,
    transparent: true, depthWrite: false, depthTest: true, blending: AdditiveBlending,
  });
  const mesh = new Mesh(geo, mat);
  mesh.frustumCulled = false;
  mesh.renderOrder = opts.renderOrder;
  bind(0, Math.min(1, L.meta.keyframes - 1));

  return {
    object3d: mesh,
    update(frameT: number) {
      const p = pairFor(L.meta.keyframe_map, frameT);
      bind(p.a, p.b);
      uniforms.uMix.value = p.mix;
    },
    setRes(w, h) { uniforms.uRes.value.set(w / 2, h / 2); },
    setWidth(px) { uniforms.uWidthPx.value = px; },
    setVisible(v) { mesh.visible = v; },
    dispose() { geo.dispose(); mat.dispose(); },
  };
}
