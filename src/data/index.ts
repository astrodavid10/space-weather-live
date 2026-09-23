/* eslint-disable @typescript-eslint/naming-convention -- field names mirror the pipeline's index JSON (snake_case) */
// =====================================================================
// data/index.json and the per-source catalogues (pipeline/index.py, storms.py)
// =====================================================================
// Two tracks, one contract (sol's rule): the app knows the pipeline only
// through these files. `data/` is found relative to the page, so the same build
// works at astrodavid10.github.io/space-weather-live/ and on `yarn serve`.

import type { PresetEntry } from "./bundle";

export type SourceStatus = "ok" | "degraded" | "stale" | "absent";

export interface IndexFile {
  schema: string;
  generated_iso: string;
  generated_unix: number;
  stale_after_hours: number;
  data_end_stale_hours: number;
  last_attempt_status: string;
  sources: {
    lookback: {
      url: string; status: SourceStatus; stale: boolean; event_id?: string; title?: string;
      generated_iso?: string; generated_unix?: number; age_hours?: number;
      data_end_iso?: string; data_age_hours?: number; feed?: string; note?: string; last_error?: string;
    };
    storms: { url: string; status: SourceStatus; count: number; published_iso?: string; published_unix?: number };
  };
}

export type PresetName = "mobile" | "desktop";

export interface SourceRow {
  /** Deep-link key: "lookback" or the dome's short storm key (gannon, octg4, …). */
  key: string;
  kind: "lookback" | "storm";
  eventId: string;
  label: string;
  title: string;
  why: string;
  window: { start: string; stop: string };
  model: string;
  keyMoments: string;
  talkingPoints: string[];
  presets: Partial<Record<PresetName, PresetEntry>>;
  /** Directory the preset urls resolve against. */
  base: URL;
  /** Cache-buster for this source's files. */
  version: number;
  /** Look-back only. */
  dataEndIso?: string;
  generatedIso?: string;
  status?: SourceStatus;
  note?: string;
}

interface StormsFile {
  schema: string;
  storms: {
    event_id: string; key: string; label: string; title: string; why: string; model: string;
    window: { start: string; stop: string }; key_moments: string; talking_points: string[];
    presets: Partial<Record<PresetName, PresetEntry>>;
  }[];
}

interface LookbackFile {
  schema: string;
  event_id: string; title: string; status: SourceStatus; model: string;
  window: { start: string; stop: string };
  data_end_iso: string; generated_iso: string; note?: string;
  provenance?: { key_moments?: string };
  presets: Partial<Record<PresetName, PresetEntry>>;
}

export function dataBaseUrl(): URL {
  return new URL("data/", document.baseURI);
}

async function getJson<T>(url: URL, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal, cache: "no-cache" });
  if (!res.ok) { throw new Error(`${url.pathname} HTTP ${res.status}`); }
  return (await res.json()) as T;
}

export interface Catalogue {
  index: IndexFile;
  sources: SourceRow[];
}

/** index.json plus every source it lists, look-back first then storms by date. */
export async function loadCatalogue(signal?: AbortSignal): Promise<Catalogue> {
  const base = dataBaseUrl();
  const index = await getJson<IndexFile>(new URL(`index.json?_=${Math.floor(Date.now() / 600000)}`, base), signal);
  if (index.schema !== "swl.index/1") { throw new Error(`index schema ${index.schema}`); }
  const sources: SourceRow[] = [];

  const lbEntry = index.sources.lookback;
  if (lbEntry && lbEntry.status !== "absent") {
    try {
      const lbBase = new URL("lookback/", base);
      const v = lbEntry.generated_unix ?? index.generated_unix;
      const lb = await getJson<LookbackFile>(new URL(`index.json?v=${v}`, lbBase), signal);
      sources.push({
        key: "lookback", kind: "lookback", eventId: lb.event_id, label: "Last 48 hours",
        title: lb.title, why: "Real solar wind measured by ACE, rebuilt every 6 hours",
        window: lb.window, model: lb.model, keyMoments: lb.provenance?.key_moments ?? "",
        talkingPoints: [], presets: lb.presets, base: lbBase, version: v,
        dataEndIso: lb.data_end_iso, generatedIso: lb.generated_iso, status: lbEntry.status, note: lb.note,
      });
    } catch (err) {
      console.warn("[index] look-back unavailable:", err);
    }
  }

  const stEntry = index.sources.storms;
  if (stEntry && stEntry.status !== "absent") {
    const stBase = new URL("storms/", base);
    const v = stEntry.published_unix ?? index.generated_unix;
    const st = await getJson<StormsFile>(new URL(`index.json?v=${v}`, stBase), signal);
    for (const s of st.storms) {
      sources.push({
        key: s.key, kind: "storm", eventId: s.event_id, label: s.label, title: s.title, why: s.why,
        window: s.window, model: s.model, keyMoments: s.key_moments, talkingPoints: s.talking_points ?? [],
        presets: s.presets, base: stBase, version: v,
      });
    }
  }
  return { index, sources };
}
