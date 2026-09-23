<template>
  <div class="layer-panel" data-camera-passthrough="false">
    <div v-for="group in groups" :key="group.title" class="lp-group">
      <p class="sol-section-head lp-head">{{ group.title }}</p>
      <button
        v-for="row in group.rows"
        :key="row.key"
        type="button"
        class="lp-row"
        :aria-pressed="layers[row.key] ? 'true' : 'false'"
        :disabled="row.absent"
        @click="layers[row.key] = !layers[row.key]"
      >
        <span class="lp-switch" :class="{ 'is-on': layers[row.key] }"><span class="lp-knob"></span></span>
        <span class="lp-swatch" :style="{ background: row.swatch }" aria-hidden="true"></span>
        <span class="lp-text">
          <span class="lp-label">{{ row.label }}</span>
          <span class="lp-hint">{{ row.absent ? "Not in this data set" : hintFor(row) }}</span>
        </span>
      </button>
    </div>

    <p class="sol-section-head lp-head">Style</p>
    <label v-for="s in sliders" :key="s.key" class="lp-slider">
      <span class="lp-slider-label">{{ s.label }}</span>
      <input
        v-model.number="style[s.key]"
        type="range"
        :min="s.min"
        :max="s.max"
        :step="s.step"
        :aria-label="s.label"
      />
      <span class="lp-value sol-num">{{ s.fmt(style[s.key]) }}</span>
    </label>
    <button type="button" class="lp-segment lp-reset" :disabled="isDefaultStyle" @click="resetStyle">
      <font-awesome-icon icon="rotate-left" /> Reset styles
    </button>

    <p class="sol-section-head lp-head">Detail</p>
    <div class="lp-segments" role="radiogroup" aria-label="Detail level">
      <button
        v-for="q in qualities"
        :key="q.id"
        type="button"
        role="radio"
        class="lp-segment"
        :class="{ 'is-on': quality === q.id }"
        :aria-checked="quality === q.id ? 'true' : 'false'"
        @click="quality = q.id"
      >{{ q.label }}</button>
    </div>
    <p class="lp-note">Auto picks by screen size. Lighter loads about 2.4 MB, Full about 9.5 MB.</p>
  </div>
</template>

<script lang="ts">
import { PropType, defineComponent } from "vue";

import { DEFAULT_STYLE, LayerFlags, StyleState, Quality, layers, quality, style } from "../state/useAppState";

interface Row { key: keyof LayerFlags; label: string; hint: string; liveHint?: string; swatch: string; bundle?: string; absent?: boolean }

const GROUPS: { title: string; rows: Row[] }[] = [
  { title: "Field", rows: [
    { key: "fieldLines", label: "Field lines", swatch: "#ffc850", bundle: "FieldLines",
      hint: "Traced through Tsyganenko's model for each moment. Gold lines close on Earth; blue and orange ones open to space" },
    { key: "fieldPulses", label: "Field pulses", swatch: "#ffe38a", bundle: "FieldLines",
      hint: "Beads riding the lines to show which way the field points" },
    { key: "imfLines", label: "Solar wind field", swatch: "#7b7ee0", bundle: "IMFLines",
      hint: "The Sun's magnetic field draped over Earth's. It turns blue where the two connect" },
    { key: "textbookField", label: "Textbook dipole", swatch: "#b8bfd6",
      hint: "A plain bar-magnet field for comparison. It is not what the wind makes" },
  ] },
  { title: "Boundaries", rows: [
    { key: "magnetopause", label: "Magnetopause", swatch: "#3e8f9c", bundle: "MagnetopauseShell",
      hint: "Where Earth's field stops the wind. Its shape is a model blend of Lin 2010 and Shue 1998" },
    { key: "bowShock", label: "Bow shock", swatch: "#b8823a", bundle: "BowShockShell",
      hint: "Where the wind first slows down. Slavin & Holzer shape, Farris & Russell distance" },
    { key: "magnetosheath", label: "Magnetosheath", swatch: "#8a6a3a", bundle: "BowShockShell",
      hint: "The churned-up wind between the two. A fill drawn between the shells, not measured" },
  ] },
  { title: "Plasma", rows: [
    { key: "solarWind", label: "Solar wind", swatch: "#b3d4ff", bundle: "WindParticles",
      hint: "Parcels at the measured speed and density. A thinned-out sample" },
    { key: "ringCurrent", label: "Ring current", swatch: "#b4553a", bundle: "RingCurrent",
      hint: "A ring of trapped particles. Its radius here is a typical value, not a measurement" },
    { key: "plasmasphere", label: "Plasmasphere", swatch: "#6b74a0", bundle: "Plasmasphere",
      hint: "Cold plasma near Earth. How far out it reaches is set by measured Kp" },
  ] },
  { title: "Aurora", rows: [
    { key: "auroraOval", label: "Aurora oval", swatch: "#7de08a", bundle: "AuroraOval",
      hint: "Where aurora is likely. A statistical model driven by Kp, not a photograph",
      liveHint: "NOAA's OVATION Prime forecast for the next half hour. A model, not a photograph" },
  ] },
  { title: "Readout", rows: [
    { key: "hud", label: "Readout", swatch: "#eaebef",
      hint: "Wind, Bz, density and Kp at the moment on the timeline" },
  ] },
];

const QUALITIES: { id: Quality; label: string }[] = [
  { id: "auto", label: "Auto" }, { id: "mobile", label: "Lighter" }, { id: "desktop", label: "Full" },
];

