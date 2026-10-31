"""Copy the vendored IGRF coefficient files into geopack's package directory.

Why this exists: geopack scrapes www.ngdc.noaa.gov at import time to download
igrf*coeffs.txt files it doesn't have locally, and an HTTP 503 on any missing
file raises out of the import (observed 2026-08-11 - NOAA rate-limits the
plain-file fetches). Once every file the listing references exists locally,
geopack's downloader skips the network entirely and the import is offline-safe
- which is exactly what a show machine needs.

Run after (re)creating the conda env:

    conda run -n magneto python vendor/install_igrf.py
"""
import shutil
import sys
from pathlib import Path

import geopack

VENDOR = Path(__file__).resolve().parent / "igrf_coeffs"


def main() -> int:
    dest = Path(geopack.__file__).resolve().parent / "igrf_coeffs"
    dest.mkdir(exist_ok=True)
    copied = 0
    for src in sorted(VENDOR.glob("igrf*coeffs.txt")):
        target = dest / src.name
        if not target.exists() or target.stat().st_size != src.stat().st_size:
            shutil.copy2(src, target)
            copied += 1
    print(f"igrf_coeffs: {copied} file(s) copied to {dest}")
    from geopack import geopack as gp  # noqa: F401  (import proves init_igrf works)
    print("geopack import OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
