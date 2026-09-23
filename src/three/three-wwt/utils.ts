// Vendored from cosmicds/three-wwt @ 80b95028d2b1e9ba7dbc117c314b25f535e80847
// (MIT — see LICENSE in this directory). Modified for Sol:
//  - direct named `three` imports instead of the setThree() injection
//    (restores tree-shaking; the npm package also bundles a duplicate WWT
//    engine, which is why we vendor the source at all)
//  - renderer sizing driven by the REAL drawing buffer (gl.drawingBufferWidth)
//    instead of canvas.clientWidth × a wwt pixel-ratio guess — the upstream
//    math breaks under our installHiDpiCanvas() shim (which makes canvas.width
//    report CSS pixels) and never tracks WWT canvas resizes
//  - createTHREERenderer throws WebGL2UnavailableError instead of crashing on
//    a null webgl2 context (WWT can fall back to WebGL1; three r163+ cannot)
//  - the camera carries ECLIPTIC_TO_WWT, so the three scene can be built in
//    true heliocentric ecliptic J2000 while WWT renders in its own left-handed
//    frame (see ../worldFrame.ts — do NOT remove without reading it, and note
//    the matrixWorldAutoUpdate line it depends on)

import {
  ACESFilmicToneMapping,
  AmbientLight,
  Matrix4,
  PerspectiveCamera,
  Scene,
  WebGLRenderer,
} from "three";
import { Matrix3d, RenderContext, WWTControl } from "@wwtelescope/engine";

import { ECLIPTIC_TO_WWT } from "../worldFrame";

interface DummyCanvasOptions {
  opacity?: number;
  attach?: boolean;
}

// Overlay canvases are not covered by installHiDpiCanvas(), so size their
// backing store from layout geometry × a capped device-pixel ratio.
const OVERLAY_MAX_DPR = 2;

function matchDimensions(source: HTMLCanvasElement, target: HTMLCanvasElement) {
  const rect = source.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio ?? 1, OVERLAY_MAX_DPR);
  target.style.width = `${rect.width}px`;
  target.style.height = `${rect.height}px`;
  target.width = Math.round(rect.width * dpr);
  target.height = Math.round(rect.height * dpr);
}

function setupResizeObserver(source: HTMLCanvasElement, target: HTMLCanvasElement): ResizeObserver {
  const observer = new ResizeObserver(() => {
    matchDimensions(source, target);
  });
  observer.observe(source);
  return observer;
}

export function createDummyCanvas(canvas: HTMLCanvasElement, options?: DummyCanvasOptions): [HTMLCanvasElement, ResizeObserver] {
  const dummy = document.createElement("canvas");
  matchDimensions(canvas, dummy);

  dummy.id = "three-js-canvas";
  dummy.style.position = "absolute";
  dummy.style.left = canvas.offsetLeft + "px";
  dummy.style.top = canvas.offsetTop + "px";

  const opacity = options?.opacity ?? 0;
  dummy.style.background = `rgba(0, 0, 0, ${opacity})`;
  dummy.style.pointerEvents = "none";

  const canvasZindex = canvas.style.zIndex;
  dummy.style.zIndex = canvasZindex ? `${canvasZindex + 10}` : "10";

  const observer = setupResizeObserver(canvas, dummy);

  if (options?.attach ?? true) {
    canvas.parentNode?.appendChild(dummy);
  }

  return [dummy, observer];
}

export function createTHREECamera(renderContext: RenderContext, far = 1): PerspectiveCamera {
  const camera = new PerspectiveCamera(75, renderContext.width / renderContext.height, renderContext.nearPlane, far);
  // Both matrices are overwritten from WWT every frame; three must not touch them.
  camera.matrixAutoUpdate = false;
  // LOAD-BEARING, and not the same flag as the line above. WebGLRenderer.render
  // calls `camera.updateMatrixWorld()` for any parent-less camera whose
  // matrixWorldAutoUpdate is true, and three's Camera override DECOMPOSES
  // matrixWorld and rebuilds matrixWorldInverse with the scale forced to
  // (1,1,1) "to be glTF conform". updateTHREECamera folds ECLIPTIC_TO_WWT into
  // the view, which decomposes to scale (-1,1,1) — so with this flag left true
  // three would strip the frame transform on every single frame and the Sun
  // would go back to sitting 90 deg out of the ecliptic. See ../worldFrame.ts.
  camera.matrixWorldAutoUpdate = false;
  return camera;
}

