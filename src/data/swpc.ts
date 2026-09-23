// =====================================================================
// NOAA SWPC — endpoints, tolerant parsers, friendly formatters
// =====================================================================
// services.swpc.noaa.gov sends `Access-Control-Allow-Origin: *`, so these are
// the only space-weather numbers the browser can fetch directly. Only the TINY
// endpoints are used here — the big ones (xrays-1-day.json 654 KB,
// rtsw_wind_1m.json 2.9 MB, solar-cycle/sunspots.json) are digested
// server-side into data/stats/summary.json by the pipeline.
//
// The parsers are deliberately shape-tolerant. SWPC's "products/summary/*"
// files have changed between bare objects and single-element arrays before,
// the planetary-K product has shipped both as an array-of-arrays table with a
// header row and as an array of objects, and numeric fields arrive sometimes
// as numbers and sometimes as strings. Nothing here throws on a surprise: a
// field that can't be read comes back null and its chip shows "—".
//
// Field names are read through string keys rather than declared interfaces
// because the wire format is snake_case / PascalCase and the repo's
// naming-convention lint rule requires camelCase type properties.

export const XRAY_FLARES_URL = "https://services.swpc.noaa.gov/json/goes/primary/xray-flares-latest.json";
export const WIND_SPEED_URL = "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json";
export const MAG_FIELD_URL = "https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json";
export const KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json";
export const SCALES_URL = "https://services.swpc.noaa.gov/products/noaa-scales.json";

// --- shape helpers --------------------------------------------------------

type Dict = Record<string, unknown>;

