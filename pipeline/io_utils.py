"""Atomic writes, JSON conventions, staging/promote, HTTP helpers.

The whole run writes into ``<out>/.staging/`` and only promotes files at the
very end.  That matters because the manifest names binary files: a run that
died between "wrote manifest" and "wrote f12.bin" would leave the app fetching
a 404 forever.  Promotion moves only the files THIS run produced, so a
partially-degraded run never deletes last-good products it didn't regenerate.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional

from .config import HEADERS

JSON_ROUND = 6
PRECISE_ROUND = 17          # effectively "don't round" for a float64

# Values whose subtree must keep full double precision (none in this project;
# kept so the helper stays byte-compatible with sol's).
PRECISE_KEYS: frozenset = frozenset()


class PipelineError(RuntimeError):
    """Fatal, reportable pipeline problem (bad contract, oversized product)."""


# ─────────────────────────────────────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(dt: datetime) -> str:
    """RFC3339 UTC with a trailing Z and second precision."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def unix_s(dt: datetime) -> int:
    return int(dt.astimezone(timezone.utc).timestamp())


def parse_iso_z(s: str) -> Optional[datetime]:
    """Parse an ISO timestamp; a MISSING offset is read as UTC, never local.

    Every upstream this pipeline reads publishes UTC, and several of them omit
    the zone: NOAA's ``rtsw_wind_1m.json`` says ``"2026-09-02T15:23:00"`` and
    ``10cm-flux.json`` says ``"2026-09-01T20:00:00"``.  A naive datetime then
    reaches ``unix_s``, whose ``.astimezone()`` interprets it in the SYSTEM
    zone -- so on the Central-time workstation that publishes by hand every
    wind sample landed +5 h in the future.  Measured 2026-09-02: the served
    ``stats/summary.json`` was generated at 15:23:30Z and carried hourly wind
    bins out to 20:00Z, five points ahead of the clock, with a matching 5 h
    hole behind them; the poison was persistent, because
    ``_merge_wind_series`` writes the shifted keys into ``.cache/wind.json``
    and later runs merge on top of them.

    Fixing it HERE rather than at each call site is deliberate: this is the one
    function every product's timestamps pass through, and a naive datetime that
    escapes it silently corrupts ``unix_s``, ``age_hours`` and every ``>=``
    comparison downstream (Python raises on aware-vs-naive subtraction, so the
    failure mode is either a wrong number or a TypeError, never a warning).
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def age_hours(dt: datetime, now: Optional[datetime] = None) -> float:
    now = now or utcnow()
    return (now - dt).total_seconds() / 3600.0


# ─────────────────────────────────────────────────────────────────────────────
# Atomic write + JSON
# ─────────────────────────────────────────────────────────────────────────────

def atomic_write(path: Path, producer: Callable[[Path], Any]) -> Path:
    """Write ``path`` via a sibling ``.tmp`` + ``os.replace``.

    ``os.replace`` is atomic within a directory on both POSIX and Windows, so a
    reader either sees the old file or the complete new one -- never a partial
    write.  The temp file MUST be a sibling for that guarantee to hold.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    producer(tmp)
    os.replace(tmp, path)
    return path


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    return atomic_write(path, lambda p: p.write_bytes(data))


