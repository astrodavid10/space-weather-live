<template>
  <div class="source-panel" data-camera-passthrough="false">
    <p class="sol-section-head sp-head">Data source</p>
    <div role="radiogroup" aria-label="Data source">
      <button
        v-for="row in rows"
        :key="row.key"
        type="button"
        role="radio"
        class="sp-row"
        :class="{ 'is-on': row.key === current }"
        :aria-checked="row.key === current ? 'true' : 'false'"
        :disabled="row.disabled"
        @click="$emit('pick', row.key)"
      >
        <span class="sp-radio" aria-hidden="true"></span>
        <span class="sp-text">
          <span class="sp-label">{{ row.label }}</span>
          <span v-if="!compact || row.key === current" class="sp-why">{{ row.why }}</span>
        </span>
        <span class="sp-tier" :class="'is-' + row.tier.toLowerCase()">{{ row.tierText }}</span>
        <span v-if="row.dot" class="sp-dot" :class="'is-' + row.dot" :title="row.dotTitle"></span>
      </button>
    </div>
  </div>
</template>

<script lang="ts">
import { PropType, defineComponent } from "vue";

import type { SourceRow } from "../data/index";
import { freshnessTier } from "../data/swpc";
import type { Tier } from "../data/live";

interface Row { key: string; label: string; why: string; tier: string; tierText: string; dot?: string; dotTitle?: string; disabled?: boolean }

const STORM_LABELS: Record<string, string> = {
  bastille: "Bastille Day · July 2000",
  nov2003: "November 2003 superstorm",
  gannon: "Gannon storm · May 2024",
  octg4: "October 2024 G4",
  jan2026: "January 2026 superstorm",
};

export default defineComponent({
  name: "SourcePanel",
  props: {
    sources: { type: Array as PropType<SourceRow[]>, required: true },
    current: { type: String, default: "" },
    liveTier: { type: String as PropType<Tier>, default: "NOFEED" },
    liveObservedMs: { type: Number as PropType<number | null>, default: null },
    compact: { type: Boolean, default: false },
  },
  emits: ["pick"],
  computed: {
    rows(): Row[] {
      const out: Row[] = [];
      const lb = this.sources.find((s) => s.kind === "lookback");
      if (lb) {
        out.push({
          key: "live", label: "Live now",
          why: "NOAA's numbers and aurora forecast right now; shapes from the last look-back",
          tier: this.liveTier === "LIVE" ? "LIVE" : this.liveTier === "CACHED" ? "CACHED" : "NOFEED",
          tierText: this.liveTier === "NOFEED" ? "NO FEED" : this.liveTier,
          dot: freshnessTier(this.liveObservedMs), dotTitle: "Age of the live solar wind reading",
        });
        const endMs = lb.dataEndIso ? Date.parse(lb.dataEndIso) : null;
        const hours = endMs ? (Date.now() - endMs) / 3.6e6 : null;
        out.push({
          key: "lookback", label: "Last 48 hours", why: lb.why + (hours != null ? ` · data through ${this.ago(hours)}` : ""),
          tier: "REPLAY", tierText: "REPLAY",
          dot: hours == null ? "old" : hours < 12 ? "fresh" : hours < 36 ? "recent" : "old",
          dotTitle: "Age of the newest look-back measurement",
        });
      }
      for (const s of this.sources.filter((x) => x.kind === "storm")) {
        out.push({ key: s.key, label: STORM_LABELS[s.key] ?? s.label, why: s.why, tier: "REPLAY", tierText: "REPLAY" });
      }
      return out;
    },
  },
  methods: {
    ago(h: number): string {
      if (h < 1) { return "under an hour ago"; }
      if (h < 48) { return `${Math.round(h)} h ago`; }
      return `${Math.round(h / 24)} days ago`;
    },
  },
});
</script>

<style lang="less" scoped>
.source-panel {
  padding: var(--sol-panel-pad);
  border: var(--sol-panel-border);
  border-radius: var(--sol-panel-radius);
  background: var(--sol-surface);
  box-shadow: var(--sol-panel-shadow);
}
.sp-head { margin: 0.1rem 0.5rem 0.3rem; }

.sp-row {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  width: 100%;
  min-height: 44px;
  padding: 0.35rem 0.5rem;
  border: 1px solid transparent;
  border-radius: var(--sol-control-radius);
  background: transparent;
  color: var(--sol-text-dim);
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  &:active { background: var(--sol-hover); }
  &:focus-visible { outline: 2px solid var(--sol-select); outline-offset: -2px; }
  &.is-on {
    border-color: rgba(var(--sol-select-rgb), 0.75);
    background: rgba(var(--sol-select-rgb), 0.1);
    color: var(--sol-text);
  }
}

.sp-radio {
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid rgba(148, 155, 175, 0.55);
  .is-on & { border-color: var(--sol-select); background: radial-gradient(var(--sol-select) 0 3px, transparent 3.5px); }
}

.sp-text { display: flex; flex-direction: column; min-width: 0; flex: 1 1 auto; }
.sp-label { font-size: 0.85rem; font-weight: 600; }
.sp-why { font-size: 0.68rem; line-height: 1.25; color: var(--sol-text-dim); }

.sp-tier {
  flex: 0 0 auto;
  padding: 0.1rem 0.4rem;
  border-radius: 999px;
  border: 1px solid var(--sol-hairline);
  font-size: 0.56rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--sol-text-dim);
  &.is-live { color: var(--swl-live); border-color: rgba(var(--swl-live-rgb), 0.5); }
  &.is-cached { color: var(--sol-warn); border-color: rgba(var(--sol-warn-rgb), 0.5); }
  &.is-nofeed { color: var(--sol-text-quiet); }
}

.sp-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  &.is-fresh { background: #58d68d; }
  &.is-recent { background: #f0b429; }
  &.is-old { background: #6b6b6b; }
}
</style>
