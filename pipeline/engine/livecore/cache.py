"""Disk cache for live payloads: the 'most recent good payload is always on disk'.

Layout under the cache root:

  cache/<key>/latest.json          - raw payload bytes of the last good fetch
  cache/<key>/latest.meta.json     - sidecar: fetched (UTC), etag,
                                     last_modified, sha256, url, status
  cache/<key>/YYYYMMDDTHHMMSSZ.json - timestamped snapshots (ovation + kp
                                     only, for the gate-A3 aurora/Kp history)
  cache/http_meta.json             - ETag / Last-Modified per URL for
                                     conditional GET

All writes are atomic (tmp file + os.replace) so a crash mid-write can never
leave a torn payload for the renderer to trip over. On a 304 the poller calls
touch() to refresh the fetched timestamp without rewriting the payload.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_SNAPSHOT_RE = re.compile(r"^(\d{8}T\d{6}Z)\.json$")
_SNAPSHOT_FMT = "%Y%m%dT%H%M%SZ"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atomic(path, data: bytes) -> None:
    """Write bytes to path via a same-directory tmp file + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


class CacheStore:
    """One directory per endpoint key; see module docstring for layout."""

    def __init__(self, root_dir):
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, key: str) -> Path:
        return self.root / key

    def _meta_path(self, key: str) -> Path:
        return self._dir(key) / "latest.meta.json"

    def _read_meta(self, key: str) -> dict | None:
        try:
            with open(self._meta_path(key), encoding="ascii") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def put(self, key: str, raw_bytes: bytes, meta: dict) -> bool:
        """Store a payload atomically; returns True if the content changed.

        meta should carry etag / last_modified / url / status from the fetch;
        'fetched' (UTC ISO) and 'sha256' are filled in here if absent.
        """
        sha = hashlib.sha256(raw_bytes).hexdigest()
        prev = self._read_meta(key)
        changed = prev is None or prev.get("sha256") != sha
        full = dict(meta)
        full.setdefault("fetched", _iso(_utcnow()))
        full["sha256"] = sha
        write_atomic(self._dir(key) / "latest.json", raw_bytes)
        write_atomic(self._meta_path(key),
                     json.dumps(full, indent=1).encode("ascii"))
        return changed

    def touch(self, key: str, now: datetime | None = None) -> None:
        """Refresh the fetched timestamp after a 304 - payload untouched."""
        meta = self._read_meta(key)
        if meta is None:
            return
        meta["fetched"] = _iso(now or _utcnow())
        meta["status"] = 304
        write_atomic(self._meta_path(key),
                     json.dumps(meta, indent=1).encode("ascii"))

    def latest(self, key: str, now: datetime | None = None):
        """Return (payload, meta, age_s) for the last good fetch.

        payload is the parsed JSON (raw bytes if it will not parse); age_s
        is seconds since the fetched timestamp. (None, None, None) when the
        key has never been cached.
        """
        meta = self._read_meta(key)
        path = self._dir(key) / "latest.json"
        try:
            raw = path.read_bytes()
        except OSError:
            return None, None, None
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = raw
        age_s = None
        if meta and meta.get("fetched"):
            try:
                fetched = datetime.strptime(
                    meta["fetched"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc)
                age_s = ((now or _utcnow()) - fetched).total_seconds()
            except ValueError:
                pass
        return payload, meta, age_s

    def snapshot(self, key: str, now: datetime | None = None) -> Path | None:
        """Timestamped copy of latest.json (ovation + kp history only)."""
        path = self._dir(key) / "latest.json"
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        name = (now or _utcnow()).strftime(_SNAPSHOT_FMT) + ".json"
        dest = self._dir(key) / name
        write_atomic(dest, raw)
        return dest

    def prune(self, days: int = 14, now: datetime | None = None) -> int:
        """Delete snapshots older than `days` across all keys; returns count."""
        cutoff = (now or _utcnow()) - timedelta(days=days)
        removed = 0
        for keydir in self.root.iterdir():
            if not keydir.is_dir():
                continue
            for f in keydir.iterdir():
                m = _SNAPSHOT_RE.match(f.name)
                if not m:
                    continue
                try:
                    ts = datetime.strptime(m.group(1), _SNAPSHOT_FMT).replace(
                        tzinfo=timezone.utc)
                except ValueError:
                    continue
                if ts < cutoff:
                    try:
                        f.unlink()
                        removed += 1
                    except OSError as exc:
                        log.warning("prune: could not remove %s: %s", f, exc)
        return removed

    # -- conditional-GET validators, per URL ------------------------------

    def load_http_meta(self) -> dict:
        """{url: {'etag': ..., 'last_modified': ...}} from cache/http_meta.json."""
        try:
            with open(self.root / "http_meta.json", encoding="ascii") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def save_http_meta(self, meta: dict) -> None:
        write_atomic(self.root / "http_meta.json",
                     json.dumps(meta, indent=1).encode("ascii"))
