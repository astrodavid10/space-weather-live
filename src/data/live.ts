/* eslint-disable @typescript-eslint/naming-convention -- SWPC field names and the ephemeris symbols (L, g, e) are the source's */
// =====================================================================
// Live space weather, straight from NOAA SWPC in the browser (CORS: *)
// =====================================================================
// The dome's livecore rules, ported (Engine/livecore/state.py, STATE_SCHEMA.md):
//   * RTSW rows interleave every L1 spacecraft; filter on "active": true and
//     DISPLAY `source` verbatim -- never branch on the name (SOLAR1 today).
//   * driving values = median of valid samples in the trailing 5 minutes,
//     measured back from each product's newest valid sample.
//   * tier LIVE if the newest valid sample is <= 15 min old, else CACHED.
//   * pdyn = 1.6726e-6 n v^2 (proton-only).
// The last good state is kept in localStorage and shown "as of" when NOAA is
// unreachable (sol's useSolarStats pattern). Degrade quietly, never lie.
//
// The live OVATION Prime oval is the only live GEOMETRY. Everything else in the
// 3D view in live mode is the newest look-back frame, and the UI says how old.

import { reactive } from "vue";

import { geoToEqRe, gmstDeg } from "./earthMath";

const BASE = "https://services.swpc.noaa.gov";
const URLS = {
  mag: `${BASE}/json/rtsw/rtsw_mag_1m.json`,
  wind: `${BASE}/json/rtsw/rtsw_wind_1m.json`,
  kp: `${BASE}/products/noaa-planetary-k-index.json`,
  dst: `${BASE}/json/geospace/geospace_dst_1_hour.json`,
  ovation: `${BASE}/json/ovation_aurora_latest.json`,
};

const TIMEOUT_MS = 8000;
const WINDOW_S = 5 * 60;
const LIVE_LIMIT_S = 15 * 60;
const FAST_MS = 60_000;
const SLOW_MS = 5 * 60_000;
const STORE_KEY = "swl:live:v1";

export type Tier = "LIVE" | "CACHED" | "NOFEED";

export interface LiveState {
  tier: Tier;
  source: string;          // active L1 spacecraft, verbatim
  observedMs: number | null;
  speed: number | null;
  density: number | null;
  bz: number | null;
  by: number | null;
  bt: number | null;
  pdyn: number | null;
  kp: number | null;
  kpMs: number | null;
  dst: number | null;      // model forecast (SWPC Geospace), labelled so
  dstMs: number | null;
  ovationMs: number | null;
  ovationForecastMs: number | null;
  error: string;
}

export const live = reactive<LiveState>({
  tier: "NOFEED", source: "", observedMs: null, speed: null, density: null, bz: null, by: null, bt: null,
  pdyn: null, kp: null, kpMs: null, dst: null, dstMs: null, ovationMs: null, ovationForecastMs: null, error: "",
});

export interface OvalPoints { pos: Float32Array; color: Float32Array; alpha: Float32Array; count: number }

/** Set when OVATION loads; consumers hold it with markRaw. */
export let ovalPoints: OvalPoints | null = null;
const listeners = new Set<() => void>();
export function onOval(fn: () => void): () => void { listeners.add(fn); return () => listeners.delete(fn); }

async function getJson(url: string): Promise<unknown> {
  const ctl = new AbortController();
  const t = window.setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(url, { signal: ctl.signal, cache: "no-cache" });
    if (!r.ok) { throw new Error(`HTTP ${r.status}`); }
    return await r.json();
  } finally {
    window.clearTimeout(t);
  }
}

const ms = (tag: unknown): number => {
  if (typeof tag !== "string") { return NaN; }
  return Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(tag) ? tag : tag + "Z");
};

