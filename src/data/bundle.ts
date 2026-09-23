/* eslint-disable @typescript-eslint/naming-convention -- field names mirror the dome engine's bundle schema (snake_case JSON) and the maths uses the README's K/N/L */
// =====================================================================
// Live Magnetosphere web bundle loader
// =====================================================================
// One source (a storm or the look-back) at one preset is two files:
//
//   <event_id>.json     the engine's manifest, byte-for-byte (schema 1)
//   <event_id>.bin.gz   the geometry buffer, gzip over the "delta-k/1" encoding
//
// The contract is the dome engine's (Engine/webexport/bundle.py and the bundle
// README). Units are EARTH RADII, frame J2000 geocentric EQUATORIAL (+Z north
// celestial pole, +X vernal equinox). Every block is 4-byte aligned, so each
// becomes a typed-array VIEW of one ArrayBuffer with no copy.
//
// delta-k/1 (pipeline/gz.py): every per-keyframe block (pos, alpha, lum) stores
// keyframe 0 verbatim and then modular differences keyframe-to-keyframe; uint16
// blocks are additionally split into a low-byte plane then a high-byte plane.
// That is what takes a mobile storm from 3.45 to 2.4 MB on the wire, and it
// is only valid because vertex i is the same physical thing in every keyframe.
//
// THREE RULES FROM THE BUNDLE README THAT A RENDERER MUST KEEP:
//  1. alpha is not optional: crossfaded field lines sit at alpha 0 while their
//     position snaps; interpolate position but ignore alpha and they sweep.
//  2. never sort, filter or re-index: the arrays pair positionally across keyframes.
//  3. the aurora has its OWN keyframe schedule (layer.keyframe_map).

export type Dtype = "uint8" | "uint16" | "uint32" | "int16" | "float32";

export interface BlockDesc {
  offset: number;
  length: number;
  dtype: Dtype;
  shape: number[];
}

export interface LayerMeta {
  name: string;
  kind: "lines" | "dots" | "mesh";
  count: number;
  keyframes: number;
  pos_lo: [number, number, number];
  pos_hi: [number, number, number];
  n_lines?: number;
  n_faces?: number;
  source_lattice?: [number, number];
  keyframe_map: number[];
  occluded_dropped?: number;
  blocks: Record<string, BlockDesc>;
}

export interface BundleManifest {
  schema: number;
  event_id: string;
  title: string;
  binary: string;
  binary_bytes: number;
  preset: { name: string };
  units: { length: string; re_km: number; frame: string };
  keyframes: { count: number; playback_s: number[]; epoch: string[]; hud: string[] };
  source: { step_min: number; window: { start: string; stop: string }; model: string; n_steps_original: number };
  provenance: Record<string, unknown> & {
    physics_version?: number;
    physics_tag?: string;
    magnetopause?: string;
    bow_shock?: string;
    aurora?: string;
    key_moments?: string;
    talking_points?: string[];
    decimation?: string;
    surfaces?: string;
  };
  layers: LayerMeta[];
}

/** One layer with its blocks resolved to typed-array views. */
export interface LayerData {
  meta: LayerMeta;
  pos: Uint16Array;          // [K, N, 3]
  alpha: Uint8Array;         // [K, N]
  color0: Uint8Array;        // [N, 3]
  lum: Uint8Array | null;    // [K, N] lines + dots
  lineStarts: Uint32Array | null;
  lineLengths: Uint32Array | null;
  faces: Uint16Array | Uint32Array | null;
}

export interface Bundle {
  manifest: BundleManifest;
  buffer: ArrayBuffer;
  layers: Map<string, LayerData>;
  /** Unix seconds for each global keyframe. */
  times: number[];
}

/** Our index row for one preset (pipeline/storms.py, pipeline/lookback.py). */
export interface PresetEntry {
  json_url: string;
  bin_gz_url: string;
  encoding?: string;
  bin_bytes: number;
  bin_gz_bytes: number;
  bin_sha256: string;
  keyframes: number;
}