function isDict(value: unknown): value is Dict {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Unwrap the `[{...}]` single-element-array form the summary products use. */
function firstRecord(json: unknown): Dict | null {
  if (Array.isArray(json)) {
    const first = json.find(isDict);
    return first ?? null;
  }
  return isDict(json) ? json : null;
}

/** First present key, read as a finite number (accepts numeric strings). */
function numField(row: Dict, ...keys: string[]): number | null {
  for (const key of keys) {
    const raw = row[key];
    if (typeof raw === "number" && Number.isFinite(raw)) { return raw; }
    if (typeof raw === "string" && raw.trim() !== "") {
      const n = Number(raw);
      if (Number.isFinite(n)) { return n; }
    }
  }
  return null;
}

/** First present key, read as a non-empty string. */
function strField(row: Dict, ...keys: string[]): string | null {
  for (const key of keys) {
    const raw = row[key];
    if (typeof raw === "string" && raw.trim() !== "") { return raw; }
    if (typeof raw === "number" && Number.isFinite(raw)) { return String(raw); }
  }
  return null;
}

/**
 * SWPC time tags are ISO-ish but frequently lack a zone ("2026-08-23T00:00:00")
 * while always meaning UTC. Normalize so Date.parse doesn't read them as local.
 */
export function parseTimeTag(timeTag: string | null): number | null {
  if (!timeTag) { return null; }
  const normalized = /(z|[+-]\d{2}:?\d{2})$/i.test(timeTag)
    ? timeTag.replace(" ", "T")
    : `${timeTag.replace(" ", "T")}Z`;
  const ms = Date.parse(normalized);
  return Number.isFinite(ms) ? ms : null;
}

/** Every parser returns this: the value plus the observation time SWPC reports. */
export interface Parsed<T> {
  value: T;
  timeTag: string | null;
}

// --- flares ---------------------------------------------------------------

export interface FlareInfo {
  /** GOES X-ray class right now, e.g. "B7.2". */
  currentClass: string | null;
  /** Largest class since the current event began, e.g. "B9.3". */
  maxClass: string | null;
  maxTime: string | null;
}

export function parseFlares(json: unknown): Parsed<FlareInfo> | null {
  const row = firstRecord(json);
  if (!row) { return null; }
  const currentClass = strField(row, "current_class", "currentClass");
  const maxClass = strField(row, "max_class", "maxClass");
  if (currentClass === null && maxClass === null) { return null; }
  return {
    value: {
      currentClass,
      maxClass,
      maxTime: strField(row, "max_time", "maxTime"),
    },
    timeTag: strField(row, "time_tag", "timeTag"),
  };
}

// --- solar wind -----------------------------------------------------------

export function parseWindSpeed(json: unknown): Parsed<number> | null {
  const row = firstRecord(json);
  if (!row) { return null; }
  const speed = numField(row, "proton_speed", "protonSpeed", "speed");
  if (speed === null) { return null; }
  return { value: speed, timeTag: strField(row, "time_tag", "timeTag") };
}

export interface MagInfo {
  /** Total field strength, nT. */
  bt: number | null;
  /** North-south component, nT. Negative Bz is what lights up aurora. */
  bz: number | null;
}

export function parseMagField(json: unknown): Parsed<MagInfo> | null {
  const row = firstRecord(json);
  if (!row) { return null; }
  const bt = numField(row, "bt");
  // The live product ships "bz_gsm"; "bz" is kept as a fallback because older
  // copies of this file used it.
  const bz = numField(row, "bz_gsm", "bz", "bz_gse");
  if (bt === null && bz === null) { return null; }
  return { value: { bt, bz }, timeTag: strField(row, "time_tag", "timeTag") };
}

// --- planetary K index ----------------------------------------------------

/** One point of a measured time series: unix SECONDS and a value. */
export interface SeriesPoint {
  t: number;
  v: number;
}

/**
 * Every Kp point SWPC published, not just the newest.
 *
 * `noaa-planetary-k-index.json` is a **3-hourly time series about seven days
 * long** — measured 2026-08-24, it began 2026-08-17T00:00 — and the app was
 * fetching all of it and keeping one point. That made the aurora chip read
 * "now" no matter where the playhead sat, which is the same dishonesty the
 * sunspot chip was fixed for. Since the fetch already happens, following the
 * scrubber here costs nothing at all.
 *
 * Handles BOTH shapes `parseKp` handles, for the same reason it does: SWPC has
 * served this endpoint as an array-of-arrays with a header row and as an array
 * of objects, and a reader that knows only one silently returns nothing.
 */
export function parseKpSeries(json: unknown): SeriesPoint[] {
  if (!Array.isArray(json) || json.length === 0) { return []; }
  const out: SeriesPoint[] = [];

  const first = json[0];
  if (Array.isArray(first)) {
    let kpCol = 1;
    let timeCol = 0;
    let start = 0;
    const names = first.map((h) => String(h).toLowerCase());
    const foundKp = names.indexOf("kp");
    const foundTime = names.indexOf("time_tag");
    if (foundKp >= 0 || foundTime >= 0) {
      if (foundKp >= 0) { kpCol = foundKp; }
      if (foundTime >= 0) { timeCol = foundTime; }
      start = 1;                       // that first row was the header
    }
    for (let i = start; i < json.length; i++) {
      const row = json[i];
      if (!Array.isArray(row)) { continue; }
      const v = Number(row[kpCol]);
      const t = parseTimeTag(typeof row[timeCol] === "string" ? row[timeCol] as string : null);
      if (Number.isFinite(v) && t !== null) { out.push({ t: t / 1000, v }); }
    }
  } else {
    for (const row of json) {
      if (!isDict(row)) { continue; }
      const v = Number(row["Kp"] ?? row["kp"] ?? row["kp_index"]);
      const tag = row["time_tag"];
      const t = parseTimeTag(typeof tag === "string" ? tag : null);
      if (Number.isFinite(v) && t !== null) { out.push({ t: t / 1000, v }); }
    }
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

/**
 * The series point nearest a moment, or null when there is none within
 * `toleranceHours`.
 *
 * NEAREST, not the last one before: Kp is a 3-hourly average, so a playhead
 * between two samples is genuinely closer to one of them. The tolerance is what
 * stops a gap in the feed from being papered over with a value hours away —
 * better to fall back to the live reading and say so.
 */
export function seriesAt(
  points: SeriesPoint[], unixSeconds: number, toleranceHours = 3,
): SeriesPoint | null {
  if (!points.length || !Number.isFinite(unixSeconds)) { return null; }
  let best = points[0];
  let bestGap = Math.abs(points[0].t - unixSeconds);
  for (let i = 1; i < points.length; i++) {
    const gap = Math.abs(points[i].t - unixSeconds);
    if (gap < bestGap) { best = points[i]; bestGap = gap; }
  }
  return bestGap <= toleranceHours * 3600 ? best : null;
}

/**
 * The NEWEST Kp point. Two shapes seen in the wild:
 *   array-of-arrays with a header row: [["time_tag","kp",...], ["...", "2.33", ...]]
 *   array-of-objects:                  [{time_tag: "...", Kp: 1.33, ...}]
 * Either way we want the LAST row. See parseKpSeries for the whole series.
 */
export function parseKp(json: unknown): Parsed<number> | null {
  if (!Array.isArray(json) || json.length === 0) { return null; }

  const last = json[json.length - 1];

  if (Array.isArray(last)) {
    const header = json[0];
    let kpCol = 1;
    let timeCol = 0;
    if (Array.isArray(header)) {
      const names = header.map((h) => String(h).toLowerCase());
      const foundKp = names.indexOf("kp");
      const foundTime = names.indexOf("time_tag");
      if (foundKp >= 0) { kpCol = foundKp; }
      if (foundTime >= 0) { timeCol = foundTime; }
      // A header row is not data — bail out if that's all there is.
      if (json.length < 2) { return null; }
    }
    const kp = Number(last[kpCol]);
    if (!Number.isFinite(kp)) { return null; }
    const tag = last[timeCol];
    return { value: kp, timeTag: typeof tag === "string" ? tag : null };
  }

  if (isDict(last)) {
    const kp = numField(last, "Kp", "kp", "kp_index", "estimated_kp");
    if (kp === null) { return null; }
    return { value: kp, timeTag: strField(last, "time_tag", "timeTag") };
  }

  return null;
}

// --- NOAA scales (R/S/G) --------------------------------------------------

export interface ScaleInfo {
  /** Radio blackouts (flares). */
  rScale: number | null;
  rText: string | null;
  /** Solar radiation storms. */
  sScale: number | null;
  sText: string | null;
  /** Geomagnetic storms. */
  gScale: number | null;
  gText: string | null;
}

function scalePair(row: Dict, key: string): { scale: number | null; text: string | null } {
  const entry = row[key];
  if (!isDict(entry)) { return { scale: null, text: null }; }
  return {
    scale: numField(entry, "Scale", "scale"),
    text: strField(entry, "Text", "text"),
  };
}

export function parseScales(json: unknown): Parsed<ScaleInfo> | null {
  if (!isDict(json)) { return null; }
  // "0" is today; "1".."3" are the forecast days, "-1" is yesterday.
  const today = json["0"];
  if (!isDict(today)) { return null; }

  const r = scalePair(today, "R");
  const s = scalePair(today, "S");
  const g = scalePair(today, "G");

  const dateStamp = strField(today, "DateStamp");
  const timeStamp = strField(today, "TimeStamp");
  const timeTag = dateStamp ? `${dateStamp}T${timeStamp ?? "00:00:00"}` : null;

  return {
    value: {
      rScale: r.scale,
      rText: r.text,
      sScale: s.scale,
      sText: s.text,
      gScale: g.scale,
      gText: g.text,
    },
    timeTag,
  };
}

// =====================================================================
// Friendly formatters
// =====================================================================
// Every chip shows a HEADLINE a ten-year-old can read and a DETAIL for the
// grown-up who wants the actual number. Never the raw jargon alone.

export interface FriendlyValue {
  headline: string;
  detail: string;
}

export const NO_DATA: FriendlyValue = { headline: "—", detail: "no data right now" };

/**
 * GOES X-ray class → plain language. A and B are the quiet background; C is a
 * small flare; M is a real one; X makes the news.
 */
// A Map rather than an object literal: single-letter keys can't be camelCase,
// which the repo's naming-convention rule requires of object properties.
const FLARE_WORDS = new Map<string, string>([
  ["X", "big flare!"],
  ["M", "medium flare"],
  ["C", "small flare"],
  ["B", "background"],
  ["A", "background"],
]);

/** Is this class a flare a guest would call a flare, i.e. C or above? */
function isFlareClass(letter: string): boolean {
  return letter === "C" || letter === "M" || letter === "X";
}

/**
 * The class designation is the HEADLINE, and the plain-language word the detail
 * — the other way round from every other chip here, and deliberately so.
 *
 * "How strong was it?" is the question a flare actually raises, and "Small
 * flare" cannot answer it: C1.0 and C9.9 are a factor of ten apart and both
 * read as "Small flare". The class carries the strength, so it goes where the
 * eye lands first. The word stays directly underneath (never the raw jargon
 * ALONE, which is this module's rule), and the letter scale itself is unpacked
 * one tap away in SunStats' explainer.
 *
 * A and B are not flares at all — they are the quiet background the Sun always
 * emits — so those keep "Quiet" as the headline and carry the class in the
 * detail, where it reads as evidence for "quiet" rather than as an event.
 */
export function flareLabel(xrayClass: string | null): FriendlyValue {
  if (!xrayClass) { return NO_DATA; }
  const cls = xrayClass.trim().toUpperCase();
  const letter = cls.charAt(0);
  const word = FLARE_WORDS.get(letter) ?? "background";
  if (!isFlareClass(letter)) {
    return { headline: "Quiet", detail: `${cls} ${word}` };
  }
  return { headline: cls, detail: word };
}

/** km/s → mph is ×2,237 (1 km/s = 2,236.94 mph). */
const MPH_PER_KM_S = 2237;

export function windLabel(speedKmS: number | null): FriendlyValue {
  if (speedKmS === null) { return NO_DATA; }
  const mph = Math.round(speedKmS * MPH_PER_KM_S);
  return {
    headline: `${Math.round(speedKmS)} km/s`,
    detail: `${mph.toLocaleString()} mph`,
  };
}

export function kpLabel(kp: number | null): FriendlyValue {
  if (kp === null) { return NO_DATA; }
  // Round to 2 dp, then drop trailing zeros: 1.33 → "1.33", 2.00 → "2".
  const detail = `Kp ${Number(kp.toFixed(2))}`;
  // One word, not a sentence. The headline shares a line with the chip's label
  // now (StatChip's `.sc-head`), so "Storm — aurora possible!" could only ever
  // arrive ellipsised — and it was redundant anyway: `SunStats.auroraAlert`
  // fires on exactly this condition (Kp >= 5) and puts the aurora sentence in
  // a full-width banner directly above these chips, where it can be read.
  if (kp >= 5) { return { headline: "Storm!", detail }; }
  if (kp >= 4) { return { headline: "Active", detail }; }
  return { headline: "Calm", detail };
}

/**
 * How many active regions NOAA is currently numbering.
 *
 * This replaced the international sunspot number, which reads as the obvious
 * choice and is the wrong one for this app: SSN is a MONTHLY mean published
 * about a month in arrears, so "the Sun Right Now" was quoting a July average
 * in late August. A region count is what is on the disk today, comes from the
 * same daily digest, and is the number a guest can actually go and count.
 *
 * A "region" is a sunspot GROUP, not a single spot, and the detail line says so
 * rather than letting the headline imply a spot count.
 */
export function sunspotLabel(regions: number | null): FriendlyValue {
  if (regions === null) { return NO_DATA; }
  if (regions === 0) { return { headline: "0", detail: "no active regions" }; }
  return {
    headline: String(regions),
    detail: regions === 1 ? "active region" : "active regions",
  };
}

// --- freshness ------------------------------------------------------------

export type FreshnessTier = "fresh" | "recent" | "old";

const FRESH_MS = 15 * 60 * 1000;
const RECENT_MS = 60 * 60 * 1000;

export function freshnessTier(observedMs: number | null): FreshnessTier {
  if (observedMs === null) { return "old"; }
  const age = Date.now() - observedMs;
  if (age < FRESH_MS) { return "fresh"; }
  if (age < RECENT_MS) { return "recent"; }
  return "old";
}

/** "3:20 AM" in the guest's own locale and timezone. */
export function clockLabel(ms: number | null): string {
  if (ms === null) { return "unknown"; }
  return new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

/** "just now" / "12 min ago" / "3 hours ago" / "2 days ago". */
export function agoLabel(ms: number | null): string {
  if (ms === null) { return "time unknown"; }
  const minutes = Math.round((Date.now() - ms) / 60000);
  if (minutes < 2) { return "just now"; }
  if (minutes < 60) { return `${minutes} min ago`; }
  const hours = Math.round(minutes / 60);
  if (hours < 36) { return hours === 1 ? "1 hour ago" : `${hours} hours ago`; }
  const days = Math.round(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}
