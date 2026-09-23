// =====================================================================
// Shared app state -- module-level refs, not pinia (sol's rule; pinia exists
// only because the WWT engine needs it). The entry chunk imports this, so it
// must stay ENGINE-FREE: no @wwtelescope or three imports here.
// =====================================================================

import { App, computed, reactive, ref } from "vue";

import { boolParam } from "../urlParams";

export type SheetId = "source" | "layers" | "camera" | "story" | "info";

/** Guest-facing layer switches. Keys match three/magnetosphere.ts LayerKey plus the HUD. */
export interface LayerFlags {
  fieldLines: boolean;
  fieldPulses: boolean;
  imfLines: boolean;
  textbookField: boolean;
  magnetopause: boolean;
  bowShock: boolean;
  magnetosheath: boolean;
  solarWind: boolean;
  ringCurrent: boolean;
  plasmasphere: boolean;
  auroraOval: boolean;
  hud: boolean;
}

export const LAYER_KEYS: (keyof LayerFlags)[] = [
  "fieldLines", "fieldPulses", "imfLines", "textbookField",
  "magnetopause", "bowShock", "magnetosheath",
  "solarWind", "ringCurrent", "plasmasphere", "auroraOval", "hud",
];

/** Defaults: phones pay for translucent overdraw, so the inner-region shells start off there. */
export function defaultLayers(isWide: boolean): LayerFlags {
  return {
    fieldLines: true, fieldPulses: true, imfLines: isWide, textbookField: false,
    magnetopause: true, bowShock: true, magnetosheath: false,
    solarWind: true, ringCurrent: isWide, plasmasphere: isWide, auroraOval: true, hud: true,
  };
}

export const layers = reactive<LayerFlags>(defaultLayers(false));

export interface StyleState { windScale: number; pulseScale: number; lineWidth: number; imfWidth: number }
export const DEFAULT_STYLE: StyleState = { windScale: 1, pulseScale: 1, lineWidth: 1.6, imfWidth: 1.2 };
export const style = reactive<StyleState>({ ...DEFAULT_STYLE });

export type Quality = "auto" | "mobile" | "desktop";
export const quality = ref<Quality>("auto");

/** Deep-link source key: "live", "lookback", or a storm key (gannon, octg4, ...). */
export const sourceKey = ref<string>("");

export type CameraId = "home" | "wide" | "top" | "headon" | "tail" | "dawn" | "dusk" | "ncusp" | "scusp" | "free";
export const camera = ref<CameraId>("home");
/** Bumped to (re)apply `camera` even when it did not change (tap Home twice). */
export const cameraToken = ref(0);
export function goCamera(id: CameraId): void {
  camera.value = id;
  cameraToken.value += 1;
}

export const wide = ref(false);

/** Fractional GLOBAL keyframe index of the playhead. */
export const frameT = ref(0);
/** Unix seconds per global keyframe of the loaded bundle. */
export const frameTimes = ref<number[]>([]);

export const sceneUnix = computed<number>(() => {
  const times = frameTimes.value;
  if (!times.length) { return Date.now() / 1000; }
  const last = times.length - 1;
  const t = Math.min(Math.max(frameT.value, 0), last);
  const a = Math.min(Math.floor(t), last), b = Math.min(a + 1, last);
  return times[a] + (times[b] - times[a]) * (t - a);
});

export const playing = ref(false);
/** Playback speed multiplier; 1x plays a 48 h window in ~2.5 min. */
export const speed = ref<1 | 2 | 4>(1);

export const sheet = ref<SheetId | null>(null);
export const kiosk = ref(boolParam("kiosk"));
export const attractDrift = ref(false);

export const resetToken = ref(0);
export function resetView(): void {
  goCamera("home");
  resetToken.value += 1;
}

/** Loaded bundle's per-keyframe HUD strings and key moments (set by the 3D view). */
export const hud = ref<string[]>([]);
export const loadState = reactive({ loading: false, doneBytes: 0, totalBytes: 0, error: "" });

let handle: App | null = null;
export function setAppHandle(app: App): void { handle = app; }
export function getAppHandle(): App | null { return handle; }