def _round_floats(obj: Any, nd: int = JSON_ROUND) -> Any:
    """Recursively round floats so products are byte-stable and compact.

    Also normalizes numpy scalars (json can't encode np.float32) and turns
    non-finite values into None -- NaN/Infinity are not legal JSON and would
    silently break ``JSON.parse`` in the browser.
    """
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return None
        r = round(obj, nd)
        return 0.0 if r == 0 else r
    if isinstance(obj, dict):
        return {k: _round_floats(v, PRECISE_ROUND if k in PRECISE_KEYS else nd)
                for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_round_floats(v, nd) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            return _round_floats(obj.item(), nd)
        except Exception:                                   # pragma: no cover
            return obj
    return obj


def json_dumps(obj: Any) -> str:
    return json.dumps(_round_floats(obj), sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def write_json(path: Path, obj: Any) -> Path:
    text = json_dumps(obj) + "\n"
    return atomic_write(path, lambda p: p.write_text(text, encoding="utf-8"))


def read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Staging
# ─────────────────────────────────────────────────────────────────────────────

class Staging:
    """A ``<out>/.staging`` tree that is promoted into ``<out>`` on success."""

    def __init__(self, out_dir: Path) -> None:
        self.out = Path(out_dir)
        self.dir = self.out / ".staging"
        self.produced: List[Path] = []            # paths relative to staging

    def reset(self) -> None:
        import shutil
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, rel: str) -> Path:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def note(self, rel: str) -> None:
        r = Path(rel)
        if r not in self.produced:
            self.produced.append(r)

    def write_json(self, rel: str, obj: Any) -> Path:
        p = write_json(self.path(rel), obj)
        self.note(rel)
        return p

    def write_bytes(self, rel: str, data: bytes) -> Path:
        p = atomic_write_bytes(self.path(rel), data)
        self.note(rel)
        return p

    def rollback(self, prefix: str) -> int:
        """Un-stage everything under ``prefix`` (a product that failed).

        Without this, a run that wrote ``pfss/topology.bin`` and then died
        would promote that one file and leave the SERVED manifest pointing at a
        topology from a different vertex plan -- a silently corrupt pairing.
        """
        keep, dropped = [], 0
        for rel in self.produced:
            if str(rel).replace("\\", "/").startswith(prefix):
                quiet_unlink(self.dir / rel)
                dropped += 1
            else:
                keep.append(rel)
        self.produced = keep
        return dropped

    def cleanup(self) -> None:
        """Remove the staging tree unconditionally (safe to call twice).

        Matters because ``.staging`` lives INSIDE the published directory: a
        leftover from a crashed run would otherwise be rsync'd to gh-pages.
        """
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def promote(self) -> List[Path]:
        """Move every produced file into ``out``, replacing in place.

        Only files this run produced are touched; anything else already served
        from ``out`` (e.g. last run's ephemeris when Horizons was down) stays.
        """
        moved = []
        for rel in self.produced:
            src = self.dir / rel
            dst = self.out / rel
            if not src.exists():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dst)
            moved.append(dst)
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)
        return moved


# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def _ipv4_only():
    """Hide AAAA records from the resolver for the duration of a call.

    Exists for one measured problem: kauai.ccmc.gsfc.nasa.gov (DONKI) publishes
    an AAAA record that black-holes, and getaddrinfo returns it FIRST.  curl
    shrugs this off because it implements Happy Eyeballs (RFC 8305) and races
    the two families; urllib walks the list in order and eats the whole TCP
    connect timeout.  Measured 2026-08-23, same URL, same machine:

        IPv6 2001:4d0:2418:1198::44   connect FAILED after 21.06 s
        IPv4 169.154.198.44           connect OK       in   0.03 s
        curl 0.87 s   vs   urllib 21.8 s

    Two fetches per run is 42 s of a ~4 min CI budget spent on a socket that
    was never going to open.

    Patching getaddrinfo rather than subclassing HTTPSConnection because the
    connection-class route silently did not take (urllib built the handler but
    never called the override), and this is the technique that is actually
    reliable across Python versions.  Single-threaded pipeline, so the global
    patch is safe; the `finally` restores it even on error.

    Checked at the same time: services.swpc.noaa.gov and sdo.gsfc.nasa.gov also
    resolve v6-first and are FINE (0.1-0.4 s), so this stays OPT-IN rather than
    becoming the default for every source.  If a host has no A record at all
    the original resolver result is used, so an IPv6-only network still works.
    """
    real = socket.getaddrinfo

    def ipv4_first(host, port, family=0, type=0, proto=0, flags=0):
        infos = real(host, port, family, type, proto, flags)
        v4 = [i for i in infos if i[0] == socket.AF_INET]
        return v4 or infos

    socket.getaddrinfo = ipv4_first
    try:
        yield
    finally:
        socket.getaddrinfo = real


