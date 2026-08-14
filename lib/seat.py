"""seat.py -- THE shared Ansys seat lock. One definition, imported by all.

This machine has ONE OpticStudio licence seat, and more than one toolchain
drives it: `stray-light-loop` here and `star-stop` in a sibling folder. On
2026-08-10 a star-stop compensator run was terminated 25 s into its C6 set --
no CLIENT_EXIT, no licence check-in, no WER dump on a day WER demonstrably
worked -- at the exact second a Speos run started here. The lock at the time
lived in `lib/.runner.lock`, private to this toolchain, so it kept two runners
apart and was blind to the neighbour actually being harmed.

The lock therefore moved to a machine-local, cross-toolchain path, shared with
`star-stop\\lib\\fixture.ps1`. **Change the format here, change it there.**

WHY NOT UNDER DROPBOX: the seat is a property of THIS MACHINE. A synced lock
would generate conflict copies and let another machine's lock masquerade as
ours -- a guard that reports the wrong machine's state is worse than none.

WHY THIS MODULE EXISTS: the lock was implemented inside `runner.py`, so only
the fleet runner honoured it. Every other Speos driver -- run-pst.py,
run-backtrace.py, confirm-angle.py, screen-angles.py, blast-top.py and the
rest -- launched Speos with no lock at all, and Speos spawns OpticStudio
through `ComponentOpticStudio.Create`, so each of them contends for the same
single seat and the same `optishpc` pool. A guard that one of thirteen callers
observes is a guard that is usually absent.
"""
import os
import subprocess

LOCK = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                    "ansys-seat", "seat.lock")

# RE-ENTRANCY FOR DESCENDANTS OF THE HOLDER.
#
# `runner.py` takes the seat for a whole stage and then, inside that stage,
# launches drivers that are themselves guarded -- confirm-angle.py above all.
# Without re-entrancy the parent deadlocks its own child: the child asks for a
# lock the parent is holding, is correctly refused, and dies.
#
# THAT IS NOT HYPOTHETICAL. It is what happened from the moment the guard was
# added to all thirteen drivers until 2026-08-13. `confirm-angle.py` -- the
# forward measurement that settles a boundary-censored stray angle -- raised
# RuntimeError on EVERY fleet run, the runner caught it, and the message went
# to lib/.stagelogs/confirm-angle.err.txt where nothing surfaced it. Every
# system since then kept its unconfirmed ranked angle and reported
# `resolved: false` -- not because the confirm found nothing, but because it
# never executed. Four published designs were measured at a near-peak angle
# and one headline figure was wrong by 16 percentage points.
#
# The marker is an ENVIRONMENT VARIABLE because that is inherited by child
# processes and by nothing else, which is exactly the scope required: a
# descendant of the holder may proceed, an unrelated process may not.
OWNER_ENV = "SL_SEAT_OWNER_PID"


def owned_by_ancestor():
    """True if this process descends from the process that holds the lock.

    Three conditions, all required. The marker must be present (so we are a
    child of SOMETHING that took the seat); the lock file's pid must still
    equal it (so the seat has not changed hands since we were spawned, which
    would make the inherited marker a lie); and that holder must still be
    alive. Checking only the marker would let a child sail past a lock now
    owned by a different toolchain entirely.
    """
    owner = os.environ.get(OWNER_ENV)
    if not owner:
        return False
    info = read_lock()
    if info.get("pid") != owner:
        return False
    return lock_holder_alive(info)


def _ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True, timeout=60).stdout.strip()


def read_lock():
    """Parse the shared lock file into a dict, or {} if absent/unreadable."""
    try:
        raw = open(LOCK).read().strip()
    except OSError:
        return {}
    out = {}
    for tok in raw.split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            out[k] = v
    return out


def lock_holder_alive(info):
    """True if the recorded pid is still the process that took the lock.

    PID REUSE is real on Windows, and a recycled pid would make a dead lock
    look live FOREVER -- the failure mode of a lock is that it never opens.
    The recorded process start time settles it.
    """
    if not info.get("pid"):
        return False
    start = _ps("$p = Get-Process -Id %s -ErrorAction SilentlyContinue; "
                "if ($p) { $p.StartTime.ToString('o') } else { '' }" % info["pid"])
    if not start:
        return False
    return ("start" not in info) or (start == info["start"])


def hold_lock():
    """True if THIS process currently holds the shared seat lock."""
    return read_lock().get("pid") == str(os.getpid())


def describe_holder():
    """One line naming who holds the seat, for an error a human can act on."""
    info = read_lock()
    if not info:
        return "nobody"
    return "pid %s (%s) since %s" % (info.get("pid", "?"),
                                     info.get("holder", "unknown"),
                                     info.get("at", "?"))


class SeatLock:
    """Only ONE process on this machine may drive the OpticStudio seat.

    The holder may be a toolchain this module has never heard of, so the error
    names whatever wrote the file rather than assuming "another runner".
    A stale lock (dead pid, or a recycled pid with a different start time) is
    reclaimed automatically.

    `who` identifies us in the file so the OTHER toolchain's error message is
    equally actionable -- this is a two-way contract.
    """

    def __init__(self, who):
        self.who = who
        self.nested = False

    def __enter__(self):
        # A descendant of the holder REUSES the seat rather than contending for
        # it. It must not rewrite the lock file (that would steal ownership from
        # its own parent) and must not remove it on exit.
        if owned_by_ancestor():
            self.nested = True
            return self
        if os.path.exists(LOCK):
            info = read_lock()
            if lock_holder_alive(info):
                raise RuntimeError(
                    "the Ansys seat is held by %s. Starting now would contend "
                    "for the single OpticStudio seat and the optishpc pool -- "
                    "a second live solve gets a LaaS 404 that Speos reports "
                    "inside StatusInfo rather than as a failure. Wait for it, "
                    "or remove %s if you are sure it is dead."
                    % (describe_holder(), LOCK))
            print("  (reclaiming stale seat lock from dead pid %s)"
                  % info.get("pid", "?"))
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        start = _ps("(Get-Process -Id %d).StartTime.ToString('o')" % os.getpid())
        import datetime
        with open(LOCK, "w") as f:
            f.write("pid=%d start=%s at=%s holder=%s"
                    % (os.getpid(), start,
                       datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                       self.who))
        # Children spawned from here inherit this and may re-enter.
        os.environ[OWNER_ENV] = str(os.getpid())
        return self

    def __exit__(self, *exc):
        # A nested holder owns nothing and releases nothing. Removing the file
        # here would hand the seat away while our own parent is still using it.
        if self.nested:
            return
        # Only ever remove OUR OWN lock. An unconditional remove would silently
        # unlock a star-stop run that had reclaimed it after we were reaped --
        # handing the seat to the next comer while a live process is mid-sweep.
        if not hold_lock():
            return
        try:
            os.remove(LOCK)
        except OSError:
            pass
        os.environ.pop(OWNER_ENV, None)
