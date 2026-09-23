<template>
  <div id="main-content">
    <div class="sol-root" :class="{ 'is-wide': wide, 'is-kiosk': kioskMode }">
      <main class="sol-area-stage">
        <div class="sol-topbar">
          <div class="sol-topbar-brand"><brand-mark class="sol-brand" /></div>
          <div class="swl-titlecol">
            <p class="sol-title no-select" aria-label="Magnetosphere, Earth in the solar wind">
              <span class="sol-title-name">Magnetosphere</span>
              <span class="sol-title-sub">Earth in the solar wind</span>
            </p>
            <button
              type="button"
              class="swl-badge"
              :aria-label="badgeAria"
              aria-haspopup="dialog"
              @click="wide ? null : toggleSheet('source')"
            >
              <span class="swl-tier" :class="'is-' + tier.toLowerCase()">{{ tierText }}</span>
              <span class="swl-badge-text">{{ badgeText }}</span>
              <font-awesome-icon v-if="!wide" icon="chevron-down" class="swl-badge-chev" />
            </button>
          </div>
        </div>

        <magneto-view-3d
          :source="currentSource"
          :preset="preset"
          :live-oval="liveOvalProp"
          :loop="!isLookbackLike"
          @loaded="onLoaded"
          @load-error="onLoadError"
        />

        <!-- Phone: button stack top-right -->
        <div v-if="!wide" class="swl-buttons" data-camera-passthrough="false">
          <button type="button" class="swl-icon-btn" :class="{ 'is-active': sheet === 'layers' }" aria-label="Layers" @click="toggleSheet('layers')">
            <font-awesome-icon icon="layer-group" />
          </button>
          <button type="button" class="swl-icon-btn" :class="{ 'is-active': sheet === 'story' }" aria-label="Story" @click="toggleSheet('story')">
            <font-awesome-icon icon="book-open" />
          </button>
          <button type="button" class="swl-icon-btn" :class="{ 'is-active': sheet === 'info' }" aria-label="About this view" @click="toggleSheet('info')">
            <font-awesome-icon icon="circle-info" />
          </button>
        </div>

        <!-- Phone popovers -->
        <div v-if="!wide && (sheet === 'source' || sheet === 'layers')" class="swl-popover" data-camera-passthrough="false">
          <source-panel
            v-if="sheet === 'source'"
            :sources="sources"
            :current="sourceKey"
            :live-tier="live.tier"
            :live-observed-ms="live.observedMs"
            @pick="pickSource"
          />
          <layer-panel v-else :present="present" :live-mode="isLive" />
        </div>

        <p v-if="caveat" class="swl-caveat" :class="{ 'is-wide': wide }">{{ caveat }}</p>

        <div class="swl-bottom" :class="{ 'is-wide': wide }">
          <story-card
            v-if="!wide && sheet === 'story' && currentSource"
            class="swl-story-pop"
            closable
            :title="storyTitle"
            :talking-points="currentSource.talkingPoints"
            :moments="moments"
            :active-moment="activeMoment"
            :kind="currentSource.kind"
            @moment="jumpToMoment"
            @close="sheet = null"
          />
          <camera-strip />
          <time-bar
            :moments="moments"
            :hud-line="!wide && layers.hud ? hudLine : ''"
            :label="loadingLabel"
            :loading="loadState.loading"
            :load-done="loadState.doneBytes"
            :load-total="loadState.totalBytes"
            :error="loadState.error ? 'This data set didn\'t load. Pick another, or try again later.' : ''"
            :note="timeNote"
            @moment="jumpToMoment"
            @scrub="onScrub"
          />
          <hud-readout
            v-if="!wide && hudExpanded && layers.hud"
            class="swl-hud-pop"
            :replay="isLive ? null : hudValues"
            :live="isLive ? live : null"
          />
          <button v-if="!wide && layers.hud" type="button" class="swl-hud-toggle" @click="hudExpanded = !hudExpanded">
            {{ hudExpanded ? "Hide readout" : "Show readout" }}
          </button>
        </div>
      </main>

      <!-- Desktop rail -->
      <template v-if="wide">
        <source-panel
          class="sol-area-source"
          compact
          :sources="sources"
          :current="sourceKey"
          :live-tier="live.tier"
          :live-observed-ms="live.observedMs"
          @pick="pickSource"
        />
        <div class="sol-area-panel">
          <div class="swl-tabs" role="tablist" aria-label="Panels">
            <button
              v-for="t in tabs"
              :key="t.id"
              type="button"
              role="tab"
              class="swl-tab"
              :class="{ 'is-on': railTab === t.id }"
              :aria-selected="railTab === t.id ? 'true' : 'false'"
              @click="railTab = t.id"
            >{{ t.label }}</button>
          </div>
          <div class="swl-tabpanel" role="tabpanel">
            <layer-panel v-if="railTab === 'layers'" class="swl-flat" :present="present" :live-mode="isLive" />
            <story-card
              v-else-if="railTab === 'story' && currentSource"
              class="swl-flat"
              :title="storyTitle"
              :talking-points="currentSource.talkingPoints"
              :moments="moments"
              :active-moment="activeMoment"
              :kind="currentSource.kind"
              @moment="jumpToMoment"
            />
            <info-modal v-else-if="railTab === 'about'" inline class="swl-flat" :updated="updatedLabel" />
          </div>
        </div>
        <hud-readout
          v-if="layers.hud"
          class="sol-area-stats"
          :replay="isLive ? null : hudValues"
          :live="isLive ? live : null"
        />
      </template>
    </div>

    <info-modal v-if="!wide && sheet === 'info'" :updated="updatedLabel" @close="sheet = null" />

    <button v-if="kioskMode" type="button" class="sol-take-home" @click="showTakeHomeQr">
      <font-awesome-icon icon="qrcode" />
      <span>Take it with you</span>
    </button>
    <p v-if="attractActive" class="sol-attract-hint" aria-hidden="true">Touch to explore</p>
    <kiosk-qr-modal v-if="qrUrl" :url="qrUrl" :title="qrTitle" @close="qrUrl = ''" />
  </div>