const ENCODING = "delta-k/1";
const DELTA_BLOCKS = ["pos", "alpha", "lum"];

/** Inflate a gzip stream. DecompressionStream is in every browser since 2023
 *  (Safari 16.4); older ones get the clear error below rather than garbage. */
async function gunzip(res: Response, total: number, onProgress?: (done: number, total: number) => void): Promise<ArrayBuffer> {
  if (typeof DecompressionStream === "undefined" || !res.body) {
    throw new Error("This browser cannot unpack the data (DecompressionStream is missing).");
  }
  let done = 0;
  const counter = new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, ctl) {
      done += chunk.byteLength;
      onProgress?.(done, total);
      ctl.enqueue(chunk);
    },
  });
  const inflated = res.body.pipeThrough(counter)
    .pipeThrough(new DecompressionStream("gzip") as unknown as ReadableWritablePair<Uint8Array, Uint8Array>);
  return await new Response(inflated).arrayBuffer();
}

/** Undo delta-k/1 in place. See the file header and pipeline/gz.py. */
export function decodeDeltaK(manifest: BundleManifest, buf: ArrayBuffer): void {
  const bytes = new Uint8Array(buf);
  for (const L of manifest.layers) {
    for (const name of DELTA_BLOCKS) {
      const b = L.blocks[name];
      if (!b || b.shape[0] !== L.keyframes || (b.dtype !== "uint8" && b.dtype !== "uint16")) { continue; }
      const K = b.shape[0];
      if (b.dtype === "uint16") {
        const half = b.length / 2;
        const seg = bytes.slice(b.offset, b.offset + b.length);   // planes: lo..., hi...
        for (let i = 0; i < half; i++) {
          bytes[b.offset + 2 * i] = seg[i];
          bytes[b.offset + 2 * i + 1] = seg[half + i];
        }
        const a = new Uint16Array(buf, b.offset, half);
        const row = half / K;
        for (let k = 1; k < K; k++) {
          const o = k * row, p = o - row;
          for (let i = 0; i < row; i++) { a[o + i] = (a[o + i] + a[p + i]) & 0xffff; }
        }
      } else {
        const a = new Uint8Array(buf, b.offset, b.length);
        const row = b.length / K;
        for (let k = 1; k < K; k++) {
          const o = k * row, p = o - row;
          for (let i = 0; i < row; i++) { a[o + i] = (a[o + i] + a[p + i]) & 0xff; }
        }
      }
    }
  }
}

function view(buf: ArrayBuffer, b: BlockDesc | undefined): ArrayBufferView | null {
  if (!b) { return null; }
  switch (b.dtype) {
  case "uint8": return new Uint8Array(buf, b.offset, b.length);
  case "uint16": return new Uint16Array(buf, b.offset, b.length / 2);
  case "uint32": return new Uint32Array(buf, b.offset, b.length / 4);
  case "int16": return new Int16Array(buf, b.offset, b.length / 2);
  case "float32": return new Float32Array(buf, b.offset, b.length / 4);
  }
  return null;
}

export function parseIsoZ(s: string): number {
  return Date.parse(s) / 1000;
}

/**
 * line_starts must be the prefix sum of line_lengths (the vertices of each
 * polyline are contiguous). The 2026-09-22 dome exporter writes the LINE INDEX
 * instead (webexport/decimate.py decimate_lines: `new_starts.append(len(out))`,
 * where `out` is a list of per-line arrays) -- so starts read 0, 1, 2, ... and
 * every polyline but the first began one vertex after the previous one's start,
 * drawing ~5 visible loops out of 140. The vertex data itself is correct, so the
 * fix is to rebuild starts from lengths. Reported upstream; remove once fixed.
 */
function repairLineStarts(name: string, starts: Uint32Array, lengths: Uint32Array, count: number): Uint32Array {
  const fixed = new Uint32Array(lengths.length);
  let acc = 0;
  for (let i = 0; i < lengths.length; i++) { fixed[i] = acc; acc += lengths[i]; }
  if (acc !== count) { throw new Error(`${name}: line lengths sum to ${acc}, layer has ${count} vertices`); }
  if (fixed.some((v, i) => v !== starts[i])) {
    console.warn(`[bundle] ${name}: line_starts is not the prefix sum of line_lengths (known exporter bug); rebuilt.`);
  }
  return fixed;
}

