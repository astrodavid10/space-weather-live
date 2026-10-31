"""Build A - Live Magnetosphere. Physics in GSM (R_E), exports light years."""
RE_M = 6_371_200.0  # geopack Earth radius (6371.2 km) in meters

# geopack asks NOAA for the IGRF file list on import, untimed: skip it when the files are local
from . import offline as _offline  # noqa: E402

_offline.install()
