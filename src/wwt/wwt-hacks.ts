// Runtime patches to the WWT engine for a Sun-centric, mobile-first app.
//
// IMPORT-TIME REQUIREMENT: the onGesture* no-ops below MUST be applied before
// the WWT component mounts. The engine attaches its input listeners with
// `ss.bind(name, obj)`, which captures the method AT BIND TIME (initControl) —
// a patch applied later never runs. `move` is called via dynamic dispatch
// (`this.move(...)`), so wrapping it works at any time; it is patched here
// anyway for one obvious home. This module is imported by the 3D async chunk
// only, before <WorldWideTelescope> is created.

import { WWTControl } from "@wwtelescope/engine";

import { orbitByPixels } from "./magnetoStage";

// Minimal view of the engine internals we patch; the official .d.ts does not
// expose these members.
interface PatchableControl {
  onGestureStart?: (e: unknown) => void;
  onGestureChange?: (e: unknown) => void;
  onGestureEnd?: (e: unknown) => void;
  move: (x: number, y: number) => void;
  roll?: (angle: number) => void;
  // eslint-disable-next-line @typescript-eslint/naming-convention -- engine API name
  get_solarSystemMode: () => boolean;
}

interface PatchableRenderContext {
  width: number;
  height: number;
}

const proto = WWTControl.prototype as unknown as PatchableControl;

// --- iOS Safari pinch fix ---------------------------------------------------
// On iOS, Safari fires proprietary gesture* events IN ADDITION to touch
// events. WWT's onGestureChange sets zoom absolutely from the gesture scale
// while its two-finger onTouchMove path ALSO applies an incremental zoom —
// both fire, they fight, and pinch zoom runs away. exo-sonification fixed
// this by deleting the engine line via modify_index.py (node_modules patch);
// no-opping the handlers here achieves the same with no build-step hack.
proto.onGestureStart = function () { /* no-op: iOS double-applies zoom */ };
proto.onGestureChange = function () { /* no-op: see modify_index.py rationale */ };
proto.onGestureEnd = function () { /* no-op */ };

// --- Orbit in solar-system mode ----------------------------------------------
// Sensitivity now lives with the orbit model it belongs to
// (sunStage.ORBIT_DEG_PER_PX), rather than as a scale factor on an engine
// behavior we no longer use.

const origMove = proto.move;
proto.move = function (this: PatchableControl, x: number, y: number) {
  if (this.get_solarSystemMode()) {
    // Our own orbit model, not a scaled version of the engine's. WWT's
    // lat/lng sphere has its poles at +/-Y — an arbitrary pair of points in
    // the ecliptic plane — so near them horizontal drag went dead and the
    // latitude guard read as an invisible wall. sunStage.orbitByPixels turns
    // about the SUN's axis instead, so the only place the controls converge is
    // the Sun's own poles, which is where a person expects a globe to
    // converge. See the comment on that function.
    orbitByPixels(x, y);
    return;
  }
  origMove.call(this, x, y);
};

// --- Touch and pointer input belong to src/wwt/gestures.ts -------------------
// ALL of these must be patched HERE, at import time, for the same reason
// onGesture* must (footgun 10): WWTControl.setup binds them with
// `ss.bind('onTouchStart', this)` (engine index.js ~68648-68663), which
// captures the method reference — a patch applied after initControl would never
// be seen. `move` is different and can be patched any time, because the engine
// looks it up on `this` at call time.
//
// WHY these are no-ops rather than fixed in place: the engine ships TWO
// independent two-finger implementations bound to the same canvas — the touch
// family and the pointer family — and both call `zoom()`. On every modern touch
// browser both fire for the same physical motion, so the applied zoom was
// roughly the SQUARE of the intended ratio; and because the two have different
// gates (the pointer path zooms from the first move, the touch path waits for
// `_twoTouchEvents > 10`) the gain also CHANGED mid-gesture. On top of that,
// `_rotating` and `_dragging` latch for the rest of a gesture, and two-finger
// mode is decided from `targetTouches`, which excludes any finger that landed
// on an overlay element. Four separate faults, each sufficient on its own.
// src/wwt/gestures.ts explains each with engine line numbers.
//
// One-finger drag is NOT lost by no-opping onTouchMove: gestures.ts handles it,
// and it routes to the same sunStage.orbitByPixels that `move` above does.
// Desktop mouse drag still goes through the engine's mousedown/mousemove, which
// calls `move`.
const NOOP_INPUT = [
  "onTouchStart", "onTouchMove", "onTouchEnd",
  "onPointerDown", "onPointerMove", "onPointerUp",
] as const;
NOOP_INPUT.forEach((name) => {
  if (typeof (proto as unknown as Record<string, unknown>)[name] === "function") {
    (proto as unknown as Record<string, unknown>)[name] = function () {
      /* no-op: src/wwt/gestures.ts owns touch input */
    };
  }
});

