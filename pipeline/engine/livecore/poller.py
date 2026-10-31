"""Background poller for the SWPC endpoints - the only thing that touches HTTP.

Design rules from the brief's shared live-data architecture:

  * Poll on ONE background timer thread, never on a render call or keypress.
  * Every successful fetch lands on disk (CacheStore) before anyone reads it.
  * The thread is unkillable by network weather: every exception is logged
    and swallowed; the schedule just moves on to the next due time.

Polling cadence comes from the endpoint tier (tier1=60 s, tier2=300 s,
tier3=3600 s) with +/-10% jitter so we never sync up with anyone else's
minute boundary. Conditional GET (If-None-Match / If-Modified-Since) is used
whenever validators were seen before; a 304 refreshes the cache freshness
timestamp without rewriting the payload. If a 200 arrives with neither ETag
nor Last-Modified we log that once and stretch a tier-1 endpoint to 120 s -
no point hammering a server that makes us re-download 1.4 MB every time.

Ovation and Kp payloads additionally get a timestamped snapshot whenever
their content actually changes (validation gate A3 needs a short history of
aurora ovals against Kp); snapshots are pruned after 14 days.

CLI:
  python -m livecore.poller --once            single pass over all endpoints
  python -m livecore.poller --soak 5          run the thread for 5 minutes
  python -m livecore.poller --record          freeze raw payloads as fixtures
  python -m livecore.poller --cache-dir DIR   cache root (default <repo>/cache)
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests

from .cache import CacheStore, write_atomic
from .endpoints import ENDPOINTS, fetch

log = logging.getLogger(__name__)

TIER_INTERVAL_S = {1: 60.0, 2: 300.0, 3: 3600.0}
STRETCHED_TIER1_S = 120.0  # tier-1 fallback when no validators are offered
SNAPSHOT_KEYS = ("ovation", "kp")
REQUEST_TIMEOUT_S = 10
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"
FIXTURES_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "payloads"


class Poller:
    """One background thread polling every registered endpoint on schedule."""

    def __init__(self, cache: CacheStore, keys=None, session=None):
        self.cache = cache
        self.keys = list(keys or ENDPOINTS)
        self.session = session or requests.Session()
        self.poll_counts: Counter = Counter()   # 200s + 304s per key
        self.error_counts: Counter = Counter()
        self._due = {k: 0.0 for k in self.keys}  # monotonic; 0 = due now
        self._stretched: set[str] = set()        # tier-1 keys without validators
        self._no_validator_logged: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _interval_s(self, key: str) -> float:
        ep = ENDPOINTS[key]
        base = TIER_INTERVAL_S[ep.tier]
        if ep.tier == 1 and key in self._stretched:
            base = STRETCHED_TIER1_S
        return base * random.uniform(0.9, 1.1)

    def poll_one(self, key: str, conditional: bool = True) -> int:
        """Fetch one endpoint into the cache. Returns the HTTP status.

        Raises on network / HTTP / JSON failure - callers decide whether to
        swallow (the thread loop does, always).
        """
        ep = ENDPOINTS[key]
        http_meta = self.cache.load_http_meta()
        stored = http_meta.get(ep.url, {}) if conditional else {}
        status, raw, etag, last_modified = fetch(
            key, self.session, timeout=REQUEST_TIMEOUT_S,
            etag=stored.get("etag"), last_modified=stored.get("last_modified"))

        if status == 304:
            self.cache.touch(key)
            log.info("%s: 304 not modified (freshness touched)", key)
            return status

        json.loads(raw)  # never cache a torn / non-JSON body
        changed = self.cache.put(key, raw, {
            "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "etag": etag, "last_modified": last_modified,
            "url": ep.url, "status": status,
        })
        if changed and key in SNAPSHOT_KEYS:
            self.cache.snapshot(key)

        if etag or last_modified:
            http_meta[ep.url] = {"etag": etag, "last_modified": last_modified}
            self.cache.save_http_meta(http_meta)
        elif key not in self._no_validator_logged:
            self._no_validator_logged.add(key)
            if ep.tier == 1:
                self._stretched.add(key)
                log.info("%s: no ETag/Last-Modified offered; "
                         "stretching interval to %.0f s", key, STRETCHED_TIER1_S)
            else:
                log.info("%s: no ETag/Last-Modified offered "
                         "(tier %d, cadence unchanged)", key, ep.tier)

        log.info("%s: %d %s (%d bytes)", key, status,
                 "changed" if changed else "unchanged", len(raw))
        return status

    def run_once(self) -> bool:
        """Single pass over every endpoint. Returns True if all succeeded."""
        ok = True
        for key in self.keys:
            try:
                self.poll_one(key)
                self.poll_counts[key] += 1
            except Exception:
                ok = False
                self.error_counts[key] += 1
                log.exception("%s: poll failed", key)
        return ok

    # -- background thread -------------------------------------------------

    def start(self) -> None:
        self.cache.prune(days=14)
        self._stop.clear()
        self._first = threading.Event()
        self._thread = threading.Thread(target=self._loop,
                                        name="livecore-poller", daemon=True)
        self._thread.start()

    def wait_first(self, timeout_s: float) -> bool:
        """Block until every endpoint has been tried once (hit or miss), or timeout_s.

        The daemon's first decide() used to run against whatever was on DISK the
        instant the thread started - an 11.8 h-old cache on 2026-09-21 - and chose
        the stored-event fallback although every SWPC endpoint answered 200 within
        the next 0.7 s. A first cycle that waits for the first sweep decides on data.
        """
        ev = getattr(self, "_first", None)
        if ev is None:
            return False
        return bool(ev.wait(timeout_s))

    def stop(self, join_timeout: float = 15.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(join_timeout)
            self._thread = None

    def _loop(self) -> None:
        log.info("poller thread up; endpoints: %s", ", ".join(self.keys))
        while not self._stop.is_set():
            now = time.monotonic()
            for key in self.keys:
                if now < self._due[key]:
                    continue
                try:
                    self.poll_one(key)
                    self.poll_counts[key] += 1
                except Exception:
                    # Network weather must never kill the thread.
                    self.error_counts[key] += 1
                    log.exception("%s: poll failed (will retry on schedule)",
                                  key)
                finally:
                    self._due[key] = time.monotonic() + self._interval_s(key)
            first = getattr(self, "_first", None)
            if first is not None and not first.is_set() and all(
                    self.poll_counts[k] + self.error_counts[k] > 0 for k in self.keys):
                first.set()
            self._stop.wait(1.0)
        log.info("poller thread down; polls=%s errors=%s",
                 dict(self.poll_counts), dict(self.error_counts))


def record_fixtures(session=None, out_dir: Path = FIXTURES_DIR) -> bool:
    """Fetch each endpoint once (unconditional) and freeze the raw payloads
    as tests/fixtures/payloads/<key>.json for offline parser tests."""
    session = session or requests.Session()
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for key in ENDPOINTS:
        try:
            status, raw, _etag, _lm = fetch(key, session,
                                            timeout=REQUEST_TIMEOUT_S)
            json.loads(raw)
            write_atomic(out_dir / f"{key}.json", raw)
            log.info("recorded %s (%d, %d bytes)", key, status, len(raw))
        except Exception:
            ok = False
            log.exception("record failed for %s", key)
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m livecore.poller", description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true",
                    help="single pass over all endpoints, then exit")
    ap.add_argument("--soak", type=float, metavar="MINUTES",
                    help="run the background thread for MINUTES, then exit")
    ap.add_argument("--record", action="store_true",
                    help="freeze raw payloads into tests/fixtures/payloads/")
    ap.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR),
                    help="cache root directory (default: %(default)s)")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.record:
        return 0 if record_fixtures() else 1

    poller = Poller(CacheStore(args.cache_dir))
    if args.once:
        return 0 if poller.run_once() else 1
    if args.soak is not None:
        poller.start()
        try:
            time.sleep(args.soak * 60.0)
        finally:
            poller.stop()
        log.info("soak done; polls=%s errors=%s",
                 dict(poller.poll_counts), dict(poller.error_counts))
        return 0

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
