"""One-time storm publish: dome bundle zip -> dist-data/{storms,lookback} + index.json.

    conda run -n magneto python scripts/publish_storms.py --zip LiveMagnetosphere_web_bundle_2026-09-22.zip

then (Git Bash):

    GITHUB_TOKEN="$(gh auth token)" GITHUB_REPOSITORY=astrodavid10/space-weather-live \
      GITHUB_SHA="$(git rev-parse HEAD)" bash scripts/publish_gh_pages.sh dist-data data
    gh release create data-2026-09-22 LiveMagnetosphere_web_bundle_2026-09-22.zip

The storms are TS05 renders CI cannot rebuild, so the zip is kept as a GitHub
Release asset and data.yml never republishes data/storms.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.index import build_index  # noqa: E402
from pipeline.storms import build  # noqa: E402
from pipeline.validate import validate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", required=True)
    ap.add_argument("--out", default="dist-data")
    ap.add_argument("--no-lookback", action="store_true", help="do not seed data/lookback from the zip")
    ap.add_argument("--release-tag", default="data-2026-09-22")
    a = ap.parse_args()
    build(Path(a.zip), Path(a.out), with_lookback=not a.no_lookback, release_tag=a.release_tag)
    build_index(Path(a.out))
    rep = validate(root=a.out)
    print(rep.text())
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