</template>

<script lang="ts">
import { defineAsyncComponent, defineComponent, getCurrentInstance, h, markRaw } from "vue";

import BrandMark from "./components/BrandMark.vue";
import CameraStrip from "./components/CameraStrip.vue";
import HudReadout from "./components/HudReadout.vue";
import InfoModal from "./components/InfoModal.vue";
import LayerPanel from "./components/LayerPanel.vue";
import SourcePanel from "./components/SourcePanel.vue";
import StoryCard from "./components/StoryCard.vue";
import TimeBar from "./components/TimeBar.vue";
import type { BundleManifest } from "./data/bundle";
import { isMetered } from "./data/connection";
import { SourceRow, loadCatalogue } from "./data/index";
import { OvalPoints, live, onOval, ovalPoints, startLive, startOvation, stopOvation } from "./data/live";
import { KeyMoment, parseHud, parseKeyMoments } from "./data/readout";
import { attractActive, initAttract, stopAttract } from "./kiosk/attract";
import { KIOSK_RELOAD_HOUR, installKioskGuards, scheduleDailyReload } from "./kiosk/kiosk";
import { statsTrack } from "./kiosk/kioskStats";
import { takeHomeUrl } from "./kiosk/takeHome";
import {
  camera, defaultLayers, frameT, frameTimes, hud, kiosk, layers, loadState, playing, quality, sceneUnix,
  setAppHandle, sheet, sourceKey, wide, SheetId,
} from "./state/useAppState";
import { initDeepLink } from "./state/useDeepLink";

const WIDE_QUERY = "(min-width: 900px)";
const CHUNK_TIMEOUT_MS = 30000;

const chunkLoading = defineComponent({
  name: "MagnetoLoading",
  render() {
    return h("div", { class: "sol-3d-placeholder no-select" }, [
      h("div", { class: "sol-spinner" }),
      h("p", { class: "sol-3d-text" }, "3D view coming online…"),
    ]);
  },
});

