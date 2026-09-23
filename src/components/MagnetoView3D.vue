<template>
  <div ref="root" class="mv-root">
    <WorldWideTelescope wwt-namespace="wwt-swl" />

    <div v-if="!ready && !failed" class="mv-cover sol-3d-placeholder">
      <div class="sol-spinner" aria-hidden="true"></div>
      <p class="sol-3d-text">Bringing Earth's magnetic field into three dimensions…</p>
    </div>
    <div v-if="failed" class="mv-cover sol-3d-placeholder" role="alert">
      <p class="sol-3d-text">
        The 3D view isn't available right now. It needs WebGL and a connection to the
        WorldWide Telescope service.
      </p>
    </div>

    <pre v-if="debug" class="mv-debug" aria-hidden="true">{{ debugText }}</pre>
  </div>
</template>

<script lang="ts">
// wwt-hacks FIRST, for its import-time side effects: the engine binds its touch
// handlers at initControl and captures the method references then (sol footgun 10).
import "../wwt/wwt-hacks";

import { SpaceTimeController } from "@wwtelescope/engine";
import { WWTAwareComponent, WWTComponent, wwtPinia } from "@wwtelescope/engine-pinia";
import { PropType, defineComponent, markRaw } from "vue";

import { Bundle, loadBundle } from "../data/bundle";
import type { PresetName, SourceRow } from "../data/index";
import { eqToEcl, wwtToEcl } from "../three/frame";
import { Magnetosphere, createMagnetosphere } from "../three/magnetosphere";
import { ThreeStage, createThreeStage } from "../three/stage";
import { installHiDpiCanvas } from "../wwt/wwt-hacks";
import { installSunGestures, type SunGestures } from "../wwt/gestures";
import {
  CameraPreset, StageHost, cameraInfo, earthWorld, goToPreset, initMagnetoStage, refitFraming, sunDirWorld,
} from "../wwt/magnetoStage";
import {
  attractDrift, camera, cameraToken, frameT, frameTimes, getAppHandle, hud, layers, loadState, playing,
  sceneUnix, speed, style,
} from "../state/useAppState";
import { consumePendingTime } from "../state/useDeepLink";
import { boolParam } from "../urlParams";

let engineInstalled = false;
function installEngine(): void {
  if (engineInstalled) { return; }
  const app = getAppHandle();
  if (!app) { console.error("[MagnetoView3D] no app handle; App.vue must call setAppHandle() at mount."); return; }
  app.use(wwtPinia);
  app.component("WorldWideTelescope", WWTComponent);
  engineInstalled = true;
}
installEngine();

const MODE_TIMEOUT_MS = 12000;
const PUBLISH_MS = 33;
/** Scene seconds per real second at 1x: a 48 h window in ~150 s. */
const BASE_RATE = (48 * 3600) / 150;
/** Nose-to-Sun tripwire. Aberration (~4 deg) is applied to the dome's magnetopause. */
const NOSE_TOLERANCE_DEG = 8;

interface WwtHost extends StageHost { waitForReady: () => Promise<void> }

interface Runtime {
  stage: ThreeStage | null;
  magneto: Magnetosphere | null;
  bundle: Bundle | null;
  abort: AbortController | null;
  gestures: SunGestures | null;
  lastMs: number;
  lastPublishMs: number;
  localT: number;
  published: number;
  destroyed: boolean;
  noseChecked: string;
  framesSinceLoad: number;
  resW: number;
  resH: number;
}

/** Fractional keyframe index for a unix time (binary search over the bundle's epochs). */
function frameForUnix(times: number[], unix: number): number {
  const n = times.length;
  if (!n) { return 0; }
  if (unix <= times[0]) { return 0; }
  if (unix >= times[n - 1]) { return n - 1; }
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= unix) { lo = mid; } else { hi = mid; }
  }
  return lo + (unix - times[lo]) / (times[hi] - times[lo]);
}

