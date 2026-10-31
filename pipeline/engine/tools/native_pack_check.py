#!/usr/bin/env python
r"""Emulate Digistar's OWN content packager on the Live Magnetosphere panel, and fail closed on what would break.

Digistar's packager (Library > right-click > Share Item via Content Package) has no command line and writes no log, and
a share of this package takes 15-40 minutes. This tool applies the packager's rules - read from its IL in Digistar
2026.0.3.3 (Ds.UI.dll ContentDiscoveryUtilities / LibraryPackageUtilities, 2026-10-15; see the digistar-packaging
skill, references\packager-rules.md) - to the panel in seconds:

  discovery   from the .dscp: its command strings and RelativeUri images; through .ds files by KEYWORD
              (`script play|include`, `js play` cut at '|', `<obj> play|open|model|include|path|...`, `X is <path>`,
              the quoted command of any attribute ending in "command" - its FIRST command only - and
              `script execute "..."`), never `system execute`; `#`/`;` lines skipped, lines after `script end` read;
              through .js files (each Ds.SendStringCommand("...") parsed as a command, every other quoted absolute
              path shipped as a file); a missing file is dropped
  placement   by extension (Scripts\, Models\<stem>\, Images\, Data\, Terrain\, ControlPanel\Media\, the package root,
              XApps\<stem>\), .vbs/.bat/.cmd/.ps1/.whl/... DROPPED; same name + different content = `_N` rename for
              scripts and models, last copy wins (an ERROR here) for .py/.json/.txt/.zip; same content under
              another name (a model or image) = stored ONCE, references pointed at the copy kept
  rewrite    every discovered path string, in every staged file: RELATIVE in .ds (.\x.ds, ..\Models\<stem>\<f>),
              ABSOLUTE `$Content\User\Packages\<SN>\<Name>\...` in the .dscp and .js

Then the GATES, on the rewritten text exactly as an installed dome would play it:

  G1  every path a staged .ds / .dscp / .js would load resolves inside the package (or is stock $Content\Library)
  G2  stored commands: a path inside an onCommand/offCommand/command string is relative (works when a .ds set it,
      dome-probed 2026-10-15) or absolute at the install path; an absolute path left behind a back tick is not
      rewritten and breaks under any other install name -> error
  G3  `system execute` names an absolute path (a relative one is resolved against DsHost's folder, dome-probed),
      and only files that exist in the package or that the engine install writes (the four .vbs launchers)
  G4  Scripts\engine-status.ds ships saying OFF
  G5  the Python the engine install needs ships: Scripts\mag_setup.py + the kickoffs, MagnetoEngine.zip at the root,
      and the zip is current (tools\build_payload.py --check)
  G6  no .js holds a quoted absolute X:\ string (the packager would ship whatever it names)
  G7  no basename collision the packager cannot rename (different content under one name)
  G8  no line the packager's `dur` regex would backtrack on for minutes (ReDoS), no ANSI non-ASCII in scripts
  G9  paths stay under 260 characters staged (~TempUpload\<guid>\...) and installed (Downloads\<SN>\<Name>\...)
  G10 every path-shaped token of a played line resolves inside the package, including ones the keywords miss
      (`cwSlideSet list 0 ..\Images\x.jpg`: the packager takes the index, so the picture was neither shipped nor
      rewritten while G1 passed - COSMOS-Web, 2026-10-18); name such files after `script end` as well

    python tools\native_pack_check.py                          report + gates (exit 1 on any error)
    python tools\native_pack_check.py --stage <dir> --name "Live Magnetosphere TEST"
        also write the package as Digistar would INSTALL it (texts rewritten for that name; big binaries hard-linked)
    python tools\native_pack_check.py --compare <~TempUpload\<guid>>   diff against a real share's staging folder
    python tools\native_pack_check.py --panel <another package's .dscp> --name "<its name>"
        the same rules on another package (G4/G5, the engine gates, are skipped there)
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import html
import os
import re
import shutil
import sys
from pathlib import Path, PureWindowsPath

ENGINE = Path(__file__).resolve().parents[1]
PACKAGE = ENGINE.parent
CONTENT = PACKAGE.parents[3]                       # <content>\User\Packages\INTUITIVE\<pkg> -> <content>
SOFTWARE_GUESS = Path(os.environ.get("DIGISTAR_SOFTWARE", r"C:\CXSoftware"))
SITE = "INTUITIVE"
DEFAULT_NAME = "Live Magnetosphere"
PANEL = PACKAGE / "ControlPanel" / "Live Magnetosphere.dscp"

SCRIPT_EXT = {".ds", ".d4s", ".js", ".py"}
MODEL_EXT = {".x", ".vla", ".vl", ".vlc", ".csv", ".du", ".dsa", ".kml", ".glb", ".gltf", ".obj", ".dsg", ".dsc",
             ".3ds", ".fbx", ".dae", ".ply", ".stl"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".dds", ".gif", ".tga", ".hdr", ".exr", ".ktx"}
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".aif", ".aiff", ".m4a", ".wma"}
VIDEO_EXT = {".mp4", ".mov", ".mpg", ".mpeg", ".webm", ".avi", ".wmv", ".mkv", ".m2v"}
TERRAIN_EXT = {".dat", ".hgt", ".tms"}
DATA_EXT = {".json", ".xml", ".tsv", ".vot"}
ROOT_EXT = {".txt", ".html", ".htm", ".dug", ".svg", ".css", ".pdf", ".zip"}
TEXT_REWRITTEN = {".ds", ".d4s", ".js", ".dscp", ".html", ".htm"}
NEVER_RENAMED = {".py", ".json", ".xml", ".tsv", ".vot", ".txt", ".zip", ".dat", ".hgt", ".tms", ".exe", ".html",
                 ".htm", ".dug", ".svg", ".css", ".pdf"}

PATH_VERBS = {"play", "open", "model", "include", "path", "image", "modeltexture", "list", "tile", "tilepath",
              "volume", "dissolve", "frequency", "launch", "texture"}
RUNTIME_LAUNCHERS = {"detect_engine.vbs", "run_live_start.vbs", "run_live_stop.vbs", "run_rebuild_lookback.vbs"}
PYTHON_FILES = ("mag_setup.py", "_kickoff_common.py", "kickoff_live_daemon.py", "kickoff_lookback_rebuild.py",
                "kickoff_event_rebuild.py", "kickoff_rebuild_all.py")
ENGINE_ZIP = "MagnetoEngine.zip"
XMLNS_DOWNGRADE = {"http://cosm.com/ui/controls": "clr-namespace:ES.Ds.GUILibrary.Controls;assembly=GUILibrary",
                   "http://cosm.com/ui/controlpanel": "clr-namespace:ES.Ds.GUI.ControlPanel;assembly=Digistar"}

_SKIP = re.compile(r"^\s*($|;|#)")
_TRAIL = re.compile(r"(\s+;.*|\s+#.*)$")
_DUR = re.compile(r"(?i)\s+dur\w*\s+\+?[0-9:.]+(\s+[0-9.%]+)*\s*$")
_SEND = re.compile(r"Ds\.SendStringCommand\((\"|')(.*)(\"|')")
_QUOTED = re.compile(r"\"((?:[^\"\\\r\n]|\\.)*)\"|'((?:[^'\\\r\n]|\\.)*)'")
_BUTTON = re.compile(r"^(A|B|X|Y|L|R|Start|Back|LStick|RStick)Button$", re.I)
_REDOS = re.compile(r"(?i)\sdur\w*(\s+[0-9.:+]+){6,}\s+\w")


# ---- paths --------------------------------------------------------------------------------------------------
def looks_like_path(tok: str) -> bool:
    t = tok.strip().strip('"')
    return bool(re.match(r"^(\$[A-Za-z]+\\|[A-Za-z]:[\\/]|\\\\|//|\.{1,2}[\\/])", t)) or t.lower().startswith(("http:", "https:"))


def resolve(tok: str, base_dir: Path) -> Path | None:
    t = tok.strip().strip('"').replace("/", "\\")
    low = t.lower()
    if low.startswith("$content\\"):
        return CONTENT / t[9:]
    if low.startswith("$software\\"):
        return SOFTWARE_GUESS / t[10:]
    if re.match(r"^[A-Za-z]:\\", t) or t.startswith("\\\\"):
        return Path(t)
    if t.startswith(".\\") or t.startswith("..\\"):
        return Path(os.path.normpath(str(base_dir / t)))
    return None


def has_ext(tok: str) -> bool:
    return bool(PureWindowsPath(tok.strip().strip('"')).suffix)


# ---- one command line -> discovered path strings --------------------------------------------------------------
def parse_command(line: str, *, js: bool = False):
    """[(original_path_text, kind)] the packager takes from ONE command (kind: 'script' | 'file')."""
    if _SKIP.match(line):
        return []
    s = line.strip()
    s = _TRAIL.sub("", s)
    s = _DUR.sub("", s)
    toks = s.split()
    if len(toks) < 2:
        return []
    t0, t1 = toks[0], toks[1]
    l0, l1 = t0.lower(), t1.lower()
    rest = toks[2:]
    out = []
    if l1.endswith("command") or (l0 == "script" and l1 == "execute"):
        r = rest
        while r and (re.match(r"^\d+$", r[0]) or _BUTTON.match(r[0])):
            r = r[1:]
        joined = " ".join(r)
        if len(joined) >= 2 and joined[0] == '"' and joined[-1] == '"':
            out += parse_command(joined[1:-1])
        return out
    if l0 == "script" and l1 in ("play", "include") and rest:
        p = " ".join(rest)
        return [(p, "script")]
    if l0 == "show" and l1 == "open" and rest:
        return [(" ".join(rest), "script")]
    if l0 == "js" and l1 == "play" and rest:
        p = " ".join(rest).split("|")[0].strip()
        return [(p, "file")] if p else []
    if l0 == "system" and l1 == "execute":
        return []                                   # not a path keyword (verified in IL); dropped anyway if .vbs
    if l1 == "is" and rest and looks_like_path(rest[0]):
        return [(" ".join(rest), "file")]
    if rest and (l1 in PATH_VERBS or l1.endswith("texture")) and not (l0 == "capture" and l1 == "path"):
        cand = rest[1:] if (rest[0].lower() == "default" and len(rest) > 1) else rest
        p = " ".join(cand)
        if looks_like_path(p):
            return [(p, "file")]
    return []


# ---- placement ------------------------------------------------------------------------------------------------
def placement(path: Path, image_of_model: bool = False) -> str | None:
    """The folder (inside the package) this file lands in, or None when the packager drops it."""
    ext = path.suffix.lower()
    if ext in SCRIPT_EXT:
        return "Scripts"
    if ext in MODEL_EXT:
        return f"Models\\{path.stem}"
    if ext == ".dscp":
        return "ControlPanel"
    if ext in IMAGE_EXT:
        return "Images"
    if ext in AUDIO_EXT:
        return "Audio"
    if ext in VIDEO_EXT:
        return "Video"
    if ext in TERRAIN_EXT:
        return "Terrain"
    if ext in DATA_EXT:
        return "Data"
    if ext in ROOT_EXT:
        return ""
    if ext == ".exe":
        return f"XApps\\{path.stem}"
    return None


class Item:
    def __init__(self, src: Path, folder: str):
        self.src = src
        self.folder = folder
        self.name = src.name
        self.originals: set[str] = set()
        self.refs: list[tuple[Path, int]] = []

    @property
    def package_path(self) -> str:
        return f"{self.folder}\\{self.name}" if self.folder else self.name


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


# ---- the scan -------------------------------------------------------------------------------------------------
class Pack:
    def __init__(self, panel: Path, name: str):
        self.panel = panel
        self.name = name
        self.folder_name = re.sub(r"[\s<>:\"/\\|?*]", "", name)
        self.items: dict[str, Item] = {}           # normcase(src) -> Item
        self.dropped: dict[str, list[str]] = collections.defaultdict(list)
        self.missing: dict[str, list[str]] = collections.defaultdict(list)
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []

    def add(self, original: str, base: Path, referrer: Path, kind: str, line_no: int) -> Item | None:
        p = resolve(original, base)
        if p is None:
            return None
        if kind == "script" and not has_ext(original):
            p = p.with_suffix(".ds")
        elif not has_ext(original):
            return None
        if not p.is_file():
            self.missing[str(p)].append(f"{referrer.name}:{line_no}")
            return None
        folder = placement(p)
        if folder is None:
            self.dropped[str(p)].append(f"{referrer.name}:{line_no}")
            return None
        key = os.path.normcase(str(p))
        it = self.items.get(key)
        new = it is None
        if new:
            it = self.items[key] = Item(p, folder)
        it.originals.add(original.strip().strip('"'))
        it.refs.append((referrer, line_no))
        if new and p.suffix.lower() in (".ds", ".d4s"):
            self.scan_ds(p)
        elif new and p.suffix.lower() == ".js":
            self.scan_js(p)
        return it

    def scan_ds(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")
            self.errors.append(f"G8 {path.name}: not UTF-8 - the packager rewrites it as UTF-8 and mangles non-ASCII")
        for i, line in enumerate(text.splitlines(), 1):
            if _REDOS.search(line):
                self.errors.append(f"G8 {path.name}:{i}: a 'dur' clause the packager's regex backtracks on: {line.strip()[:80]}")
            for orig, kind in parse_command(line):
                self.add(orig, path.parent, path, kind, i)

    def scan_js(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            m = _SEND.search(line)
            if m:
                cmd = m.group(2).replace("\\\\", "\\")
                for orig, kind in parse_command(cmd, js=True):
                    self.add(orig, path.parent, path, kind, i)
                continue
            for q in _QUOTED.finditer(line):
                s = (q.group(1) if q.group(1) is not None else q.group(2) or "").replace("\\\\", "\\")
                if re.match(r"^[A-Za-z]:\\", s) or s.startswith("\\\\"):
                    self.errors.append(f"G6 {path.name}:{i}: quoted absolute path {s!r} - the packager ships what it names")
                    self.add(s, path.parent, path, "file", i)
                elif s.startswith(".\\") and has_ext(s):
                    self.add(s, path.parent, path, "file", i)

    def scan_panel(self) -> None:
        text = self.panel.read_text(encoding="utf-8-sig")
        self.items[os.path.normcase(str(self.panel))] = Item(self.panel, "ControlPanel")
        for m in re.finditer(r"<s:String>(.*?)</s:String>", text, re.S):
            cmd = html.unescape(m.group(1))
            line_no = text.count("\n", 0, m.start()) + 1
            for orig, kind in parse_command(cmd):
                self.add(orig, self.panel.parent, self.panel, kind, line_no)
        media = self.panel.parent / "Media"
        for m in re.finditer(r"(?:BackgroundImageRelativeUri2?|RelativeUri)=\"([^\"{][^\"]*)\"", text):
            name = html.unescape(m.group(1))
            p = Path(name) if re.match(r"^[A-Za-z]:\\", name) else media / PureWindowsPath(name).name
            if p.is_file():
                key = os.path.normcase(str(p))
                if key not in self.items:
                    self.items[key] = Item(p, "ControlPanel\\Media")
            else:
                self.missing[str(p)].append(f"{self.panel.name} image")

    # ---- preinstalled content -----------------------------------------------------------------------------------
    PREINSTALLED = ("$content\\library", "$content\\resources", "$content\\backgrounds", "$software\\apps",
                    "$software\\bin", "$software\\config", "$software\\localization", "$software\\utilities")
    PREINSTALLED_KEEP = ("$content\\resources\\terrain\\maps", "$content\\resources\\gp\\temp\\cookedassets\\terrain",
                         "$content\\resources\\gp\\temp\\cookedassets\\skysurveys")

    def filter_preinstalled(self) -> None:
        """FilterOutPreinstalledContent: both share commands call GetAllContent(item, filterPreinstalled=true), so
        stock files every dome already has are discovered (and followed) but not packaged, and their references
        keep their absolute $Content\\Library paths. Terrain tiles are kept (FileType 13)."""
        def unresolved(p: Path) -> str:
            for root, token in ((CONTENT, "$content"), (SOFTWARE_GUESS, "$software")):
                try:
                    return f"{token}\\{p.relative_to(root)}".lower()
                except ValueError:
                    continue
            return str(p).lower()
        drop = []
        for key, it in self.items.items():
            u = unresolved(it.src)
            if it.src.suffix.lower() in TERRAIN_EXT or not u.startswith(self.PREINSTALLED):
                continue
            if not u.startswith(self.PREINSTALLED_KEEP):
                drop.append(key)
        for key in drop:
            self.notes.append(f"stock content, not packaged (every dome has it): {self.items.pop(key).src}")

    # ---- collisions -------------------------------------------------------------------------------------------
    def resolve_collisions(self) -> None:
        by_pp: dict[str, list[Item]] = collections.defaultdict(list)
        for it in self.items.values():
            by_pp[it.package_path.lower()].append(it)
        for pp, group in sorted(by_pp.items()):
            if len(group) < 2:
                continue
            digests = {}
            for it in group:
                digests.setdefault(sha(it.src), []).append(it)
            if len(digests) == 1:
                self.notes.append(f"merged {len(group)} identical copies of {group[0].package_path}")
                continue
            ext = group[0].src.suffix.lower()
            if ext in NEVER_RENAMED:
                self.errors.append(f"G7 {group[0].package_path}: {len(digests)} different files share this name and "
                                   f"the packager never renames a {ext} - the last copy wins: "
                                   + ", ".join(str(g.src) for g in group))
                continue
            for n, (_, its) in enumerate(sorted(digests.items(), key=lambda kv: str(kv[1][0].src))):
                if n == 0:
                    continue
                for it in its:
                    if ext in MODEL_EXT:
                        it.folder = f"{it.folder}_{n}"
                    else:
                        it.name = f"{it.src.stem}_{n}{it.src.suffix}"
            self.warnings.append(f"renamed {len(digests) - 1} different file(s) named {group[0].name}")

    # ---- content merges ---------------------------------------------------------------------------------------
    def merge_duplicates(self) -> None:
        """The packager's MergeDuplicates step (LibraryPackageUtilities.ProcessContentForPackage): two items under
        DIFFERENT names whose SHA-256 and children match - a model or image without textures always does - are
        stored ONCE, and every reference to the other is rewritten to the copy kept. Seen on the 2026-10-15 share:
        each render's last mesh frame repeats the one before it, and the 24 last frames were not in the package.
        Scripts are left alone (their children are their own references), and so are the panel's Media images
        (they are named by bare RelativeUri, not a rewritten path). The copy kept is the first by name - what that
        share kept; Digistar's Merge() decides it by parent and merge count, which this package never exercises."""
        by_size: dict[int, list[Item]] = collections.defaultdict(list)
        for it in self.items.values():
            if it.originals and it.src.suffix.lower() not in TEXT_REWRITTEN:
                by_size[it.src.stat().st_size].append(it)
        merged: dict[int, Item] = {}
        for group in by_size.values():
            if len(group) < 2:
                continue
            by_sha: dict[str, list[Item]] = collections.defaultdict(list)
            for it in group:
                by_sha[sha(it.src)].append(it)
            for same in by_sha.values():
                same.sort(key=lambda i: i.package_path.lower())
                for it in same[1:]:
                    if it.package_path.lower() == same[0].package_path.lower():
                        continue                        # one name, one entry already (resolve_collisions)
                    same[0].originals |= it.originals
                    same[0].refs += it.refs
                    merged[id(it)] = same[0]
        if not merged:
            return
        pairs = sorted((it.package_path, merged[id(it)].package_path) for it in self.items.values() if id(it) in merged)
        self.items = {k: it for k, it in self.items.items() if id(it) not in merged}
        self.notes.append(f"merged {len(pairs)} file(s) with the same content as another under a different name - "
                          f"stored once, references rewritten to the copy kept: "
                          + ", ".join(f"{a.split(chr(92))[-1]} -> {b.split(chr(92))[-1]}" for a, b in pairs[:3])
                          + (" ..." if len(pairs) > 3 else ""))

    # ---- rewriting --------------------------------------------------------------------------------------------
    def install_root(self, cloud: bool = False) -> str:
        return f"$Content\\User\\{'Downloads' if cloud else 'Packages'}\\{SITE}\\{self.folder_name}"

    def _originals(self) -> dict[str, Item]:
        """{lowercased original path text: item} - every string the scan discovered, as it was written."""
        if not hasattr(self, "_orig_cache"):
            d: dict[str, Item] = {}
            for other in self.items.values():
                for orig in other.originals:
                    d.setdefault(orig.lower(), other)
            self._orig_cache = d
        return self._orig_cache

    def rewrite(self, it: Item, text: str, cloud: bool = False) -> str:
        """The packager's text replacement, done as ONE pass over whole tokens.

        Its own loop runs Regex.Replace(text, (?<=\\s)<orig>(?=[\\s|;#"]|$)) once per package item; every match is
        therefore a whole whitespace-delimited token (a back tick is not a delimiter, so `x.ds`next` is left alone),
        which is exactly what a token scan with a dictionary lookup finds - in seconds instead of hours."""
        ext = it.src.suffix.lower()
        origs = self._originals()
        spaced = [(o, other) for o, other in origs.items() if " " in o]
        if ext in (".ds", ".d4s"):
            def repl_for(other: Item) -> str:
                return f".\\{other.name}" if other.folder == it.folder else f"..\\{other.package_path}"

            def sub(m):
                other = origs.get(m.group(0).lower())
                return repl_for(other) if other else m.group(0)
            text = re.sub(r"(?<=\s)[^\s\"|;#]+(?=[\s|;#\"]|$)", sub, text, flags=re.M)
            for o, other in spaced:
                if o in text.lower():
                    text = re.sub(r"(?<=\s)" + re.escape(o) + r"(?=[\s|;#\"]|$)", lambda _m, r=repl_for(other): r,
                                  text, flags=re.I | re.M)
        elif ext == ".dscp":
            root = self.install_root(cloud)
            enc = {html.escape(o, quote=False).lower(): other for o, other in origs.items()}

            def sub(m):
                other = enc.get(m.group(0).lower())
                return html.escape(f"{root}\\{other.package_path}", quote=False) if other else m.group(0)
            # the panel's own lookahead has no '|': `x.js|f()` is not rewritten, `x.js | f()` is
            text = re.sub(r"(?<=[\s>])[^\s;#\"<]+(?=[\s;#\"<])", sub, text)
            for o, other in spaced:
                e = html.escape(o, quote=False)
                if e.lower() in text.lower():
                    text = re.sub(r"(?<=[\s>])" + re.escape(e) + r"(?=[\s;#\"<])",
                                  lambda _m, r=html.escape(f"{root}\\{other.package_path}", quote=False): r, text,
                                  flags=re.I)
            text = re.sub(r"((?:BackgroundImageRelativeUri2?|RelativeUri)=\")\$[^\"]*\\([^\"\\]+\")", r"\1\2", text)
            # the share writes the two Cosm XAML namespaces in their older assembly form (2026-10-15 staging)
            for uri, clr in XMLNS_DOWNGRADE.items():
                text = text.replace(f'"{uri}"', f'"{clr}"')
        elif ext == ".js":
            root = self.install_root(cloud)
            low = text.lower()
            for o, other in sorted(origs.items(), key=lambda kv: -len(kv[0])):    # no boundaries: longest first
                dbl = o.replace("\\", "\\\\")
                if o not in low and dbl not in low:
                    continue
                # BOTH passes write the DOUBLED form: a single-backslash path in a .js comment came back as
                # `$Content\\User\\...` from a real share (COSMOS-Web, 2026-10-18)
                r = f"{root}\\{other.package_path}".replace("\\", "\\\\")
                text = re.sub(re.escape(o), lambda _m, r=r: r, text, flags=re.I)
                text = re.sub(re.escape(dbl), lambda _m, r=r: r, text, flags=re.I)
                low = text.lower()
        return text

    def staged_texts(self, cloud: bool = False) -> dict[str, str]:
        out = {}
        for it in self.items.values():
            if it.src.suffix.lower() in TEXT_REWRITTEN:
                out[it.package_path] = self.rewrite(it, it.src.read_text(encoding="utf-8-sig", errors="replace"), cloud)
        return out


