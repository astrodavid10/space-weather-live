# Magnetosphere — Earth in the solar wind

[![freshness](https://github.com/astrodavid10/space-weather-live/actions/workflows/freshness.yml/badge.svg)](https://github.com/astrodavid10/space-weather-live/actions/workflows/freshness.yml)

**Live:** https://astrodavid10.github.io/space-weather-live/

The web version of the INTUITIVE Planetarium's *Earth's Dynamic Magnetosphere* dome show,
and a sibling of [Sol](https://astrodavid10.github.io/sol-solar-viewer/). Data to Dome, Dome to Phone.

- **The shield itself** — Tsyganenko-traced field lines (gold closed, blue and orange open
  lobes), the Lin/Shue hybrid magnetopause, the Slavin & Holzer bow shock, the magnetosheath,
  ring current and plasmasphere, all in 3D around WorldWide Telescope's Earth.
- **What the Sun is throwing at it** — solar-wind parcels at the measured speed and density,
  and the interplanetary field draping over Earth's and connecting to it.
- **Right now** — live NOAA SWPC wind, Bz, density and Kp, and NOAA's OVATION Prime aurora
  forecast, with the tier (LIVE / CACHED / REPLAY) and data age always on screen.
- **The last 48 hours and five great storms** — Bastille Day 2000, November 2003, Gannon
  May 2024, October 2024 G4 and January 2026, scrubbable, with the key moments marked.

Model output is never presented as observation: every layer says what is measured and what
is modelled.

## Stack

Vue 3 + TypeScript (Vue CLI 5, Yarn 4), the WorldWide Telescope WebGL engine in solar-system
mode with a three.js overlay drawn into its GL context (vendored `three-wwt`), and a Python
pipeline that runs the dome's own physics engine in GitHub Actions.

```
pipeline/            python -m pipeline {lookback,storms,index,validate,from-published}
pipeline/engine/     the dome engine snapshot (see SNAPSHOT.md)
scripts/             publish_gh_pages.sh (single orphan commit), publish_storms.py
src/three/           frame bridge, keyframe morph, layers
src/wwt/             Earth-pinned WWT stage, gestures
.github/workflows/   data (6-hourly look-back), app-deploy, build, freshness, keepalive
```

## Development

```bash
corepack enable
yarn install
python -m pipeline from-published --out public/data     # or: yarn data:local (from the bundle zip)
yarn serve                                               # http://localhost:8080/?debug=1
yarn lint && yarn typecheck && yarn build
python -m pytest pipeline/tests -q
```

`?debug=1` shows the frame tripwire (magnetopause nose vs. WWT's Sun), camera and epoch.
Other URL parameters: `src`, `t`, `cam`, `layers`, `style`, `q`, `kiosk=1`.

See `CLAUDE.md` for the conventions and footguns.

## Credits

Created by A. David Weigel, with Alex Shepard and Emily Watson. INTUITIVE Planetarium,
U.S. Space & Rocket Center, Huntsville, Alabama. Live data: NOAA SWPC. Look-back: NASA CCMC
ISWA (ACE, GFZ, Kyoto). Storms: NASA/GSFC CDAWeb OMNI. Field: Tsyganenko T89/TS05 with IGRF-14
via geopack. Engine: WorldWide Telescope. Built on the CosmicDS data-story framework. Full,
link-verified references in the app's About panel and `THIRD-PARTY.md`.
