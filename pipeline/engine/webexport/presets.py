"""Web export presets - the single place the decimation numbers live.

Same idiom as magneto/presets.py: a frozen dataclass, one named instance per target, and the
chosen preset's name is recorded in the bundle so a file can always say how it was built.

The per-layer numbers are `keep` counts, not strides, so a layer whose source resolution
changes still lands on the same web budget.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass(frozen=True)
class WebPreset:
    name: str
    keyframes: int
    keep_lines: int            # FieldLines: polylines kept
    keep_line_verts: int       # FieldLines: vertices per line
    shell_ring_stride: int
    shell_phi_stride: int
    keep_wind: int
    keep_ring: int
    keep_plasma: int
    keep_aurora: int
    aurora_keyframes: int
    keep_imf_lines: int
    keep_imf_verts: int
    # FieldPulses is deliberately absent: a pulse is a bead gliding along +B on a line the
    # FieldLines layer already carries, so it is a mix() in the vertex shader given the
    # line's arc-length parameter and a time uniform. Shipping it as data is pure waste.
    layers: tuple = field(default=("FieldLines", "MagnetopauseShell", "BowShockShell",
                                   "RingCurrent", "Plasmasphere", "IMFLines",
                                   "WindParticles", "AuroraOval"))

    def as_json(self) -> dict:
        d = asdict(self)
        d["layers"] = list(self.layers)
        return d


# ~2-2.5 MB after brotli. Sized for a phone viewport, where anything finer is below a pixel.
MOBILE = WebPreset(
    name="mobile",
    keyframes=60,
    keep_lines=72, keep_line_verts=32,
    shell_ring_stride=2, shell_phi_stride=2,
    keep_wind=1200, keep_ring=1000, keep_plasma=800,
    keep_aurora=2760, aurora_keyframes=24,
    keep_imf_lines=18, keep_imf_verts=48,
)

# For a desktop canvas; still ~10x smaller than the dome tree.
DESKTOP = WebPreset(
    name="desktop",
    keyframes=120,
    keep_lines=140, keep_line_verts=48,
    shell_ring_stride=1, shell_phi_stride=2,
    keep_wind=4000, keep_ring=3000, keep_plasma=2000,
    keep_aurora=8280, aurora_keyframes=54,
    keep_imf_lines=18, keep_imf_verts=48,
)

# The §8 first milestone: three layers, no wind, no aurora. Proves the frame convention
# end to end, which is the only part of this that has historically gone wrong.
FIRST = WebPreset(
    name="first",
    keyframes=40,
    keep_lines=72, keep_line_verts=32,
    shell_ring_stride=2, shell_phi_stride=2,
    keep_wind=0, keep_ring=0, keep_plasma=0,
    keep_aurora=0, aurora_keyframes=0,
    keep_imf_lines=0, keep_imf_verts=0,
    layers=("FieldLines", "MagnetopauseShell", "BowShockShell"),
)

PRESETS = {p.name: p for p in (MOBILE, DESKTOP, FIRST)}