function median(xs: number[]): number | null {
  if (!xs.length) { return null; }
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

type Row = Record<string, unknown>;

/** Active-spacecraft rows with every key present, newest first. */
function activeRows(json: unknown, keys: string[]): Row[] {
  if (!Array.isArray(json)) { return []; }
  return (json as Row[])
    .filter((r) => r && r.active === true && keys.every((k) => typeof r[k] === "number"))
    .sort((a, b) => ms(b.time_tag) - ms(a.time_tag));
}

function windowMedian(rows: Row[], key: string): number | null {
  if (!rows.length) { return null; }
  const t0 = ms(rows[0].time_tag);
  return median(rows.filter((r) => t0 - ms(r.time_tag) <= WINDOW_S * 1000).map((r) => r[key] as number));
}

function save(): void {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(live)); } catch { /* private mode */ }
}

function restore(): void {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (!raw) { return; }
    const s = JSON.parse(raw) as LiveState;
    Object.assign(live, s, { tier: s.observedMs ? "CACHED" : "NOFEED" });
  } catch { /* ignore */ }
}

function retier(): void {
  if (live.observedMs == null) { live.tier = "NOFEED"; return; }
  live.tier = (Date.now() - live.observedMs) / 1000 <= LIVE_LIMIT_S ? "LIVE" : "CACHED";
}

async function pollFast(): Promise<void> {
  try {
    const [mag, wind] = await Promise.all([getJson(URLS.mag), getJson(URLS.wind)]);
    const m = activeRows(mag, ["bz_gsm", "bt"]);
    const w = activeRows(wind, ["proton_speed", "proton_density"]);
    if (m.length && w.length) {
      live.bz = windowMedian(m, "bz_gsm");
      live.by = windowMedian(m, "by_gsm");
      live.bt = windowMedian(m, "bt");
      live.speed = windowMedian(w, "proton_speed");
      live.density = windowMedian(w, "proton_density");
      live.pdyn = live.speed != null && live.density != null ? 1.6726e-6 * live.density * live.speed * live.speed : null;
      live.source = String(w[0].source ?? m[0].source ?? "");
      live.observedMs = Math.min(ms(w[0].time_tag), ms(m[0].time_tag));
      live.error = "";
      save();
    }
  } catch (err) {
    live.error = String((err as Error).message ?? err);
  }
  retier();
}

async function pollSlow(): Promise<void> {
  try {
    const kp = await getJson(URLS.kp) as Row[];
    const rows = (Array.isArray(kp) ? kp : []).filter((r) => typeof r.Kp === "number").sort((a, b) => ms(b.time_tag) - ms(a.time_tag));
    if (rows.length) { live.kp = rows[0].Kp as number; live.kpMs = ms(rows[0].time_tag); }
  } catch { /* keep last */ }
  try {
    const dst = await getJson(URLS.dst) as Row[];
    const rows = (Array.isArray(dst) ? dst : []).filter((r) => typeof r.dst === "number").sort((a, b) => ms(b.time_tag) - ms(a.time_tag));
    if (rows.length) { live.dst = rows[0].dst as number; live.dstMs = ms(rows[0].time_tag); }
  } catch { /* keep last */ }
  save();
}

// --- OVATION -----------------------------------------------------------------
// 360 x 181 grid of aurora probability (0-100), [lon, lat, prob]. Drawn at
// 110 km (1.0173 R_E) with the dome's colour ramp (Engine/magneto/model/aurora.py):
// green to 40, green -> yellow-green at 60, -> red at 100; cells in daylight
// (solar zenith < 100 deg) dimmed x0.2, as on the dome -- the model still
// predicts aurora there, but nobody would see it.

const OVAL_ALT_RE = 110 / 6371;
const RAMP = [[0, 0.1, 1.0, 0.35], [40, 0.1, 1.0, 0.35], [60, 0.6, 1.0, 0.3], [100, 1.0, 0.3, 0.25]];

function ramp(p: number): [number, number, number] {
  for (let i = 1; i < RAMP.length; i++) {
    if (p <= RAMP[i][0]) {
      const [p0, r0, g0, b0] = RAMP[i - 1], [p1, r1, g1, b1] = RAMP[i];
      const t = (p - p0) / Math.max(1e-6, p1 - p0);
      return [r0 + (r1 - r0) * t, g0 + (g1 - g0) * t, b0 + (b1 - b0) * t];
    }
  }
  return [1, 0.3, 0.25];
}

