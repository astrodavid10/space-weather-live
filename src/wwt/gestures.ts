// =====================================================================
// Touch gestures — pinch to zoom, twist to rotate, one finger to orbit
// =====================================================================
// This module OWNS touch input for the stage. The engine's own two-finger
// handling is switched off in wwt-hacks.ts, and the reason is not that it is
// badly written but that it is structurally wrong for this app. Four
// independent faults, all read from the engine source (@wwtelescope/engine
// index.js) and all sufficient on their own to make pinch feel broken:
//
//   1. TWO COMPETING IMPLEMENTATIONS. `WWTControl.setup` binds `touchstart/
//      move/end` AND `pointerdown/move/up` to the same canvas (~68648-68663),
//      and both call `zoom()` — `onTouchMove` at ~68206 and `onPointerMove` at
//      ~68350. Every modern touch browser fires both families for one finger,
//      so a pinch applied roughly the SQUARE of the intended ratio. Worse, the
//      two paths have different gates: the pointer path zooms from the first
//      move, the touch path waits for `_twoTouchEvents > 10` (~67062, ~68185).
//      The gain therefore CHANGES mid-gesture — slow, then abruptly ~2x
//      faster, which is exactly the "unreliable" signature.
//   2. `_rotating` LATCHES. `onTouchMove` only zooms when
//      `radialMagnitude > angularMagnitude && !this._rotating` (~68202). Two
//      thumbs converging are almost never purely radial, so the first frames
//      often read as rotation, `_rotating` goes true (~68224) and the zoom
//      branch is blocked for the REST of the gesture — cleared only in
//      `onTouchEnd`. And because the engine's `roll()` did nothing in this app,
//      the branch that won produced no visible motion at all.
//   3. `_dragging` LATCHES TOO. The two-finger block is gated on
//      `!this._dragging` (~68184), which goes true after 8 px of one-finger
//      drag (~68244) and is cleared at ~68285 — which `onTouchEnd` SKIPS via an
//      early return when two touches were involved (~68274). So "drag the Sun,
//      then add a second finger to zoom" never zoomed.
//   4. `targetTouches`. Two-finger mode is decided by
//      `ev.targetTouches.length === 2` (~68153), and `targetTouches` only holds
//      touches whose target is the canvas. Touch events do not bubble from a
//      sibling overlay, so a finger landing on ANY `pointer-events: auto`
//      element never reached the engine and the pinch silently degraded to an
//      orbit. The offenders sit right on the disk: the 44 px region and
//      spacecraft chips.
//
// Fault 4 is the user's actual complaint — "we need to be able to pinch zoom
// anywhere on the screen that is not a layer overlay" — and it is why this
// module listens on the STAGE ROOT in the CAPTURE phase rather than on the
// canvas. Capture runs ancestor-first, so a second finger landing on a label
// chip is seen here before the chip sees it.
//
// What is NOT the problem, so nobody "fixes" it again: `preventDefault` is not
// missing. The engine preventDefaults touchstart, both touchmove branches,
// touchend, pointermove and wheel; its listeners are non-passive because they
// are bound to the canvas; `touch-action: none` is set on
// `.wwtelescope-component`; and `main.ts` preventDefaults document
// `gesturestart`. Browser page-zoom is not stealing the gesture.

import {
  addUserRoll, currentZoom, orbitByPixels, zoomTo,
} from "./magnetoStage";

/** Below this, a two-finger gesture is too small to have a stable centre. */
const MIN_PINCH_SEPARATION_PX = 24;

/**
 * Twist that must accumulate before rotation engages, in radians (~6 deg).
 *
 * Not a latch — once passed, rotation stays live for the gesture and the
 * threshold is subtracted out so there is no jump. It exists because two
 * fingers pinching are never perfectly parallel, and without it every pinch
 * would also roll the Sun a few degrees.
 */
const TWIST_DEADZONE_RAD = 0.1;

/** Wheel zoom per notch. Gentler than the engine's fixed 10%. */
const WHEEL_ZOOM_STEP = 0.055;

