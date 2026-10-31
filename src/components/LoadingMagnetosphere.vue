<template>
  <svg class="loading-magneto" viewBox="-16 -21 64 42" aria-hidden="true">
    <defs>
      <radialGradient id="lm-sun" gradientUnits="userSpaceOnUse" cx="-22" cy="0" r="20">
        <stop offset="0" class="sun-a" />
        <stop offset="1" class="sun-b" />
      </radialGradient>
      <!-- The tail fades out down-stream instead of ending at a hard edge. -->
      <linearGradient id="lm-fade" gradientUnits="userSpaceOnUse" x1="32" x2="48" y1="0" y2="0">
        <stop offset="0" stop-color="#fff" />
        <stop offset="1" stop-color="#fff" stop-opacity="0" />
      </linearGradient>
      <mask id="lm-mask" maskUnits="userSpaceOnUse" x="-16" y="-21" width="64" height="42">
        <rect x="-16" y="-21" width="64" height="42" fill="url(#lm-fade)" />
      </mask>
    </defs>
    <rect x="-16" y="-21" width="20" height="42" fill="url(#lm-sun)" />
    <g mask="url(#lm-mask)">
      <path v-for="(d, i) in wind" :key="'w' + i" :d="d" class="wind" :style="{ animationDelay: (-i * 0.37) + 's' }" />
      <path :d="pause" class="pause" />
      <path v-for="(d, i) in open" :key="'o' + i" :d="d" class="open" />
      <path v-for="(d, i) in closed" :key="'c' + i" :d="d" class="closed" />
      <circle r="4" class="night" />
      <path d="M0,-4 A4,4 0 0 0 0,4 Z" class="day" />
      <circle r="4" class="rim" />
    </g>
  </svg>
</template>

<script lang="ts">
import { defineComponent } from "vue";

// The loading mark: Earth in the solar wind, Sun to the LEFT. A schematic, not
// data -- but the shapes are the real ones: a Shue-type magnetopause (squashed
// sunward, flaring into a tail), dipole loops compressed on the dayside and
// stretched on the nightside, gold closed / blue open as in the 3D view. Each
// cycle a gust pushes the nose in and thins the tail, then it relaxes.
// Transparent background, so it sits on any cover.

const CYCLE_MS = 2600;
const R_EARTH = 4;
const ALPHA = 0.5;           // Shue flaring exponent
const TAIL_THETA = 2.82;     // rad from the sunward axis -- reaches the right edge
const LOOPS = [5.5, 7, 9];   // dipole L (in drawing units) of the closed loops
const OPEN_Y = [4.2, 6.6, 9];
const WIND_Y = [2, 5.5, 9, 12.5, 16];

const f = (n: number): string => n.toFixed(2);

/** Gust profile 0..1 over one cycle: a quick push, a slower relaxation. */
function gust(t: number): number {
  const s = Math.sin(Math.PI * Math.min(1, t / 0.75));
  return s * s * (t < 0.75 ? 1 : 0);
}

/** Magnetopause point at angle th from the sunward (-x) axis, scaled by k. */
function mp(th: number, r0: number, k = 1): [number, number] {
  const r = k * r0 * Math.pow(2 / (1 + Math.cos(th)), ALPHA);
  return [-r * Math.cos(th), r * Math.sin(th)];
}

