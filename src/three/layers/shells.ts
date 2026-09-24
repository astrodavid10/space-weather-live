// =====================================================================
// Morphing translucent surfaces: magnetopause, bow shock, ring current,
// plasmasphere -- and the magnetosheath fill
// =====================================================================
// Every keyframe of a mesh has the same vertex count and topology (bundle
// README, "Meshes can be interpolated too"), so the same mix() as the lines
// applies and the web shells can be smoother than the dome's.
//
// Shading is a view-dependent rim (Fresnel-ish) on a flat colour: a thin
// translucent surface reads as an edge-on glow, which is how the dome draws it.
// Normals come from screen-space derivatives of the view position, so they
// are always right for the interpolated shape and cost no attribute; abs()
// makes the result independent of the face winding and WWT's mirrored frame.
//
// The plasmasphere is an L-shell surface r = L cos^2(lat) that PINCHES TO THE
// GEOCENTRE at the poles; fragments with r < uClipRe are discarded (bundle README).

import { BufferAttribute, BufferGeometry, DoubleSide, Mesh, NormalBlending, AdditiveBlending, ShaderMaterial, Vector3 } from "three";

import type { LayerData } from "../../data/bundle";
import { MORPH_GLSL, VertexMorph, boxUniforms } from "../morph";

const VERT = /* glsl */ `
  precision highp float;
  ${MORPH_GLSL}
  attribute float aAlphaA; attribute float aAlphaB;
  varying vec3 vView; varying float vR; varying float vAlpha; varying vec3 vPos;
  void main() {
    vec3 p = morphPos();
    vR = length(p);
    vPos = p;
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    vView = mv.xyz;
    vAlpha = mix(aAlphaA, aAlphaB, uMix);
    gl_Position = projectionMatrix * mv;
  }
`;

const FRAG = /* glsl */ `
  precision highp float;
  uniform vec3 uColor; uniform float uOpacity; uniform float uRim; uniform float uClipRe;
  uniform vec3 uAxis; uniform float uFadeFrom; uniform float uFadeTo; uniform float uFadeMin;
  varying vec3 vView; varying float vR; varying float vAlpha; varying vec3 vPos;
  void main() {
    if (vR < uClipRe) { discard; }
    // Down-tail fade: full strength sunward of uFadeFrom (R_E along the Sun
    // axis), uFadeMin by uFadeTo. The dome ramps its shells from the nose to
    // the tail the same way (boundaries.py OPACITY_NOSE -> OPACITY_TAIL).
    float x = dot(vPos, uAxis);
    float fade = mix(uFadeMin, 1.0, smoothstep(uFadeTo, uFadeFrom, x));
    vec3 n = normalize(cross(dFdx(vView), dFdy(vView)));
    float facing = abs(dot(n, normalize(-vView)));
    float rim = pow(1.0 - facing, 2.0);
    float a = uOpacity * vAlpha * fade * (0.35 + uRim * rim);
    gl_FragColor = vec4(uColor * (0.55 + 1.2 * rim), clamp(a, 0.0, 1.0));
  }
`;

export interface MorphShell {
  object3d: Mesh;
  update(frameT: number): void;
  setVisible(v: boolean): void;
  dispose(): void;
}

export interface ShellOptions {
  /** Display colour. The bundle's color0 is the DOME holder colour (dark on
   *  purpose, the dome lights it); the web brightens it with the same hue. */
  color: [number, number, number];
  opacity: number;
  rim: number;
  clipRe?: number;
  renderOrder: number;
  additive?: boolean;
  /** Shared sunward axis (equatorial, unit) and the down-tail fade, R_E along it. */
  axis?: Vector3;
  fade?: { from: number; to: number; min: number };
}

/** One layer's shared morph (reused by the sheath, which draws the bow shock twice). */
export function createMorphShell(L: LayerData, opts: ShellOptions, shared?: { geometry: BufferGeometry; morph: VertexMorph }): MorphShell & { geometry: BufferGeometry; morph: VertexMorph } {
  let geometry: BufferGeometry, morph: VertexMorph;
  if (shared) {
    ({ geometry, morph } = shared);
  } else {
    geometry = new BufferGeometry();
    morph = new VertexMorph(L, geometry);
    geometry.setIndex(new BufferAttribute(L.faces as Uint16Array, 1));
  }
  const uniforms = {
    ...boxUniforms(L),
    uColor: { value: new Vector3(...opts.color) },
    uOpacity: { value: opts.opacity },
    uRim: { value: opts.rim },
    uClipRe: { value: opts.clipRe ?? 0 },
    uAxis: { value: opts.axis ?? new Vector3(1, 0, 0) },
    uFadeFrom: { value: opts.fade?.from ?? 1e6 },
    uFadeTo: { value: opts.fade?.to ?? -1e6 },
    uFadeMin: { value: opts.fade?.min ?? 1 },
  };
  const mat = new ShaderMaterial({
    vertexShader: VERT, fragmentShader: FRAG, uniforms,
    transparent: true, depthWrite: false, depthTest: true, side: DoubleSide,
    blending: opts.additive ? AdditiveBlending : NormalBlending,
  });
  const mesh = new Mesh(geometry, mat);
  mesh.frustumCulled = false;
  mesh.renderOrder = opts.renderOrder;
  return {
    object3d: mesh, geometry, morph,
    update(frameT) { uniforms.uMix.value = morph.update(frameT); },
    setVisible(v) { mesh.visible = v; },
    dispose() { if (!shared) { geometry.dispose(); } mat.dispose(); },
  };
}

/** Brighten a dome holder colour (0..255) to a web display colour, keeping its hue. */
export function displayColor(rgb: ArrayLike<number>, target = 0.95): [number, number, number] {
  const r = rgb[0] / 255, g = rgb[1] / 255, b = rgb[2] / 255;
  const m = Math.max(r, g, b, 1e-6);
  return [(r / m) * target, (g / m) * target, (b / m) * target];
}
