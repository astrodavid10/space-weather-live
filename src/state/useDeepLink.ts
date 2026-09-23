// =====================================================================
// Deep links -- one-way read at boot, debounced write-back thereafter (sol's)
// =====================================================================
// A guest arrives from a QR code printed weeks earlier. INVALID VALUES FALL
// BACK SILENTLY, and we never pushState (the back button is the browser's).
// Only non-default values are written, so a shared URL stays short.
//
//   ?src=gannon            source key (live | lookback | bastille | nov2003 | gannon | octg4 | jan2026)
//   &t=2024-05-11T00:35Z   playhead as an ISO minute (robust across bundle rebuilds)
//   &cam=headon            camera preset
//   &layers=fieldLines,solarWind,...   the ON set, when it differs from the default
//   &style=1,1,1.6,1.2     wind, pulse, line width, IMF width
//   &q=mobile|desktop      quality preset

import { watch } from "vue";

import { queryParams } from "../urlParams";
import {
  CameraId, DEFAULT_STYLE, LAYER_KEYS, LayerFlags, camera, defaultLayers, frameT, frameTimes, layers,
  quality, sceneUnix, sourceKey, style, wide,
} from "./useAppState";

const DEBOUNCE_MS = 400;
const OWNED = ["src", "t", "cam", "layers", "style", "q"];
const CAMS: CameraId[] = ["home", "wide", "top", "headon", "tail", "dawn", "dusk", "ncusp", "scusp"];

let started = false;
let timer = 0;
/** Playhead requested by the URL, applied once the bundle's times are known. */
export let pendingTimeUnix: number | null = null;

export function consumePendingTime(): number | null {
  const t = pendingTimeUnix;
  pendingTimeUnix = null;
  return t;
}

function readOnce(): void {
  const p = queryParams();
  const src = p.get("src");
  if (src && /^[a-z0-9_-]{2,40}$/i.test(src)) { sourceKey.value = src.toLowerCase(); }
  const cam = p.get("cam") as CameraId | null;
  if (cam && CAMS.includes(cam)) { camera.value = cam; }
  const t = p.get("t");
  if (t) {
    const ms = Date.parse(t.endsWith("Z") ? t : t + "Z");
    if (Number.isFinite(ms)) { pendingTimeUnix = ms / 1000; }
  }
  const ls = p.get("layers");
  if (ls !== null) {
    const on = new Set(ls.split(",").filter(Boolean));
    for (const k of LAYER_KEYS) { layers[k] = on.has(k); }
  }
  const st = p.get("style");
  if (st) {
    const v = st.split(",").map(Number);
    if (v.length === 4 && v.every((x) => Number.isFinite(x) && x > 0 && x < 10)) {
      [style.windScale, style.pulseScale, style.lineWidth, style.imfWidth] = v;
    }
  }
  const q = p.get("q");
  if (q === "mobile" || q === "desktop") { quality.value = q; }
}

function query(): string {
  const p = queryParams();
  OWNED.forEach((k) => p.delete(k));
  if (sourceKey.value && sourceKey.value !== "lookback") { p.set("src", sourceKey.value); }
  if (frameTimes.value.length && sourceKey.value !== "live") {
    p.set("t", new Date(sceneUnix.value * 1000).toISOString().slice(0, 16) + "Z");
  }
  if (camera.value !== "home" && camera.value !== "free") { p.set("cam", camera.value); }
  const def = defaultLayers(wide.value);
  if (LAYER_KEYS.some((k) => layers[k] !== def[k as keyof LayerFlags])) {
    p.set("layers", LAYER_KEYS.filter((k) => layers[k]).join(","));
  }
  const sv = [style.windScale, style.pulseScale, style.lineWidth, style.imfWidth];
  const dv = [DEFAULT_STYLE.windScale, DEFAULT_STYLE.pulseScale, DEFAULT_STYLE.lineWidth, DEFAULT_STYLE.imfWidth];
  if (sv.some((x, i) => Math.abs(x - dv[i]) > 1e-6)) { p.set("style", sv.map((x) => +x.toFixed(2)).join(",")); }
  if (quality.value !== "auto") { p.set("q", quality.value); }
  return p.toString().replace(/%2C/g, ",").replace(/%3A/g, ":");
}

function writeNow(): void {
  const qs = query();
  window.history.replaceState(window.history.state, "", window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash);
}

export function scheduleDeepLinkWrite(): void {
  window.clearTimeout(timer);
  timer = window.setTimeout(writeNow, DEBOUNCE_MS);
}

export function initDeepLink(): void {
  if (started) { return; }
  started = true;
  readOnce();
  watch([sourceKey, camera, quality, () => ({ ...layers }), () => ({ ...style })], scheduleDeepLinkWrite, { deep: true });
  // The playhead changes 30x a second while playing; write it only when it settles.
  watch(frameT, scheduleDeepLinkWrite);
}