export default defineComponent({
  name: "LoadingMagnetosphere",

  data() {
    return { t: 0, raf: 0, start: 0 };
  },

  computed: {
    g(): number { return gust(this.t); },
    r0(): number { return 8.5 - 1.3 * this.g; },

    pause(): string {
      const pts: string[] = [];
      for (let i = -60; i <= 60; i++) {
        const [x, y] = mp((i / 60) * TAIL_THETA, this.r0);
        pts.push(`${f(x)},${f(y)}`);
      }
      return "M" + pts.join("L");
    },

    closed(): string[] {
      const day = 0.8 - 0.08 * this.g;          // dayside squash
      return LOOPS.map((L) => {
        const night = (1 + 0.07 * (L - R_EARTH)) * (1 + 0.12 * this.g);
        const lamMax = Math.acos(Math.sqrt(R_EARTH / L));
        const pts: string[] = [];
        // One closed figure: the dayside loop north -> south, then the nightside back.
        for (const side of [-1, 1]) {
          for (let i = 0; i <= 24; i++) {
            const lam = side * (-lamMax + (2 * lamMax * i) / 24);
            const c = Math.cos(lam);
            const x = side * L * c * c * c * (side < 0 ? day : night);
            const y = -L * c * c * Math.sin(lam);
            pts.push(`${f(x)},${f(y)}`);
          }
        }
        return "M" + pts.join("L") + "Z";
      });
    },

    open(): string[] {
      const thin = 1 - 0.1 * this.g;
      const out: string[] = [];
      for (const s of [-1, 1]) {
        OPEN_Y.forEach((h, i) => {
          const y0 = s * 3.6, yEnd = s * h * thin;
          // Leave the polar cap tilted sunward (the cusp), then sweep down-tail.
          out.push(`M${f(-1.2 + 0.4 * i)},${f(y0)} C${f(-3 - i)},${f(s * (9 + 2 * i) * thin)} ` +
            `${f(12 + 4 * i)},${f(yEnd * 1.05)} 50,${f(yEnd)}`);
        });
      }
      return out;
    },

    /** Streamlines: straight in from the Sun, then riding just outside the magnetopause. */
    wind(): string[] {
      const out: string[] = [];
      const k = 1.22;
      for (const s of [-1, 1]) {
        for (const y of WIND_Y) {
          const pts: string[] = [`-18,${f(s * y)}`];
          let on = false;
          for (let i = 0; i <= 60; i++) {
            const [x, yy] = mp((i / 60) * TAIL_THETA, this.r0, k);
            if (!on && Math.abs(yy) >= y) { on = true; }
            if (on) { pts.push(`${f(x)},${f(s * yy)}`); }
          }
          if (!on) { pts.push(`50,${f(s * y)}`); }
          out.push("M" + pts.join("L"));
        }
      }
      return out;
    },
  },

  mounted() {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { return; }
    this.start = performance.now();
    const tick = (now: number): void => {
      this.t = ((now - this.start) % CYCLE_MS) / CYCLE_MS;
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  },

  beforeUnmount() {
    cancelAnimationFrame(this.raf);
  },
});
</script>

<style lang="less">
.loading-magneto {
  width: 10rem;
  height: auto;

  .sun-a { stop-color: rgb(var(--sol-accent-rgb)); stop-opacity: 0.3; }
  .sun-b { stop-color: rgb(var(--sol-accent-rgb)); stop-opacity: 0; }

  .wind {
    fill: none;
    stroke: rgba(var(--sol-select-rgb), 0.5);
    stroke-width: 0.6;
    stroke-linecap: round;
    stroke-dasharray: 1.6 5.4;
    animation: lm-flow 0.9s linear infinite;
  }

  .pause {
    fill: rgba(var(--sol-accent2-rgb), 0.07);
    stroke: rgba(var(--sol-select-rgb), 0.55);
    stroke-width: 0.7;
  }

  .open { fill: none; stroke: rgba(var(--sol-accent2-rgb), 0.75); stroke-width: 0.6; }
  .closed { fill: none; stroke: rgba(var(--sol-accent-rgb), 0.9); stroke-width: 0.6; }

  .night { fill: #172133; }
  .day { fill: #8fc3ff; }
  .rim { fill: none; stroke: rgba(var(--sol-accent2-rgb), 0.7); stroke-width: 0.5; }
}

@keyframes lm-flow {
  to { stroke-dashoffset: -7; }
}

@media (prefers-reduced-motion: reduce) {
  .loading-magneto .wind { animation: none; }
}
</style>