// --- Roll ---------------------------------------------------------------------
// The engine's own roll() is left disabled: it was reached only from the
// two-finger path above, which is gone, and it wrote `targetCamera.rotation`
// directly — which orbitByPixels recomputes from scratch on every drag step to
// keep solar north up, so a roll written there survived only until the guest's
// next pan. Two-finger twist now goes through sunStage.addUserRoll(), which is
// separate state that the framing is added to rather than overwriting.
if (typeof proto.roll === "function") {
  proto.roll = function () { /* no-op: see sunStage.addUserRoll */ };
}

// --- HiDPI canvas ------------------------------------------------------------
// Copied from exo-sonification src/wwt-hacks.ts (lines 237-285), parameterized
// with a DPR cap: 1-px field lines read as intended on 2-3x phone screens, but
// full 3x rendering costs too much fill rate, so cap at 2.
export function installHiDpiCanvas(maxDpr = 2): void {
  const dpr = Math.min(window.devicePixelRatio ?? 1, maxDpr);
  if (dpr <= 1) return;

  const ctl = WWTControl.singleton;
  // `canvas` is real but absent from the official .d.ts
  const canvas = (ctl as unknown as { canvas: HTMLCanvasElement }).canvas;
  const rc = ctl.renderContext as unknown as PatchableRenderContext;

  // Canvas property shim: the engine reads/writes CSS-pixel sizes; the real
  // backing store is scaled by dpr behind its back.
  const canvasProto = HTMLCanvasElement.prototype;
  const origW = Object.getOwnPropertyDescriptor(canvasProto, "width");
  const origH = Object.getOwnPropertyDescriptor(canvasProto, "height");
  if (!origW?.get || !origW.set || !origH?.get || !origH.set) return;

  Object.defineProperty(canvas, "width", {
    get(): number { return Math.round(origW.get?.call(this) / dpr); },
    set(cssW: number) {
      origW.set?.call(this, Math.round(cssW * dpr));
      (this as HTMLCanvasElement).style.width = cssW + "px";
    },
    configurable: true,
  });
  Object.defineProperty(canvas, "height", {
    get(): number { return Math.round(origH.get?.call(this) / dpr); },
    set(cssH: number) {
      origH.set?.call(this, Math.round(cssH * dpr));
      (this as HTMLCanvasElement).style.height = cssH + "px";
    },
    configurable: true,
  });

  // renderContext property shim
  let rcW = Math.round((rc.width || canvas.clientWidth) * dpr);
  let rcH = Math.round((rc.height || canvas.clientHeight) * dpr);
  Object.defineProperty(rc, "width", {
    get(): number { return rcW; },
    set(cssW: number) { rcW = Math.round(cssW * dpr); },
    configurable: true,
  });
  Object.defineProperty(rc, "height", {
    get(): number { return rcH; },
    set(cssH: number) { rcH = Math.round(cssH * dpr); },
    configurable: true,
  });

  // Force initial physical sizing
  canvas.width = canvas.clientWidth;
  canvas.height = canvas.clientHeight;
}
