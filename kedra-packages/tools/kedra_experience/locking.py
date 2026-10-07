"""Advisory locking for Kedra's own state.

Scope is deliberately narrow: this locks *one file that Kedra owns*, so that two
Kedra processes cannot interleave a read-decide-write cycle on the state file.
It does not lock dconf, GSettings, GNOME, or anything else on the system --
taking a global desktop lock to write a Kedra bookkeeping file would be wildly
out of proportion and would break unrelated software.

`flock(2)` is used rather than a lock *file whose existence* means "locked":

- The kernel releases it when the file descriptor closes, including on process
  crash or kill -9, so a dead process cannot leave a permanent stale lock.
- It needs no daemon, no PID file and no staleness heuristic.
- It is advisory, which is all that is wanted here: it coordinates Kedra with
  Kedra, and does not interfere with a user editing the file by hand.

The lock lives in a sibling `.lock` file rather than on the state file itself,
because the state file is replaced by `rename(2)` on every save. A lock held on
the old inode would say nothing about the new one.
"""

from __future__ import annotations

import errno
import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path

from .state import STATE_FILENAME, default_state_dir

LOCK_FILENAME = "experience-state.lock"


class StateLocked(Exception):
    """Raised when another process holds the state lock."""


def default_lock_path(environ: dict | None = None) -> Path:
    """$XDG_STATE_HOME/kedra/experience-state.lock.

    Computes a path only; creates nothing.
    """
    return default_state_dir(environ) / LOCK_FILENAME


def lock_path_for(state_path: Path) -> Path:
    """The lock that guards a given state file.

    Derived from the state path so tests and alternate installs lock the file
    they are actually using, never the invoking user's real state.
    """
    state_path = Path(state_path)
    if state_path.name == STATE_FILENAME:
        return state_path.with_name(LOCK_FILENAME)
    return state_path.with_name(state_path.name + ".lock")


@contextmanager
def state_lock(state_path: Path, *, timeout: float = 0.0, poll: float = 0.05):
    """Hold an exclusive advisory lock on the state file's lock sibling.

    `timeout` of 0 fails immediately if the lock is held. A positive timeout
    retries until it elapses. Blocking forever is deliberately not offered: a
    developer tool that hangs indefinitely on a lock is worse than one that
    says who is holding it.

    The lock is released -- and the descriptor closed -- on any exit path,
    including an exception.
    """
    path = lock_path_for(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 0o644: the lock carries no data and no secrets. Opened O_RDWR because
    # some filesystems refuse an exclusive flock on a read-only descriptor.
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise StateLocked(
                        f"another process holds the Kedra state lock at {path}"
                    ) from exc
                time.sleep(poll)
        try:
            yield path
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