/** Resolve a manifest + buffer into typed-array views, checking the contract. */
export function resolveBundle(manifest: BundleManifest, buffer: ArrayBuffer): Bundle {
  if (manifest.schema !== 1) { throw new Error(`bundle schema ${manifest.schema} (expected 1)`); }
  if (buffer.byteLength !== manifest.binary_bytes) {
    throw new Error(`geometry is ${buffer.byteLength} bytes, manifest says ${manifest.binary_bytes}`);
  }
  const layers = new Map<string, LayerData>();
  for (const L of manifest.layers) {
    for (const [name, b] of Object.entries(L.blocks)) {
      if (b.offset % 4 !== 0 || b.offset + b.length > buffer.byteLength) {
        throw new Error(`${L.name}.${name} is outside the geometry buffer`);
      }
    }
    if (!(L.count > 0) || L.keyframe_map.length !== L.keyframes) {
      throw new Error(`${L.name} has an inconsistent vertex count or keyframe map`);
    }
    const lengths = view(buffer, L.blocks.line_lengths) as Uint32Array | null;
    let starts = view(buffer, L.blocks.line_starts) as Uint32Array | null;
    if (starts && lengths) { starts = repairLineStarts(L.name, starts, lengths, L.count); }
    layers.set(L.name, {
      meta: L,
      pos: view(buffer, L.blocks.pos) as Uint16Array,
      alpha: view(buffer, L.blocks.alpha) as Uint8Array,
      color0: view(buffer, L.blocks.color0) as Uint8Array,
      lum: view(buffer, L.blocks.lum) as Uint8Array | null,
      lineStarts: starts,
      lineLengths: lengths,
      faces: view(buffer, L.blocks.faces) as Uint16Array | Uint32Array | null,
    });
  }
  return { manifest, buffer, layers, times: manifest.keyframes.epoch.map(parseIsoZ) };
}

export interface LoadOptions {
  signal?: AbortSignal;
  onProgress?: (doneBytes: number, totalBytes: number) => void;
  /** Cache-buster from index.json (Pages sends max-age=600). */
  version?: number | string;
}

/** Load one preset of one source. `base` is the directory the entry's urls are relative to. */
export async function loadBundle(base: URL, entry: PresetEntry, opts: LoadOptions = {}): Promise<Bundle> {
  const q = opts.version != null ? `?v=${opts.version}` : "";
  const mres = await fetch(new URL(entry.json_url + q, base), { signal: opts.signal });
  if (!mres.ok) { throw new Error(`manifest HTTP ${mres.status}`); }
  const manifest = (await mres.json()) as BundleManifest;
  const bres = await fetch(new URL(entry.bin_gz_url + q, base), { signal: opts.signal });
  if (!bres.ok) { throw new Error(`geometry HTTP ${bres.status}`); }
  const buffer = await gunzip(bres, entry.bin_gz_bytes, opts.onProgress);
  if (entry.encoding === ENCODING) {
    decodeDeltaK(manifest, buffer);
  } else if (entry.encoding) {
    throw new Error(`unknown geometry encoding ${entry.encoding}`);
  }
  return resolveBundle(manifest, buffer);
}

/** Dequantise one vertex of one keyframe into `out` (Earth radii). */
export function vertexAt(L: LayerData, k: number, i: number, out: number[] | Float32Array, o = 0): void {
  const { pos_lo: lo, pos_hi: hi, count } = L.meta;
  const base = (k * count + i) * 3;
  out[o] = lo[0] + (L.pos[base] / 65535) * (hi[0] - lo[0]);
  out[o + 1] = lo[1] + (L.pos[base + 1] / 65535) * (hi[1] - lo[1]);
  out[o + 2] = lo[2] + (L.pos[base + 2] / 65535) * (hi[2] - lo[2]);
}
