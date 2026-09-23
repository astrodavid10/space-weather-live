<template>
  <button
    type="button"
    class="stat-chip no-select"
    :class="{ 'is-active': active, 'is-stale': stale }"
    :aria-pressed="active"
    @click="$emit('select')"
  >
    <!-- TWO lines, not three. The label used to own a line of its own above the
         value, which cost every chip ~15px of a phone screen to say a word the
         guest reads once and then ignores — four chips deep, that is a visible
         bite out of the Sun. Label and value share a BASELINE here, so the pair
         reads as one "name: number" statement and the row is only as tall as
         the value it contains. -->
    <span class="sc-head">
      <span class="sc-label">{{ label }}</span>
      <span class="sc-value">{{ value }}</span>
      <!-- Freshness is never hidden: green under 15 min, amber under an hour,
           gray beyond. The title carries the actual time for anyone who cares. -->
      <span class="sc-dot" :class="'is-' + tier" :title="freshnessTitle"></span>
    </span>
    <span class="sc-detail">{{ detailText }}</span>
  </button>
</template>

<script lang="ts">
import { defineComponent, PropType } from "vue";

import { agoLabel, clockLabel, freshnessTier } from "../data/swpc";

export default defineComponent({
  name: "StatChip",

  props: {
    label: { type: String, required: true },
    /** Big, plain-language headline: "Quiet", "427 km/s", "Storm — aurora possible!". */
    value: { type: String, required: true },
    /** Small print under it: the actual number or class. */
    detail: { type: String, default: "" },
    /** Observation time, epoch ms. null when we have nothing. */
    observedMs: { type: Number as PropType<number | null>, default: null },
    /** Older than the staleness threshold: dim it and say when. */
    stale: { type: Boolean, default: false },
    /** This chip's explainer is showing. */
    active: { type: Boolean, default: false },
  },

  emits: ["select"],

  computed: {
    tier(): string {
      return freshnessTier(this.observedMs);
    },

    detailText(): string {
      if (this.stale && this.observedMs !== null) {
        return `as of ${clockLabel(this.observedMs)}`;
      }
      return this.detail;
    },

    freshnessTitle(): string {
      if (this.observedMs === null) { return "No measurement yet"; }
      return `Measured ${agoLabel(this.observedMs)} (${clockLabel(this.observedMs)})`;
    },
  },
});
</script>

<style lang="less" scoped>
.stat-chip {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.05rem;
  // 44px, not the 56px this was: the floor is now the touch-target minimum
  // rather than a number sized for three stacked lines. Two lines of real
  // content measure ~41px inside this padding, so the min-height is doing what
  // a min-height should — guaranteeing the tap target, not setting the height.
  min-height: 44px;
  padding: 0.34rem 0.5rem;
  // Deliberately identical to `.lp-segment` in LayerPanel.vue: hairline border,
  // the shared control radius, transparent ground. These are the same KIND of
  // thing -- a tappable tile inside a panel -- and they sit one above the other
  // in the desktop rail, so any difference reads as an accident.
  border: 1px solid var(--sol-hairline);
  border-radius: var(--sol-control-radius);
  background: transparent;
  color: var(--sol-text);
  text-align: left;
  cursor: pointer;
  -webkit-tap-highlight-color: transparent;
  transition: border-color 150ms ease, background 160ms ease, opacity 200ms ease;

  &:hover {
    background: var(--sol-hover);
  }

  &:focus-visible {
    outline: 2px solid var(--sol-select);
    outline-offset: -2px;
  }

  // The layer panel's "on" treatment, to the letter: brighter border, faint
  // neutral fill, full-strength text. Three signals, not one hue.
  &.is-active {
    border-color: rgba(var(--sol-select-rgb), 0.75);
    background: rgba(var(--sol-select-rgb), 0.14);
  }

  // Stale values stay visible but stop shouting — they are last-known, not now.
  &.is-stale {
    opacity: 0.6;
  }
}

.sc-head {
  display: flex;
  // The label sits on the value's baseline, which is what keeps a 0.58rem word
  // and a 0.95rem number reading as one line rather than two things that happen
  // to overlap vertically.
  align-items: baseline;
  gap: 0.45rem;
  width: 100%;
}

// `flex: 0 0 auto` — the label never shrinks, the VALUE does (below). Backwards
// from the old three-line layout, where the label owned the row and pushed the
// freshness dot to the far end. Here the label is a fixed short word (the
// longest is "SUNSPOTS", ~50px) and the value is the variable-length part, so
// the value is the only sane place to spend or reclaim space. Ellipsising the
// label instead would truncate the one word that says WHICH number this is.
.sc-label {
  flex: 0 0 auto;
  font-size: 0.58rem;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--sol-text-dim);
  white-space: nowrap;
}

.sc-dot {
  flex: 0 0 auto;
  // Pushed to the far end of the row, past the value. `align-self` because a
  // 7px empty span has no text baseline worth aligning to — in a
  // baseline-aligned row its bottom margin edge becomes the baseline, which
  // would hang it below the label.
  margin-left: auto;
  align-self: center;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #6b6b6b;

  &.is-fresh {
    background: #58d68d;
  }

  &.is-recent {
    background: #f0b429;
  }

  &.is-old {
    background: #6b6b6b;
  }
}

// The one part of the row that gives up space when there isn't enough, hence
// `1 1 auto` + `min-width: 0` (a flex item's default `min-width: auto` refuses
// to shrink below its content and would push the freshness dot off the edge).
.sc-value {
  flex: 1 1 auto;
  min-width: 0;
  font-size: 0.95rem;
  font-weight: 700;
  line-height: 1.1;
  color: var(--sol-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

// The narrowest screens still in circulation -- a Galaxy Fold's ~280px cover
// screen -- and the only place this layout runs out of room. Measured at 280px:
// two columns give each chip 107px of content, and "425 km/s" at 0.95rem needs
// 66px of the 58px it gets, i.e. it ellipsises to "425 km/…" and loses the
// unit, which is the half that carries the meaning. Tightening the two head
// gaps recovers 4.8px and the smaller value another 6.3px, which clears it with
// room to spare -- and both are recovered from SPACING and from a size that is
// still the largest thing in the chip, so nothing has to be dropped and the
// grid stays 2x2 as it is at every other width. 300px, not 320: an iPhone SE
// (320px) has the room and should keep the full-size number.
@media (max-width: 300px) {
  .sc-head {
    gap: 0.3rem;
  }

  .sc-value {
    font-size: 0.86rem;
  }
}

.sc-detail {
  font-size: 0.62rem;
  line-height: 1.3;
  color: var(--sol-text-dim);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
</style>
