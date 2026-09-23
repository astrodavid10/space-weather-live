# Third-party components

Space Weather Live itself is MIT licensed (see `LICENSE.txt`). It redistributes the components
below, each under its own terms.

---

## three-wwt (vendored source)

`src/three/three-wwt/` — MIT. Vendored from
[cosmicds/three-wwt](https://github.com/cosmicds/three-wwt) at commit
`80b95028d2b1e9ba7dbc117c314b25f535e80847`, with local modifications documented
in each file's header. The upstream license text is kept verbatim alongside the
source at `src/three/three-wwt/LICENSE`.

Vendored rather than taken from npm because `@cosmicds/three-wwt@0.0.3` bundles
a duplicate copy of the WWT engine (1.7 MB) and ships a broken CJS entry point.

## Overpass

`src/assets/Overpass-SemiBold.ttf` — SIL Open Font License 1.1 (the font is dual
licensed OFL 1.1 / LGPL 2.1). Copyright 2016 Red Hat, Inc. The license text is
redistributed with it at `src/assets/Overpass-LICENSE.md`, and the font
self-declares both the license (name record 13) and its URL (record 14) in its
own `name` table. Taken from
[RedHatOfficial/Overpass](https://github.com/RedHatOfficial/Overpass) v3.0.5.

This is the app's only bundled face, used for the app title and the info-modal
heading. One weight, `font-display: swap` — it is decoration on a short string
and must never hold up the first paint.

**It replaced Highway Gothic Narrow**, which shipped here until this repository
was made public. That file recorded `copyright: "2009"` and
`trademark: "Ash Pikachu Font"` but carried **no license record and no license
URL**, and publishing the repository would have redistributed it on unknown
terms. Overpass descends from the same US FHWA Standard Alphabets, so the change
is within the same lineage rather than a new look.

`Roboto.ttf`, `RobotoCondensed.ttf` and `RobotoCondensed-Italic.ttf` were also
removed. They had **no `@font-face` rule**, so the browser never loaded them —
naming "Roboto" in a CSS stack only ever resolved to a system copy where one
existed. They were 500 KB of dead weight in every clone.

## WorldWide Telescope engine

`@wwtelescope/engine`, `@wwtelescope/engine-pinia` and related packages are
consumed from npm, not vendored, and carry their own licenses (MIT). The app
also fetches the WWT imageset catalog from `worldwidetelescope.org` at
runtime; `public/hips-surveys.wtml` is an excerpt of that catalog.

## Data sources

Not redistributed as code, but fetched or digested at build/run time. All are
public, US-government or publicly-funded scientific products; the full,
link-verified list is in the app's About panel (`src/data/credits.ts`,
transcribed from the dome show's `Engine/docs/CREDITS.html`):

- **NOAA SWPC** (`services.swpc.noaa.gov`) — real-time solar wind from the
  active L1 spacecraft, Kp, Geospace Dst forecast, OVATION Prime aurora.
- **NASA CCMC ISWA** (`iswa.gsfc.nasa.gov/hapi`) — ACE MAG + SWEPAM, GFZ Kp and
  Kyoto Dst quick-look for the 48-hour look-back.
- **NASA/GSFC CDAWeb OMNI** 1-minute data — the five stored storms.
- **Tsyganenko T89 / TS05, IGRF-14, geopack** — the field models the dome
  engine traces through (see the engine snapshot's own notices).

The five storm bundles in `data/storms/` were rendered by the INTUITIVE
Planetarium's Live Magnetosphere engine; the source zip is attached to the
`data-2026-09-22` GitHub Release.

## Institutional marks

Two marks of the INTUITIVE Planetarium at the U.S. Space & Rocket Center ship in
the bundle and are used as a credit:

- `src/assets/ip-ussrc.png` — the USSRC lock-up.
- `src/assets/ip-wordmark-white.svg` — the planetarium ICON. The filename says
  "wordmark", but the artwork is square (`viewBox="0 0 160 160"`) and its ids are
  `Logos_Icon_1` / `White_Icon_1`; per the brand kit that is the icon, not the
  wordmark.

Their use here is authorized by the planetarium's director, who is the author of
this proof of concept. The marks themselves are not covered by this repository's
MIT grant — the MIT terms apply to the software, not to the institution's
trademarks. The brand kit also requires that the planetarium wordmark never
appear without the USSRC logo, which is why `InfoModal`'s credit lockup keeps
`ip-ussrc.png` as a single paired image rather than two separable marks.

## Toolkit marks

Two more marks ship in the bundle, each used purely as attribution and each
linking to its owner:

- `src/assets/logo_wwt.png` — WorldWide Telescope, credited as "Powered by
  WorldWide Telescope". WWT is an open-source project of the American
  Astronomical Society.
- `src/assets/logo_cosmicds.png` — the CosmicDS mark, credited as "Interactive
  developed using the CosmicDS toolkit". CosmicDS (Cosmic Data Stories) is an
  NSF/NASA-funded program at the Center for Astrophysics | Harvard &
  Smithsonian; this app is built on its Vue toolkit and its WWT integration.
  Vendored locally rather than hotlinked from the CosmicDS site so the credit
  cannot break, and so a kiosk with no internet still shows it. The mark is the
  spiral device only — the wordmark half of the full logo is dark navy and would
  be illegible on this app's dark panels, and the caption beside it names
  CosmicDS anyway.

Neither mark is covered by this repository's MIT grant. Both are reproduced at
credit size (22 px) in the About panel, in the manner these projects ask to be
credited; they are not used as branding for this app and imply no endorsement.
