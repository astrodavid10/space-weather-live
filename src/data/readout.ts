// =====================================================================
// The bundle's per-keyframe HUD strings and key moments, parsed for the UI
// =====================================================================
// hud: "Wind 416 km/s|Bz +0.2 nT|n 8.8 /cc|Kp 2.3|2024-05-10 08:05 UT|OMNI - REPLAY"
// key_moments: "KEY MOMENTS: shock 18:35 UT 10 May (13 -> 44 nPa) | Bz min -44 nT at
//   00:35 UT 11 May | wind peaks 1022 km/s 01:25 UT 12 May | Kp 9 from 00:35 UT 11 May |
//   SYM-H min -498 nT at 02:15 UT 11 May | Bz below -10 nT for 18 h"
// Both are the dome's own text; the UI shows them, it never re-derives them.

export interface HudValues {
  speed: number | null;     // km/s
  bz: number | null;        // nT
  density: number | null;   // /cc
  kp: number | null;
  stamp: string;            // "2024-05-10 08:05 UT"
  source: string;           // "OMNI - REPLAY"
  pdyn: number | null;      // nPa, derived 1.6726e-6 n v^2 (STATE_SCHEMA)
}

const num = (s: string | undefined): number | null => {
  if (!s) { return null; }
  const m = s.match(/[-+]?\d+(\.\d+)?/);
  return m ? Number(m[0]) : null;
};

export function parseHud(s: string | undefined): HudValues {
  const f = (s ?? "").split("|");
  const speed = num(f[0]), density = num(f[2]);
  return {
    speed, bz: num(f[1]), density, kp: num(f[3]),
    stamp: (f[4] ?? "").trim(), source: (f[5] ?? "").trim(),
    pdyn: speed != null && density != null ? 1.6726e-6 * density * speed * speed : null,
  };
}

/**
 * Shue et al. (1998) subsolar magnetopause distance, R_E. The dome's drawn
 * nose is a Lin 2010 / Shue 1998 hybrid; this is only for the "Nose" readout
 * and the live rescale, and is labelled as a model everywhere it appears.
 */
export function shueR0(bz: number, pdyn: number): number {
  return (10.22 + 1.29 * Math.tanh(0.184 * (bz + 8.14))) * Math.pow(Math.max(pdyn, 0.05), -1 / 6.6);
}

export type MomentKind = "shock" | "bzmin" | "symh" | "wind" | "kp" | "other";

export interface KeyMoment {
  unix: number;
  kind: MomentKind;
  /** The dome's own phrase, e.g. "Bz min -44 nT". */
  label: string;
  /** Anything after the time, e.g. "(13 -> 44 nPa)". */
  detail: string;
}

const MONTHS: Record<string, number> = {
  jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11,
};

function kindOf(label: string): MomentKind {
  const l = label.toLowerCase();
  if (l.startsWith("shock")) { return "shock"; }
  if (l.startsWith("bz min")) { return "bzmin"; }
  if (l.startsWith("sym-h") || l.startsWith("dst")) { return "symh"; }
  if (l.startsWith("wind")) { return "wind"; }
  if (l.startsWith("kp")) { return "kp"; }
  return "other";
}

/** Parse the manifest's key_moments into dated marks; parts with no instant (durations) are skipped. */
export function parseKeyMoments(text: string, windowStartIso: string): KeyMoment[] {
  const year = new Date(windowStartIso).getUTCFullYear();
  const out: KeyMoment[] = [];
  const body = text.replace(/^KEY MOMENTS:\s*/i, "");
  for (const raw of body.split("|")) {
    const part = raw.trim();
    const m = part.match(/^(.*?)\s+(?:at\s+|from\s+)?([0-2]\d):([0-5]\d) UT (\d{1,2}) ([A-Za-z]{3})(.*)$/);
    if (!m) { continue; }
    const month = MONTHS[m[5].toLowerCase()];
    if (month == null) { continue; }
    const unix = Date.UTC(year, month, Number(m[4]), Number(m[2]), Number(m[3])) / 1000;
    const label = m[1].replace(/\s+(at|from)$/i, "").trim();
    out.push({ unix, kind: kindOf(label), label, detail: m[6].trim() });
  }
  return out.sort((a, b) => a.unix - b.unix);
}

/** "Bz min -44 nT" -> "Bz minimum, -44 nT" style guest phrasing (light touch; keep the numbers). */
export function momentTitle(k: KeyMoment): string {
  switch (k.kind) {
  case "shock": return "Shock arrives";
  case "bzmin": return k.label.replace(/^Bz min/i, "Most southward field:");
  case "symh": return k.label.replace(/^SYM-H min/i, "Ring current peaks: SYM-H").replace(/^Dst min/i, "Ring current peaks: Dst");
  case "wind": return k.label.replace(/^wind peaks/i, "Fastest wind:");
  case "kp": return k.label.replace(/^Kp/i, "Kp reaches");
  }
  return k.label;
}

/** Split "WATCH: ..." / "PHYSICS: ..." / "IMPACT: ..." talking points. */
export function parseTalkingPoints(points: string[]): { tag: string; text: string }[] {
  return points.map((p) => {
    const m = p.match(/^([A-Z]+):\s*(.*)$/s);
    return m ? { tag: m[1], text: m[2] } : { tag: "", text: p };
  });
}
