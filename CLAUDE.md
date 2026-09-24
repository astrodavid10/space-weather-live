# CLAUDE.md — Space Weather Live

Sibling of `../sol` (sol-solar-viewer); read sol's CLAUDE.md for the WWT / three-wwt footguns
this project inherits. The approved project plan lives at
`~/.claude/plans/live-space-weather-interactive-stateless-glade.md`.

## Commands

```
yarn serve | build | lint | typecheck
python -m pipeline storms --zip <bundle.zip> --out public/data --with-lookback
python -m pipeline validate --root dist-data --strict
python -m pipeline lookback --out dist-data --workers 4      # needs pipeline/engine
python -m pytest pipeline/tests -q
```

## Architecture (the rules that matter)

- **Two tracks, one contract.** The app knows the pipeline only through `data/index.json`
  (`swl.index/1`), `data/storms/index.json`, `data/lookback/index.json` and the engine's bundle
  schema 1. The entry chunk is engine-free; `MagnetoView3D.vue` is the lazy `magneto3d` chunk.
- **gh-pages is one orphan commit.** `scripts/publish_gh_pages.sh` takes `src:dest` PAIRS so the
  data job replaces only `data/lookback` + `data/index.json`. `data/storms` is published once by
  hand (TS05 renders CI cannot rebuild); the source zip is the `data-2026-09-22` Release.
- **Honesty law.** Model output is never presented as observation; tier + data age always on
  screen; magnetopause is a "Lin 2010 / Shue 1998 hybrid"; aurora oval is a model.

## Footguns (numbered; add to the end)

1. **The three scene is EARTH-CENTRED.** WWT renders relative to `viewTarget`, and
   `wwt/magnetoStage.ts` pins `viewTarget` to Earth by wrapping `Planets.updatePlanetLocations`
   (re-pinning in a frame callback lags one frame = ~0.7 R_E of jitter during playback). Bundle
   geometry needs only `EQ_RE_TO_SCENE` (equatorial->ecliptic rotation x WWT's drawn Earth radius
   4.27813e-5 AU/R_E). Never translate by Earth's heliocentric position. Never `setTrackedObject`.
2. **Verified frame:** magnetopause vertex 0 (the apex) is 0.3 deg from WWT's Sun at Gannon's
   Bz minimum. `?debug=1` shows it; a > 8 deg reading warns in the console.
3. **Clear depth explicitly.** `three-wwt/setupThreeWWT.ts` sets `depthMask(true)`,
   `clearDepth(1)` and disables scissor before its depth clear. three's `resetState()` does NOT
   reissue `clearDepth`; with WWT's stars on, the inherited value made everything beyond a few R_E
   fail the depth test (wind invisible).
4. **`line_starts` in the 2026-09-22 bundles is wrong** (the dome exporter stores the line index,
   not the vertex offset). `data/bundle.ts` rebuilds it from `line_lengths`; the validator reports
   it as an advisory. Remove the repair once the exporter is fixed upstream.
5. **delta-k/1 wire encoding.** `.bin.gz` = gzip over keyframe deltas with uint16 byte planes
   (`pipeline/gz.py` <-> `data/bundle.ts decodeDeltaK`). Change both or neither; the index's
   `encoding` field names it and `bin_sha256` is of the DECODED buffer.
6. **Additive shaders premultiply once.** Fragment output is `vec4(color * a, a)` with three's
   AdditiveBlending (SrcAlpha, One) — the net is color*a^2 by design; tune gains, not blending.
7. **Background tabs freeze WWT's camera easing** (rAF suspended). For scripted checks write
   both cameras (`goToPreset(id, true)`), and take a screenshot to force a frame.
8. `wwt/magnetoStage.ts` computes directions in WWT's MIRRORED world: a cross product there is the
   negative of the physical one (dawn = cross3(north, sun) in world coords). See its comments.
9. **`Float32BufferAttribute` COPIES its array.** Anything the CPU rewrites per frame (pulse beads,
   the live OVATION oval) must use `BufferAttribute(array, n)`, or the GPU never sees the writes.
10. **Wind parcels recycle.** A parcel leaving down-tail is relaunched upstream; with 10-30 min
    keyframes the mix() flew it back past Earth. `points.ts` treats a >10 R_E SUNWARD step between
    keyframes as a relaunch and crossfades in place (measured: 4.8% of Gannon steps, all > 50 R_E).