/**
 * Which way the world turns for a given finger twist.
 *
 * NOT DERIVABLE, and deliberately a named constant for the same reason
 * sunStage's AZIMUTH_SIGN and ELEVATION_SIGN are: the chain from a screen-space
 * finger angle to an apparent world rotation runs through WWT's `lookUp =
 * (sin(-rotation), cos(-rotation), 1e-5)` and a left-handed view matrix, and
 * getting the sign right on paper has a 50% success rate. Screen coordinates
 * also put +y downward, so an increasing atan2 is clockwise ON SCREEN, not
 * counter-clockwise.
 *
 * -1 is the reasoned guess: the camera rolls one way, so the world appears to
 * roll the other. **UNVERIFIED ON A TOUCH DEVICE.** If twist rotates the wrong
 * way, flip this one character rather than restructuring anything.
 */
const TWIST_SIGN = -1;

interface Pt { x: number; y: number }

export interface SunGestures {
  dispose: () => void;
}

/**
 * Attach gesture handling to the stage element.
 *
 * Returns a disposer; call it on unmount. (The WWT canvas itself must never be
 * unmounted — footgun 17 — but these listeners are ours and are cheap to
 * rebind.)
 */
export function installSunGestures(root: HTMLElement): SunGestures {
  /** Live pointers, by id, in the order they arrived. */
  const points = new Map<number, Pt>();
  /** Pointers we have claimed for the camera (a tap on a control is not ours). */
  const claimed = new Set<number>();

  // Drag deltas are ACCUMULATED and applied once per animation frame rather
  // than per event. A museum touchscreen or a 240 Hz trackpad fires several
  // pointermove events per rendered frame (kiosk.ts:67 already notes this), and
  // orbitByPixels does real trigonometry — so applying per event did the same
  // work three or four times for one visible result.
  let pendingDx = 0;
  let pendingDy = 0;
  let frame = 0;

  // Pinch baseline, taken when the second finger lands.
  let baseSeparation = 0;
  let baseAngle = 0;
  let baseZoom = 0;
  let twistLive = false;
  let pinching = false;

  function flush(): void {
    frame = 0;
    if (pendingDx === 0 && pendingDy === 0) { return; }
    const dx = pendingDx;
    const dy = pendingDy;
    pendingDx = 0;
    pendingDy = 0;
    orbitByPixels(dx, dy);
  }

  function schedule(): void {
    if (frame === 0) { frame = window.requestAnimationFrame(flush); }
  }

  /**
   * Would this element rather have the tap than the camera?
   *
   * A single finger landing on a button, the scrubber or a label chip belongs to
   * that control. A SECOND finger never does — see `claimAll`.
   */
  function isControl(target: EventTarget | null): boolean {
    if (!(target instanceof Element)) { return false; }
    return target.closest(
      "button, a, input, select, textarea, [role=\"button\"], [data-camera-passthrough=\"false\"]",
    ) !== null;
  }

  /**
   * Take over every live pointer for the camera.
   *
   * Called when the second finger lands. This is the fix for fault 4: the first
   * finger may well have landed on a label chip and been left to it, but a
   * two-finger gesture is unambiguously a camera gesture wherever it starts, so
   * both pointers are claimed retroactively.
   */
  function claimAll(): void {
    points.forEach((_pt, id) => claimed.add(id));
  }

  function separationAndAngle(): { sep: number; angle: number } | null {
    const it = points.values();
    const a = it.next().value as Pt | undefined;
    const b = it.next().value as Pt | undefined;
    if (!a || !b) { return null; }
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    return { sep: Math.hypot(dx, dy), angle: Math.atan2(dy, dx) };
  }

  function beginPinch(): void {
    const m = separationAndAngle();
    if (!m || m.sep < MIN_PINCH_SEPARATION_PX) { return; }
    baseSeparation = m.sep;
    baseAngle = m.angle;
    baseZoom = currentZoom();
    twistLive = false;
    pinching = true;
    // A pinch is not a pan: drop anything the first finger had queued so the
    // view does not lurch as the second finger lands.
    pendingDx = 0;
    pendingDy = 0;
  }

  function onPointerDown(event: PointerEvent): void {
    if (event.pointerType === "mouse" && event.button !== 0) { return; }
    points.set(event.pointerId, { x: event.clientX, y: event.clientY });

    if (points.size >= 2) {
      claimAll();
      beginPinch();
      // Only now do we take the events away from whatever is underneath. Doing
      // it on the FIRST pointer would break every button on the stage.
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    // One finger. Mouse input is left entirely to the engine, which already
    // handles click-drag and has momentum we do not want to reimplement.
    if (event.pointerType === "mouse") { return; }
    if (isControl(event.target)) { return; }
    claimed.add(event.pointerId);
    event.stopPropagation();
  }

  function onPointerMove(event: PointerEvent): void {
    const previous = points.get(event.pointerId);
    if (!previous) { return; }
    const next = { x: event.clientX, y: event.clientY };
    points.set(event.pointerId, next);

    if (pinching && points.size >= 2) {
      const m = separationAndAngle();
      if (m && baseSeparation > 0) {
        // Zoom is ABSOLUTE against the baseline, not accumulated per event.
        // Accumulating is what let the engine's two paths compound into a
        // squared ratio; against a baseline, a doubled separation is exactly
        // one zoom step however many events it took to get there.
        //
        // Separation up means the guest is spreading their fingers, which means
        // "closer" — and closer is a SMALLER zoom value, since the engine's
        // solar-system distance is 4*zoom/9 AU (footgun 14).
        zoomTo(baseZoom * (baseSeparation / Math.max(1, m.sep)));

        let twist = m.angle - baseAngle;
        // Shortest way round, so passing through +/-pi does not spin the Sun.
        while (twist > Math.PI) { twist -= Math.PI * 2; }
        while (twist < -Math.PI) { twist += Math.PI * 2; }
        if (!twistLive && Math.abs(twist) > TWIST_DEADZONE_RAD) {
          twistLive = true;
          // Re-baseline at the edge of the deadzone rather than at zero, so
          // rotation starts from where the fingers actually are and the Sun
          // does not jump by the deadzone the instant it engages.
          baseAngle += Math.sign(twist) * TWIST_DEADZONE_RAD;
          twist -= Math.sign(twist) * TWIST_DEADZONE_RAD;
        }
        if (twistLive) {
          addUserRoll(TWIST_SIGN * twist);
          baseAngle = m.angle;
        }
      }
      event.preventDefault();
      event.stopPropagation();
      return;
    }

    if (!claimed.has(event.pointerId)) { return; }
    pendingDx += next.x - previous.x;
    pendingDy += next.y - previous.y;
    schedule();
    event.preventDefault();
    event.stopPropagation();
  }

  function onPointerUp(event: PointerEvent): void {
    const wasClaimed = claimed.has(event.pointerId);
    points.delete(event.pointerId);
    claimed.delete(event.pointerId);

    if (points.size < 2) {
      pinching = false;
      twistLive = false;
      baseSeparation = 0;
    }
    if (wasClaimed) {
      // Do NOT preventDefault here: a claimed pointer that never moved was a
      // tap, and the element under it should still see the click.
      event.stopPropagation();
    }
  }

  function onWheel(event: WheelEvent): void {
    // The engine's own handler is a fixed +/-10% per event with no deltaMode
    // handling, which makes a trackpad lurch and a precision mouse crawl.
    // Normalize the three modes to "lines" and scale smoothly.
    let lines = event.deltaY;
    if (event.deltaMode === 0) { lines = event.deltaY / 53; }      // pixels
    else if (event.deltaMode === 2) { lines = event.deltaY * 20; } // pages
    if (!Number.isFinite(lines) || lines === 0) { return; }
    // Clamp so one violent flick cannot cross the whole zoom range.
    const clamped = Math.max(-4, Math.min(4, lines));
    zoomTo(currentZoom() * Math.exp(clamped * WHEEL_ZOOM_STEP));
    event.preventDefault();
    event.stopPropagation();
  }

  // Capture phase, on the stage root: this is what lets a finger that lands on
  // a label chip still count toward a pinch (fault 4). `passive: false` because
  // these handlers call preventDefault.
  const opts: AddEventListenerOptions = { capture: true, passive: false };
  root.addEventListener("pointerdown", onPointerDown as EventListener, opts);
  root.addEventListener("pointermove", onPointerMove as EventListener, opts);
  root.addEventListener("pointerup", onPointerUp as EventListener, opts);
  root.addEventListener("pointercancel", onPointerUp as EventListener, opts);
  root.addEventListener("wheel", onWheel as EventListener, opts);

  return {
    dispose(): void {
      if (frame !== 0) { window.cancelAnimationFrame(frame); }
      frame = 0;
      points.clear();
      claimed.clear();
      root.removeEventListener("pointerdown", onPointerDown as EventListener, opts);
      root.removeEventListener("pointermove", onPointerMove as EventListener, opts);
      root.removeEventListener("pointerup", onPointerUp as EventListener, opts);
      root.removeEventListener("pointercancel", onPointerUp as EventListener, opts);
      root.removeEventListener("wheel", onWheel as EventListener, opts);
    },
  };
}
