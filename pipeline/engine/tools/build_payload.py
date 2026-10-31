#!/usr/bin/env python
r"""Build <package>\MagnetoEngine.zip - the Python engine as Digistar's packager can carry it.

Digistar's content packager ships a .py only FLAT in the package's Scripts\ folder (by file name,
so five __init__.py collapse into one) and drops .vbs/.bat/.ps1/.whl; a .zip it copies to the
package root unchanged. So the engine travels as this one archive,
declared in Scripts\magnetoPythonFiles.ds (`py play ...\MagnetoEngine.zip`), and Scripts\mag_setup.py
unpacks it to <package>\Engine\ after the operator agrees to the install.

What goes in (paths inside the zip are relative to Engine\):

    magneto\ livecore\ dsexport\ webexport\   the packages, every .py
    tools\*.py                                 the tools the kickoffs and the installer run
    vendor\install_igrf.py, vendor\igrf_coeffs\igrf*coeffs.txt   (geopack ships none)
    events\<storm>\event.json, timeline.jsonl  the five curated storms (storm re-renders and the
                                               live daemon's fallback event read them)
    render_manifests\<event_id>.json           each SHIPPED render's Models\event_frames.json:
                                               an installed dome has no Renders\ tree, and the
                                               shim writer needs them (write_shims.manifest_path)
    payload.json                               the build stamp + sha256 and size of every file

Never: tests, caches, .venv, raw downloads, look-back timelines (Rebuild Look-back makes its own).
The archive is deterministic (sorted names, fixed timestamps, explicit directory entries), so the
same tree always gives the same bytes and `--check` can tell whether the shipped zip is current.

    python tools\build_payload.py            build (prints the sha256)
    python tools\build_payload.py --check    exit 1 when the zip is missing or differs from the tree
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from magneto import events, paths                            # noqa: E402

ZIP_NAME = "MagnetoEngine.zip"
PAYLOAD_JSON = "payload.json"
PACKAGES = ("magneto", "livecore", "dsexport", "webexport")
STAMP = (2026, 1, 1, 0, 0, 0)          # every entry's timestamp: identical trees -> identical bytes
SKIP_DIRS = {"__pycache__", ".pytest_cache"}


def zip_path() -> Path:
    return paths.PACKAGE / ZIP_NAME


def _py_files(folder: Path):
    for p in sorted(folder.rglob("*.py")):
        if not SKIP_DIRS.intersection(p.relative_to(ENGINE).parts):
            yield p


def collect() -> dict[str, bytes]:
    """{zip name (forward slashes, relative to Engine\\): bytes} - everything the payload carries."""
    out: dict[str, bytes] = {}

    def add(arc: str, data: bytes) -> None:
        if arc in out:
            raise RuntimeError(f"{arc} collected twice")
        out[arc] = data

    for pkg in PACKAGES:
        for p in _py_files(ENGINE / pkg):
            add(p.relative_to(ENGINE).as_posix(), p.read_bytes())
    for p in sorted((ENGINE / "tools").glob("*.py")):
        add(f"tools/{p.name}", p.read_bytes())
    add("vendor/install_igrf.py", (ENGINE / "vendor" / "install_igrf.py").read_bytes())
    coeffs = sorted((ENGINE / "vendor" / "igrf_coeffs").glob("igrf*coeffs.txt"))
    if not coeffs:
        raise RuntimeError("vendor\\igrf_coeffs holds no igrf*coeffs.txt - geopack would need the network")
    for p in coeffs:
        add(f"vendor/igrf_coeffs/{p.name}", p.read_bytes())
    for eid in events.EVENTS:
        for name in ("event.json", "timeline.jsonl"):
            p = events.EVENTS_DIR / eid / name
            if not p.exists():
                raise RuntimeError(f"{p} is missing - run: python -m magneto.events fetch {eid}")
            add(f"events/{eid}/{name}", p.read_bytes())

    from tools import write_shims as ws                       # the shim writer's own idea of "shipped"
    for eid in ws.rendered_ids():
        m = ws.manifest_path(eid)
        if not m.exists():
            raise RuntimeError(f"{eid} is rendered but has no manifest at {m}")
        add(f"render_manifests/{eid}.json", m.read_bytes())
    return out


def _dirs(names) -> list[str]:
    seen = set()
    for n in names:
        parts = n.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            seen.add("/".join(parts[:i]) + "/")
    return sorted(seen)


def build_bytes(files: dict[str, bytes]) -> tuple[bytes, dict]:
    manifest = {
        "schema": 1,
        "what": "Live Magnetosphere engine payload - unpacked to <package>\\Engine by Scripts\\mag_setup.py",
        "physics_version": _physics_version(),
        "files": {n: {"sha256": hashlib.sha256(b).hexdigest(), "size": len(b)} for n, b in sorted(files.items())},
    }
    # the build id is a digest of the CONTENT (not a clock), so an unchanged tree keeps its id
    manifest["build_id"] = hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode()).hexdigest()[:16]
    body = json.dumps(manifest, indent=1, sort_keys=True).encode("ascii")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d in _dirs(list(files) + [PAYLOAD_JSON]):          # explicit directory entries
            z.writestr(zipfile.ZipInfo(d, STAMP), b"")
        for n in sorted(files):
            zi = zipfile.ZipInfo(n, STAMP)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, files[n])
        zi = zipfile.ZipInfo(PAYLOAD_JSON, STAMP)
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, body)
    return buf.getvalue(), manifest


def _physics_version():
    try:
        from magneto.model import PHYSICS_VERSION
        return PHYSICS_VERSION
    except Exception:
        return None


def build(dest: Path | None = None) -> tuple[Path, dict]:
    files = collect()
    data, manifest = build_bytes(files)
    dest = Path(dest) if dest else zip_path()
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    with zipfile.ZipFile(tmp) as z:                           # verify the artifact, not the intent
        bad = z.testzip()
        if bad:
            raise RuntimeError(f"{tmp}: corrupt entry {bad}")
        if json.loads(z.read(PAYLOAD_JSON))["build_id"] != manifest["build_id"]:
            raise RuntimeError("payload.json did not round-trip")
    tmp.replace(dest)
    return dest, manifest


def check(dest: Path | None = None) -> tuple[bool, str]:
    """Is the zip on disk exactly what build() would write now?"""
    dest = Path(dest) if dest else zip_path()
    if not dest.exists():
        return False, f"{dest.name} is missing - run: python Engine\\tools\\build_payload.py"
    want, manifest = build_bytes(collect())
    have = dest.read_bytes()
    if have == want:
        return True, f"{dest.name} is current (build {manifest['build_id']}, {len(manifest['files'])} files)"
    try:
        with zipfile.ZipFile(dest) as z:
            old = json.loads(z.read(PAYLOAD_JSON))["files"]
    except Exception:
        old = {}
    changed = sorted(n for n in set(old) | set(manifest["files"]) if old.get(n) != manifest["files"].get(n))
    return False, (f"{dest.name} is STALE - {len(changed)} file(s) differ, e.g. {', '.join(changed[:5])}; "
                   f"run: python Engine\\tools\\build_payload.py")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when the zip is missing or stale")
    ap.add_argument("--out", default=None, help="write here instead of <package>\\MagnetoEngine.zip")
    a = ap.parse_args(argv)
    if a.check:
        ok, msg = check(a.out)
        print(msg)
        return 0 if ok else 1
    dest, manifest = build(a.out)
    size = dest.stat().st_size
    print(f"{dest}  {size:,} bytes  {len(manifest['files'])} files  build {manifest['build_id']}  "
          f"sha256 {hashlib.sha256(dest.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