/** Sub-solar point (deg) for a unix time: low-precision solar ephemeris, ~0.01 deg. */
function subsolar(unix: number): { lat: number; lon: number } {
  const d = unix / 86400 + 2440587.5 - 2451545.0;
  const g = ((357.529 + 0.98560028 * d) * Math.PI) / 180;
  const q = 280.459 + 0.98564736 * d;
  const L = ((q + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * Math.PI) / 180;
  const e = ((23.439 - 0.00000036 * d) * Math.PI) / 180;
  const ra = Math.atan2(Math.cos(e) * Math.sin(L), Math.cos(L));
  const dec = Math.asin(Math.sin(e) * Math.sin(L));
  const lon = ((ra * 180) / Math.PI - gmstDeg(unix) + 540) % 360 - 180;
  return { lat: (dec * 180) / Math.PI, lon };
}

export async function loadOvation(): Promise<void> {
  let json: { coordinates?: number[][]; "Observation Time"?: string; "Forecast Time"?: string };
  try {
    json = await getJson(URLS.ovation) as typeof json;
  } catch {
    return;
  }
  const cells = (json.coordinates ?? []).filter((c) => c[2] >= 3);
  const obs = Date.parse(json["Observation Time"] ?? "") || Date.now();
  const unix = obs / 1000;
  const gmst = gmstDeg(unix);
  const sun = subsolar(unix);
  const sLat = (sun.lat * Math.PI) / 180, sLon = (sun.lon * Math.PI) / 180;
  const n = cells.length;
  const pos = new Float32Array(n * 3), color = new Float32Array(n * 3), alpha = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const [lon, lat, p] = cells[i];
    const v = geoToEqRe(lon, lat, OVAL_ALT_RE, gmst);
    pos.set(v, i * 3);
    const [r, g, b] = ramp(p);
    const la = (lat * Math.PI) / 180, lo = (lon * Math.PI) / 180;
    const cosZ = Math.sin(la) * Math.sin(sLat) + Math.cos(la) * Math.cos(sLat) * Math.cos(lo - sLon);
    const zen = (Math.acos(Math.max(-1, Math.min(1, cosZ))) * 180) / Math.PI;
    const dim = zen < 100 ? 0.2 : 1;
    const k = Math.min(1, 0.25 + p / 60) * dim;
    color[i * 3] = r * k; color[i * 3 + 1] = g * k; color[i * 3 + 2] = b * k;
    alpha[i] = Math.min(1, 0.2 + p / 50) * dim;
  }
  ovalPoints = { pos, color, alpha, count: n };
  live.ovationMs = obs;
  live.ovationForecastMs = Date.parse(json["Forecast Time"] ?? "") || null;
  listeners.forEach((f) => f());
}

let started = false;
let fastTimer = 0, slowTimer = 0, ovTimer = 0;

/** Start polling (idempotent). Polls pause while the tab is hidden. */
export function startLive(): void {
  if (started) { return; }
  started = true;
  restore();
  const kick = () => {
    if (document.hidden) { return; }
    void pollFast();
  };
  void pollFast();
  void pollSlow();
  fastTimer = window.setInterval(kick, FAST_MS);
  slowTimer = window.setInterval(() => { if (!document.hidden) { void pollSlow(); } }, SLOW_MS);
  window.setInterval(retier, 30_000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { kick(); } });
  void fastTimer; void slowTimer;
}

/** OVATION is ~0.9 MB: fetched only when the live oval is actually wanted. */
export function startOvation(): void {
  if (ovTimer) { return; }
  void loadOvation();
  ovTimer = window.setInterval(() => { if (!document.hidden) { void loadOvation(); } }, 10 * 60_000);
}

export function stopOvation(): void {
  window.clearInterval(ovTimer);
  ovTimer = 0;
}