# ---- the gates on the staged text ----------------------------------------------------------------------------
def gates(pk: Pack, texts: dict[str, str]) -> None:
    files = {it.package_path.lower() for it in pk.items.values()}
    root = pk.install_root().lower()

    def in_package(tok: str, script_folder: str) -> str | None:
        t = tok.strip().strip('"').replace("/", "\\")
        low = t.lower()
        if low.startswith(root + "\\"):
            return t[len(root) + 1:].lower()
        if t.startswith(".\\") or t.startswith("..\\"):
            return os.path.normpath(os.path.join(script_folder, t)).lower()
        return None

    def check(tok: str, kind: str, where: str, script_folder: str, gate: str = "G1") -> None:
        t = tok.strip().strip('"')
        if kind == "script" and not has_ext(t):
            t += ".ds"
        rel = in_package(t, script_folder)
        if rel is not None:
            if rel not in files:
                pk.errors.append(f"{gate} {where}: '{tok}' -> {rel} is not in the package")
            return
        p = resolve(t, PACKAGE)
        if p is None:
            return
        low = str(p).lower()
        if "\\library\\" in low or low.startswith(str(SOFTWARE_GUESS).lower()):
            if not p.exists():
                pk.warnings.append(f"G1 {where}: '{tok}' (stock content) is not on this PC")
            return
        pk.errors.append(f"G1 {where}: '{tok}' points OUTSIDE the package - it will not exist on another dome")

    for pp, text in sorted(texts.items()):
        folder = pp.rsplit("\\", 1)[0] if "\\" in pp else ""
        ext = os.path.splitext(pp)[1].lower()
        lines = text.splitlines()
        ended = False
        if ext == ".dscp":
            cmds = [(html.unescape(m.group(1)), text.count("\n", 0, m.start()) + 1)
                    for m in re.finditer(r"<s:String>(.*?)</s:String>", text, re.S)]
        elif ext == ".js":
            cmds = []
            for i, ln in enumerate(lines, 1):
                m = _SEND.search(ln)
                if m and not ln.lstrip().startswith("//"):
                    cmds.append((m.group(2).replace("\\\\", "\\"), i))
        else:
            cmds = []
            for i, ln in enumerate(lines, 1):
                if re.match(r"^\s*script\s+end\s*$", ln, re.I):
                    ended = True
                if not ended and not _SKIP.match(ln):
                    cmds.append((ln, i))
        for cmd, i in cmds:
            where = f"{pp}:{i}"
            s = _TRAIL.sub("", cmd.strip())
            toks = s.split()
            if len(toks) >= 3 and toks[1].lower().endswith("command"):
                inner = " ".join(t for t in toks[2:])
                inner = re.sub(r"^(\d+|\w+Button)\s+", "", inner)
                if inner.startswith('"') and inner.endswith('"'):
                    for part in inner[1:-1].split("`"):
                        for orig, kind in parse_command(part):
                            check(orig, kind, where + " (stored)", folder)
                    for m in re.finditer(r"(\$Content\\[^`\"]*?\.\w+)`", inner):
                        pk.errors.append(f"G2 {where}: '{m.group(1)}' sits right before a back tick, so the packager "
                                         f"does not rewrite it - it only works for an install named exactly like this one")
                continue
            if len(toks) >= 3 and toks[0].lower() == "system" and toks[1].lower() == "execute":
                target = " ".join(toks[2:]).strip('"')
                if target.startswith(".\\") or target.startswith("..\\"):
                    pk.errors.append(f"G3 {where}: relative `system execute {target}` - resolved against DsHost's folder")
                    continue
                p = resolve(target, PACKAGE)
                name = PureWindowsPath(target).name.lower()
                if p is None:
                    pk.warnings.append(f"G3 {where}: `system execute {target}` - not a path this check can follow")
                elif name in RUNTIME_LAUNCHERS:
                    if not str(p).lower().startswith(str(CONTENT / "User" / "Packages" / SITE / pk.folder_name).lower()):
                        pk.notes.append(f"G3 {where}: launcher {name} at another install path until the engine install "
                                        f"rewrites the shims")
                elif in_package(target, folder) is None or in_package(target, folder) not in files:
                    pk.errors.append(f"G3 {where}: `system execute {target}` - not shipped and not written by the installer")
                continue
            found = parse_command(cmd)
            for orig, kind in found:
                check(orig, kind, where, folder)
            # G10: a path the packager's keywords do not reach (`slideSet list 0 <path>` - it takes the index) is
            # neither shipped nor rewritten, and G1 never sees it
            taken = {o.strip().strip('"').lower() for o, _ in found}
            for tok in toks:
                t = tok.strip().strip('"')
                if looks_like_path(t) and has_ext(t) and t.lower() not in taken:
                    check(t, "file", where, folder, gate="G10")

    if pk.panel.resolve().parent.parent != PACKAGE.resolve():
        pk.notes.append("G4/G5 skipped: they check the Live Magnetosphere engine, and this is another package")
        return
    es = texts.get("Scripts\\engine-status.ds", "")
    if not es:
        pk.errors.append("G4 Scripts\\engine-status.ds is not in the package")
    elif re.search(r"MagIf_engine\s+status\s+on", es, re.I):
        pk.errors.append("G4 Scripts\\engine-status.ds ships saying ON - run tools\\write_shims.py --all before sharing")

    for name in PYTHON_FILES:
        if f"scripts\\{name}".lower() not in files:
            pk.errors.append(f"G5 Scripts\\{name} would not ship")
    if ENGINE_ZIP.lower() not in files:
        pk.errors.append(f"G5 {ENGINE_ZIP} would not ship (tools\\build_payload.py, then tools\\write_shims.py --all)")
    else:
        sys.path.insert(0, str(ENGINE))
        try:
            from tools import build_payload
            ok, msg = build_payload.check()
            (pk.notes if ok else pk.errors).append(("" if ok else "G5 ") + msg)
        except Exception as e:                          # never pass a gate that could not run
            pk.errors.append(f"G5 could not check {ENGINE_ZIP}: {e}")