export function createTHREEScene(ambientLight = true): Scene {
  const scene = new Scene();
  if (ambientLight) {
    const light = new AmbientLight(0xffffff, 1);
    scene.add(light);
  }
  return scene;
}

export class WebGL2UnavailableError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WebGL2UnavailableError";
  }
}

export function createTHREERenderer(_control: WWTControl, canvas: HTMLCanvasElement): WebGLRenderer {
  // For target:"wwt" this returns WWT's OWN context (getContext returns the
  // existing context of the same type). If WWT fell back to WebGL1, webgl2
  // returns null — and three r163+ requires WebGL2 — so the caller must retry
  // with an overlay canvas of its own.
  const context = canvas.getContext("webgl2");
  if (context === null) {
    throw new WebGL2UnavailableError(
      "Canvas has no WebGL2 context (WWT may be on WebGL1); use target:'overlay'.");
  }
  const renderer = new WebGLRenderer({
    canvas,
    context,
    antialias: true,
    alpha: true,
  });
  // Real size comes from syncRendererSize() each frame; pixelRatio stays 1 so
  // setViewport units are device pixels. NEVER renderer.setSize() on a shared
  // canvas — see makeSizeSync below for why (it broke mobile registration).
  renderer.setPixelRatio(1);
  renderer.setViewport(0, 0, context.drawingBufferWidth, context.drawingBufferHeight);
  renderer.setClearColor(0x000000, 0);

  renderer.toneMapping = ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1;

  return renderer;
}

// Track the true drawing-buffer size (WWT resizes its canvas whenever the
// window changes; installHiDpiCanvas scales it behind the engine's back).
// Cheap: two integer reads per frame, viewport update only on change.
//
// CRITICAL: this must NOT call renderer.setSize(). setSize writes
// canvas.width/height — on the shared canvas those are WWT's to manage, and
// under installHiDpiCanvas the shimmed setter multiplies by dpr, so
// setSize(physicalW, …) re-doubles the backing store; WWT then shrinks it
// back next frame, and three ends up with a viewport covering only the
// bottom-left quadrant of the buffer. Symptom: on any DPR>1 screen (every
// phone) the overlay renders offset far from WWT's Sun. The drawing buffer is
// already correct — only three's viewport needs to match it.
export function makeSizeSync(renderer: WebGLRenderer): () => void {
  const gl = renderer.getContext();
  let lastW = 0;
  let lastH = 0;
  return function syncRendererSize() {
    const w = gl.drawingBufferWidth;
    const h = gl.drawingBufferHeight;
    if (w !== lastW || h !== lastH) {
      renderer.setViewport(0, 0, w, h);
      lastW = w;
      lastH = h;
    }
  };
}

export function wwtMatrixToTHREE(mat: Matrix3d): Matrix4 {
  // WWT matrices are row-vector/D3D convention; three is column-vector.
  // Element-by-element transpose.
  const matrix = new Matrix4();
  matrix.set(
    mat.get_m11(), mat.get_m21(), mat.get_m31(), mat.get_m41(),
    mat.get_m12(), mat.get_m22(), mat.get_m32(), mat.get_m42(),
    mat.get_m13(), mat.get_m23(), mat.get_m33(), mat.get_m43(),
    mat.get_m14(), mat.get_m24(), mat.get_m34(), mat.get_m44(),
  );
  return matrix;
}

export function updateTHREECamera(camera: PerspectiveCamera, renderContext: RenderContext): void {
  camera.projectionMatrix.copy(wwtMatrixToTHREE(renderContext.get_projection()));
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();

  // View, then the frame swap: the scene is in true ecliptic J2000 and WWT is
  // not (../worldFrame.ts), so what three must draw with is
  //     V_effective = V_wwt . ECLIPTIC_TO_WWT
  // and the camera's own world matrix is its exact inverse. ECLIPTIC_TO_WWT is
  // an involution, so `premultiply` on matrixWorld is that inverse — cheaper
  // and exact, where a second invert() would only be nearly so.
  camera.matrixWorldInverse.copy(wwtMatrixToTHREE(renderContext.get_view()));
  camera.matrixWorld.copy(camera.matrixWorldInverse).invert();
  camera.matrixWorldInverse.multiply(ECLIPTIC_TO_WWT);
  camera.matrixWorld.premultiply(ECLIPTIC_TO_WWT);
  camera.matrixWorldNeedsUpdate = false;
}
