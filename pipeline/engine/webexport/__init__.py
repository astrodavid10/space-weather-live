"""Slim web export for the Live Magnetosphere.

A POST-PASS over a published render tree - it reads the .vlc/.obj files the dome pipeline
already wrote and never touches the renderer. numpy is the only dependency, so this package
runs on a machine with no Digistar, no geopack and no astropy.

See Engine/docs/WEB_EXPORT.md for the budget, the format and the three gotchas that will
otherwise bite a web loader (the .obj y-up precompensation is the one that matters most).
"""
from .read_vlc import read_vlc, VlcFrame, RE_LY          # noqa: F401
from .read_obj import read_obj, read_glb, read_mesh, undo_yup, ObjMesh  # noqa: F401
from .presets import PRESETS, MOBILE, DESKTOP, FIRST     # noqa: F401