def lengths(pk: Pack) -> None:
    staged = len(str(CONTENT / "User" / "~TempUpload" / ("x" * 36))) + 1
    installed = len(str(CONTENT / "User" / "Downloads" / SITE / pk.folder_name)) + 1
    worst = max(pk.items.values(), key=lambda it: len(it.package_path))
    n = len(worst.package_path)
    pk.notes.append(f"G9 longest packaged path {n} chars ({worst.package_path}): staged {staged + n}, "
                    f"installed {installed + n}")
    if staged + n >= 260 or installed + n >= 260:
        pk.errors.append(f"G9 {worst.package_path}: {max(staged, installed) + n} characters - over Windows' 260")


# ---- stage / compare ------------------------------------------------------------------------------------------
def stage(pk: Pack, dest: Path, cloud: bool = False) -> None:
    """The package as Digistar would INSTALL it: rewritten texts written, everything else hard-linked (same volume)
    or copied. dest must not exist (never written over)."""
    if dest.exists():
        raise SystemExit(f"{dest} exists - remove it first (never staged over)")
    texts = pk.staged_texts(cloud)
    for it in pk.items.values():
        out = dest / it.package_path
        out.parent.mkdir(parents=True, exist_ok=True)
        if it.package_path in texts:
            data = texts[it.package_path]
            # the packager writes staged scripts back as UTF-8 WITH a BOM
            with open(out, "w", encoding="utf-8-sig" if it.src.suffix.lower() in (".ds", ".d4s", ".js") else "utf-8",
                      newline="") as fh:
                fh.write(data)
        else:
            try:
                os.link(it.src, out)
            except OSError:
                shutil.copy2(it.src, out)
    print(f"staged {len(pk.items)} files into {dest}")