def http_get_full(url: str, timeout: float = 30.0, prefer_ipv4: bool = False
                  ) -> "tuple[bytes, dict]":
    """(body, lowercased response headers).

    The headers matter for the SDO ``latest_*.jpg`` fallback: that URL carries
    no timestamp in its name, so ``Last-Modified`` is the only honest source
    for the observation time.  (The browser cannot read it -- sdo.gsfc has no
    CORS -- but a server-side fetch can.)

    ``prefer_ipv4`` works around a host whose AAAA record black-holes; see
    _ipv4_only.  Opt-in, because it is a real (if small) loss of function.
    """
    req = urllib.request.Request(url, headers=HEADERS)
    with contextlib.ExitStack() as stack:
        if prefer_ipv4:
            stack.enter_context(_ipv4_only())
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), {k.lower(): v for k, v in resp.headers.items()}


def http_get(url: str, timeout: float = 30.0) -> bytes:
    return http_get_full(url, timeout)[0]


def http_size(url: str, timeout: float = 15.0) -> Optional[int]:
    """Byte size of a remote file, or None if it is not there.

    Exists so ``validate --url`` can assert that every file a manifest names
    is actually served without downloading it: the published texture tree is
    ~28 MB across 110 files, and confirming presence is not worth 28 MB.

    HEAD first -- GitHub Pages answers it with a real ``Content-Length``.  The
    fallback is a one-byte ranged GET, because a HEAD is the request origins
    are most likely to refuse (CCMC DONKI 403s a HEAD and 200s the identical
    GET, per the data-sources notes), and a 206 carries ``Content-Range``
    whose total after the slash is the true size.  A 200 to the ranged request
    means the origin ignored the range, so the body length is the size.

    None means "could not be confirmed present", which is what the caller
    reports -- deliberately NOT distinguished from a 404, because for a
    publish decision an unreachable file and a missing one have the same
    consequence for the guest.
    """
    req = urllib.request.Request(url, headers=HEADERS, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            n = resp.headers.get("content-length")
            if n is not None:
                return int(n)
            return len(resp.read())
    except Exception:                                          # noqa: BLE001
        pass
    req = urllib.request.Request(
        url, headers=dict(HEADERS, Range="bytes=0-0"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rng = resp.headers.get("content-range") or ""
            total = rng.rpartition("/")[2].strip()
            if total.isdigit():
                return int(total)
            body = resp.read()
            if resp.status == 200:
                return len(body)
            # A 206 with no parseable Content-Range still proves the file is
            # there and non-empty, which is all this check is for.
            return len(body) or None
    except Exception:                                          # noqa: BLE001
        return None


def http_get_text(url: str, timeout: float = 30.0) -> str:
    return http_get(url, timeout).decode("utf-8", errors="replace")


def http_get_json(url: str, timeout: float = 30.0) -> Any:
    return json.loads(http_get_text(url, timeout))


def quiet_unlink(*paths: Optional[Path]) -> None:
    """Best-effort delete; ignore missing / locked files (Windows sharing)."""
    for p in paths:
        try:
            if p is not None and Path(p).exists():
                Path(p).unlink()
        except OSError:
            pass


def prune_dirs(parent: Path, keep: int) -> List[Path]:
    """Keep the ``keep`` most recently modified subdirectories of ``parent``."""
    import shutil
    parent = Path(parent)
    if not parent.is_dir():
        return []
    subs = sorted((p for p in parent.iterdir() if p.is_dir()),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    removed = []
    for p in subs[keep:]:
        shutil.rmtree(p, ignore_errors=True)
        removed.append(p)
    return removed


def human_bytes(n: int) -> str:
    if n < 1024:
        return "{0} B".format(n)
    if n < 1024 * 1024:
        return "{0:.1f} KB".format(n / 1024.0)
    return "{0:.2f} MB".format(n / (1024.0 * 1024.0))


def iter_files(root: Path) -> Iterable[Path]:
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            yield p
