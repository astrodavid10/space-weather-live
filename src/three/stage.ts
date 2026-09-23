// =====================================================================
// three.js stage — lifecycle around the vendored three-wwt bridge
// =====================================================================
// This is the ONLY file in src/three/ that touches WWT (CLAUDE.md footgun 12):
// everything else takes a scene/camera and does maths. If WWT's 3D mode ever
// proves too heavy on phones, replacing this file with an OrbitControls stage
// is the whole port.
//
// Coordinates: the scene is heliocentric ecliptic J2000 in AU, right-handed,
// Sun at the origin — the frame the pipeline publishes in, so nothing in
// src/three/ transforms anything. WWT's OWN world frame is that with Y and Z
// swapped, and the bridge folds the swap into the three camera when it copies
// WWT's view/projection matrices (three-wwt/utils.updateTHREECamera, and
// ./worldFrame.ts for why). Believing those two frames were the same is what
// shipped a Sun 90 deg out of the ecliptic — CLAUDE.md footgun 47.

import { Camera, NoToneMapping, Object3D, Scene, WebGLRenderer } from "three";

import { WebGL2UnavailableError, setupThreeWWT } from "./three-wwt";
import type { ThreeWWTSetup } from "./three-wwt/setupThreeWWT";
import { assertWinding } from "./winding";

export type StageTarget = "wwt" | "overlay";

export interface ThreeStageOptions {
  /** Preferred render target. `?three=overlay` forces "overlay". */
  target?: StageTarget;
  /** Called every frame after the camera sync, before the draw. */
  onBeforeRender?: () => void;
  /**
   * The GPU dropped everything (tab backgrounded on a low-memory phone,
   * driver reset). Rebuild GPU objects from the ArrayBuffers you retained.
   */
  onContextRestored?: () => void;
}

export interface ThreeStage {
  scene: Scene;
  camera: Camera;
  renderer: WebGLRenderer;
  canvas: HTMLCanvasElement;
  /** Which target we actually got — "overlay" means no depth occlusion. */
  target: StageTarget;
  /** True between webglcontextlost and webglcontextrestored. */
  contextLost: () => boolean;
  /** Drawing-buffer size in device pixels, for DOM label projection. */
  bufferSize: () => { width: number; height: number };
  /**
   * Pause/resume three's per-frame work. Used when the 3D view is hidden with
   * v-show (the component stays mounted so WWT's GL state survives — a
   * teardown/remount leaves the engine's global texture caches pointing at a
   * dead context and the Sun comes back black). Composes with context loss.
   */
  setEnabled: (enabled: boolean) => void;
  dispose: () => void;
}

/**
 * Create the shared-canvas stage. Tries WWT's own context first — that gives us
 * WWT's depth buffer, so the Sun sphere occludes far-side field lines — and
 * falls back to an overlay canvas when WWT is on WebGL1 (three r163+ needs
 * WebGL2). The `?three=overlay` escape hatch is CLAUDE.md footgun 4.
 */
export function createThreeStage(options: ThreeStageOptions = {}): ThreeStage {
  const wanted: StageTarget = options.target ?? "wwt";

  let setup: ThreeWWTSetup;
  let target: StageTarget = wanted;

  // Two left-handed conventions meet at this camera — WWT's D3D projection and
  // its Y/Z-swapped world frame — and they cancel. winding.ts states the net
  // result as a constant and the materials act on it; this re-derives it from
  // the live camera on the first real frame, so losing either one shows up as a
  // warning rather than an inside-out Sun with no sprites.
  function beforeRender(): void {
    assertWinding(setup.camera);
    options.onBeforeRender?.();
  }

  try {
    setup = setupThreeWWT({
      target: wanted,
      // Every material here is unlit (additive lines, sprites with baked
      // gradients), so an ambient light would only cost a uniform block.
      ambientLight: false,
      onBeforeRender: beforeRender,
    });
  } catch (err) {
    if (!(err instanceof WebGL2UnavailableError) || wanted === "overlay") { throw err; }
    console.warn("[stage] WWT canvas has no WebGL2 context; falling back to an overlay canvas.");
    target = "overlay";
    setup = setupThreeWWT({
      target: "overlay",
      ambientLight: false,
      onBeforeRender: beforeRender,
    });
  }

  // three-wwt sets ACES tone mapping, which built-in materials (sprites, the
  // trail lines) apply in their fragment shader while our custom field-line
  // ShaderMaterial does not. That split would have the glow tone-mapped and the
  // field lines not, so the dome palette would only be exact on half the scene.
  // Nothing here is physically-lit, so no tone mapping is the correct choice.
  setup.renderer.toneMapping = NoToneMapping;

  // --- context loss -------------------------------------------------------
  // Chrome on Android drops WebGL contexts when memory gets tight. Without
  // preventDefault() the browser never fires "restored" and the view is dead
  // until reload.
  let lost = false;
  let enabled = true;

  function applyRenderState(): void {
    setup.enableRendering(enabled && !lost);
  }

  function onLost(event: Event): void {
    event.preventDefault();
    lost = true;
    applyRenderState();
    console.warn("[stage] WebGL context lost; pausing three.js rendering.");
  }

  function onRestored(): void {
    lost = false;
    console.warn("[stage] WebGL context restored; rebuilding GPU buffers.");
    options.onContextRestored?.();
    applyRenderState();
  }

  setup.canvas.addEventListener("webglcontextlost", onLost, false);
  setup.canvas.addEventListener("webglcontextrestored", onRestored, false);

  function disposeSubtree(root: Object3D): void {
    root.traverse((node) => {
      const holder = node as Object3D & {
        geometry?: { dispose?: () => void };
        material?: { dispose?: () => void } | { dispose?: () => void }[];
      };
      holder.geometry?.dispose?.();
      const material = holder.material;
      if (Array.isArray(material)) {
        material.forEach((m) => m.dispose?.());
      } else {
        material?.dispose?.();
      }
    });
  }

  return {
    scene: setup.scene,
    camera: setup.camera,
    renderer: setup.renderer,
    canvas: setup.canvas,
    target,
    contextLost: () => lost,

    bufferSize(): { width: number; height: number } {
      const gl = setup.renderer.getContext();
      return { width: gl.drawingBufferWidth, height: gl.drawingBufferHeight };
    },

    setEnabled(value: boolean): void {
      enabled = value;
      applyRenderState();
    },

    dispose(): void {
      setup.canvas.removeEventListener("webglcontextlost", onLost, false);
      setup.canvas.removeEventListener("webglcontextrestored", onRestored, false);
      setup.enableRendering(false);
      // Layers own their own geometries/materials, but sweep the graph anyway:
      // a leaked ShaderMaterial keeps a compiled program alive for the life of
      // the page, and guests flip between views repeatedly.
      disposeSubtree(setup.scene);
      setup.scene.clear();
      setup.disconnect();
    },
  };
}