def compare(pk: Pack, real: Path, cloud: bool = False) -> int:
    """Diff the emulation against a real share's staging folder (~TempUpload\\<guid>). `cloud` must match the share:
    a Cloud staging is rewritten for Downloads\\<SN>\\<Name> (until 2026-10-18 this always compared against the
    content-package text, so every Cloud share showed the panel and the .js as different)."""
    want = {it.package_path.lower(): it for it in pk.items.values()}
    have = {str(p.relative_to(real)).lower(): p for p in real.rglob("*") if p.is_file() and p.name != "Item.xml"}
    only_emul = sorted(set(want) - set(have))
    only_real = sorted(set(have) - set(want))
    texts = {k.lower(): v for k, v in pk.staged_texts(cloud).items()}
    differ = []
    for k in sorted(set(want) & set(have)):
        if k in texts:
            real_text = have[k].read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
            if real_text != texts[k].replace("\r\n", "\n"):
                differ.append(k)
        elif have[k].stat().st_size != want[k].src.stat().st_size:
            differ.append(k)
    print(f"compare with {real}: {len(want)} emulated, {len(have)} real; only emulated {len(only_emul)}, "
          f"only real {len(only_real)}, content differs {len(differ)}")
    for label, lst in (("only in the emulation", only_emul), ("only in the real share", only_real),
                       ("content differs", differ)):
        for k in lst[:15]:
            print(f"  {label}: {k}")
    return 0 if not (only_emul or only_real or differ) else 1


