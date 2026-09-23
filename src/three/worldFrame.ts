// =====================================================================
// The ecliptic → WWT world transform, as three.js sees it
// =====================================================================
// The convention itself, and the evidence for it, live in
// `data/solarFrames.ts` (eclipticToWwtWorld) and CLAUDE.md footgun 47. The
// one-line version:
//
//     (x, y, z)_wwt = (X, Z, Y)_ecliptic     — +Y is the ecliptic pole,
//                                              det = -1, a MIRROR.
//
// This file is the three-side seam: the same swap as a Matrix4, plus the two
// things that must never be separated from it.
//
// ---------------------------------------------------------------------
// It goes on the CAMERA, not on the scene
// ---------------------------------------------------------------------
// Everything in the three.js scene stays in TRUE heliocentric ecliptic J2000:
// the pipeline's `quat_carr_to_ecl`, Horizons positions, `dir_ecl`, the solar
// axis, sub-Earth vectors — all of it unmodified, exactly as every header in
// src/three/ already claims. The swap is folded into the three CAMERA
// (three-wwt/utils.updateTHREECamera), so
//
//     clip = P_wwt . V_wwt . ECLIPTIC_TO_WWT . p_ecliptic
//
// which registers our geometry with WWT's own rendering while leaving
// `modelMatrix`, `matrixWorld`, the `cameraPosition` uniform and every dot
// product in the app in ONE frame.
//
// The alternatives were both worse, and the reason is worth keeping: a
// reflecting group at the scene root splits the app into two frames — a
// shader's `modelMatrix * position` would be WWT-world while
// `sunSurface.subEarthFrame()`'s vectors stay ecliptic, and every site that
// mixes camera with data (the limb-darkening uniform, the off-limb billboard
// basis, the marker facing dots, `projectTargets`) becomes a place to get a
// sign wrong. Converting at ingest instead would mean mirroring the quantized
// u16 field-line vertex buffer, the sphere geometry that carries the texture,
// and every piece of Carrington-frame maths in sunSurface.ts.
//
// ---------------------------------------------------------------------
// TWO THINGS THAT MUST TRAVEL WITH THIS TRANSFORM
// ---------------------------------------------------------------------
//  1. `camera.matrixWorldAutoUpdate = false` (createTHREECamera). three's
//     `Camera.updateMatrixWorld` DECOMPOSES matrixWorld and, when the scale is
//     not exactly (1,1,1), rebuilds matrixWorldInverse from position +
//     quaternion with scale forced to 1 "to be glTF conform"
//     (three/src/cameras/Camera.js). A reflection decomposes to scale
//     (-1, 1, 1), so that path would silently STRIP the swap on every frame
//     and put the 90 deg bug straight back. `WebGLRenderer.render` calls it
//     only when `matrixWorldAutoUpdate === true`, so the flag is the whole
//     defence — and note the camera has ALWAYS relied on that call being
//     harmless (it recomputes matrixWorldInverse from matrixWorld every
//     frame), which it was only because WWT's view matrix is rigid.
//  2. `winding.CAMERA_REVERSES_WINDING` is FALSE because of this file. Folding
//     a det = -1 matrix into the view flips the sign of det(P . V), which is
//     exactly the quantity footgun 19 is about: the projection's reversal and
//     the frame's mirror now cancel, and three's ordinary FrontSide is correct
//     again. `assertWinding()` re-derives that from the live camera each
//     session, so removing this transform reports itself in the console
//     instead of rendering the Sun inside-out.
//
// No WWT imports (CLAUDE.md footgun 12): this states a fact about the matrices
// the bridge is fed, in three's own types, and nothing more.

import { Matrix4 } from "three";

/**
 * Heliocentric ecliptic J2000 (right-handed, +Z at the ecliptic pole) → WWT's
 * solar-system world frame (left-handed, +Y at the ecliptic pole).
 *
 * `(x, y, z) → (x, z, y)`, matching `solarFrames.eclipticToWwtWorld`. Its own
 * inverse, and det = -1 — see the header for why that mirror is correct rather
 * than a bug to be rotated away.
 */
export const ECLIPTIC_TO_WWT = /* @__PURE__ */ new Matrix4().set(
  1, 0, 0, 0,
  0, 0, 1, 0,
  0, 1, 0, 0,
  0, 0, 0, 1,
);
