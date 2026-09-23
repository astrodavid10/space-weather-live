// =====================================================================
// WWT solar-system stage, pinned to EARTH
// =====================================================================
// Adapted from sol's wwt/sunStage.ts. Read its header for the camera algebra;
// everything below uses the same, re-verified formulas:
//
//   cameraPosition = d * ( -cos(lat) sin(lng), sin(lat), cos(lat) cos(lng) )
//   lookUp         = sin(-rotation) e1 + cos(-rotation) e2
//     e1 = ( cos(lng), 0, sin(lng) ),  e2 = ( sin(lat) sin(lng), cos(lat), -sin(lat) cos(lng) )
//   distance_AU = 4 zoom / 9
//
// in WWT's WORLD frame (ecliptic J2000 with Y and Z swapped; +Y = ecliptic north).
//
// What is new here:
//
// 1. THE PIN IS EARTH, NOT THE SUN. target = SolarSystemObjects.custom (20) and
//    viewTarget = Earth's heliocentric position, which makes WWT's rendering
//    origin the centre of Earth (three/frame.ts). NEVER setTrackedObject(earth):
//    getPlanetTargetPoint adds a lat/lng-dependent SURFACE offset (sol footgun 1).
//
// 2. THE PIN IS RE-WRITTEN INSIDE THE ENGINE'S FRAME, not after it. The engine
//    per frame does: updateClock -> Planets.updatePlanetLocations ->
//    _updateViewParameters -> setupMatricesSolarSystem -> draw planets relative
//    to viewTarget -> frame callbacks. A frame callback that re-pins would lag by
//    one frame, and during playback Earth moves ~0.7 R_E per frame (2 days of
//    orbit in 20 s) -- visible jitter. So Planets.updatePlanetLocations is
//    wrapped (it is called through the module object, so patching works at any
//    time) and the pin is written right after the planets move.
//
// 3. The close-camera regime (< 0.0008 AU = 18.7 R_E) only changes anything via
//    viewCamera.angle; every writer here keeps angle = 0, so the formulas above
//    hold at every zoom (verified in setupMatricesSolarSystem).

import { EngineSetting, Planets, WWTControl } from "@wwtelescope/engine";

import { WWT_EARTH_RADIUS_AU } from "../three/frame";

type Vec3 = [number, number, number];

const SS_CUSTOM = 20;
const SS_EARTH = 19;
const MAX_LAT_DEG = 89.5;
const MAX_ELEV_DEG = 88;
const FIT_FACTOR = 2.414;          // 1/tan(22.5 deg): WWT's FOV is a fixed pi/4 VERTICAL
const ORBIT_DEG_PER_PX = 0.2;

/** Earth radii -> zoom (distance_AU = 4 zoom / 9). */
function zoomForRe(re: number): number {
  return ((re * WWT_EARTH_RADIUS_AU - 1e-6) * 9) / 4;
}
export const MIN_ZOOM = zoomForRe(2.2);
export const MAX_ZOOM = zoomForRe(700);

const STAGE_SETTINGS: EngineSetting[] = [
  ["solarSystemStars", true],
  ["solarSystemCosmos", false],
  ["solarSystemMilkyWay", true],
  ["solarSystemMinorPlanets", false],
  ["solarSystemOrbits", false],
  ["solarSystemLighting", true],
  ["solarSystemPlanets", true],
  ["actualPlanetScale", true],
  ["solarSystemScale", 1],
  ["showCrosshairs", false],
  ["showConstellationFigures", false],
  ["showConstellationBoundries", false],
  ["galacticMode", false],
  ["showEcliptic", false],
  ["showGrid", false],
];

export interface StageHost {
  applySetting: (setting: EngineSetting) => void;
  setBackgroundImageByName: (name: string) => void;
  setForegroundImageByName: (name: string) => void;
  setClockSync: (synced: boolean) => void;
  setTime: (time: Date) => void;
}

interface MutableCamera {
  lat: number; lng: number; zoom: number; rotation: number; angle: number;
  target: number; targetReferenceFrame: string;
  viewTarget: { x: number; y: number; z: number };
  copy: () => MutableCamera;
}

interface MutableRenderContext {
  viewCamera: MutableCamera;
  targetCamera: MutableCamera;
}

function rc(): MutableRenderContext | null {
  const control = WWTControl.singleton;
  if (!control?.renderContext) { return null; }
  return control.renderContext as unknown as MutableRenderContext;
}

