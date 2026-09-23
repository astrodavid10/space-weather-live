<template>
  <div :class="inline ? 'im-inline' : 'modal im-backdrop'" @click.self="!inline && $emit('close')">
    <div class="im-panel" role="dialog" :aria-modal="inline ? undefined : 'true'" aria-labelledby="im-title" data-camera-passthrough="false">
      <button v-if="!inline" type="button" class="im-close" aria-label="Close" @click="$emit('close')">
        <font-awesome-icon icon="times" />
      </button>

      <h2 id="im-title" class="im-title">Earth's Dynamic Magnetosphere</h2>
      <p class="im-subtitle">Earth in the solar wind</p>

      <div class="im-body">
        <p>
          Earth sits inside a magnetic bubble that the solar wind squeezes, stretches and sometimes tears open.
          This is that bubble, drawn from real measurements of the wind: live from NOAA, from the last two days,
          and from five of the biggest storms on record.
        </p>
        <p>
          Every shape here is computed from measured solar wind. The numbers are observations; the field lines
          and boundaries are what published physics models make of them. We say which is which, everywhere.
        </p>

        <p class="sol-section-head im-head">How to use it</p>
        <ul class="im-list">
          <li>Drag to orbit Earth, pinch or scroll to zoom, twist with two fingers to roll.</li>
          <li>Pick a storm or the last 48 hours, then press play. Marks on the timeline jump to the key moments.</li>
          <li>The camera buttons fly to the classic views: head-on to the bow shock, down the tail, over a cusp.</li>
          <li>Layers turns each part of the picture on and off, and says what it is.</li>
        </ul>

        <p class="sol-section-head im-head">Measured or modelled?</p>
        <ul class="im-list">
          <li><strong>Solar wind, Kp, SYM-H, Dst.</strong> Measured: speed, density and field at the L1 point, a million and a half km upstream; Kp and the ring-current indices from ground magnetometers.</li>
          <li><strong>Field lines.</strong> Model: traced through Tsyganenko's T89 or TS05 field with the IGRF-14 internal field, driven by the measured wind.</li>
          <li><strong>Magnetopause.</strong> Model: the Lin et al. 2010 shape on a nose distance that blends to Shue et al. 1998 outside Lin's fitted range. Every storm's peak sits on Shue.</li>
          <li><strong>Bow shock.</strong> Model: Slavin &amp; Holzer shape, Farris &amp; Russell standoff, bounded on a few storm frames.</li>
          <li><strong>Magnetosheath.</strong> Drawn: a tint between the two boundaries to show where the shocked wind lives. Nothing inside it is measured.</li>
          <li><strong>Ring current.</strong> Half and half: its energy follows from the measured SYM-H, but where the ring sits is climatology.</li>
          <li><strong>Plasmasphere.</strong> Its edge follows Kp exactly as Carpenter &amp; Anderson 1992 published; the shape is a dipole shell and does not draw the duskside plume.</li>
          <li><strong>Aurora oval.</strong> Model: a statistical Kp oval in the replays, NOAA's OVATION Prime forecast in live mode. Never a photograph.</li>
          <li><strong>Solar wind field.</strong> Model: the interplanetary field carried through the wind's own flow, turning blue where Kan &amp; Lee's merging rate says it has connected.</li>
          <li><strong>Field pulses and the textbook dipole.</strong> Drawn in your browser to help you read the picture. The pulses show direction, not speed; the dipole is the field with no solar wind at all.</li>
          <li><strong>Everything is a subsample.</strong> Each layer is thinned for the web, so densities look lower than the model's.</li>
          <li><strong>Live mode.</strong> The numbers and the aurora oval are live. The shapes are the newest look-back frame, and the view says how old it is.</li>
        </ul>

        <p class="sol-section-head im-head">The storms</p>
        <ul class="im-list">
          <li v-for="s in storms" :key="s.key">
            <strong>{{ s.title }}.</strong> {{ s.why }}
            <span v-for="l in s.links" :key="l.href"> <a :href="l.href" target="_blank" rel="noopener">{{ l.text }}</a></span>
          </li>
        </ul>

        <p class="sol-section-head im-head">Where the data comes from</p>
        <p class="im-fine">{{ summary }}</p>
        <div v-for="g in credits" :key="g.title" class="im-credit-group">
          <p class="im-credit-title">{{ g.title }}</p>
          <ul class="im-list im-credits">
            <li v-for="c in g.items" :key="c.name">
              <strong>{{ c.name }}.</strong> {{ c.role }}
              <span v-for="l in c.links" :key="l.href"> <a :href="l.href" target="_blank" rel="noopener">{{ l.text }}</a></span>
            </li>
          </ul>
        </div>

        <p class="im-fine">
          Created by A. David Weigel, with Alex Shepard and Emily Watson. INTUITIVE Planetarium,
          U.S. Space &amp; Rocket Center, Huntsville, Alabama.
          <template v-if="updated"><br />Data last rebuilt {{ updated }}.</template>
        </p>

        <div class="im-logos">
          <a href="https://www.rocketcenter.com/INTUITIVEPlanetarium" target="_blank" rel="noopener"
             aria-label="INTUITIVE Planetarium at the U.S. Space &amp; Rocket Center">
            <img class="im-lockup" src="../assets/ip-ussrc.png" alt="INTUITIVE Planetarium · U.S. Space &amp; Rocket Center" />
          </a>
          <p class="im-attr">
            <img src="../assets/logo_cosmicds.png" alt="" /> Interactive developed using the CosmicDS toolkit
          </p>
          <p class="im-attr">
            <img src="../assets/logo_wwt.png" alt="" /> Powered by WorldWide Telescope
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts">
import { defineComponent } from "vue";

