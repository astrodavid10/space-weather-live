<template>
  <div class="story-card" data-camera-passthrough="false">
    <div class="st-head">
      <p class="st-title">{{ title }}</p>
      <button v-if="closable" type="button" class="st-close" aria-label="Close story" @click="$emit('close')">
        <font-awesome-icon icon="times" />
      </button>
    </div>
    <template v-if="points.length">
      <p v-for="p in points" :key="p.tag" class="st-point">
        <span class="st-tag">{{ p.tag }}</span>{{ p.text }}
      </p>
    </template>
    <p v-else class="st-point">{{ fallback }}</p>

    <template v-if="moments.length">
      <p class="sol-section-head st-sub">Key moments</p>
      <button
        v-for="(m, i) in moments"
        :key="i"
        type="button"
        class="st-moment"
        :class="{ 'is-on': i === activeMoment }"
        @click="$emit('moment', i)"
      >
        <span class="st-shape" :class="'is-' + m.kind" aria-hidden="true"></span>
        <span class="st-mtext">{{ momentTitle(m) }}<span v-if="m.detail" class="st-mdetail"> {{ m.detail }}</span></span>
        <span class="st-when sol-num">{{ stamp(m.unix) }}</span>
      </button>
    </template>
  </div>
</template>

<script lang="ts">
import { PropType, defineComponent } from "vue";

import { KeyMoment, momentTitle, parseTalkingPoints } from "../data/readout";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export default defineComponent({
  name: "StoryCard",
  props: {
    title: { type: String, default: "" },
    talkingPoints: { type: Array as PropType<string[]>, default: () => [] },
    moments: { type: Array as PropType<KeyMoment[]>, default: () => [] },
    activeMoment: { type: Number, default: -1 },
    kind: { type: String, default: "storm" },
    closable: { type: Boolean, default: false },
  },
  emits: ["moment", "close"],
  computed: {
    points(): { tag: string; text: string }[] { return parseTalkingPoints(this.talkingPoints); },
    fallback(): string {
      return this.kind === "lookback"
        ? "Two days of real solar wind, measured by ACE. Quiet days look like this; drag the timeline to watch the field breathe."
        : "Too recent for a published overview. Replayed straight from the OMNI record, with no interpretive claims attached.";
    },
  },
  methods: {
    momentTitle,
    stamp(u: number): string {
      const d = new Date(u * 1000);
      return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")} UT`;
    },
  },
});
</script>

<style lang="less" scoped>
.story-card {
  padding: 0.8rem 0.9rem;
  border: var(--sol-panel-border);
  border-radius: var(--sol-panel-radius);
  background: var(--sol-surface);
  box-shadow: var(--sol-panel-shadow);
  font-size: 0.82rem;
  line-height: 1.4;
}
.st-head { display: flex; align-items: start; gap: 0.5rem; }
.st-title { flex: 1 1 auto; margin: 0 0 0.4rem; font-family: "Overpass", system-ui, sans-serif; font-weight: 600; font-size: 0.98rem; }
.st-close { flex: 0 0 auto; width: 32px; height: 32px; margin: -0.3rem -0.3rem 0 0; border: none; background: transparent; color: var(--sol-text-dim); cursor: pointer; }
.st-point { margin: 0 0 0.45rem; color: var(--sol-text); }
.st-tag { margin-right: 0.4rem; font-size: 0.62rem; font-weight: 700; letter-spacing: 0.14em; color: var(--sol-text-dim); }
.st-sub { margin: 0.7rem 0 0.3rem; }

.st-moment {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  min-height: 40px;
  padding: 0.25rem 0.4rem;
  border: 1px solid transparent;
  border-radius: var(--sol-control-radius);
  background: transparent;
  color: var(--sol-text);
  text-align: left;
  font-size: 0.78rem;
  cursor: pointer;
  &:active { background: var(--sol-hover); }
  &:focus-visible { outline: 2px solid var(--sol-select); outline-offset: -2px; }
  &.is-on { border-color: rgba(var(--sol-select-rgb), 0.6); background: rgba(var(--sol-select-rgb), 0.1); }
}
.st-mtext { flex: 1 1 auto; min-width: 0; }
.st-mdetail { color: var(--sol-text-dim); }
.st-when { flex: 0 0 auto; font-size: 0.7rem; color: var(--sol-text-dim); }

.st-shape {
  flex: 0 0 auto;
  width: 10px;
  height: 10px;
  background: var(--sol-text);
  &.is-shock { transform: rotate(45deg) scale(0.85); background: #d49a4a; }
  &.is-bzmin { clip-path: polygon(0 0, 100% 0, 50% 100%); background: var(--sol-accent2); }
  &.is-symh { border-radius: 50%; background: #c8694a; }
  &.is-wind { transform: rotate(45deg) scale(0.8); background: transparent; box-shadow: inset 0 0 0 2px #b3d4ff; }
  &.is-kp { transform: scale(0.75); background: #7de08a; }
  &.is-other { border-radius: 50%; transform: scale(0.6); }
}
</style>