const chunkFailed = defineComponent({
  name: "MagnetoFailed",
  render() {
    return h("div", { class: "sol-3d-placeholder no-select" }, [
      h("p", { class: "sol-3d-text" }, "The 3D view couldn't load. The live numbers and the storm stories still work."),
    ]);
  },
});

const magnetoView3d = defineAsyncComponent({
  loader: () => import(/* webpackChunkName: "magneto3d" */ "./components/MagnetoView3D.vue"),
  loadingComponent: chunkLoading,
  errorComponent: chunkFailed,
  timeout: CHUNK_TIMEOUT_MS,
  delay: 0,
});

const kioskQrModal = defineAsyncComponent(() => import(/* webpackChunkName: "kioskqr" */ "./components/KioskQrModal.vue"));

type RailTab = "layers" | "story" | "about";

export default defineComponent({
  name: "SpaceWeatherLive",
  components: {
    "brand-mark": BrandMark, "camera-strip": CameraStrip, "hud-readout": HudReadout, "info-modal": InfoModal,
    "kiosk-qr-modal": kioskQrModal, "layer-panel": LayerPanel, "magneto-view-3d": magnetoView3d,
    "source-panel": SourcePanel, "story-card": StoryCard, "time-bar": TimeBar,
  },
  props: {
    kioskMode: { type: Boolean, default: false },
    kioskHomeUrl: { type: String, default: "" },
  },

  setup() {
    // Defaults depend on the screen, and the deep link must be able to override
    // them -- so decide `wide` and the defaults BEFORE the URL is read.
    wide.value = window.matchMedia(WIDE_QUERY).matches;
    Object.assign(layers, defaultLayers(wide.value));
    initDeepLink();
    startLive();
    return { attractActive, sheet, wide, layers, live, loadState, sourceKey, frameT };
  },

  data() {
    return {
      mediaQuery: null as MediaQueryList | null,
      sources: [] as SourceRow[],
      generatedIso: "",
      present: [] as string[],
      hudExpanded: false,
      railTab: "layers" as RailTab,
      tabs: [{ id: "layers", label: "Layers" }, { id: "story", label: "Story" }, { id: "about", label: "About" }] as { id: RailTab; label: string }[],
      liveOval: null as OvalPoints | null,
      qrUrl: "",
      qrTitle: "",
      cleanups: [] as (() => void)[],
      catalogueError: "",
    };
  },

  computed: {
    isLive(): boolean { return sourceKey.value === "live"; },
    isLookbackLike(): boolean { return sourceKey.value === "live" || sourceKey.value === "lookback"; },
    currentSource(): SourceRow | null {
      const key = this.isLive ? "lookback" : sourceKey.value;
      return this.sources.find((s) => s.key === key) ?? null;
    },
    preset(): "mobile" | "desktop" {
      if (quality.value === "mobile" || quality.value === "desktop") { return quality.value; }
      return wide.value && !isMetered() ? "desktop" : "mobile";
    },
    moments(): KeyMoment[] {
      const s = this.currentSource;
      return s ? parseKeyMoments(s.keyMoments, s.window.start) : [];
    },
    activeMoment(): number {
      const u = sceneUnix.value;
      return this.moments.findIndex((m) => Math.abs(m.unix - u) <= 20 * 60);
    },
    hudIndex(): number { return Math.min(Math.max(Math.round(frameT.value), 0), Math.max(0, hud.value.length - 1)); },
    hudValues() { return parseHud(hud.value[this.hudIndex]); },
    hudLine(): string {
      if (this.isLive) {
        const f = (v: number | null, d = 0) => (v == null ? "—" : v.toFixed(d));
        return `${f(live.speed)} km/s · Bz ${f(live.bz, 1)} nT · ${f(live.density, 1)} /cc · Kp ${f(live.kp, 1)} · ${live.source || "L1"}`;
      }
      const v = this.hudValues;
      if (v.speed == null) { return ""; }
      const bz = v.bz == null ? "—" : `${v.bz >= 0 ? "+" : "−"}${Math.abs(v.bz).toFixed(1)}`;
      return `${v.speed.toFixed(0)} km/s · Bz ${bz} nT · ${v.density?.toFixed(1)} /cc · Kp ${v.kp?.toFixed(1)} · ${v.source}`;
    },
    tier(): string {
      if (this.isLive) { return live.tier === "NOFEED" ? "NOFEED" : live.tier; }
      return "REPLAY";
    },
    tierText(): string { return this.tier === "NOFEED" ? "NO LIVE FEED" : this.tier; },
    badgeText(): string {
      if (this.isLive) {
        if (live.tier === "NOFEED") { return "Last 48 h · ACE"; }
        const age = live.observedMs ? this.agoShort(Date.now() - live.observedMs) : "";
        return `${live.source || "L1"} · ${live.tier === "LIVE" ? age + " ago" : "as of " + this.clockUT(live.observedMs)}`;
      }
      const s = this.currentSource;
      if (!s) { return this.catalogueError ? "No data" : "Loading…"; }
      return s.kind === "lookback" ? "Last 48 h · ACE" : `${s.label} · OMNI`;
    },
    badgeAria(): string { return `${this.tierText}: ${this.badgeText}. Change data source`; },
    storyTitle(): string { return this.currentSource?.title ?? ""; },
    loadingLabel(): string { return this.currentSource?.label ?? "data"; },
    timeNote(): string {
      if (!this.isLookbackLike || !frameTimes.value.length) { return ""; }
      const lastT = frameTimes.value[frameTimes.value.length - 1];
      if (Math.abs(sceneUnix.value - lastT) < 60) {
        const h = (Date.now() / 1000 - lastT) / 3600;
        return `newest frame, ${h < 48 ? Math.round(h) + " h" : Math.round(h / 24) + " days"} old`;
      }
      return "replay";
    },
    caveat(): string {
      if (!this.isLive) { return ""; }
      const lastT = frameTimes.value[frameTimes.value.length - 1];
      const h = lastT ? (Date.now() / 1000 - lastT) / 3600 : null;
      const age = h == null ? "" : h < 48 ? `${Math.round(h)} h` : `${Math.round(h / 24)} days`;
      if (live.tier === "NOFEED") { return "NOAA's live feed isn't reachable right now. Showing the last 48 hours instead."; }
      const oval = layers.auroraOval ? " Aurora oval: OVATION Prime forecast, a model, not a photograph." : "";
      return `Numbers are live. The field shapes are from the last look-back frame${age ? ", " + age + " old" : ""}.${oval}`;
    },
    updatedLabel(): string { return this.generatedIso ? this.generatedIso.slice(0, 10) : ""; },
    liveOvalProp(): OvalPoints | null {
      return this.isLive && this.liveOval ? markRaw(this.liveOval) as OvalPoints : null;
    },
  },

  watch: {
    attractActive(a: boolean) { if (a) { this.qrUrl = ""; } },
    isLive: { immediate: true, handler(v: boolean) { this.syncOvation(v, layers.auroraOval); } },
    "layers.auroraOval"(v: boolean) { this.syncOvation(this.isLive, v); },
  },

  async mounted() {
    const instance = getCurrentInstance();
    if (instance) { setAppHandle(instance.appContext.app); }
    if (this.kioskMode) { kiosk.value = true; this.installKiosk(); }
    this.mediaQuery = window.matchMedia(WIDE_QUERY);
    this.mediaQuery.addEventListener("change", this.onWideChange);
    this.cleanups.push(onOval(() => { this.liveOval = ovalPoints ? markRaw(ovalPoints) : null; }));
    try {
      const cat = await loadCatalogue();
      this.sources = cat.sources;
      this.generatedIso = cat.index.generated_iso;
      const keys = new Set(["live", ...cat.sources.map((s) => s.key)]);
      if (!sourceKey.value || !keys.has(sourceKey.value)) {
        sourceKey.value = cat.sources.some((s) => s.kind === "lookback") ? "live" : (cat.sources[0]?.key ?? "");
      }
    } catch (err) {
      console.error("[app] catalogue failed", err);
      this.catalogueError = String(err);
      loadState.error = "catalogue";
    }
  },

  beforeUnmount() {
    this.mediaQuery?.removeEventListener("change", this.onWideChange);
    this.cleanups.forEach((f) => f());
  },

  methods: {
    onWideChange(e: MediaQueryListEvent): void { wide.value = e.matches; },
    toggleSheet(id: SheetId): void { sheet.value = sheet.value === id ? null : id; },
    pickSource(key: string): void {
      if (key !== sourceKey.value) { playing.value = false; }
      sourceKey.value = key;
      if (!wide.value) { sheet.value = null; }
      statsTrack("select", key);
    },
    onLoaded(m: BundleManifest): void {
      this.present = m.layers.map((l) => l.name);
      // Storms start playing (the guest asked for a movie); the look-back rests
      // at its newest frame, like sol; reduced motion never autoplays.
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      playing.value = !this.isLookbackLike && !reduce;
    },
    onLoadError(msg: string): void { console.warn("[app] load error", msg); },
    onScrub(): void {
      // Scrubbing the live source back in time makes it a replay of the look-back.
      if (this.isLive) { sourceKey.value = "lookback"; }
    },
    jumpToMoment(i: number): void {
      const m = this.moments[i];
      const t = frameTimes.value;
      if (!m || !t.length) { return; }
      let lo = 0, hi = t.length - 1;
      if (m.unix <= t[0]) { frameT.value = 0; } else if (m.unix >= t[hi]) { frameT.value = hi; } else {
        while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (t[mid] <= m.unix) { lo = mid; } else { hi = mid; } }
        frameT.value = lo + (m.unix - t[lo]) / (t[hi] - t[lo]);
      }
      playing.value = false;
      if (this.isLive) { sourceKey.value = "lookback"; }
    },
    syncOvation(isLive: boolean, on: boolean): void {
      if (isLive && on) { startOvation(); } else { stopOvation(); }
    },
    agoShort(ms: number): string {
      const m = Math.round(ms / 60000);
      if (m < 60) { return `${m} min`; }
      return `${Math.round(m / 60)} h`;
    },
    clockUT(ms: number | null): string {
      if (!ms) { return "—"; }
      const d = new Date(ms);
      return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")} UT`;
    },
    installKiosk(): void {
      this.cleanups.push(installKioskGuards({ onExternalLink: (url, title) => this.showQr(url, title, url) }));
      this.cleanups.push(scheduleDailyReload(KIOSK_RELOAD_HOUR, () => attractActive.value));
      initAttract();
      this.cleanups.push(stopAttract);
    },
    showQr(url: string, title: string, detail: string): void {
      this.qrUrl = url;
      this.qrTitle = title || "Scan to visit";
      sheet.value = null;
      statsTrack("qr", detail);
    },
    showTakeHomeQr(): void {
      const params: Record<string, string> = { src: sourceKey.value };
      if (!this.isLive && frameTimes.value.length) { params.t = new Date(sceneUnix.value * 1000).toISOString().slice(0, 16) + "Z"; }
      if (camera.value !== "home" && camera.value !== "free") { params.cam = camera.value; }
      this.showQr(takeHomeUrl(this.kioskHomeUrl, params), "Take it with you", sourceKey.value);
      statsTrack("takeHome");
    },
  },
});
</script>