import { CREDITS, CREDITS_SUMMARY, STORM_REFERENCES } from "../data/credits";

const TITLES: Record<string, string> = {
  bastille: "Bastille Day · 14–16 July 2000",
  nov2003: "Superstorm · 20 November 2003",
  gannon: "Gannon storm · 10–11 May 2024",
  octg4: "G4 storm · 10–11 October 2024",
  jan2026: "Superstorm · 19 January 2026",
};

export default defineComponent({
  name: "InfoModal",
  props: {
    inline: { type: Boolean, default: false },
    updated: { type: String, default: "" },
  },
  emits: ["close"],
  setup() {
    const storms = Object.entries(STORM_REFERENCES).map(([key, v]) => ({ key, title: TITLES[key], ...v }));
    return { credits: CREDITS, summary: CREDITS_SUMMARY, storms };
  },
});
</script>

<style lang="less" scoped>
.im-backdrop {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0.75rem;
  background: rgba(0, 0, 10, 0.72);
}

.im-inline { height: 100%; min-height: 0; display: flex; }

.im-panel {
  position: relative;
  width: min(100%, 36rem);
  max-height: 100%;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 1.1rem 1.1rem 1rem;
  border: var(--sol-panel-border);
  border-radius: var(--sol-panel-radius);
  background: var(--sol-surface);
  box-shadow: var(--sol-panel-shadow);
  .im-inline & { width: 100%; }
  .im-backdrop & { max-height: calc(var(--app-content-height) - 1.5rem); }
}

.im-close {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  width: 44px;
  height: 44px;
  border: none;
  border-radius: 11px;
  background: transparent;
  color: var(--sol-text-dim);
  font-size: 1.1rem;
  cursor: pointer;
}

.im-title {
  margin: 0 2.5rem 0 0;
  font-family: "Overpass", system-ui, sans-serif;
  font-weight: 600;
  font-size: 1.3rem;
  color: var(--sol-text);
}
.im-subtitle { margin: 0.15rem 0 0.6rem; font-size: 0.8rem; color: var(--sol-text-dim); }

.im-body {
  font-size: 0.86rem;
  line-height: 1.45;
  color: var(--sol-text);
  p { margin: 0 0 0.6rem; }
  a { color: var(--sol-select); text-decoration: underline; text-underline-offset: 2px; }
}

.im-head { margin: 1rem 0 0.4rem !important; }

.im-list {
  margin: 0 0 0.6rem;
  padding-left: 1.1rem;
  li { margin-bottom: 0.35rem; }
}
.im-credits { font-size: 0.78rem; color: var(--sol-text-dim); strong { color: var(--sol-text); } }
.im-credit-title { margin: 0.5rem 0 0.2rem !important; font-size: 0.72rem; font-weight: 700; color: var(--sol-text-dim); }
.im-fine { font-size: 0.75rem; color: var(--sol-text-dim); }

.im-logos {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.4rem;
  margin-top: 0.8rem;
  padding-top: 0.8rem;
  border-top: 1px solid var(--sol-hairline);
}
.im-lockup { max-width: 10rem; opacity: 0.85; }
.im-attr {
  display: flex;
  align-items: center;
  gap: 0.45rem;
  margin: 0 !important;
  font-size: 0.72rem;
  color: var(--sol-text-dim);
  img { height: 22px; }
}
</style>
