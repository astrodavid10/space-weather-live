// =====================================================================
// Triangle winding under WWT's camera
// =====================================================================
// TWO left-handed conventions meet at this camera, and they CANCEL. Getting
// that arithmetic wrong renders the Sun inside-out, so the whole chain is
// written down.
//
// 1. THE PROJECTION reverses. WWT's render context is a port of the original
//    Direct3D code and builds its matrices with Matrix3d.lookAtLH +
//    Matrix3d.perspectiveFovLH — LEFT-handed, D3D convention (clip w = +z_view,
//    depth range [0,1]). three.js assumes the OpenGL right-handed convention
//    (clip w = -z_view, depth [-1,1]). three-wwt/utils.ts hands those matrices
//    to the three camera as they are, so:
//
//        det(P_wwt)   = +w*h*zn*zf/(zf-zn)   > 0     (perspectiveFovLH)
//        det(P_three) = -w*h*2*zn*zf/(zf-zn) < 0     (ordinary RH perspective)
//        det(V_wwt)   = +1 (lookAtLH's rotation rows (xaxis, yaxis, zaxis)
//                       form a right-handed orthonormal set)
//
//    Measured, not assumed: at fov=pi/4, aspect=0.5, zn=1e-4, zf=1 the two
//    determinants are +1.166e-3 and -2.332e-3.
//
// 2. THE WORLD FRAME reverses too. WWT's solar-system world frame is ecliptic
//    J2000 with Y and Z SWAPPED, so it is left-handed with respect to physical
//    space (three/worldFrame.ts, CLAUDE.md footgun 47). The scene is built in
//    TRUE ecliptic J2000 and updateTHREECamera folds that swap into the view:
//
//        det(V_effective) = det(V_wwt) * det(ECLIPTIC_TO_WWT) = -1
//
// So the transform three actually draws with is
// det(P_wwt . V_wwt . ECLIPTIC_TO_WWT) < 0 — the same SIGN as an ordinary
// three.js setup, and three's ordinary FrontSide is correct. That is what
// CAMERA_REVERSES_WINDING = false records.
//
// THE HISTORY MATTERS, because it is why this file exists. Until 2026-08-24
// the app fed WWT its ecliptic vectors unswapped, so only reversal (1) was in
// play and every solid mesh had its front faces culled. Symptoms, all one
// cause:
//   - the Sun's SDO / synthetic texture was visible "through" the sphere: what
//     you were looking at was the INSIDE of the far hemisphere;
//   - that surface looked dark, because sunSurface's limb term
//     `mu = max(dot(normalize(vWorld), view), 0.08)` is negative on a back
//     face, clamps to 0.08, and pow(0.08, 0.6) = 0.21 -> 21% brightness;
//   - the sun-glow halo and the spacecraft marker dots vanished entirely:
//     three's Sprite quad is wound CCW and SpriteMaterial defaults to
//     FrontSide, so the flip culled every sprite in the scene.
// Adding the frame swap cancelled reversal (1), which is why SOLID_SIDE went
// back to FrontSide in the same change. If that swap is ever removed, this
// file has to change back in the SAME commit — assertWinding() below is the
// tripwire that says so.
//
// Note what three cannot do for us here: WebGLRenderer decides winding with
//
//     const frontFaceCW = ( object.isMesh && object.matrixWorld.determinantAffine() < 0 );
//
// which looks at the OBJECT only and never at the camera. `side` is therefore
// the only knob, and it is per-material. Lines and Points have no winding and
// are unaffected.
//
// No WWT imports (CLAUDE.md footgun 12): this file states a fact about the
// matrices the stage is fed, and assertWinding() re-derives that fact at
// runtime from the camera itself, so if either convention changes we get a
// console warning instead of a silently inside-out Sun.

import { BackSide, DoubleSide, FrontSide } from "three";
import type { Camera, Side } from "three";

/**
 * Does the world -> clip transform three draws with reverse orientation?
 *
 * FALSE, and only because TWO reversals cancel: WWT's D3D projection and the
 * left-handed world frame the camera now carries. Either one alone makes it
 * true — see the header.
 */
export const CAMERA_REVERSES_WINDING = false;

/**
 * `side` for a solid, single-sided mesh (the Sun sphere). FrontSide while the
 * two reversals cancel. BackSide is three's supported way to say "invert this
 * material's winding" and is what this becomes if either one goes away — it
 * costs nothing and keeps one triangle per fragment, unlike DoubleSide.
 */
export const SOLID_SIDE: Side = CAMERA_REVERSES_WINDING ? BackSide : FrontSide;

/**
 * `side` for flat, camera-facing geometry (sprites). DoubleSide disables face
 * culling altogether, which is both correct and free for a single quad, and
 * survives a future convention change without another edit here.
 */
export const FLAT_SIDE: Side = DoubleSide;

/**
 * Does this camera's world->clip transform reverse orientation? Recomputed
 * from the live matrices, so it reflects whatever WWT actually handed us.
 */
export function cameraReversesWinding(camera: Camera): boolean {
  return camera.projectionMatrix.determinant()
    * camera.matrixWorldInverse.determinant() > 0;
}

let warned = false;

/**
 * One-shot check that CAMERA_REVERSES_WINDING still matches reality. Called
 * from stage.ts on the first frame that has real matrices (WWT's camera is
 * identity until the first render, and det(identity) = 1 would be a false
 * positive, so a degenerate projection is ignored).
 */
export function assertWinding(camera: Camera): void {
  if (warned) { return; }
  const p = camera.projectionMatrix.determinant();
  if (p === 0 || Math.abs(p) > 0.5) { return; }  // not a real perspective matrix yet
  warned = true;
  const actual = cameraReversesWinding(camera);
  if (actual !== CAMERA_REVERSES_WINDING) {
    console.warn(
      `[winding] camera winding is ${actual ? "" : "NOT "}reversed, but `
      + `CAMERA_REVERSES_WINDING is ${CAMERA_REVERSES_WINDING}. Solid meshes will `
      + "render inside-out and sprites may disappear; see src/three/winding.ts.");
  }
}