<style lang="less" scoped>
.sol-root {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: var(--sol-bg);
  color: var(--sol-text);
  -webkit-tap-highlight-color: transparent;
  user-select: none;
}

.sol-topbar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  z-index: 6;
  display: grid;
  grid-template-columns: 4.25rem 1fr;
  padding-right: 3.75rem;
  pointer-events: none;
  .is-wide & { padding-right: 0.75rem; }
}
.sol-topbar-brand { position: relative; }
.sol-brand { top: calc(env(safe-area-inset-top) + 0.75rem); left: 0.75rem; }

.swl-titlecol {
  justify-self: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.35rem;
  min-width: 0;
  max-width: 100%;
  margin-top: calc(env(safe-area-inset-top) + 0.5rem);
}

// sol's title pill, minus backdrop-filter (no blur over the moving canvas).
.sol-title {
  min-width: 0;
  max-width: 100%;
  margin: 0;
  display: flex;
  align-items: baseline;
  gap: 0.4rem;
  padding: 0.28rem 0.85rem;
  border: var(--sol-panel-border);
  border-radius: 999px;
  background: var(--sol-surface);
  white-space: nowrap;
  overflow: hidden;
  pointer-events: none;
}
.sol-title-name { flex: 0 0 auto; font-family: "Overpass", system-ui, sans-serif; font-weight: 600; font-size: 1.05rem; line-height: 1.1; color: var(--sol-text); }
.sol-title-sub { min-width: 0; overflow: hidden; text-overflow: ellipsis; font-size: 0.76rem; color: var(--sol-text-dim); }