# ---- main -----------------------------------------------------------------------------------------------------
def run(name: str = DEFAULT_NAME, panel: Path = PANEL) -> Pack:
    pk = Pack(panel, name)
    pk.scan_panel()
    pk.filter_preinstalled()
    pk.resolve_collisions()
    pk.merge_duplicates()
    gates(pk, pk.staged_texts())
    lengths(pk)
    return pk


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", default=DEFAULT_NAME, help="the package name typed in the Share dialog")
    ap.add_argument("--panel", default=str(PANEL))
    ap.add_argument("--stage", default=None, help="write the installed layout here (hard links for binaries)")
    ap.add_argument("--cloud", action="store_true", help="rewrite for a Cloud download (Downloads\\<SN>\\<Name>)")
    ap.add_argument("--compare", default=None, help="a real share's ~TempUpload\\<guid> folder to diff against")
    ap.add_argument("--list", action="store_true", help="print every packaged path")
    a = ap.parse_args(argv)
    pk = run(a.name, Path(a.panel))
    kinds = collections.Counter(it.package_path.split("\\")[0] if "\\" in it.package_path else "(root)"
                                for it in pk.items.values())
    total = sum(it.src.stat().st_size for it in pk.items.values())
    print(f"package '{a.name}' -> {pk.install_root(a.cloud)}")
    print(f"  {len(pk.items)} files, {total / 1e9:.2f} GB: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    if a.list:
        for it in sorted(pk.items.values(), key=lambda i: i.package_path.lower()):
            print("   ", it.package_path)
    for src, refs in sorted(pk.dropped.items()):
        print(f"  dropped by the packager: {src}  (named at {', '.join(refs[:3])})")
    for src, refs in sorted(pk.missing.items()):
        print(f"  named but missing (dropped silently): {src}  ({', '.join(refs[:3])})")
    for n in pk.notes:
        print("  note:", n)
    for w in pk.warnings:
        print("  WARNING:", w)
    for e in pk.errors:
        print("  ERROR:", e)
    rc = 1 if pk.errors else 0
    print("native packaging gates:", "FAIL (%d errors)" % len(pk.errors) if rc else "PASS")
    if a.compare:
        rc = compare(pk, Path(a.compare), a.cloud) or rc
    if a.stage:
        stage(pk, Path(a.stage), a.cloud)
    return rc


if __name__ == "__main__":
    sys.exit(main())