// ---------------------------------------------------------------------
// Earth pin
// ---------------------------------------------------------------------

interface PlanetsInternals {
  _planet3dLocations: { x: number; y: number; z: number }[] | null;
  updatePlanetLocations: (solarSystemMode: boolean) => void;
}

const planets = Planets as unknown as PlanetsInternals;
let pinInstalled = false;

/** Earth's heliocentric position in WWT world coordinates, or null before the first update. */
export function earthWorld(): Vec3 | null {
  const loc = planets._planet3dLocations?.[SS_EARTH];
  return loc ? [loc.x, loc.y, loc.z] : null;
}

function pin(cam: MutableCamera): void {
  cam.target = SS_CUSTOM;
  cam.targetReferenceFrame = "";
  const e = earthWorld();
  if (e) {
    cam.viewTarget.x = e[0];
    cam.viewTarget.y = e[1];
    cam.viewTarget.z = e[2];
  }
}

function installEarthPin(): void {
  if (pinInstalled) { return; }
  const original = planets.updatePlanetLocations;
  planets.updatePlanetLocations = function wrapped(this: unknown, mode: boolean) {
    original.call(Planets, mode);
    const ctx = rc();
    if (ctx) {
      pin(ctx.viewCamera);
      pin(ctx.targetCamera);
    }
  };
  pinInstalled = true;
}

// ---------------------------------------------------------------------
// Camera maths (sol's, unchanged)
// ---------------------------------------------------------------------

function norm3(v: Vec3): Vec3 {
  const n = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / n, v[1] / n, v[2] / n];
}