@media (max-width: 420px) {
  .sol-title { flex-direction: column; align-items: center; gap: 0; border-radius: 14px; padding: 0.25rem 0.8rem; }
}

.swl-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.45rem;
  min-height: 36px;
  max-width: 100%;
  padding: 0.2rem 0.7rem 0.2rem 0.35rem;
  border: var(--sol-panel-border);
  border-radius: 999px;
  background: var(--sol-surface);
  color: var(--sol-text);
  font-size: 0.74rem;
  pointer-events: auto;
  cursor: pointer;
  .is-wide & { cursor: default; }
  &:focus-visible { outline: 2px solid var(--sol-select); outline-offset: 2px; }
}
.swl-badge-text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.swl-badge-chev { font-size: 0.65rem; color: var(--sol-text-dim); }

.swl-tier {
  flex: 0 0 auto;
  padding: 0.12rem 0.45rem;
  border-radius: 999px;
  font-size: 0.58rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--sol-text-dim);
  border: 1px solid var(--sol-hairline);
  &.is-live { color: var(--swl-live); border-color: rgba(var(--swl-live-rgb), 0.55); background: rgba(var(--swl-live-rgb), 0.12); }
  &.is-cached { color: var(--sol-warn); border-color: rgba(var(--sol-warn-rgb), 0.55); }
  &.is-nofeed { color: var(--sol-text-quiet); }
}

