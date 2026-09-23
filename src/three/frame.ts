// =====================================================================
// The magnetosphere's frame: J2000 equatorial Earth radii -> the three scene
// =====================================================================
// THE SCENE IS EARTH-CENTRED. This differs from sol and is the single most
// important fact in the app. WWT's solar-system renderer builds its view as
// lookAtLH(cameraPosition, ORIGIN, up) with an identity world matrix, then
// draws every planet at (planet - viewTarget) (setupMatricesSolarSystem and
// renderOneFrame in @wwtelescope/engine 7.39, read 2026-09-23). With the
// camera pinned to Earth (wwt/magnetoStage.ts), the rendering origin IS the
// centre of Earth, so three.js geometry at the origin sits on WWT's Earth with
// no heliocentric translation -- and no float32 precision loss at 1 AU.
//
// The scene's AXES are still sol's: right-handed ecliptic J2000 in AU, with
// WWT's Y/Z-swapped world folded into the camera (three/worldFrame.ts).
//
// So the only transform a bundle needs is a rotation and a scale:
//
//     p_scene_AU = S * Rx(eps) * p_eq_RE
//
//   Rx(eps) = [[1, 0, 0], [0, cos, sin], [0, -sin, cos]]   equatorial -> ecliptic
//     (the celestial pole (0,0,1) lands on (0, sin eps, cos eps): tilted toward
//      +Y_ecliptic, which is where the June-solstice Sun is -- the pole leans
//      toward it in northern summer, as it must)
//   S = Planets.getAdjustedPlanetRadius(earth) = 8.55626412117809e-5 / 2 AU.
//     NOT the physical 6371 km (4.2587e-5 AU): WWT draws Earth 0.46% larger,
//     and the aurora oval at 1.017 R_E would sit INSIDE the drawn sphere.

import { Matrix4, Vector3 } from "three";

/** J2000 mean obliquity of the ecliptic, degrees (IAU 1976). */
export const OBLIQUITY_J2000_DEG = 23.4392911;

/** WWT's drawn Earth radius in AU at solarSystemScale 1 (Planets._planetDiameters[19] / 2). */
export const WWT_EARTH_RADIUS_AU = 8.55626412117809e-5 / 2;

/** Physical Earth radius in AU, for labels and distances only. */
export const RE_AU = 6371 / 149597870.7;

const e = (OBLIQUITY_J2000_DEG * Math.PI) / 180;
const c = Math.cos(e);
const s = Math.sin(e);

/** Equatorial J2000 Earth radii -> scene (ecliptic J2000 AU, Earth at the origin). */
export const EQ_RE_TO_SCENE = /* @__PURE__ */ new Matrix4().set(
  WWT_EARTH_RADIUS_AU, 0, 0, 0,
  0, c * WWT_EARTH_RADIUS_AU, s * WWT_EARTH_RADIUS_AU, 0,
  0, -s * WWT_EARTH_RADIUS_AU, c * WWT_EARTH_RADIUS_AU, 0,
  0, 0, 0, 1,
);

/** Rotate a direction (no scale) from equatorial to ecliptic. */
export function eqToEcl(v: [number, number, number]): [number, number, number] {
  return [v[0], c * v[1] + s * v[2], -s * v[1] + c * v[2]];
}

/** WWT world (Y/Z swapped ecliptic) -> right-handed ecliptic. Its own inverse. */
export function wwtToEcl(v: { x: number; y: number; z: number }): Vector3 {
  return new Vector3(v.x, v.z, v.y);
}

/** Scene distance (AU) for a distance in Earth radii, on the drawn Earth's scale. */
export function reToScene(re: number): number {
  return re * WWT_EARTH_RADIUS_AU;
}

export { geoToEqRe, gmstDeg } from "../data/earthMath";
