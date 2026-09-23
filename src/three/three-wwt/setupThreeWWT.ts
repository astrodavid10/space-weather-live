// Vendored from cosmicds/three-wwt @ 80b95028d2b1e9ba7dbc117c314b25f535e80847
// (MIT — see LICENSE in this directory). Modified for Sol:
//  - hooks WWTControl.addFrameCallback directly (present in engine ≥7.36)
//    instead of ScriptInterface.add_on_frame, so we don't depend on
//    WWTControl.scriptInterface being populated
//  - per-frame drawing-buffer size sync (see utils.makeSizeSync)
//  - no `three` injection option (direct imports)
//  - disconnect() also disposes the renderer, resize observer, and any
//    overlay canvas it created

import { Camera, Scene, WebGLRenderer } from "three";
import { ScriptInterface, WWTControl } from "@wwtelescope/engine";
import {
  createDummyCanvas,
  createTHREECamera,
  createTHREERenderer,
  createTHREEScene,
  makeSizeSync,
  updateTHREECamera,
} from "./utils";

export interface BaseUseThreeOptions {
  render?: boolean;
  far?: number;
  ambientLight?: boolean;
  // Called every frame after the camera sync, before rendering — the place
  // for animation updates that must use WWT's current-frame matrices.
  onBeforeRender?: () => void;
}

export interface WWTConnectionOptions {
  control?: WWTControl;
}

interface BasicRenderTargetOptions {
  target?: "wwt" | "overlay";
  canvas?: never;
}

interface CustomRenderTargetOptions {
  target: "custom";
  canvas: HTMLCanvasElement;
}

export type RenderTargetOptions = BasicRenderTargetOptions | CustomRenderTargetOptions;
export type UseThreeOptions = BaseUseThreeOptions & RenderTargetOptions & WWTConnectionOptions;

export interface ThreeWWTSetup {
  camera: Camera;
  renderer: WebGLRenderer;
  scene: Scene;
  canvas: HTMLCanvasElement;
  resizeObserver: ResizeObserver | null;
  enableRendering: (enable: boolean) => void;
  disconnect: () => void;
}

export function setupThreeWWT(options: UseThreeOptions): ThreeWWTSetup {
  const control = options.control ?? WWTControl.singleton;

  const scene = createTHREEScene(options?.ambientLight ?? true);
  const camera = createTHREECamera(control.renderContext, options.far ?? 1);

  if (options.target === "custom" && options.canvas == undefined) {
    throw new Error("You must provide a canvas element when using a custom render target");
  }

  const wwtCanvas: HTMLCanvasElement = control.canvas;

  let canvas = wwtCanvas;
  let resizeObserver: ResizeObserver | null = null;
  let ownsCanvas = false;
  switch (options.target) {
  case "custom":
    canvas = options.canvas;
    break;
  case "overlay":
    [canvas, resizeObserver] = createDummyCanvas(wwtCanvas);
    ownsCanvas = true;
    break;
  }

  const renderTarget = options.target ?? "wwt";
  // May throw WebGL2UnavailableError for target:"wwt" when WWT is on WebGL1 —
  // callers catch it and retry with target:"overlay".
  const renderer = createTHREERenderer(control, canvas);
  renderer.autoClear = renderTarget !== "wwt";
  const syncSize = makeSizeSync(renderer);

  let render = options?.render ?? true;

  function enableRendering(enable: boolean) {
    render = enable;
    if (!render && renderTarget !== "wwt") {
      renderer.clear();
    }
  }

  const gl = renderer.getContext();

  function renderScene() {
    if (renderTarget === "wwt") {
      // Sharing WWT's context: bracket our draw so three's GL-state cache
      // never trusts (or corrupts) WWT's state.
      renderer.resetState();
      // CRITICAL, raw GL on purpose: three's state.reset() ends with
      // `gl.viewport(0, 0, gl.canvas.width, gl.canvas.height)` — and under
      // installHiDpiCanvas the shimmed canvas.width getter returns CSS pixels,
      // so on every DPR>1 screen (all phones) reset() re-clamps the viewport
      // to the bottom-left quadrant AFTER any setViewport() we did, while
      // three's cached viewport still claims full size (so setViewport would
      // no-op). Restoring via the raw context every frame bypasses the stale
      // cache and costs nothing. Symptom when missing: the overlay (field
      // lines, spacecraft) renders far offset from WWT's Sun on mobile.
      gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
      // ALSO CRITICAL: start our pass with a clean depth buffer. WWT's planet
      // pass leaves depth values we cannot reason about (its TileShader never
      // sets depthMask, inheriting whatever the previous GL user left; its
      // per-planet world scaling makes the written values engine-internal),
      // and depth-testing our geometry against them made WWT's own Sun
      // texture occlude our sun-surface sphere completely ("the native WWT
      // texture is in front of anything else", user-reported). WWT's COLORS
      // are already composited — clearing depth only makes OUR pass
      // self-consistent: the sun-surface mesh writes fresh depth in our
      // projection and everything else (field lines, wind, trails) correctly
      // occludes against IT. glClear honors depthMask + scissor; three's
      // state.reset() above set depthMask(true), clearDepth(1), scissor off.
      // Space Weather Live: set the clear value and mask EXPLICITLY. three's
      // state.reset() does not reissue gl.clearDepth, so the clear used whatever
      // WWT last left there -- with WWT's stars and Milky Way switched on, that
      // was a near value, and every one of our fragments farther than a few
      // Earth radii failed its depth test (measured: wind parcels invisible
      // with depthTest on, all 4000 drawn with it off).
      gl.depthMask(true);
      gl.clearDepth(1.0);
      gl.disable(gl.SCISSOR_TEST);
      gl.clear(gl.DEPTH_BUFFER_BIT);
      renderer.render(scene, camera);
      renderer.resetState();
    } else {
      renderer.render(scene, camera);
    }
  }

  function frameCallback(_si: ScriptInterface) {
    if (render) {
      syncSize();
      updateTHREECamera(camera, control.renderContext);
      options.onBeforeRender?.();
      renderScene();
    }
  }

  control.addFrameCallback(frameCallback);

  function disconnect() {
    control.removeFrameCallback(frameCallback);
    resizeObserver?.disconnect();
    renderer.dispose();
    if (ownsCanvas) {
      canvas.remove();
    }
  }

  return {
    scene,
    camera,
    canvas,
    renderer,
    enableRendering,
    resizeObserver,
    disconnect,
  };
}