export default defineComponent({
  name: "LayerPanel",
  props: {
    /** Layer names present in the loaded bundle (empty = unknown yet). */
    present: { type: Array as PropType<string[]>, default: () => [] },
    liveMode: { type: Boolean, default: false },
  },
  setup() {
    const sliders: { key: keyof StyleState; label: string; min: number; max: number; step: number; fmt: (v: number) => string }[] = [
      { key: "windScale", label: "Wind dot size", min: 0.4, max: 2.5, step: 0.05, fmt: (v) => `×${v.toFixed(1)}` },
      { key: "pulseScale", label: "Pulse dot size", min: 0.4, max: 2.5, step: 0.05, fmt: (v) => `×${v.toFixed(1)}` },
      { key: "lineWidth", label: "Field line width", min: 0.6, max: 4, step: 0.1, fmt: (v) => `${v.toFixed(1)} px` },
      { key: "imfWidth", label: "Solar wind field width", min: 0.6, max: 4, step: 0.1, fmt: (v) => `${v.toFixed(1)} px` },
    ];
    return { layers, style, quality, sliders, qualities: QUALITIES };
  },
  computed: {
    groups(): { title: string; rows: Row[] }[] {
      const have = new Set(this.present);
      return GROUPS.map((g) => ({
        title: g.title,
        rows: g.rows.map((r) => ({ ...r, absent: !!(r.bundle && have.size && !have.has(r.bundle)) })),
      }));
    },
    isDefaultStyle(): boolean {
      return (Object.keys(DEFAULT_STYLE) as (keyof StyleState)[]).every((k) => Math.abs(style[k] - DEFAULT_STYLE[k]) < 1e-6);
    },
  },
  methods: {
    hintFor(r: Row): string { return this.liveMode && r.liveHint ? r.liveHint : r.hint; },
    resetStyle(): void { Object.assign(style, DEFAULT_STYLE); },
  },
});
</script>

<style lang="less" scoped>
.layer-panel {
  display: flex;
  flex-direction: column;
  min-width: 15rem;
  min-height: 0;
  padding: var(--sol-panel-pad);
  border: var(--sol-panel-border);
  border-radius: var(--sol-panel-radius);
  background: var(--sol-surface);
  box-shadow: var(--sol-panel-shadow);
}

.lp-group { display: contents; }

.lp-head {
  margin: 0.7rem 0.5rem 0.2rem;
  &:first-child { margin-top: 0.1rem; }
}

.lp-row {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  min-height: 44px;
  padding: 0.3rem 0.5rem;
  border: none;
  border-radius: var(--sol-control-radius);
  background: transparent;
  color: var(--sol-text);
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  &:active { background: var(--sol-hover); }
  &:focus-visible { outline: 2px solid var(--sol-select); outline-offset: -2px; }
  &:disabled { opacity: 0.45; cursor: default; }
}

.lp-switch {
  flex: 0 0 auto;
  position: relative;
  width: 34px;
  height: 20px;
  border-radius: 999px;
  background: rgba(148, 155, 175, 0.28);
  transition: background 160ms ease;
  &.is-on { background: rgba(var(--sol-select-rgb), 0.55); }
}

.lp-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--sol-text);
  box-shadow: 0 0 0 1px var(--sol-casing), 0 1px 2px rgba(0, 0, 0, 0.5);
  transition: transform 160ms ease;
  .is-on & { transform: translateX(14px); }
}

// The panel doubles as the legend: an in-scene colour swatch per layer. Data
// colours, so gold is allowed here (sol.less: gold means data).
.lp-swatch {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.6);
}

.lp-text { display: flex; flex-direction: column; min-width: 0; }
.lp-label { font-size: 0.85rem; font-weight: 600; }
.lp-hint { color: var(--sol-text-dim); font-size: 0.68rem; line-height: 1.25; }

.lp-slider {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-areas: "label value" "range range";
  align-items: center;
  column-gap: 0.5rem;
  min-height: 44px;
  padding: 0.15rem 0.5rem;
  input { grid-area: range; width: 100%; accent-color: var(--sol-select); touch-action: pan-y; }
}
.lp-slider-label { grid-area: label; font-size: 0.8rem; }
.lp-value { grid-area: value; font-size: 0.75rem; color: var(--sol-text-dim); }

.lp-segments { display: flex; gap: 0.3rem; flex-wrap: wrap; padding: 0 0.5rem; }

.lp-segment {
  flex: 1 1 0;
  min-height: 44px;
  padding: 0 0.4rem;
  border: 1px solid var(--sol-hairline);
  border-radius: var(--sol-control-radius);
  background: transparent;
  color: var(--sol-text-dim);
  font-size: 0.72rem;
  font-weight: 600;
  cursor: pointer;
  &:focus-visible { outline: 2px solid var(--sol-select); outline-offset: -2px; }
  &.is-on { border-color: rgba(var(--sol-select-rgb), 0.75); background: rgba(var(--sol-select-rgb), 0.14); color: var(--sol-text); }
  &:disabled { opacity: 0.4; cursor: default; }
}

.lp-reset { margin: 0.3rem 0.5rem 0; flex: 0 0 auto; }
.lp-note { margin: 0.4rem 0.5rem 0; font-size: 0.66rem; color: var(--sol-text-quiet); }
</style>
