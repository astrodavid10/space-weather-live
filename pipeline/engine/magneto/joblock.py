"""One render at a time - the RUNNING.lock interlock.

geopack is not thread-safe and one render already owns every core, so two
kickoffs must never overlap. The lock is a small JSON file in the kickoff log
directory, created with O_EXCL (atomic on NTFS), holding the owner's pid and
what it is doing. A lock whose pid is dead is stale and is taken over.

    from magneto.joblock import JobLock
    with JobLock("rebuild-all", event="2024-05-10_gannon") as lock:
        if not lock.acquired:
            say("Another render is running: " + lock.describe_owner()); return 3
        ...

The panel's watcher JS reads the same file (Ds.FileAccess(path, 0) == 0 means
"still running"), so the name and location are part of the contract.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from magneto import paths

LOCK_NAME = "RUNNING.lock"


def lock_path() -> Path:
    return paths.kickoff_log_dir() / LOCK_NAME


def pid_alive(pid: int) -> bool:
    """True if a process with this pid exists (Windows: OpenProcess; else kill 0)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            k32 = ctypes.windll.kernel32                     # type: ignore[attr-defined]
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
                STILL_ACTIVE = 259
                return bool(ok) and code.value == STILL_ACTIVE
            finally:
                k32.CloseHandle(h)
        except Exception:                                    # noqa: BLE001
            return True                                      # cannot tell: assume alive
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def status() -> dict | None:
    """The current lock's contents (+ 'alive'), or None when no lock exists."""
    p = lock_path()
    try:
        with open(p, encoding="utf-8") as fh:
            info = json.load(fh)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        info = {"pid": 0, "job": "unreadable lock"}
    info = dict(info) if isinstance(info, dict) else {"pid": 0}
    info["alive"] = pid_alive(info.get("pid", 0))
    info["path"] = str(p)
    return info


class JobLock:
    def __init__(self, job: str, event: str | None = None, pid: int | None = None):
        self.job, self.event = str(job), event
        self.pid = int(pid or os.getpid())
        self.acquired = False
        self.owner: dict | None = None

    def acquire(self) -> bool:
        p = lock_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                cur = status()
                if cur and cur.get("alive"):
                    self.owner = cur
                    return False
                try:                                         # stale: take it over
                    os.remove(str(p))
                except OSError:
                    self.owner = cur
                    return False
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": self.pid, "job": self.job, "event": self.event,
                           "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                           "host": os.environ.get("COMPUTERNAME", "")}, fh)
            self.acquired = True
            return True
        return False

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            cur = status()
            if cur and int(cur.get("pid", 0)) == self.pid:
                os.remove(str(lock_path()))
        except OSError:
            pass
        self.acquired = False

    def describe_owner(self) -> str:
        o = self.owner or {}
        return "{} (pid {}, since {})".format(
            o.get("job", "?") + (" " + str(o["event"]) if o.get("event") else ""),
            o.get("pid", "?"), o.get("started", "?"))

    def __enter__(self) -> "JobLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def wait_free(timeout_s: float = 0.0, poll_s: float = 5.0) -> bool:
    """Block until no live lock exists (or timeout). Returns True when free."""
    t0 = time.time()
    while True:
        cur = status()
        if not cur or not cur.get("alive"):
            return True
        if timeout_s and time.time() - t0 > timeout_s:
            return False
        time.sleep(poll_s)


if __name__ == "__main__":                                 # pragma: no cover
    print(json.dumps(status(), indent=1))
