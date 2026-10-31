r"""HAPI in CSV - the two requests the engine makes (/info and /data), and nothing else.

This replaced hapiclient on 2026-10-15, for packaging reasons:

* hapiclient publishes no wheel (sdist only), so the engine's hash-pinned, binary-only install
  (Scripts\mag_setup.py, the Terrain Pipeline pattern) could not include it;
* it pulls in pandas, joblib and isodate, which the engine never uses itself;
* its cache names (``<host>_hapi\data\<dataset>_<every parameter>_<t0>_<t1>.csv``) reached 242
  characters, 253 while being written, under an installed package - 6 short of Windows' 260.

What :func:`fetch` returns is what hapiclient's CSV path returned (``hapiclient.get._parse_csv``),
field for field, so nothing downstream changed:

* data - a numpy structured array, one field per parameter, Time first: ``isotime`` ->
  ``S<length>``, ``string`` -> ``U<length>`` (or the longest value when the server declares no
  length), ``double`` -> float64, ``integer`` -> int32 (float64 with NaN if a cell is empty);
* meta - the dataset's /info with ``parameters`` cut to Time + the requested ones, plus
  hapiclient's ``x_server``, ``x_dataset``, ``x_parameters``, ``x_time.min``, ``x_time.max``,
  ``x_requestDate``.

Parameters must be requested in the server's declared order (hapiclient enforced it; so does
this). Scalar parameters only - every dataset the engine reads is scalar, and a ``size`` raises
rather than being guessed at.

The untouched server responses are kept as provenance under SHORT names in ``raw_dir``:
``<dataset>.json`` (the full /info) and ``<dataset>_<t0>_<t1>.csv``.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np

TIMEOUT_S = (20, 300)       # (connect, read): a CDAWeb data request takes ~21 s
RETRIES = 2                 # network/5xx failures only; a 4xx is the request's fault
_NAN_TEXT = {"nan", "+nan", "-nan"}


class HapiError(RuntimeError):
    """The server refused the request, or answered something this reader does not understand."""


# ---- network ------------------------------------------------------------------------------------
def _get(url: str, params: dict | None = None) -> str:
    import requests                                    # the engine's only HTTP dependency
    last = None
    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            last = e
        else:
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
            msg = _status_message(r.text) or r.text[:200].strip()
            if r.status_code < 500:
                raise HapiError(f"HTTP {r.status_code} from {r.url}: {msg}")
            last = HapiError(f"HTTP {r.status_code} from {r.url}: {msg}")
        if attempt < RETRIES:
            time.sleep(3.0 * (attempt + 1))
    raise HapiError(f"{url}: no answer after {RETRIES + 1} tries ({last})")


def _status_message(text: str) -> str:
    """The HAPI status message of an error response, when it is JSON."""
    try:
        st = json.loads(text).get("status", {})
        return f"{st.get('code')} {st.get('message')}".strip()
    except (ValueError, AttributeError):
        return ""


def _query_names(meta: dict) -> tuple[str, str, str]:
    """HAPI 3 renamed id/time.min/time.max to dataset/start/stop (hapiclient util.query_name)."""
    if str(meta.get("HAPI", "")).startswith("3"):
        return "dataset", "start", "stop"
    return "id", "time.min", "time.max"


# ---- metadata -----------------------------------------------------------------------------------
def info(server: str, dataset: str) -> dict:
    """The dataset's /info, all parameters."""
    meta = json.loads(_get(server.rstrip("/") + "/info", {"id": dataset}))
    _check_status(meta, f"{dataset} /info")
    return meta


def _check_status(meta: dict, what: str) -> None:
    code = (meta.get("status") or {}).get("code")
    if code is not None and int(code) not in (1200, 1201):
        raise HapiError(f"{what}: HAPI status {code} {(meta.get('status') or {}).get('message')}")


