"""Doc Agent durable state (PRD §1, §3, §11.1): one JSON file + `.lock`, held for the whole run.

Every `save()` is flushed (write temp → fsync → rename → fsync dir) so a U record or a Log
`in_flight` journal entry exists on disk BEFORE the action that depends on it.
"""
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

STEPS = ("rooming", "memo", "log")
GREEN = {"verified", "already_done", "nothing_to_do"}


class StateBusyError(RuntimeError):
    """Another Doc Agent run holds the lock."""


class DocState:
    def __init__(self, path, data):
        self.path = Path(path)
        self.data = data

    def traveler(self, tm):
        t = self.data.setdefault("travelers", {}).setdefault(tm, {})
        t.setdefault("steps", {s: {"status": "not_run"} for s in STEPS})
        return t

    def set_step(self, tm, step, status, **extra):
        self.traveler(tm)["steps"][step] = {"status": status, **extra}
        self.save()

    def save(self):
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def blocking_travelers(self, tm):
        """Other travelers whose steps are not all green (§3 next-traveler rule)."""
        out = []
        for other, t in self.data.get("travelers", {}).items():
            if other != tm and any(t["steps"][s]["status"] not in GREEN for s in STEPS):
                out.append(other)
        return sorted(out)


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StateBusyError("another Doc Agent run is active (state lock held).") from None
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema": 1}
        yield DocState(path, data)
