<template>
  <div class="time-bar" data-camera-passthrough="false">
    <div class="tb-controls">
      <button
        type="button"
        class="tb-btn"
        :class="{ 'is-idle-pulse': idlePulse }"
        :aria-label="playing ? 'Pause' : 'Play'"
        :disabled="count < 2"
        @click="toggle"
      >
        <font-awesome-icon :icon="playing ? 'pause' : 'play'" />
      </button>
      <button type="button" class="tb-btn tb-speed sol-num" :aria-label="`Playback speed ${speed}x, press to change`" @click="nextSpeed">
        {{ speed }}×
      </button>

      <div class="tb-track">
        <div v-if="loading" class="tb-loaded" :style="{ width: loadPct + '%' }"></div>
        <span
          v-for="(t, i) in ticks"
          :key="'t' + i"
          class="tb-tick"
          :class="{ 'is-major': t.major }"
          :style="{ left: t.pct + '%' }"
        ></span>
        <button
          v-for="(m, i) in marks"
          :key="'m' + i"
          type="button"
          class="tb-mark"
          :class="'is-' + m.kind"
          :style="{ left: m.pct + '%' }"
          :aria-label="`Jump to ${m.title}, ${m.when}`"
          :title="`${m.title} · ${m.when}`"
          @click="$emit('moment', i)"
        ></button>
        <input
          class="tb-range"
          type="range"
          min="0"
          :max="Math.max(0, count - 1)"
          step="0.01"
          :value="frameT"
          :disabled="count < 2"
          aria-label="Time"
          @input="onInput"
          @pointerdown="grab"
          @pointerup="release"
          @change="release"
        />
      </div>
    </div>

    <p class="tb-label">
      <template v-if="loading">Loading {{ label }}… {{ mb(loadDone) }} of {{ mb(loadTotal) }} MB</template>
      <template v-else-if="error"><span class="tb-error">{{ error }}</span></template>
      <template v-else>
        <strong class="sol-num">{{ stamp }}</strong>
        <span v-if="caption"> · {{ caption }}</span>
      </template>
    </p>
    <p v-if="showHud && hudLine" class="tb-hud sol-num">{{ hudLine }}</p>
  </div>
</template>

<script lang="ts">
import { PropType, defineComponent } from "vue";

