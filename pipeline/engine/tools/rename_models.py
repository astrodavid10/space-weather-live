r"""Give every model file a basename unique across the whole package.

WHY
---
Digistar's .dsz packager does not preserve a render's directory. Every packaged model lands
somewhere under a single `Models\` root, and the safe reading of the evidence is that it may be
placed in a folder named after the file itself (`Models\<name>\<name>.<ext>` - the shape used by
every packaged house package on this box). Today all six renders contain the SAME basenames:

    Renders\storm_2024-05-10_gannon\Models\FieldLines_0000.vlc
    Renders\lookback\Models\FieldLines_0000.vlc          <- identical name, different geometry

so packaging them together collapses six renders onto one set and five storms would silently
play the wrong geometry. Prefixing the basename with the event key removes the collision under
EVERY reading of the packager's behaviour, which is why it is worth the churn while the
round-trip test (package -> unpack elsewhere -> load) is still unrun. Nobody can run that test
from here; it is a UI-only operation.

WHAT IT TOUCHES
---------------
The manifest is the authority, not a glob: every name comes from `layers[].files` plus the
`shells` block a library-shell render carries. That way a layer this tool has never heard of
still gets renamed, and a stray file that is not part of the render is left alone.

Renaming the files is only half of it - the scripts still name the old ones - so this tool
rewrites the manifest and then RE-EMITS every script through tools/retime_event.py at the
render's own step, which is a seconds-long text operation and never a re-render.

IDEMPOTENCE
-----------
A second pass would prefix the prefix. Every converted manifest is stamped with
`model_basename_prefix`, and a stamped render is skipped, so this is safe to re-run over a tree
that is half converted - the realistic case if a render lands while it is being used.

    python -m tools.rename_models --root <render>     # one render
    python -m tools.rename_models --all               # every render under Renders\
    python -m tools.rename_models --all --dry-run     # report only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from magneto import paths, presets                       # noqa: E402

STAMP = "model_basename_prefix"
MANIFEST = "event_frames.json"


def _manifest_path(root: Path) -> Path:
    return root / "Models" / MANIFEST


def prefix_for(event_id: str) -> str:
    """'2024-05-10_gannon' -> 'gannon_'. Short, stable, and already the panel's vocabulary."""
    return f"{presets.event_key(str(event_id))}_"


def _file_lists(man: dict):
    """Every (container, key) holding a list of model basenames, manifest-driven."""
    out = []
    for lay in man.get("layers", []):
        if isinstance(lay.get("files"), list):
            out.append((lay, "files"))
    sh = man.get("shells")
    if isinstance(sh, dict):
        for v in sh.values():
            if isinstance(v, dict) and isinstance(v.get("files"), list):
                out.append((v, "files"))
    return out


def rename_root(root: Path, dry_run: bool = False) -> dict:
    root = Path(root)
    mp = _manifest_path(root)
    if not mp.exists():
        return {"skipped": "no manifest"}
    man = json.loads(mp.read_text(encoding="utf-8"))
    if man.get(STAMP):
        return {"skipped": f"already prefixed '{man[STAMP]}'"}

    pre = prefix_for(man["event_id"])
    models = root / "Models"
    lists = _file_lists(man)

    # The tree may be in EITHER layout, and the MANIFEST decides which one is real -
    # never the filesystem, and never a fixed preference. Two ways this went wrong:
    #
    #   1. The first version looked only at the flat path. In a convention tree it found
    #      nothing, renamed nothing, and stamped the manifest anyway, so the manifest
    #      named gannon_FieldLines_0000 while the disk still held
    #      FieldLines_0000\FieldLines_0000.vlc. Every model line missed.
    #   2. The second looked flat FIRST. The look-back held both - its plasmasphere and
    #      ring current also existed as flat leftovers from an earlier render, one step
    #      off in epoch - so it renamed the leftovers and left the files in use alone.
    #      3,790 model lines pointed at nothing, and the copies it promoted were the
    #      wrong epoch, which is worse than a missing file because it would have drawn.
    #
    # Hence: search the declared layout, and refuse when a name exists in both, because
    # that means the tree holds two candidates and this tool cannot know which is live.
    declared_conv = bool(man.get(CONV_STAMP))

    def _paths(name):
        flat = models / name
        conv = models / Path(name).stem / name
        return (conv, flat) if declared_conv else (flat, conv)

    def _dest(name):
        new = pre + name
        return (models / Path(new).stem / new) if declared_conv else (models / new)

    planned: list[tuple[Path, Path]] = []
    missing: list[str] = []
    both: list[str] = []
    seen: set[str] = set()
    for container, key in lists:
        for name in container[key]:
            if name.startswith(pre) or name in seen:
                continue
            seen.add(name)
            first, second = _paths(name)
            if first.exists() and second.exists():
                both.append(name)
            elif first.exists():
                planned.append((first, _dest(name)))
            elif second.exists():
                # present, but not where the manifest says it is - a mixed tree
                both.append(name)
            else:
                missing.append(name)

    if both:
        raise RuntimeError(
            f"{root.name}: {len(both)} file(s) are not unambiguously in the "
            f"{'convention' if declared_conv else 'flat'} layout the manifest declares, "
            f"e.g. {both[0]} - refusing to rename a mixed tree. Settle the layout first "
            f"(tools/normalize_model_layout.py) and remove whichever copy is stale.")

    # Fail CLOSED. A manifest that lists files which are in neither layout is a tree
    # this tool does not understand, and stamping it would leave the package naming
    # files that do not exist.
    if missing:
        raise RuntimeError(f"{root.name}: {len(missing)} file(s) in the manifest are in "
                           f"neither layout, e.g. {missing[0]} - refusing to rename")

    collisions = [d for _, d in planned if d.exists()]
    if collisions:
        raise RuntimeError(f"{root.name}: {len(collisions)} target name(s) already exist, "
                           f"e.g. {collisions[0].name} - refusing to overwrite")

    if not dry_run:
        for src, dst in planned:
            dst.parent.mkdir(exist_ok=True)
            src.replace(dst)
            # the old per-file folder is now empty (convention layout only)
            if src.parent != models:
                try:
                    src.parent.rmdir()
                except OSError:
                    pass
        for container, key in lists:
            container[key] = [n if n.startswith(pre) else pre + n for n in container[key]]
        man[STAMP] = pre
        tmp = mp.with_suffix(mp.suffix + ".tmp")
        tmp.write_text(json.dumps(man, indent=1, allow_nan=False), encoding="utf-8")
        tmp.replace(mp)
    return {"renamed": len(planned), "prefix": pre}