def subset_meta(meta: dict, parameters: str) -> dict:
    """``meta`` with ``parameters`` cut to Time + the requested ones, which must be in the order the
    server declares them (a different order is refused, as hapiclient refused it)."""
    out = dict(meta)
    params = meta.get("parameters") or []
    if not params:
        raise HapiError("/info declares no parameters")
    wanted = [p for p in parameters.split(",") if p] if parameters else []
    if len(wanted) != len(set(wanted)):
        raise HapiError(f"duplicate parameter in {parameters!r}")
    names = [p["name"] for p in params]
    missing = [w for w in wanted if w not in names]
    if missing:
        raise HapiError(f"not in the dataset: {', '.join(missing)}")
    idx = [names.index(w) for w in wanted]
    if idx != sorted(idx):
        raise HapiError(f"parameters must be requested in the server's order: "
                        f"{','.join(n for n in names if n in wanted)}")
    keep = [params[0]] + [params[i] for i in idx if i != 0] if wanted else list(params)
    out["parameters"] = [dict(p) for p in keep]
    for p in out["parameters"]:
        size = p.get("size")
        if size not in (None, 1, [1]):
            raise HapiError(f"{p['name']}: size {size} - only scalar parameters are supported")
    return out


# ---- CSV ------------------------------------------------------------------------------------------
def _number(cell: str) -> float:
    s = cell.strip().strip('"')
    if s == "" or s.lower() in _NAN_TEXT:
        return float("nan")
    return float(s)


def parse_csv(text: str, meta: dict) -> np.ndarray:
    """A HAPI CSV body -> the structured array described in the module docstring.

    ``meta`` is the subset metadata (Time + the requested parameters, in column order)."""
    params = meta["parameters"]
    rows = [r for r in csv.reader(io.StringIO(text), skipinitialspace=True) if r]
    n = len(rows)
    for i, r in enumerate(rows):
        if len(r) != len(params):
            raise HapiError(f"CSV row {i + 1} has {len(r)} columns, the metadata {len(params)}")
    cols = list(zip(*rows)) if rows else [()] * len(params)
    fields, values = [], []
    for p, col in zip(params, cols):
        name, kind = p["name"], p.get("type")
        if kind in ("isotime", "string"):
            text_vals = list(col)          # as pandas.read_csv(skipinitialspace=True) left them
            length = p.get("length")
            if not length:
                length = max([len(v.encode("utf-8")) if kind == "isotime" else len(v)
                              for v in text_vals] or [1])
            if kind == "isotime":
                dt = f"S{int(length)}"
                arr = np.array([v.encode("ascii", "replace") for v in text_vals], dtype=dt)
            else:
                dt = f"U{int(length)}"
                arr = np.array(text_vals, dtype=dt)
        elif kind == "integer":
            try:
                arr = np.array([int(c.strip()) for c in col], dtype="<i4")
                dt = "<i4"
            except ValueError:                      # an empty cell: hapiclient fell back to floats
                arr = np.array([_number(c) for c in col], dtype="<f8")
                dt = "<f8"
        elif kind == "double":
            arr = np.array([_number(c) for c in col], dtype="<f8")
            dt = "<f8"
        else:
            raise HapiError(f"{name}: unsupported HAPI type {kind!r}")
        fields.append((name, dt))
        values.append(arr)
    data = np.empty(n, dtype=fields)
    for (name, _), arr in zip(fields, values):
        if n:
            data[name] = arr
    return data


# ---- the request ----------------------------------------------------------------------------------
def _stamp(t: str) -> str:
    """'2003-11-19T18:00:00Z' -> '20031119T180000' (hapiclient's file-name form, short)."""
    digits = re.sub(r"[^0-9T]", "", t)
    return (digits + "000000")[:15] if "T" in digits else digits


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def fetch(server: str, dataset: str, parameters: str, start: str, stop: str,
          raw_dir: Path | None = None) -> tuple[np.ndarray, dict]:
    """GET /info then /data (CSV) for one dataset and time range: ``(data, meta)``."""
    server = server.rstrip("/")
    full_text = _get(server + "/info", {"id": dataset})
    full = json.loads(full_text)
    _check_status(full, f"{dataset} /info")
    meta = subset_meta(full, parameters)
    q_id, q_start, q_stop = _query_names(full)
    query = {q_id: dataset, q_start: start, q_stop: stop, "format": "csv"}
    if parameters:
        query["parameters"] = parameters
    body = _get(server + "/data", query)
    data = parse_csv(body, meta)
    if raw_dir is not None:
        raw_dir = Path(raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        _write(raw_dir / f"{dataset}.json", full_text)
        _write(raw_dir / f"{dataset}_{_stamp(start)}_{_stamp(stop)}.csv", body)
    meta.update({"x_server": server, "x_dataset": dataset, "x_parameters": parameters,
                 "x_time.min": start, "x_time.max": stop,
                 "x_requestDate": datetime.now().isoformat()[0:19]})
    return data, meta
