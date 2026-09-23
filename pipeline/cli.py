"""python -m pipeline <command>

    storms          lay the dome bundle zip out as data/storms (+ optional look-back seed)
    index           rebuild data/index.json from what is on disk
    validate        judge a data tree (--root DIR or --url URL); exit 1 on failure
    from-published  mirror the live data tree into public/data for front-end work
    lookback        fetch + render + export the 48 h look-back (needs pipeline/engine)

Exit-code contract (sol's): ``lookback`` exits 0 whenever it wrote an index,
degraded or not, so "the build step succeeded" means "the tree is publishable".
Health is read from index.json, not from $?.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _cmd_storms(a) -> int:
    from .index import build_index
    from .storms import build
    build(Path(a.zip), Path(a.out), with_lookback=a.with_lookback, release_tag=a.release_tag)
    idx = build_index(Path(a.out))
    print(json.dumps(idx["sources"], indent=1))
    return 0


def _cmd_index(a) -> int:
    from .index import build_index
    idx = build_index(Path(a.out), run_id=os.environ.get("GITHUB_RUN_ID"))
    print(json.dumps(idx["sources"], indent=1))
    return 0


def _cmd_validate(a) -> int:
    from .validate import validate
    rep = validate(root=a.root, base_url=a.url, verbose=a.verbose)
    print(rep.text())
    if not rep.ok or (a.strict and rep.warnings):
        return 1
    return 0


def _cmd_from_published(a) -> int:
    from .io_utils import atomic_write_bytes, http_get
    base = a.site.rstrip("/") + "/data/"
    out = Path(a.out)
    idx = json.loads(http_get(base + "index.json"))
    atomic_write_bytes(out / "index.json", json.dumps(idx, indent=1).encode())
    wanted = set(a.sources.split(","))
    for name in ("storms", "lookback"):
        if name not in wanted:
            continue
        sub = json.loads(http_get(base + name + "/index.json"))
        atomic_write_bytes(out / name / "index.json", json.dumps(sub, indent=1).encode())
        rows = sub.get("storms") if name == "storms" else [sub]
        for row in rows or []:
            for entry in (row.get("presets") or {}).values():
                for rel in (entry["json_url"], entry["bin_gz_url"]):
                    print("  " + name + "/" + rel)
                    atomic_write_bytes(out / name / rel, http_get(base + name + "/" + rel))
    return 0


def _cmd_lookback(a) -> int:
    from .lookback import run
    return run(a)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("storms", help="publish-layout the dome bundle zip")
    s.add_argument("--zip", required=True)
    s.add_argument("--out", default="dist-data")
    s.add_argument("--with-lookback", action="store_true")
    s.add_argument("--release-tag", default=None)
    s.set_defaults(fn=_cmd_storms)

    s = sub.add_parser("index", help="rebuild index.json")
    s.add_argument("--out", default="dist-data")
    s.set_defaults(fn=_cmd_index)

    s = sub.add_parser("validate", help="validate a data tree")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--root")
    g.add_argument("--url")
    s.add_argument("--strict", action="store_true", help="warnings are fatal")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=_cmd_validate)

    s = sub.add_parser("from-published", help="mirror the live data tree")
    s.add_argument("--out", default="public/data")
    s.add_argument("--site", default="https://astrodavid10.github.io/space-weather-live")
    s.add_argument("--sources", default="storms,lookback")
    s.set_defaults(fn=_cmd_from_published)

    s = sub.add_parser("lookback", help="fetch + render + export the look-back (needs pipeline/engine)")
    s.add_argument("--out", default="dist-data")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--presets", default="mobile,desktop")
    s.add_argument("--days", type=float, default=2.0)
    s.add_argument("--end", default=None, help="pin the window end, ISO-8601 Z")
    s.add_argument("--feed", default="auto", choices=("auto", "ace", "rtsw"))
    s.add_argument("--force", action="store_true")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=_cmd_lookback)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
