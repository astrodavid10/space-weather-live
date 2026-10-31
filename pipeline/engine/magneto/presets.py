"""The SHOW preset - the one place the render settings live.

Every rendered package (the five curated storms, the 48 h look-back, the live
daemon's frames) must be built with the SAME settings, or the panel's layer
toggles and style sliders behave differently from one button to the next.
Before 2026-09-14 the flags were scattered across three kickoff scripts and
two CLIs and had drifted (storms: GSM frame, 1.3 s steps, no shells, no
pulses; look-back: J2000, 0.8 s, shells, pulses). This module ends that.

    from magneto.presets import SHOW
    cmd = [python, "-m", "magneto.render", "event", eid, *SHOW.cli_args(workers=24), ...]
    render.render_event(eid, root, **SHOW.render_kwargs(external=external_for("storm")))

What is NOT a preset: the number of worker processes (a property of the
machine - see magneto.paths.workers()) and the external field model, which
is a property of the KIND of render: curated storms run the state's own
model - T96, or TS05 (geopack t04, W indices from the timeline) whenever the
storm leaves T96's fitted range, which all five curated storms do (`--model
auto`; T89 has no dynamic-pressure term, gate 5) - while the look-back and the
live feed run T89 (quiet conditions, minutes instead of hours).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class RenderPreset:
    step_min: float = 10.0            # simulated minutes per field step
    step_seconds: float = 0.8         # dome seconds per field step
    frame: str = "j2000"              # export frame (Digistar WCS axes, no holder attitude)
    coord_digits: int = 9             # float32-exact light-year coordinates
    with_shells: bool = True          # translucent boundary shells (effectModelClass)
    with_pulses: bool = True          # beads gliding along the field lines
    preload_window: int = 60          # frame objects per layer alive ahead of the playhead
    fieldline_every: int = 1          # trace every step
    wind_sub: int = 4                 # wind frames per field step
    wind_ratio: float = 150.0         # seconds of real parcel flow per dome second
    aurora_max_hold_min: float = 60.0
    lookback_days: float = 2.0        # the 48 h look-back window
    with_ring: bool = True           # ring current from the measured SYM-H
    with_plasmasphere: bool = True   # plasmapause from Carpenter & Anderson 1992
    with_imf: bool = True            # IMF drape + Kan & Lee merging
    shell_preset: str = "P2"         # shell look: dsexport.ds.DS_SHELL_PRESETS
    shell_mode: str = "step"         # "step": one OBJ per step, crossfaded.
                                     # "library" (a few reference shapes scaled per step)
                                     # is only valid while the magnetopause family is
                                     # self-similar - see model.physics_stamp()
                                     # ["self_similar_shells"]. It stays available for a
                                     # Shue-only render and is refused otherwise.
    holder_intensities: dict = field(default_factory=dict)   # {} = the writers' defaults

    @property
    def wind_stream_s(self) -> float:
        """Seconds of real parcel flow per field step - scales with step_seconds so
        the tuned ~150x flow look survives a retime (120 s at 0.8 s steps)."""
        return round(self.wind_ratio * self.step_seconds, 6)

    def cli_args(self, *, workers: int | None = None) -> list[str]:
        """The flag list both `magneto.render event` and `tools/run_lookback.py`
        accept. Booleans are spelled explicitly so a changed default can never
        silently re-render a package differently."""
        args = ["--step-min", _num(self.step_min),
                "--step-seconds", _num(self.step_seconds),
                "--frame", self.frame,
                "--coord-digits", str(int(self.coord_digits)),
                "--preload-window", str(int(self.preload_window)),
                "--fieldline-every", str(int(self.fieldline_every)),
                "--wind-sub", str(int(self.wind_sub)),
                "--wind-stream-s", _num(self.wind_stream_s),
                "--aurora-max-hold-min", _num(self.aurora_max_hold_min),
                "--shell-preset", self.shell_preset,
                "--shell-mode", self.shell_mode,
                "--shells" if self.with_shells else "--no-shells",
                "--pulses" if self.with_pulses else "--no-pulses",
                "--ring" if self.with_ring else "--no-ring",
                "--plasmasphere" if self.with_plasmasphere else "--no-plasmasphere",
                "--imf" if self.with_imf else "--no-imf"]
        for layer, value in sorted(self.holder_intensities.items()):
            args += ["--holder-intensity", f"{layer}={_num(value)}"]
        if workers:
            args += ["--workers", str(int(workers))]
        return args

    def render_kwargs(self, *, external: str | None = None,
                      workers: int | None = None) -> dict:
        """Keyword arguments for magneto.render.render_event()."""
        kw = dict(step_min=self.step_min, step_seconds=self.step_seconds,
                  frame=self.frame, coord_digits=self.coord_digits,
                  with_shells=self.with_shells, with_pulses=self.with_pulses,
                  with_ring=self.with_ring,
                  with_plasmasphere=self.with_plasmasphere,
                  with_imf=self.with_imf,
                  preload_window=self.preload_window,
                  fieldline_every=self.fieldline_every,
                  wind_sub=self.wind_sub, wind_stream_s=self.wind_stream_s,
                  aurora_max_hold_min=self.aurora_max_hold_min,
                  holder_intensities=dict(self.holder_intensities) or None,
                  shell_preset=self.shell_preset, shell_mode=self.shell_mode,
                  external=external)
        if workers:
            kw["workers"] = int(workers)
        return kw

    def as_dict(self) -> dict:
        d = asdict(self)
        d["wind_stream_s"] = self.wind_stream_s
        return d


def _num(x) -> str:
    """10.0 -> '10', 0.8 -> '0.8' - the shortest exact spelling."""
    f = float(x)
    return str(int(f)) if f == int(f) else repr(f)


SHOW = RenderPreset()

# The external field model is a property of the render KIND, not of the preset.
EXTERNAL_BY_KIND = {"storm": "auto",     # TS05 when the storm leaves T96's range, else T96
                    "lookback": "t89",   # quiet conditions; minutes, not hours
                    "live": "t89"}


def external_for(kind: str) -> str | None:
    try:
        return EXTERNAL_BY_KIND[str(kind).lower()]
    except KeyError:
        raise ValueError(f"unknown render kind {kind!r}") from None


# --- show-level names shared by the shims, the kickoffs and the panel ----------
# Digistar object names may not contain '-' or '.', so every curated event has a
# short KEY (used in toggle object names such as MagEvent_gannon_toggle and in
# MagnetoToggles.js | eventStarted('gannon', 1)) and a short LABEL that fits a
# 255-character system message.
EVENT_KEYS = {
    "2000-07-15_bastille":   "bastille",
    "2003-11-20_superstorm": "nov2003",
    "2024-05-10_gannon":     "gannon",
    "2024-10-10_g4":         "octg4",
    "2026-01-19_kp9":        "jan2026",
}
LOOKBACK_KEY = "lookback"
EVENT_LABELS = {
    "2000-07-15_bastille":   "Bastille Day 2000",
    "2003-11-20_superstorm": "Nov 2003 superstorm",
    "2024-05-10_gannon":     "Gannon May 2024",
    "2024-10-10_g4":         "Oct 2024 G4",
    "2026-01-19_kp9":        "Jan 2026 Kp9",
}


def event_key(event_id: str) -> str:
    if str(event_id).startswith("lookback-"):
        return LOOKBACK_KEY
    return EVENT_KEYS.get(event_id, "".join(ch for ch in str(event_id) if ch.isalnum()))


def event_label(event_id: str) -> str:
    if str(event_id).startswith("lookback-"):
        return "48h look-back"
    return EVENT_LABELS.get(event_id, str(event_id))


def as_json() -> str:
    return json.dumps({"show": SHOW.as_dict(), "external_by_kind": EXTERNAL_BY_KIND,
                       "event_keys": EVENT_KEYS, "event_labels": EVENT_LABELS},
                      indent=1, sort_keys=True)


if __name__ == "__main__":                                 # pragma: no cover
    print(as_json())
