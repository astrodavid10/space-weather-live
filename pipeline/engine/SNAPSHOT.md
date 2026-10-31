# Engine snapshot

A Digistar-free snapshot of the dome's `Engine/`, vendored verbatim from
`MagnetoEngine_2026-10-31.zip` (the engine's own payload format, unpacked by `mag_setup.py`
on the dome).

- **Source:** magneto-stormy session payload, `build_id` 9d66d2a20bd1c31a, 2026-10-31
- **Physics:** `physics_version` 7, `physics_tag` `lin2010-hybrid-aberrated-shocked-imf-drawnclip2d-seedsafe`
  (`magneto/model/__init__.py`), the same physics as the published storms
- **Integrity:** `payload.json` lists all 85 files with sha256; every one matched in the zip.
  Git normalises line endings to LF (`.gitattributes`), so verify against the zip, not a checkout
- **Contents:** magneto/, dsexport/, livecore/, webexport/, tools/, vendor/ (IGRF 9-14 coefficients),
  render_manifests/ (the dome's own renders, for timing reference), payload.json.
  `events/` (the five curated storm timelines) is unpacked locally but gitignored; the look-back
  is fetched into the same `events/`
  directory, because `magneto.render` has no flag to read it from anywhere else.
- **Not included** (asked for in plan section 9): `requirements.txt`, `tests/`, `docs/`.
  `pipeline/requirements.txt` covers every third-party import (numpy, scipy, astropy, requests,
  geopack); the engine reads HAPI with its own `magneto/hapi_csv.py`.
- **Local diffs:** none. The one interface fix lives on our side: `pipeline/lookback.py` passes
  `--model t89` (the engine has no `--external` flag).
- **Reference timing:** the dome rendered `lookback-20261005-20261007` (288 steps at 10 min) in
  287 s, 2.7 GB of models (`render_manifests/`), on the dome machine's worker count.

To update: unpack a new payload over this directory, re-run the sha256 check against its
`payload.json`, update this file, then dispatch `data.yml` with `dry_run`.