function cross3(a: Vec3, b: Vec3): Vec3 {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function dot3(a: Vec3, b: Vec3): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function rotateAbout(v: Vec3, axis: Vec3, ang: number): Vec3 {
  const c = Math.cos(ang), s = Math.sin(ang), d = dot3(axis, v);
  return [
    v[0] * c + (axis[1] * v[2] - axis[2] * v[1]) * s + axis[0] * d * (1 - c),
    v[1] * c + (axis[2] * v[0] - axis[0] * v[2]) * s + axis[1] * d * (1 - c),
    v[2] * c + (axis[0] * v[1] - axis[1] * v[0]) * s + axis[2] * d * (1 - c),
  ];
}

function directionFor(latDeg: number, lngDeg: number): Vec3 {
  const lat = (latDeg * Math.PI) / 180, lng = (lngDeg * Math.PI) / 180;
  return [-Math.cos(lat) * Math.sin(lng), Math.sin(lat), Math.cos(lat) * Math.cos(lng)];
}

function latLngFor(u: Vec3): { latDeg: number; lngDeg: number } {
  return {
    latDeg: (Math.asin(Math.min(1, Math.max(-1, u[1]))) * 180) / Math.PI,
    lngDeg: (Math.atan2(-u[0], u[2]) * 180) / Math.PI,
  };
}

function rollFor(latDeg: number, lngDeg: number, up: Vec3): number {
  const lat = (latDeg * Math.PI) / 180, lng = (lngDeg * Math.PI) / 180;
  const e1: Vec3 = [Math.cos(lng), 0, Math.sin(lng)];
  const e2: Vec3 = [Math.sin(lat) * Math.sin(lng), Math.cos(lat), -Math.sin(lat) * Math.cos(lng)];
  return -Math.atan2(dot3(up, e1), dot3(up, e2));
}

function clampLat(deg: number): number {
  return Math.min(Math.max(deg, -MAX_LAT_DEG), MAX_LAT_DEG);
}

function clampZoom(z: number): number {
  return Math.min(Math.max(z, MIN_ZOOM), MAX_ZOOM);
}

/** WWT world: +Y is ecliptic north. */
const NORTH: Vec3 = [0, 1, 0];

/** Sun direction from Earth, WWT world, unit. */
export function sunDirWorld(): Vec3 {
  const e = earthWorld();
  return e ? norm3([-e[0], -e[1], -e[2]]) : [1, 0, 0];
}

let userRollRad = 0;

/** Up on screen: ecliptic north projected, unless looking along it (then sunward). */
function uprightRoll(u: Vec3, latDeg: number, lngDeg: number): number {
  let up = NORTH;
  if (Math.abs(dot3(u, NORTH)) > 0.985) { up = sunDirWorld(); }
  const along = dot3(up, u);
  const proj = norm3([up[0] - along * u[0], up[1] - along * u[1], up[2] - along * u[2]]);
  return rollFor(latDeg, lngDeg, proj) + userRollRad;
}

export function aspectPad(): number {
  const w = window.innerWidth, h = window.innerHeight;
  return w > 0 && h > 0 ? Math.max(1, h / w) : 1;
}

export function cameraDistanceAu(zoom: number): number {
  return (4 * zoom) / 9 + 1e-6;
}

export function cameraDistanceRe(): number {
  const ctx = rc();
  return ctx ? cameraDistanceAu(ctx.viewCamera.zoom) / WWT_EARTH_RADIUS_AU : 0;
}

export function currentZoom(): number {
  const ctx = rc();
  return ctx ? ctx.targetCamera.zoom : MIN_ZOOM;
}

export function zoomTo(zoom: number): void {
  const ctx = rc();
  if (!ctx || !Number.isFinite(zoom)) { return; }
  ctx.targetCamera.zoom = clampZoom(zoom);
}

export function zoomBy(factor: number): void {
  const ctx = rc();
  if (!ctx || !(factor > 0)) { return; }
  ctx.targetCamera.zoom = clampZoom(ctx.targetCamera.zoom * factor);
}

export function addUserRoll(deltaRad: number): void {
  if (!Number.isFinite(deltaRad)) { return; }
  userRollRad = (((userRollRad + deltaRad) % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  const ctx = rc();
  if (!ctx) { return; }
  for (const cam of [ctx.targetCamera, ctx.viewCamera]) {
    const u = norm3(directionFor(cam.lat, cam.lng));
    cam.rotation = uprightRoll(u, cam.lat, cam.lng);
    cam.angle = 0;
  }
}

/** Drag = orbit about ecliptic north (azimuth) and tilt toward it (elevation), stopping short of the pole. */
export function orbitByPixels(dxPx: number, dyPx: number): void {
  const ctx = rc();
  if (!ctx) { return; }
  const cam = ctx.targetCamera.copy();
  let u = directionFor(cam.lat, cam.lng);
  u = rotateAbout(u, NORTH, (dxPx * ORBIT_DEG_PER_PX * Math.PI) / 180);
  const right = cross3(NORTH, u);
  if (Math.hypot(...right) > 1e-6) {
    const next = rotateAbout(u, norm3(right), (-dyPx * ORBIT_DEG_PER_PX * Math.PI) / 180);
    const elev = (Math.asin(Math.min(1, Math.max(-1, next[1]))) * 180) / Math.PI;
    if (Math.abs(elev) <= MAX_ELEV_DEG) { u = next; }
  }
  u = norm3(u);
  const { latDeg, lngDeg } = latLngFor(u);
  cam.lat = clampLat(latDeg);
  cam.lng = lngDeg;
  cam.rotation = uprightRoll(u, cam.lat, cam.lng);
  cam.angle = 0;
  pin(cam);
  const keepZoom = ctx.viewCamera.zoom;
  ctx.targetCamera = cam;
  const view = cam.copy();
  view.zoom = keepZoom;
  pin(view);
  ctx.viewCamera = view;
}

// ---------------------------------------------------------------------
// Presets
// ---------------------------------------------------------------------

export type CameraPreset = "home" | "wide" | "top" | "headon" | "tail" | "dawn" | "dusk" | "ncusp" | "scusp";

export const CAMERA_PRESETS: { id: CameraPreset; label: string }[] = [
  { id: "home", label: "Home" },
  { id: "wide", label: "Wide" },
  { id: "top", label: "Top-down" },
  { id: "headon", label: "Head-on" },
  { id: "tail", label: "Down the tail" },
  { id: "dawn", label: "Dawn flank" },
  { id: "dusk", label: "Dusk flank" },
  { id: "ncusp", label: "N cusp" },
  { id: "scusp", label: "S cusp" },
];

/**
 * Direction (from Earth, toward the camera) and distance in R_E for a preset.
 * s = sunward, n = ecliptic north, d = DAWN (-Y_GSE).
 *
 * Careful: physically dusk = n x s (+Y_GSE = Z x X). But these vectors are in
 * WWT's WORLD frame, which is a mirror (det -1), and a cross product computed
 * in mirrored coordinates comes out negated: cross3(n_w, s_w) is physical
 * -(n x s) = dawn. Checked by hand: n_w=(0,1,0), s_w=(1,0,0) -> (0,0,-1)_w ->
 * ecliptic (0,-1,0) = -Y = dawn.
 */
function presetGeometry(id: CameraPreset): { dir: Vec3; re: number } {
  const s = sunDirWorld();
  const n = NORTH;
  const d = norm3(cross3(n, s));
  const mix = (a: number, b: number, c: number): Vec3 =>
    norm3([a * s[0] + b * d[0] + c * n[0], a * s[1] + b * d[1] + c * n[1], a * s[2] + b * d[2] + c * n[2]]);
  switch (id) {
  case "home": return { dir: mix(0.35, 0.85, 0.4), re: 42 };
  case "wide": return { dir: mix(0.25, 0.9, 0.35), re: 110 };
  case "top": return { dir: mix(0.0, 0.0, 1), re: 60 };
  case "headon": return { dir: mix(1, 0.05, 0.08), re: 36 };
  case "tail": return { dir: mix(-1, 0.04, 0.12), re: 70 };
  case "dawn": return { dir: mix(0.0, 1, 0.05), re: 45 };
  case "dusk": return { dir: mix(0.0, -1, 0.05), re: 45 };
  case "ncusp": return { dir: mix(0.35, 0.05, 1), re: 16 };
  case "scusp": return { dir: mix(0.35, 0.05, -1), re: 16 };
  }
  return { dir: mix(0.35, 0.85, 0.4), re: 42 };
}

/** Fly to a preset. `instant` writes both cameras (deep-link restore, reduced motion). */
export function goToPreset(id: CameraPreset, instant = false): void {
  const ctx = rc();
  if (!ctx) { return; }
  const { dir, re } = presetGeometry(id);
  const { latDeg, lngDeg } = latLngFor(dir);
  const cam = ctx.targetCamera.copy();
  cam.lat = clampLat(latDeg);
  cam.lng = lngDeg;
  userRollRad = 0;
  cam.rotation = uprightRoll(dir, cam.lat, cam.lng);
  cam.angle = 0;
  // Frame on the SHORT axis: WWT's FOV is fixed vertically, so a portrait
  // phone needs the aspect pad or every preset frames ~2x too tight.
  cam.zoom = clampZoom(zoomForRe(re * Math.min(aspectPad(), 1.8)));
  pin(cam);
  ctx.targetCamera = cam;
  if (instant) {
    const v = cam.copy();
    pin(v);
    ctx.viewCamera = v;
  }
}

export function cameraInfo(): { latDeg: number; lngDeg: number; zoom: number; distanceRe: number } {
  const ctx = rc();
  if (!ctx) { return { latDeg: 0, lngDeg: 0, zoom: 0, distanceRe: 0 }; }
  const cam = ctx.viewCamera;
  return { latDeg: cam.lat, lngDeg: cam.lng, zoom: cam.zoom, distanceRe: cameraDistanceRe() };
}

/** Distance scale a feature of `re` Earth radii needs to fit (unused by presets; for the fit tests). */
export function fitRe(re: number): number {
  return re * FIT_FACTOR;
}

let lastPad = 0;

export function refitFraming(): void {
  const pad = aspectPad();
  const ctx = rc();
  if (ctx && lastPad > 0 && Math.abs(pad - lastPad) > 1e-6) {
    const ratio = pad / lastPad;
    ctx.targetCamera.zoom = clampZoom(ctx.targetCamera.zoom * ratio);
    ctx.viewCamera.zoom = clampZoom(ctx.viewCamera.zoom * ratio);
  }
  lastPad = pad;
}

/** Enter solar-system mode, pin to Earth and frame the home preset. After waitForReady(). */
export function initMagnetoStage(host: StageHost, when: Date): void {
  host.setClockSync(false);
  host.setTime(when);
  host.setBackgroundImageByName("Solar System");
  host.setForegroundImageByName("Solar System");
  STAGE_SETTINGS.forEach((setting) => host.applySetting(setting));
  const control = WWTControl.singleton;
  if (control) {
    control.setSolarSystemMinZoom(MIN_ZOOM);
    control.setSolarSystemMaxZoom(MAX_ZOOM);
  }
  installEarthPin();
  // Planet positions exist only after the engine's first frame; the pin
  // re-writes viewTarget then, and goToPreset uses the current sun direction.
  planets.updatePlanetLocations(true);
  lastPad = aspectPad();
  goToPreset("home", true);
}