CONV_STAMP = "model_convention_dirs"


def convention_root(root: Path, dry_run: bool = False) -> dict:
    r"""Move Models\<name>.<ext> -> Models\<name>\<name>.<ext>, the packager's own layout.

    For any render that SHIPS, which since 2026-09-20 is the look-back too - David asked
    for one file structure across all six, and the look-back is in the .dsz like the
    storms. Only the live feed stays flat, and that is not a layout choice: its
    Models\c000042\ per-cycle directories ARE the turnover mechanism.
    """
    root = Path(root)
    mp = _manifest_path(root)
    man = json.loads(mp.read_text(encoding="utf-8"))
    if man.get(CONV_STAMP):
        return {"skipped": "already in the packaging convention"}
    models = root / "Models"
    moved = 0
    for container, key in _file_lists(man):
        for name in container[key]:
            src = models / name
            if not src.exists():
                continue
            dst_dir = models / Path(name).stem
            if not dry_run:
                dst_dir.mkdir(exist_ok=True)
                src.replace(dst_dir / name)
            moved += 1
    if not dry_run:
        man[CONV_STAMP] = True
        tmp = mp.with_suffix(mp.suffix + ".tmp")
        tmp.write_text(json.dumps(man, indent=1, allow_nan=False), encoding="utf-8")
        tmp.replace(mp)
    return {"moved": moved}


def reemit(root: Path) -> None:
    """Rewrite the scripts so they name the new files. Text only - never a re-render."""
    sys.path.insert(0, str(ENGINE / "tools"))
    import retime_event
    man = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
    retime_event.retime(root, step_seconds=float(man["step_seconds"]))


def prepare_for_packaging(root) -> dict:
    r"""Everything a fresh render needs before it can go in a .dsz. Call it on EVERY path.

    A render comes off the writers flat and with bare basenames, which is wrong for a
    package twice over: every render uses the same basenames, and the packager keeps a
    model's path only relative to the nearest Models\ folder, so six renders collapse
    onto one set and five storms silently play another storm's geometry; and Digistar's
    own packager writes Models\<name>\<name>.<ext>, so a source tree already in that
    shape makes the .dsz round trip a no-op.

    This exists as ONE function because it was previously three tools with no caller.
    A freshly rendered Gannon on 2026-09-20 had none of them applied, and the symptom
    is an empty dome, not an error. The storm queue and the look-back both call this
    now, so no render path can forget it.
    """
    root = Path(root)
    out = {"renamed": rename_root(root), "convention": convention_root(root)}
    reemit(root)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.rename_models",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--convention", action="store_true",
                    help="also move each model into its own folder "
                         "(Models\\<name>\\<name>.<ext>) - for a render that SHIPS")
    ap.add_argument("--no-reemit", action="store_true",
                    help="rename and restamp only; leave the scripts naming the old files "
                         "(they will be broken until retime_event runs)")
    args = ap.parse_args(argv)
    if not args.root and not args.all:
        ap.error("give --root or --all")

    roots = [Path(args.root)] if args.root else []
    if args.all:
        roots += [d for d in sorted(paths.renders_root().glob("*"))
                  if _manifest_path(d).exists()]

    total = 0
    for r in roots:
        res = rename_root(r, args.dry_run)
        if "skipped" in res:
            print(f"{r.name:34s} rename skipped - {res['skipped']}")
            if not args.convention:
                continue
            res = {"renamed": 0, "prefix": ""}
        total += res["renamed"]
        verb = "would rename" if args.dry_run else "renamed"
        print(f"{r.name:34s} {verb} {res['renamed']:5d} file(s) with prefix '{res['prefix']}'")
        if args.convention:
            c = convention_root(r, args.dry_run)
            if "skipped" in c:
                print(f"{'':34s} convention: {c['skipped']}")
            else:
                verb2 = "would move" if args.dry_run else "moved"
                print(f"{'':34s} convention: {verb2} {c['moved']} file(s) into per-name folders")
        if not args.dry_run and not args.no_reemit and (res["renamed"] or args.convention):
            reemit(r)
            print(f"{'':34s} scripts re-emitted")
    print(f"\n{'would rename' if args.dry_run else 'renamed'} {total} model file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
