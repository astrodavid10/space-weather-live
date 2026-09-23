// Pure Earth-frame maths with no three.js import, so the entry chunk (live.ts)
// can use it. three/frame.ts re-exports these.

/**
 * Greenwich mean sidereal time, degrees, for a unix time (IAU 1982, adequate to
 * ~0.1 s over this century). Used for anything computed client-side in the
 * Earth-fixed frame: the live OVATION oval and the textbook dipole axis.
 */
export function gmstDeg(unix: number): number {
  const jd = unix / 86400 + 2440587.5;
  const d = jd - 2451545.0;
  const g = 280.46061837 + 360.98564736629 * d;
  return ((g % 360) + 360) % 360;
}

/**
 * Geodetic lon/lat (deg) at altitude h (Earth radii above a sphere) -> J2000
 * equatorial Earth radii, ignoring precession/nutation (< 0.4 deg this decade)
 * and the ellipsoid (the aurora sits on a 1.017 R_E shell either way).
 */
export function geoToEqRe(lonDeg: number, latDeg: number, h: number, gmst: number): [number, number, number] {
  const lon = ((lonDeg + gmst) * Math.PI) / 180;
  const lat = (latDeg * Math.PI) / 180;
  const r = 1 + h;
  return [r * Math.cos(lat) * Math.cos(lon), r * Math.cos(lat) * Math.sin(lon), r * Math.sin(lat)];
}
