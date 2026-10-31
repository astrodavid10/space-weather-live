r"""Keep library imports off the network (2026-10-15).

geopack.geopack calls ``init_igrf()`` -> ``update_igrf()`` when it is imported: an HTTP request to
www.ngdc.noaa.gov for the list of IGRF coefficient files, with NO timeout, then a download of any
listed file that is missing locally, with no error handling. Every process that imports geopack
pays it - the live daemon, each render worker, the installer's self-test - and on a dome whose
network is slow or filtered the import can hang, which is a blank dome on show night.

The engine vendors the coefficient files (``vendor\install_igrf.py``, run by the installer), so
:func:`install` registers a one-shot import hook: while ``geopack.geopack`` executes, and only when
its ``igrf_coeffs`` folder already holds at least one ``igrf*coeffs.txt``, ``urlopen`` refuses at
once - geopack's own ``except`` prints "Failed to get the list of IGRF coefficients." and it loads
the local files. With no local files the import runs untouched (and downloads, as before).

astropy: :func:`quiet_astropy` turns ``iers.conf.auto_download`` off before the aurora's first
``Time``. Converting UTC needs the leap-second table, and astropy tries to download a fresh one
when its bundled table is near expiry (the Python 3.8 pin's table is from 2023). No leap second
has been added since 2017, so the bundled table gives the same answer.
"""
from __future__ import annotations

import glob
import importlib.abc
import importlib.util
import os
import sys

_TARGET = "geopack.geopack"
_astropy_done = False


def _refuse(*_a, **_k):
    raise OSError("IGRF file listing skipped: the coefficient files are already installed")


class _GeopackOffline(importlib.abc.MetaPathFinder):
    """Finds geopack.geopack once, and runs its module code with urlopen refusing."""

    def find_spec(self, fullname, path, target=None):
        if fullname != _TARGET:
            return None
        try:
            sys.meta_path.remove(self)                 # one shot, and never recurse into ourselves
        except ValueError:
            pass
        spec = importlib.util.find_spec(fullname)
        if spec is None or spec.loader is None or not hasattr(spec.loader, "exec_module"):
            return spec
        here = os.path.dirname(spec.origin or "")
        if not glob.glob(os.path.join(here, "igrf_coeffs", "igrf*coeffs.txt")):
            return spec                                # nothing local yet: let geopack download
        real_exec = spec.loader.exec_module

        def exec_module(module):
            import urllib.request
            real = urllib.request.urlopen
            urllib.request.urlopen = _refuse           # geopack does `from urllib.request import urlopen`
            try:
                real_exec(module)
            finally:
                urllib.request.urlopen = real
                module.urlopen = real                  # an explicit update_igrf() later still works

        spec.loader.exec_module = exec_module
        return spec


def install() -> None:
    """Register the geopack hook (idempotent; a no-op once geopack.geopack is imported)."""
    if _TARGET in sys.modules:
        return
    if not any(isinstance(f, _GeopackOffline) for f in sys.meta_path):
        sys.meta_path.insert(0, _GeopackOffline())


def quiet_astropy() -> None:
    """astropy never downloads IERS / leap-second tables in this process."""
    global _astropy_done
    if _astropy_done:
        return
    _astropy_done = True
    try:
        from astropy.utils import iers
        iers.conf.auto_download = False
    except Exception:                                  # astropy absent or older: nothing to turn off
        pass