import type { KeyMoment } from "../data/readout";
import { momentTitle } from "../data/readout";
import { frameT, frameTimes, playing, sceneUnix, speed } from "../state/useAppState";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function utcStamp(unix: number, withYear = true): string {
  const d = new Date(unix * 1000);
  const hh = String(d.getUTCHours()).padStart(2, "0"), mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}${withYear ? ", " + d.getUTCFullYear() : ""} · ${hh}:${mm} UT`;
}

export default defineComponent({
  name: "TimeBar",
  props: {
    moments: { type: Array as PropType<KeyMoment[]>, default: () => [] },
    hudLine: { type: String, default: "" },
    showHud: { type: Boolean, default: true },
    label: { type: String, default: "" },
    loading: { type: Boolean, default: false },
    loadDone: { type: Number, default: 0 },
    loadTotal: { type: Number, default: 0 },
    error: { type: String, default: "" },
    /** Suffix for the stamp, e.g. "newest frame, 3 h old". */
    note: { type: String, default: "" },
  },
  emits: ["moment", "scrub"],
  data() { return { wasPlaying: false, touched: false }; },
  setup() { return { frameT, frameTimes, playing, speed, sceneUnix }; },
  computed: {
    count(): number { return frameTimes.value.length; },
    span(): [number, number] {
      const t = frameTimes.value;
      return t.length ? [t[0], t[t.length - 1]] : [0, 1];
    },
    loadPct(): number { return this.loadTotal ? Math.min(100, (100 * this.loadDone) / this.loadTotal) : 0; },
    idlePulse(): boolean { return !this.playing && !this.touched && this.count > 1; },
    stamp(): string { return this.count ? utcStamp(sceneUnix.value) : "—"; },
    /** Frame fraction (0..100 %) for a unix time -- the axis is keyframe index, as the range is. */
    ticks(): { pct: number; major: boolean }[] {
      const t = frameTimes.value;
      if (t.length < 2) { return []; }
      const out: { pct: number; major: boolean }[] = [];
      const first = Math.ceil(t[0] / 3600) * 3600;
      for (let u = first; u <= t[t.length - 1]; u += 3600) {
        const h = new Date(u * 1000).getUTCHours();
        if (h % 6 !== 0) { continue; }
        out.push({ pct: this.pctFor(u), major: h === 0 });
      }
      return out;
    },
    marks(): { pct: number; kind: string; title: string; when: string }[] {
      const [a, b] = this.span;
      return this.moments.filter((m) => m.unix >= a && m.unix <= b).map((m) => ({
        pct: this.pctFor(m.unix), kind: m.kind, title: momentTitle(m), when: utcStamp(m.unix, false),
      }));
    },
    caption(): string {
      const u = sceneUnix.value;
      const near = this.moments.find((m) => Math.abs(m.unix - u) <= 20 * 60);
      if (near) { return momentTitle(near); }
      return this.note;
    },
  },
  methods: {
    pctFor(unix: number): number {
      const t = frameTimes.value;
      const n = t.length;
      if (n < 2) { return 0; }
      let lo = 0, hi = n - 1;
      if (unix <= t[0]) { return 0; }
      if (unix >= t[n - 1]) { return 100; }
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (t[mid] <= unix) { lo = mid; } else { hi = mid; } }
      return (100 * (lo + (unix - t[lo]) / (t[hi] - t[lo]))) / (n - 1);
    },
    toggle(): void { this.touched = true; playing.value = !playing.value; },
    nextSpeed(): void { speed.value = speed.value === 1 ? 2 : speed.value === 2 ? 4 : 1; },
    onInput(e: Event): void {
      this.touched = true;
      frameT.value = Number((e.target as HTMLInputElement).value);
      this.$emit("scrub");
    },
    grab(): void { this.wasPlaying = playing.value; playing.value = false; },
    release(): void { if (this.wasPlaying) { playing.value = true; this.wasPlaying = false; } },
    mb(b: number): string { return (b / 1048576).toFixed(1); },
  },
});
</script>

<style lang="less" scoped>
.time-bar {
  padding: 0.55rem 0.7rem 0.5rem;
  border: var(--sol-panel-border);
  border-radius: var(--sol-panel-radius);
  background: var(--sol-surface);
  box-shadow: var(--sol-panel-shadow);
}

.tb-controls { display: flex; align-items: center; gap: 0.45rem; }

.tb-btn {
  flex: 0 0 auto;
  width: 44px;
  height: 44px;
  border: 1px solid var(--sol-hairline);
  border-radius: 11px;
  background: rgba(9, 2, 24, 0.6);
  color: var(--sol-text);
  font-size: 0.95rem;
  cursor: pointer;
  &:focus-visible { outline: 2px solid var(--sol-select); outline-offset: -2px; }
  &:disabled { opacity: 0.4; }
}
.tb-speed { font-size: 0.8rem; font-weight: 700; }

@media (prefers-reduced-motion: no-preference) {
  .is-idle-pulse { animation: tb-pulse 2.2s ease-in-out infinite; }
}
@keyframes tb-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(var(--sol-select-rgb), 0); }
  50% { box-shadow: 0 0 0 4px rgba(var(--sol-select-rgb), 0.25); }
}

.tb-track {
  position: relative;
  flex: 1 1 auto;
  height: 44px;
  min-width: 0;
}

.tb-loaded {
  position: absolute;
  left: 0;
  top: 20px;
  height: 4px;
  border-radius: 2px;
  background: rgba(var(--sol-select-rgb), 0.35);
}

.tb-tick {
  position: absolute;
  top: 27px;
  width: 1px;
  height: 5px;
  background: rgba(148, 155, 175, 0.45);
  &.is-major { height: 9px; background: rgba(148, 155, 175, 0.8); }
}

// Moment marks: SHAPE carries the kind, so colour is never the only signal.
.tb-mark {
  position: absolute;
  top: 3px;
  width: 22px;
  height: 22px;
  margin-left: -11px;
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
  z-index: 2;
  &::before {
    content: "";
    position: absolute;
    left: 6px;
    top: 6px;
    width: 10px;
    height: 10px;
    background: var(--sol-text);
  }
  &:focus-visible { outline: 2px solid var(--sol-select); border-radius: 4px; }
  &.is-shock::before { transform: rotate(45deg) scale(0.85); background: #d49a4a; }
  &.is-bzmin::before { clip-path: polygon(0 0, 100% 0, 50% 100%); background: var(--sol-accent2); }
  &.is-symh::before { border-radius: 50%; background: #c8694a; }
  &.is-wind::before { transform: rotate(45deg) scale(0.8); background: transparent; box-shadow: inset 0 0 0 2px #b3d4ff; }
  &.is-kp::before { transform: scale(0.75); background: #7de08a; }
  &.is-other::before { border-radius: 50%; transform: scale(0.6); }
}

.tb-range {
  position: absolute;
  left: 0;
  right: 0;
  top: 12px;
  width: 100%;
  margin: 0;
  height: 20px;
  background: transparent;
  touch-action: pan-y;
  accent-color: var(--sol-select);
  cursor: pointer;
}

.tb-label {
  margin: 0.15rem 0 0;
  font-size: 0.78rem;
  color: var(--sol-text-dim);
  strong { color: var(--sol-text); font-weight: 600; }
}
.tb-error { color: var(--sol-warn); }

.tb-hud {
  margin: 0.1rem 0 0;
  font-size: 0.72rem;
  color: var(--sol-text-dim);
}
</style>
