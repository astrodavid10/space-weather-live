"""Constants shared by every pipeline module. No logic here."""

from __future__ import annotations

from pathlib import Path

from . import PIPELINE_VERSION

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ENGINE_DIR = HERE / "engine"          # the vendored dome Engine snapshot (see engine/SNAPSHOT.md)
WORK_DIR = HERE / ".work"             # gitignored: renders, fetched events, logs
CACHE_DIR = HERE / ".cache"           # gitignored: HAPI probes, raw responses

SITE_URL = "https://astrodavid10.github.io/space-weather-live"

USER_AGENT = "space-weather-live/%s (+https://github.com/astrodavid10/space-weather-live)" % PIPELINE_VERSION
HEADERS = {"User-Agent": USER_AGENT}

SCHEMA_INDEX = "swl.index/1"
SCHEMA_LOOKBACK = "swl.lookback/1"
SCHEMA_STORMS = "swl.storms/1"
BUNDLE_SCHEMA = 1                     # the engine's own bundle manifest schema

# Freshness. 6 h cadence + 3 h of measured cron slop (sol: median 74 min late,
# worst gaps 13 h). data_end is the newest EPOCH in the look-back, which lags
# the wall clock by however far behind CCMC ISWA is -- measured on run #1.
STALE_AFTER_HOURS = 9.0
DATA_END_STALE_HOURS = 30.0

PRESETS = ("mobile", "desktop")
WEB_LAYERS = ("FieldLines", "MagnetopauseShell", "BowShockShell", "RingCurrent",
              "Plasmasphere", "IMFLines", "WindParticles", "AuroraOval")

# Web budgets for the gzipped geometry (WARN, not FAIL: a bigger bundle is slower, not wrong).
GZ_BUDGET_BYTES = {"mobile": 4 * 1024 * 1024, "desktop": 16 * 1024 * 1024}

# Dome show keys and labels (Engine/magneto/presets.py EVENT_KEYS / EVENT_LABELS),
# plus the one-line "why it is in the show" from docs/CREDITS.html.
STORMS = {
    "2000-07-15_bastille":   {"key": "bastille", "label": "Bastille Day 2000",
                              "why": "Two magnetic clouds and their shocks drive the boundary far inside its usual stand."},
    "2003-11-20_superstorm": {"key": "nov2003", "label": "Nov 2003 superstorm",
                              "why": "The largest geomagnetic storm of solar cycle 23."},
    "2024-05-10_gannon":     {"key": "gannon", "label": "Gannon May 2024",
                              "why": "The first severe (G5) storm in two decades."},
    "2024-10-10_g4":         {"key": "octg4", "label": "Oct 2024 G4",
                              "why": "Sympathetic eruptions; aurora reached the Gulf Coast."},
    "2026-01-19_kp9":        {"key": "jan2026", "label": "Jan 2026 Kp9",
                              "why": "Too recent for a published overview - replayed straight from the record."},
}
