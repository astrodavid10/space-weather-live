<template>
  <div class="kiosk-stats">
    <header class="ks-header">
      <h1>Sol Kiosk — Usage Stats</h1>
      <p class="ks-sub">Anonymous, local to this device. No personal data, no network.</p>
      <div class="ks-actions">
        <button class="ks-btn" @click="downloadCsv">Download CSV</button>
        <button class="ks-btn" @click="downloadJson">Download JSON</button>
        <button class="ks-btn ks-btn-danger" @click="clearData">Clear data</button>
      </div>
    </header>

    <p v-if="days.length === 0" class="ks-empty">No usage data recorded yet.</p>

    <template v-else>
      <!-- Totals -->
      <div class="ks-tiles">
        <div class="ks-tile"><span class="ks-tile-num">{{ totals.sessions }}</span><span class="ks-tile-lbl">sessions</span></div>
        <div class="ks-tile"><span class="ks-tile-num">{{ fmtDuration(totals.avgDwellMs) }}</span><span class="ks-tile-lbl">avg dwell</span></div>
        <div class="ks-tile"><span class="ks-tile-num">{{ totals.selects }}</span><span class="ks-tile-lbl">exoplanet selects</span></div>
        <div class="ks-tile"><span class="ks-tile-num">{{ totals.taps }}</span><span class="ks-tile-lbl">taps</span></div>
        <div class="ks-tile"><span class="ks-tile-num">{{ totals.takeHome }}</span><span class="ks-tile-lbl">take-home QRs</span></div>
      </div>

      <!-- Busy hours -->
      <section class="ks-section">
        <h2>Busy hours (session starts, all days)</h2>
        <div class="ks-hours">
          <div v-for="h in 24" :key="h" class="ks-hour">
            <div class="ks-hour-bar" :style="{ height: hourBarPct(h - 1) + '%' }" :title="`${h - 1}:00 — ${hourly[h - 1]} sessions`"></div>
            <span class="ks-hour-lbl">{{ (h - 1) % 6 === 0 ? (h - 1) : '' }}</span>
          </div>
        </div>
      </section>

      <!-- Per-day table -->
      <section class="ks-section">
        <h2>By day</h2>
        <div class="ks-table-wrap">
          <table class="ks-table">
            <thead>
              <tr><th>Date</th><th>Sessions</th><th>Avg dwell</th><th>Taps</th><th>Selects</th><th>3D</th><th>Tuning Fork</th><th>Take-home</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in dayRows" :key="row.date">
                <td>{{ row.date }}</td>
                <td>{{ row.sessions }}</td>
                <td>{{ fmtDuration(row.avgDwellMs) }}</td>
                <td>{{ row.taps }}</td>
                <td>{{ row.selects }}</td>
                <td>{{ row.mode3d }}</td>
                <td>{{ row.tuningFork }}</td>
                <td>{{ row.takeHome }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div class="ks-cols">
        <section class="ks-section ks-col">
          <h2>Top exoplanets</h2>
          <ol class="ks-list">
            <li v-for="item in topPlanets" :key="item.name"><span class="ks-list-name">{{ item.name }}</span><span class="ks-list-count">{{ item.count }}</span></li>
            <li v-if="topPlanets.length === 0" class="ks-none">—</li>
          </ol>
        </section>
        <section class="ks-section ks-col">
          <h2>Top QR links</h2>
          <ol class="ks-list">
            <li v-for="item in topQr" :key="item.name"><span class="ks-list-name">{{ item.name }}</span><span class="ks-list-count">{{ item.count }}</span></li>
            <li v-if="topQr.length === 0" class="ks-none">—</li>
          </ol>
        </section>
      </div>
    </template>
  </div>
</template>

<script lang="ts">
import { defineComponent } from "vue";
// readStore is imported rather than reimplemented: the local copy parsed
// without validating the result, so a stored "null" (which JSON.parse accepts,
// returning null) set store = null and made the days() computed throw on
// Object.keys(null) — outside its try/catch — blanking the whole panel. The
// module version rejects null/array/primitive payloads and repairs short
// hourly arrays. It also owns STORAGE_KEY, so the panel no longer carries a
// duplicate literal that a rename could silently orphan.
import {
  statsExportCsv, statsExportJson, statsClear, readStore, StatsMap, DayRollup,
} from "../kiosk/kioskStats";

interface DayRow extends DayRollup { date: string; avgDwellMs: number; }
interface Ranked { name: string; count: number; }

export default defineComponent({
  name: "KioskStatsPanel",
  data() {
    return { store: {} as StatsMap };
  },
  created() {
    this.store = readStore();
  },
  computed: {
    days(): string[] {
      return Object.keys(this.store).sort().reverse();
    },
    dayRows(): DayRow[] {
      return this.days.map((date) => {
        const r = this.store[date];
        return { date, ...r, avgDwellMs: r.sessions > 0 ? r.totalDwellMs / r.sessions : 0 };
      });
    },
    hourly(): number[] {
      const sum = new Array(24).fill(0);
      for (const date of this.days) {
        const h = this.store[date].hourly;
        if (Array.isArray(h)) { for (let i = 0; i < 24; i++) { sum[i] += h[i] || 0; } }
      }
      return sum;
    },
    hourlyMax(): number {
      return Math.max(1, ...this.hourly);
    },
    totals(): { sessions: number; selects: number; taps: number; takeHome: number; avgDwellMs: number } {
      let sessions = 0, selects = 0, taps = 0, takeHome = 0, dwell = 0;
      for (const date of this.days) {
        const r = this.store[date];
        sessions += r.sessions; selects += r.selects; taps += r.taps;
        takeHome += r.takeHome; dwell += r.totalDwellMs;
      }
      return { sessions, selects, taps, takeHome, avgDwellMs: sessions > 0 ? dwell / sessions : 0 };
    },
    topPlanets(): Ranked[] {
      return this.rankMap((r) => r.planets).slice(0, 10);
    },
    topQr(): Ranked[] {
      return this.rankMap((r) => r.qr).slice(0, 10);
    },
  },
  methods: {
    rankMap(pick: (r: DayRollup) => Record<string, number>): Ranked[] {
      const agg: Record<string, number> = {};
      for (const date of this.days) {
        const m = pick(this.store[date]) || {};
        for (const k of Object.keys(m)) { agg[k] = (agg[k] || 0) + m[k]; }
      }
      return Object.keys(agg).map((name) => ({ name, count: agg[name] })).sort((a, b) => b.count - a.count);
    },
    hourBarPct(h: number): number {
      return Math.round((this.hourly[h] / this.hourlyMax) * 100);
    },
    fmtDuration(ms: number): string {
      const total = Math.round(ms / 1000);
      const m = Math.floor(total / 60);
      const s = total % 60;
      return `${m}:${String(s).padStart(2, "0")}`;
    },
    download(filename: string, text: string, mime: string): void {
      const blob = new Blob([text], { type: mime });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    },
    downloadCsv(): void {
      this.download("kiosk-stats.csv", statsExportCsv(), "text/csv");
    },
    downloadJson(): void {
      this.download("kiosk-stats.json", statsExportJson(), "application/json");
    },
    clearData(): void {
      // eslint-disable-next-line no-alert
      if (window.confirm("Erase all kiosk usage stats on this device? This cannot be undone.")) {
        statsClear();
        this.store = {};
      }
    },
  },
});
</script>

<style scoped>
.kiosk-stats {
  min-height: 100vh;
  box-sizing: border-box;
  padding: 2rem clamp(1rem, 4vw, 3rem);
  background: var(--sol-bg);
  color: var(--sol-text);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
.ks-header h1 { margin: 0; color: var(--sol-text); font-size: 1.6rem; }
.ks-sub { margin: 0.3rem 0 1rem; color: var(--sol-text-dim); font-size: 0.9rem; }
.ks-actions { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
.ks-btn {
  padding: 0.5rem 1rem; border-radius: 8px; cursor: pointer;
  background: var(--sol-surface); border: var(--sol-panel-border); color: var(--sol-text);
  font-size: 0.9rem; font-weight: 600;
}
.ks-btn:hover { box-shadow: 0 0 0 1px var(--sol-select); }
.ks-btn-danger { border-color: var(--sol-ember); color: var(--sol-ember); }
.ks-btn-danger:hover { box-shadow: 0 0 0 1px var(--sol-ember); }
.ks-empty { color: var(--sol-text-dim); font-size: 1rem; }

.ks-tiles { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 2rem; }
.ks-tile {
  display: flex; flex-direction: column; align-items: center;
  min-width: 6rem; padding: 0.9rem 1.2rem; border-radius: 10px;
  background: rgba(153, 200, 255, 0.06); border: 1px solid rgba(240, 171, 82, 0.3);
}
.ks-tile-num { font-size: 1.5rem; font-weight: 700; color: var(--sol-text);
  font-variant-numeric: tabular-nums; }
.ks-tile-lbl { font-size: 0.75rem; color: var(--sol-text-dim); margin-top: 0.2rem; }

.ks-section { margin-bottom: 2rem; }
.ks-section h2 { font-size: 1rem; color: var(--sol-text-dim); margin: 0 0 0.7rem; font-weight: 600; }

.ks-hours { display: flex; align-items: flex-end; gap: 3px; height: 120px; }
.ks-hour { flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; justify-content: flex-end; }
.ks-hour-bar { width: 70%; min-height: 2px; background: var(--sol-accent2); border-radius: 2px 2px 0 0; }
.ks-hour-lbl { font-size: 0.6rem; color: var(--sol-text-quiet); margin-top: 0.25rem; height: 0.9rem; }

.ks-table-wrap { overflow-x: auto; }
.ks-table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
.ks-table th, .ks-table td { padding: 0.4rem 0.7rem; text-align: right; border-bottom: 1px solid var(--sol-hairline); }
.ks-table th:first-child, .ks-table td:first-child { text-align: left; }
.ks-table th { color: var(--sol-text-dim); font-weight: 600; }

.ks-cols { display: flex; gap: 2rem; flex-wrap: wrap; }
.ks-col { flex: 1; min-width: 16rem; }
.ks-list { margin: 0; padding-left: 1.2rem; }
.ks-list li { display: flex; justify-content: space-between; gap: 1rem; padding: 0.25rem 0; border-bottom: 1px solid var(--sol-hairline); }
.ks-list-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ks-list-count { color: var(--sol-text); font-weight: 600; flex: 0 0 auto; }
.ks-none { color: var(--sol-text-quiet); list-style: none; }
</style>
