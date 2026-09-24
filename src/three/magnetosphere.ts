/* eslint-disable @typescript-eslint/naming-convention -- L = one bundle layer, as in the bundle README */
// =====================================================================
// The magnetosphere scene: every bundle layer + client-side layers in one group
// =====================================================================
// Everything inside `root` is in J2000 EQUATORIAL EARTH RADII -- the bundle's
// own frame -- and `root.matrix` is EQ_RE_TO_SCENE (three/frame.ts), the ONE
// place the frame and the scale are applied. Keep it that way: a second
// transform anywhere is how a shell ends up rotated against the field lines
// (the dome lost three weeks to exactly that, bundle README "Three things").

import { Group, Vector3 } from "three";

import type { Bundle, LayerData } from "../data/bundle";
import { EQ_RE_TO_SCENE, gmstDeg } from "./frame";
import { createEarthOccluder, createPulses, createTextbookDipole, Pulses } from "./layers/extras";
import { MorphPoints, StaticPoints, createMorphPoints, createStaticPoints } from "./layers/points";
import { createMorphShell, displayColor } from "./layers/shells";
import { QuadLines, createQuadLines } from "./layers/quadLines";

export type LayerKey =
  | "fieldLines" | "fieldPulses" | "imfLines" | "textbookField"
  | "magnetopause" | "bowShock" | "magnetosheath"
  | "solarWind" | "ringCurrent" | "plasmasphere"
  | "auroraOval";

export interface StyleValues {
  windScale: number;     // x
  pulseScale: number;    // x
  lineWidth: number;     // px (full width)
  imfWidth: number;      // px
}

export const DEFAULT_STYLE: StyleValues = { windScale: 1, pulseScale: 1, lineWidth: 1.6, imfWidth: 1.2 };

export interface Magnetosphere {
  root: Group;
  /** Layer names present in this bundle (the renderer never invents a layer). */
  present: Set<string>;
  update(frameT: number, nowS: number, sceneUnix: number): void;
  setVisible(flags: Record<LayerKey, boolean>): void;
  setStyle(s: StyleValues): void;
  setRes(w: number, h: number, dpr: number): void;
  /** Live OVATION oval: replaces the bundle oval while set (null = back to the bundle's). */
  setLiveAurora(points: { pos: Float32Array; color: Float32Array; alpha: Float32Array; count: number } | null): void;
  /** Magnetopause nose direction (unit, equatorial) at the current frame, for the ?debug tripwire. */
  noseDirection(frameT: number): [number, number, number] | null;
  dispose(): void;
}