.sol-area-stage {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
}

.swl-buttons {
  position: absolute;
  top: calc(env(safe-area-inset-top) + 0.5rem);
  right: 0.5rem;
  z-index: 7;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.swl-icon-btn {
  width: 44px;
  height: 44px;
  border: 1px solid var(--sol-hairline);
  border-radius: 11px;
  background: rgba(9, 2, 24, 0.85);
  color: var(--sol-text);
  font-size: 1rem;
  cursor: pointer;
  &.is-active { border-color: rgba(var(--sol-select-rgb), 0.75); }
  &:focus-visible { outline: 2px solid var(--sol-select); outline-offset: -2px; }
}

.swl-popover {
  position: absolute;
  top: calc(env(safe-area-inset-top) + 6rem);
  left: 0.5rem;
  right: 0.5rem;
  z-index: 8;
  max-width: 34rem;
  margin: 0 auto;
  max-height: calc(100% - 17rem);
  overflow-y: auto;
  overscroll-behavior: contain;
  border-radius: var(--sol-panel-radius);
}

.swl-caveat {
  position: absolute;
  top: calc(env(safe-area-inset-top) + 5.4rem);
  left: 0.75rem;
  right: 3.9rem;
  z-index: 5;
  margin: 0;
  padding: 0.35rem 0.6rem;
  border: 1px solid var(--sol-hairline);
  border-radius: 10px;
  background: rgba(9, 2, 24, 0.9);
  color: var(--sol-text-dim);
  font-size: 0.7rem;
  line-height: 1.3;
  pointer-events: none;
  &.is-wide { top: calc(env(safe-area-inset-top) + 5.4rem); right: 0.75rem; max-width: 30rem; }
}

.swl-bottom {
  position: absolute;
  left: 0.4rem;
  right: 0.4rem;
  bottom: calc(env(safe-area-inset-bottom) + 0.4rem);
  z-index: 5;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  &.is-wide { left: var(--sol-rail-gutter); right: 0; bottom: var(--sol-rail-gutter); }
}
.swl-story-pop, .swl-hud-pop { max-height: 40vh; overflow-y: auto; }
.swl-hud-pop {
  padding: 0.5rem;
  border: var(--sol-panel-border);
  border-radius: var(--sol-panel-radius);
  background: var(--sol-surface);
}
.swl-hud-toggle {
  align-self: center;
  min-height: 32px;
  padding: 0 0.8rem;
  margin-top: -0.2rem;
  border: 1px solid var(--sol-hairline);
  border-radius: 999px;
  background: rgba(9, 2, 24, 0.85);
  color: var(--sol-text-dim);
  font-size: 0.68rem;
  cursor: pointer;
}

// --- kiosk --------------------------------------------------------------
.sol-root.is-kiosk { padding-bottom: calc(env(safe-area-inset-bottom) + 3.2rem); }
.sol-take-home {
  position: absolute;
  right: 0.75rem;
  bottom: calc(env(safe-area-inset-bottom) + 0.45rem);
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 0.55rem;
  min-height: 48px;
  padding: 0.5rem 1.15rem;
  border: 1px solid rgba(243, 73, 130, 0.55);
  border-radius: 999px;
  background: var(--sol-surface);
  color: var(--sol-ember);
  font-size: 1rem;
  font-weight: 700;
  cursor: pointer;
}
.sol-attract-hint { display: none; }
@media (min-width: 600px) {
  .sol-attract-hint {
    display: block;
    position: absolute;
    left: 0.75rem;
    bottom: calc(env(safe-area-inset-bottom) + 0.45rem);
    z-index: 30;
    margin: 0;
    padding: 0.7rem 1.2rem;
    border: 1px solid var(--sol-hairline);
    border-radius: 999px;
    background: var(--sol-surface);
    color: var(--sol-text);
    font-size: 1rem;
    font-weight: 600;
    pointer-events: none;
  }
}

// --- desktop rail -------------------------------------------------------
.sol-root.is-wide {
  display: grid;
  grid-template-columns: 65fr 35fr;
  grid-template-rows: auto 1fr auto;
  grid-template-areas:
    "stage source"
    "stage panel"
    "stage stats";
  column-gap: 0;
  row-gap: var(--sol-rail-gutter);

  .sol-area-stage { grid-area: stage; min-width: 0; }
  .sol-area-source, .sol-area-panel, .sol-area-stats { margin-left: var(--sol-rail-gutter); margin-right: var(--sol-rail-gutter); }
  .sol-area-source { grid-area: source; margin-top: var(--sol-rail-gutter); }
  .sol-area-panel {
    grid-area: panel;
    min-height: 0;
    display: flex;
    flex-direction: column;
    border: var(--sol-panel-border);
    border-radius: var(--sol-panel-radius);
    background: var(--sol-surface);
    box-shadow: var(--sol-panel-shadow);
    overflow: hidden;
  }
  .sol-area-stats {
    grid-area: stats;
    margin-bottom: var(--sol-rail-gutter);
    padding: var(--sol-panel-pad);
    border: var(--sol-panel-border);
    border-radius: var(--sol-panel-radius);
    background: var(--sol-surface);
    box-shadow: var(--sol-panel-shadow);
  }
}

.swl-tabs {
  display: flex;
  gap: 0.3rem;
  padding: 0.5rem 0.6rem 0.4rem;
  border-bottom: 1px solid var(--sol-hairline);
}
.swl-tab {
  flex: 1 1 0;
  min-height: 40px;
  border: 1px solid var(--sol-hairline);
  border-radius: var(--sol-control-radius);
  background: transparent;
  color: var(--sol-text-dim);
  font-size: 0.76rem;
  font-weight: 600;
  cursor: pointer;
  &.is-on { border-color: rgba(var(--sol-select-rgb), 0.75); background: rgba(var(--sol-select-rgb), 0.14); color: var(--sol-text); }
  &:focus-visible { outline: 2px solid var(--sol-select); outline-offset: -2px; }
}
.swl-tabpanel { flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain; }
// Panels inside the tabbed rail card lose their own frame.
.swl-flat, .swl-flat :deep(.im-panel) {
  border: none !important;
  box-shadow: none !important;
  background: transparent !important;
  min-width: 0 !important;
}
</style>
