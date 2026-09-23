<template>
  <div class="hud-readout" data-camera-passthrough="false">
    <div class="hr-grid">
      <stat-chip
        v-for="c in chips"
        :key="c.id"
        :label="c.label"
        :value="c.value"
        :detail="c.detail"
        :observed-ms="c.observedMs"
        :stale="c.stale"
        :active="selected === c.id"
        @select="selected = selected === c.id ? '' : c.id"
      />
    </div>
    <p v-if="selected" class="hr-explain">{{ explain[selected] }}</p>
  </div>
</template>

<script lang="ts">
import { PropType, defineComponent } from "vue";

import StatChip from "./StatChip.vue";
import type { LiveState } from "../data/live";
import { HudValues, shueR0 } from "../data/readout";
import { kpLabel } from "../data/swpc";

interface Chip { id: string; label: string; value: string; detail: string; observedMs: number | null; stale: boolean }

const EXPLAIN: Record<string, string> = {
  wind: "The solar wind is the Sun's atmosphere streaming past Earth. Faster wind pushes harder on the magnetic shield.",
  bz: "Bz is the north-south part of the wind's magnetic field. Southward (negative) lets the wind's field connect to Earth's and pour energy in. That is storm fuel.",
  density: "How many particles are packed into each cubic centimetre of wind. Density and speed together set the pressure on the shield.",
  kp: "Kp measures how hard the wind is shaking Earth's magnetic field, from 0 to 9. At 5 and above the aurora pushes toward the mid-latitudes.",
  pressure: "Dynamic pressure, from density and speed. It is what pushes the magnetopause toward Earth.",
  nose: "How far toward the Sun the magnetopause reaches, in Earth radii, from the Shue 1998 model. About 10 on a quiet day, below 6 in a great storm. A model, not a measurement.",
};

const fmt = (v: number | null, d = 0, unit = ""): string => (v == null ? "—" : `${v.toFixed(d)}${unit}`);
const sign = (v: number | null): string => (v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)} nT`);

export default defineComponent({
  name: "HudReadout",
  components: { "stat-chip": StatChip },
  props: {
    /** Replay values under the playhead (bundle HUD). */
    replay: { type: Object as PropType<HudValues | null>, default: null },
    /** Live values; when set, the chips are live. */
    live: { type: Object as PropType<LiveState | null>, default: null },
  },
  data() { return { selected: "", explain: EXPLAIN }; },
  computed: {
    chips(): Chip[] {
      const lv = this.live;
      if (lv) {
        const stale = lv.tier !== "LIVE";
        const ob = lv.observedMs;
        const nose = lv.bz != null && lv.pdyn != null ? shueR0(lv.bz, lv.pdyn) : null;
        return [
          { id: "wind", label: "Wind", value: fmt(lv.speed, 0, " km/s"), detail: lv.source ? `at L1 · ${lv.source}` : "at L1", observedMs: ob, stale },
          { id: "bz", label: "Bz", value: sign(lv.bz), detail: lv.bz == null ? "" : lv.bz < 0 ? "southward: storm fuel" : "northward: quiet", observedMs: ob, stale },
          { id: "density", label: "Density", value: fmt(lv.density, 1, " /cc"), detail: "particles per cm³", observedMs: ob, stale },
          { id: "kp", label: "Kp", value: fmt(lv.kp, 1), detail: lv.kp == null ? "" : kpLabel(lv.kp).headline, observedMs: lv.kpMs, stale: false },
          { id: "pressure", label: "Pressure", value: fmt(lv.pdyn, 1, " nPa"), detail: "squeezes the shield", observedMs: ob, stale },
          { id: "nose", label: "Nose", value: fmt(nose, 1, " R_E"), detail: "model, not measured", observedMs: null, stale: false },
        ];
      }
      const rv = this.replay;
      const nose = rv && rv.bz != null && rv.pdyn != null ? shueR0(rv.bz, rv.pdyn) : null;
      return [
        { id: "wind", label: "Wind", value: fmt(rv?.speed ?? null, 0, " km/s"), detail: rv?.source ?? "", observedMs: null, stale: false },
        { id: "bz", label: "Bz", value: sign(rv?.bz ?? null), detail: rv?.bz == null ? "" : rv.bz < 0 ? "southward: storm fuel" : "northward: quiet", observedMs: null, stale: false },
        { id: "density", label: "Density", value: fmt(rv?.density ?? null, 1, " /cc"), detail: "particles per cm³", observedMs: null, stale: false },
        { id: "kp", label: "Kp", value: fmt(rv?.kp ?? null, 1), detail: rv?.kp == null ? "" : kpLabel(rv.kp).headline, observedMs: null, stale: false },
        { id: "pressure", label: "Pressure", value: fmt(rv?.pdyn ?? null, 1, " nPa"), detail: "squeezes the shield", observedMs: null, stale: false },
        { id: "nose", label: "Nose", value: fmt(nose, 1, " R_E"), detail: "model, not measured", observedMs: null, stale: false },
      ];
    },
  },
});
</script>

<style lang="less" scoped>
.hr-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.35rem;
}
.hr-explain {
  margin: 0.45rem 0.2rem 0;
  font-size: 0.72rem;
  line-height: 1.35;
  color: var(--sol-text-dim);
}
</style>