export function createMagnetosphere(bundle: Bundle): Magnetosphere {
  const root = new Group();
  root.matrixAutoUpdate = false;
  root.matrix.copy(EQ_RE_TO_SCENE);
  root.matrixWorldNeedsUpdate = true;

  const get = (name: string): LayerData | undefined => bundle.layers.get(name);
  const present = new Set(bundle.layers.keys());

  root.add(createEarthOccluder());

  const disposers: (() => void)[] = [];
  const updaters: ((frameT: number) => void)[] = [];
  const resizers: ((w: number, h: number, dpr: number) => void)[] = [];
  const toggles: Partial<Record<LayerKey, (v: boolean) => void>> = {};
  /** Sunward axis (equatorial, unit), updated per frame from the magnetopause
   *  apex -- the axis the dome builds its shells around (aberration included).
   *  Shared by reference with the shell fades and the wind's recycle cut. */
  const axis = new Vector3(1, 0, 0);

  // --- surfaces ----------------------------------------------------------
  const shell = (name: string, key: LayerKey, o: { opacity: number; rim: number; clipRe?: number; order: number; target?: number; fade?: { from: number; to: number; min: number } }): ReturnType<typeof createMorphShell> | null => {
    const L = get(name);
    if (!L || !L.faces) { return null; }
    const s = createMorphShell(L, {
      color: displayColor(L.color0.subarray(0, 3), o.target), opacity: o.opacity, rim: o.rim,
      clipRe: o.clipRe, renderOrder: o.order, axis, fade: o.fade,
    });
    root.add(s.object3d);
    updaters.push(s.update);
    disposers.push(s.dispose);
    toggles[key] = s.setVisible;
    return s;
  };
  shell("RingCurrent", "ringCurrent", { opacity: 0.32, rim: 0.9, order: 10 });
  shell("Plasmasphere", "plasmasphere", { opacity: 0.22, rim: 0.9, clipRe: 1.02, order: 11 });
  shell("MagnetopauseShell", "magnetopause", { opacity: 0.13, rim: 1.1, order: 20 });
  // The bow shock fades as it flares down-tail (David 2026-09-24): full at the
  // nose, gone ~70 R_E behind Earth. The sheath fades the same way, but less.
  const bs = shell("BowShockShell", "bowShock", { opacity: 0.1, rim: 1.1, order: 21, target: 0.9, fade: { from: 5, to: -70, min: 0 } });
  if (bs) {
    const L = get("BowShockShell") as LayerData;
    const sheath = createMorphShell(L, {
      color: displayColor(L.color0.subarray(0, 3), 0.55), opacity: 0.05, rim: 0.3, renderOrder: 22, additive: true,
      axis, fade: { from: 0, to: -150, min: 0.2 },
    }, { geometry: bs.geometry, morph: bs.morph });
    root.add(sheath.object3d);
    updaters.push(sheath.update);
    disposers.push(sheath.dispose);
    toggles.magnetosheath = sheath.setVisible;
  }

  // --- lines --------------------------------------------------------------
  let fieldLines: QuadLines | null = null;
  let imfLines: QuadLines | null = null;
  const fl = get("FieldLines");
  if (fl?.lineStarts) {
    fieldLines = createQuadLines(fl, { widthPx: DEFAULT_STYLE.lineWidth / 2, gain: 1.15, renderOrder: 30 });
    root.add(fieldLines.object3d);
    updaters.push(fieldLines.update);
    resizers.push(fieldLines.setRes);
    disposers.push(fieldLines.dispose);
    toggles.fieldLines = fieldLines.setVisible;
  }
  const imf = get("IMFLines");
  if (imf?.lineStarts) {
    imfLines = createQuadLines(imf, { widthPx: DEFAULT_STYLE.imfWidth / 2, gain: 1.1, opacity: 0.85, renderOrder: 31 });
    root.add(imfLines.object3d);
    updaters.push(imfLines.update);
    resizers.push(imfLines.setRes);
    disposers.push(imfLines.dispose);
    toggles.imfLines = imfLines.setVisible;
  }

  // --- dots ----------------------------------------------------------------
  let wind: MorphPoints | null = null;
  const w = get("WindParticles");
  if (w) {
    // Small, opaque, crisp grains (David 2026-09-24), with the recycle cut so a
    // parcel relaunched upstream never flies back past Earth (points.ts header).
    wind = createMorphPoints(w, {
      sizeRe: 0.26, minPx: 1.6, maxPx: 3.4, gain: 1.35, renderOrder: 40,
      look: "solid", alphaFloor: 0.6, axis, recycleCut: true,
    });
    root.add(wind.object3d);
    updaters.push(wind.update);
    resizers.push(wind.setRes);
    disposers.push(wind.dispose);
    toggles.solarWind = wind.setVisible;
  }
  let aurora: MorphPoints | null = null;
  const au = get("AuroraOval");
  if (au) {
    aurora = createMorphPoints(au, { sizeRe: 0.045, minPx: 1.4, maxPx: 7, gain: 1.25, renderOrder: 41 });
    root.add(aurora.object3d);
    updaters.push(aurora.update);
    resizers.push(aurora.setRes);
    disposers.push(aurora.dispose);
  }
  const liveAurora: StaticPoints = createStaticPoints(65160, { sizeRe: 0.03, minPx: 1.4, maxPx: 6, renderOrder: 41 });
  liveAurora.setVisible(false);
  root.add(liveAurora.object3d);
  resizers.push(liveAurora.setRes);
  disposers.push(liveAurora.dispose);
  let liveOn = false;
  let auroraWanted = true;
  toggles.auroraOval = (v) => {
    auroraWanted = v;
    aurora?.setVisible(v && !liveOn);
    liveAurora.setVisible(v && liveOn);
  };

  // --- client-side ------------------------------------------------------------
  let pulses: Pulses | null = null;
  if (fl?.lineStarts) {
    pulses = createPulses(fl);
    root.add(pulses.points.object3d);
    resizers.push(pulses.points.setRes);
    disposers.push(pulses.dispose);
    toggles.fieldPulses = pulses.setVisible;
  }
  const dipole = createTextbookDipole();
  root.add(dipole.object3d);
  disposers.push(dipole.dispose);
  toggles.textbookField = dipole.setVisible;

  const mp = get("MagnetopauseShell");

  return {
    root,
    present,
    update(frameT, nowS, sceneUnix) {
      const nose = this.noseDirection(frameT);
      if (nose) { axis.set(nose[0], nose[1], nose[2]); }
      for (const u of updaters) { u(frameT); }
      pulses?.update(frameT, nowS);
      dipole.setGmst(gmstDeg(sceneUnix));
    },
    setVisible(flags) {
      for (const [k, fn] of Object.entries(toggles)) { fn?.(!!flags[k as LayerKey]); }
    },
    setStyle(s) {
      wind?.setScale(s.windScale);
      pulses?.points.setScale(s.pulseScale);
      fieldLines?.setWidth(s.lineWidth / 2);
      imfLines?.setWidth(s.imfWidth / 2);
    },
    setRes(wpx, hpx, dpr) { for (const r of resizers) { r(wpx, hpx, dpr); } },
    setLiveAurora(p) {
      liveOn = !!p;
      if (p) {
        liveAurora.positions.set(p.pos.subarray(0, p.count * 3));
        liveAurora.colors.set(p.color.subarray(0, p.count * 3));
        liveAurora.alphas.set(p.alpha.subarray(0, p.count));
        liveAurora.commit(p.count);
      }
      toggles.auroraOval?.(auroraWanted);
    },
    noseDirection(frameT) {
      if (!mp) { return null; }
      // Vertex 0 of the magnetopause shell is the subsolar APEX (the engine's
      // apex-and-rings shell topology, kept first by decimate_shell). Measured
      // on Gannon 2024-05-10T08:05Z: 0.33 deg from the true Sun direction.
      const k = Math.round(Math.min(Math.max(frameT, 0), mp.meta.keyframes - 1));
      const { pos_lo: lo, pos_hi: hi, count: n } = mp.meta;
      const b = k * n * 3;
      const x = lo[0] + (mp.pos[b] / 65535) * (hi[0] - lo[0]);
      const y = lo[1] + (mp.pos[b + 1] / 65535) * (hi[1] - lo[1]);
      const z = lo[2] + (mp.pos[b + 2] / 65535) * (hi[2] - lo[2]);
      const r = Math.hypot(x, y, z) || 1;
      return [x / r, y / r, z / r];
    },
    dispose() {
      for (const d of disposers) { d(); }
      root.clear();
    },
  };
}