export default defineComponent({
  name: "MagnetoView3D",
  extends: WWTAwareComponent,

  props: {
    source: { type: Object as PropType<SourceRow | null>, default: null },
    preset: { type: String as PropType<PresetName>, default: "mobile" },
    /** Live OVATION points (equatorial R_E) to replace the bundle oval, or null. */
    liveOval: {
      type: Object as PropType<{ pos: Float32Array; color: Float32Array; alpha: Float32Array; count: number } | null>,
      default: null,
    },
    /** Loop storms; rest at the newest frame for the look-back. */
    loop: { type: Boolean, default: true },
  },

  emits: ["loaded", "load-error"],

  beforeCreate() { installEngine(); },

  data() {
    return {
      ready: false,
      failed: false,
      debug: boolParam("debug"),
      debugText: "",
      rt: markRaw({
        stage: null, magneto: null, bundle: null, abort: null, gestures: null,
        lastMs: 0, lastPublishMs: 0, localT: 0, published: -1, destroyed: false,
        noseChecked: "", framesSinceLoad: 0, resW: 0, resH: 0,
      } as Runtime),
    };
  },

  watch: {
    source() { this.loadSource(); },
    preset() { this.loadSource(); },
    cameraToken() { this.applyCamera(false); },
    liveOval(v) { this.rt.magneto?.setLiveAurora(v); },
    layers: { deep: true, handler() { this.applyLayers(); } },
    style: { deep: true, handler() { this.rt.magneto?.setStyle({ ...style }); } },
    frameT(v: number) {
      // An outside change (scrubber, key-moment tap): adopt it.
      if (Math.abs(v - this.rt.published) > 1e-6) { this.rt.localT = v; }
    },
  },

  setup() {
    return { layers, style, cameraToken, frameT };
  },

  async mounted() {
    const host = this as unknown as WwtHost;
    const timer = window.setTimeout(() => { if (!this.ready) { this.failed = true; } }, MODE_TIMEOUT_MS);
    try {
      await host.waitForReady();
    } catch (err) {
      console.error("[MagnetoView3D] WWT failed to start", err);
      this.failed = true;
      return;
    }
    window.clearTimeout(timer);
    if (this.rt.destroyed) { return; }
    installHiDpiCanvas(2);
    initMagnetoStage(host, new Date());
    try {
      this.rt.stage = markRaw(createThreeStage({
        target: boolParam("overlay") ? "overlay" : "wwt",
        onBeforeRender: this.tick,
        onContextRestored: () => this.loadSource(),
      }));
    } catch (err) {
      console.error("[MagnetoView3D] three.js stage failed", err);
      this.failed = true;
      return;
    }
    const root = this.$refs.root as HTMLElement | undefined;
    if (root) { this.rt.gestures = installSunGestures(root); }
    window.addEventListener("resize", refitFraming);
    this.ready = true;
    if (this.debug) {
      (window as unknown as { swlDebug: unknown }).swlDebug = { rt: this.rt, sunDirWorld, earthWorld, eqToEcl, cameraInfo, goToPreset };
    }
    this.applyCamera(true);
    this.loadSource();
  },

  beforeUnmount() {
    this.rt.destroyed = true;
    this.rt.abort?.abort();
    this.rt.gestures?.dispose();
    window.removeEventListener("resize", refitFraming);
    this.rt.magneto?.dispose();
    this.rt.stage?.dispose();
  },

  methods: {
    applyCamera(instant: boolean) {
      if (!this.ready || camera.value === "free") { return; }
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      goToPreset(camera.value as CameraPreset, instant || reduce);
    },

    applyLayers() {
      this.rt.magneto?.setVisible({ ...layers });
    },

    async loadSource() {
      const src = this.source;
      if (!this.ready || !src) { return; }
      const entry = src.presets[this.preset] ?? src.presets.mobile ?? src.presets.desktop;
      if (!entry) { this.$emit("load-error", "This source has no web bundle."); return; }
      this.rt.abort?.abort();
      const abort = new AbortController();
      this.rt.abort = abort;
      loadState.loading = true;
      loadState.error = "";
      loadState.doneBytes = 0;
      loadState.totalBytes = entry.bin_gz_bytes;
      let bundle: Bundle;
      try {
        bundle = await loadBundle(src.base, entry, {
          signal: abort.signal, version: src.version,
          onProgress: (d, t) => { loadState.doneBytes = d; loadState.totalBytes = t; },
        });
      } catch (err) {
        if (abort.signal.aborted) { return; }
        console.error("[MagnetoView3D] bundle failed", err);
        loadState.loading = false;
        loadState.error = String((err as Error).message ?? err);
        this.$emit("load-error", loadState.error);
        return;
      }
      if (abort.signal.aborted || this.rt.destroyed || !this.rt.stage) { return; }

      const old = this.rt.magneto;
      if (old) { this.rt.stage.scene.remove(old.root); old.dispose(); }
      const magneto = markRaw(createMagnetosphere(bundle));
      this.rt.stage.scene.add(magneto.root);
      this.rt.magneto = magneto;
      this.rt.bundle = markRaw(bundle);
      this.rt.resW = 0;
      this.rt.noseChecked = "";
      this.rt.framesSinceLoad = 0;
      magneto.setStyle({ ...style });
      magneto.setLiveAurora(this.liveOval);
      this.applyLayers();

      frameTimes.value = bundle.times;
      hud.value = bundle.manifest.keyframes.hud;
      const pending = consumePendingTime();
      const last = bundle.times.length - 1;
      const start = pending != null ? frameForUnix(bundle.times, pending) : (this.loop ? 0 : last);
      this.rt.localT = start;
      this.rt.published = start;
      frameT.value = start;
      loadState.loading = false;
      this.$emit("loaded", bundle.manifest);
    },

    /** Every frame, inside three-wwt's onBeforeRender (after WWT's camera sync). */
    tick() {
      const rt = this.rt;
      const now = performance.now();
      const dt = rt.lastMs ? Math.min(0.1, (now - rt.lastMs) / 1000) : 0;
      rt.lastMs = now;
      const stage = rt.stage;
      const bundle = rt.bundle;
      if (!stage || !rt.magneto || !bundle) { return; }

      const times = bundle.times;
      const last = times.length - 1;
      if (playing.value && last > 0) {
        const unix = sceneUnix.value + dt * BASE_RATE * speed.value;
        let t = frameForUnix(times, unix);
        if (unix >= times[last]) {
          if (this.loop) { t = 0; } else { t = last; playing.value = false; }
        }
        rt.localT = t;
      }
      if (attractDrift.value) {
        // Kiosk attract: nothing to do here beyond playback; cameras cycle in App.
      }

      const { width, height } = stage.bufferSize();
      if (width !== rt.resW || height !== rt.resH) {
        rt.resW = width; rt.resH = height;
        rt.magneto.setRes(width, height, Math.min(window.devicePixelRatio || 1, 2));
      }

      // WWT's clock follows the playhead, so its Earth, terminator and Sun match
      // the keyframe epoch. set_now is cheap; planet positions update next frame
      // and the Earth pin follows them inside the engine (magnetoStage.ts).
      const tUnix = this.unixAt(times, rt.localT);
      SpaceTimeController.set_now(new Date(tUnix * 1000));

      rt.magneto.update(rt.localT, now / 1000, tUnix);
      rt.framesSinceLoad += 1;

      if (now - rt.lastPublishMs > PUBLISH_MS) {
        rt.lastPublishMs = now;
        if (Math.abs(rt.localT - frameT.value) > 1e-6) {
          rt.published = rt.localT;
          frameT.value = rt.localT;
        }
        if (this.debug) { this.updateDebug(); }
        this.checkNose();
      }
    },

    unixAt(times: number[], t: number): number {
      const last = times.length - 1;
      const c = Math.min(Math.max(t, 0), last);
      const a = Math.floor(c), b = Math.min(a + 1, last);
      return times[a] + (times[b] - times[a]) * (c - a);
    },

    /** Angle between the magnetopause nose and the Sun, degrees. */
    noseAngle(): number | null {
      const m = this.rt.magneto;
      if (!m) { return null; }
      const eq = m.noseDirection(this.rt.localT);
      if (!eq) { return null; }
      const nose = eqToEcl(eq);
      const s = wwtToEcl({ x: sunDirWorld()[0], y: sunDirWorld()[1], z: sunDirWorld()[2] }).normalize();
      const d = Math.max(-1, Math.min(1, nose[0] * s.x + nose[1] * s.y + nose[2] * s.z));
      return (Math.acos(d) * 180) / Math.PI;
    },

    /** Frame tripwire (plan section 5.1): once per source, warn if the nose misses the Sun. */
    checkNose() {
      const key = this.rt.bundle?.manifest.event_id ?? "";
      // Wait a few frames: WWT moves Earth one frame AFTER set_now, so the very
      // first check would compare the bundle with the Earth of the old clock.
      if (!key || this.rt.noseChecked === key || !earthWorld() || this.rt.framesSinceLoad < 10) { return; }
      const ang = this.noseAngle();
      if (ang == null) { return; }
      this.rt.noseChecked = key;
      if (ang > NOSE_TOLERANCE_DEG) {
        console.warn(`[frame] magnetopause nose is ${ang.toFixed(1)} deg from the Sun (tolerance ${NOSE_TOLERANCE_DEG}) -- frame bug?`);
      }
    },

    updateDebug() {
      const c = cameraInfo();
      const e = earthWorld();
      const ang = this.noseAngle();
      this.debugText = [
        `epoch   ${new Date(sceneUnix.value * 1000).toISOString().slice(0, 16)}Z  frame ${this.rt.localT.toFixed(2)}`,
        `camera  lat ${c.latDeg.toFixed(1)}  lng ${c.lngDeg.toFixed(1)}  dist ${c.distanceRe.toFixed(1)} R_E`,
        `earth   ${e ? e.map((v) => v.toFixed(4)).join(", ") : "-"} AU (WWT world)`,
        `nose    ${ang == null ? "-" : ang.toFixed(1) + " deg from Sun"}`,
        `layers  ${[...(this.rt.magneto?.present ?? [])].join(" ")}`,
      ].join("\n");
    },
  },
});
</script>

<style lang="less">
.mv-root {
  position: absolute;
  inset: 0;
  overflow: hidden;
  background: #000;

  .wwtelescope-component {
    position: absolute;
    inset: 0;
    touch-action: none;
  }
}

.mv-cover {
  position: absolute;
  inset: 0;
  z-index: 20;
  background: #000;
}

.mv-debug {
  position: absolute;
  left: 0.5rem;
  top: 5rem;
  z-index: 30;
  margin: 0;
  padding: 0.5rem 0.6rem;
  font: 11px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: #cfe3ff;
  background: rgba(4, 6, 15, 0.85);
  border: 1px solid rgba(148, 155, 175, 0.3);
  border-radius: 8px;
  pointer-events: none;
  white-space: pre;
}
</style>
