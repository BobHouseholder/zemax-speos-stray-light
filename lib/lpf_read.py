"""lpf_read.py -- THE .lpf reader. One implementation, imported by all.

Four scripts read Speos ray files, and until 2026-08-09 each carried its own
copy of the same eleven lines of Illumine SWIG boilerplate -- including the
same trap, spelled out in each: `InitLpfFileName` returns an ERROR OBJECT that
is truthy even on success, so branching on it silently accepts a failed open.
Four copies of one convention is the shape this codebase has been bitten by
three times (see field_slots.py). So: one module, imported.

WHAT CHANGED, AND WHAT DID NOT
------------------------------
The backend is now `ansys.speos.core.lxp` (PySpeos) instead of the Illumine
SWIG bindings. That matters because Illumine loads under Ansys's bundled
CPython **3.10 and nothing else**, which forced every ray-reading script to run
under a second interpreter. `lxp` is ordinary Python.

The numbers did not change, and that was verified rather than assumed: over 30
`SV_Back_*` files spanning 85 systems (1,387,410 rays), the two backends agree
on trace count, discarded count, escaping count, min/median/max escape angle
and **every bin of the 2-degree histogram** -- 30/30 identical, mean differing
by 3.9e-14 (float summation order). Both regimes were covered: files where
every ray escapes, and files discarding 8,638 of 40,882.

THE COST, MEASURED
------------------
`lxp` needs a running `SpeosRPC_Server.exe`, which checks out ONE
`speos_level1` seat (of 10) the moment it launches -- not lazily on read, and
it holds it for the server's whole life. Measured concurrently with a real
Speos sweep: 68 s against a 73.7 s control, `SWEEP OK` both times, no
licensing error. So it does not contend with a simulation on this licence.

Consequences encoded below:
  * ONE server per process, created lazily and reused. Never one per file --
    that would be a 5-second launch and a seat churn per `.lpf`.
  * Closed at exit, always. A leaked seat is this toolchain's oldest failure
    mode, and an OpticStudio seat was found leaked during this very
    investigation, held by a process that had finished.
"""
import atexit
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402

_SPEOS = None


def _server():
    """The one RPC server for this process. Launched on first use."""
    global _SPEOS
    if _SPEOS is None:
        from ansys.speos.core import launcher
        _SPEOS = launcher.launch_local_speos_rpc_server(
            version=settings.ANSYS_VERSION)
        atexit.register(close)
    return _SPEOS


def close():
    """Release the server and its licence seat. Idempotent."""
    global _SPEOS
    if _SPEOS is not None:
        s, _SPEOS = _SPEOS, None
        try:
            s.close()
        except Exception:                                      # noqa: BLE001
            pass                     # a failed close must not mask a result


def read(path):
    """Open a .lpf and return its LightPathFinder.

    Raises on a file that is absent or carries no traces. The old Illumine
    path could not test the open directly -- its return value was truthy
    whatever happened -- so the trace count was the only honest validation.
    That check is kept, because it is still the one that matters.
    """
    if not os.path.isfile(path):
        raise IOError("no such .lpf: %s" % path)
    from ansys.speos.core import lxp
    f = lxp.LightPathFinder(speos=_server(), path=path)
    if f.nb_traces <= 0:
        raise RuntimeError("no traces in %s" % path)
    return f


def nb_traces(path):
    return read(path).nb_traces


def last_directions(path):
    """(iterable of (dx, dy, dz), trace count).

    The native escape direction per ray. Do NOT derive this from impacts: the
    final impact is the last GEOMETRY interaction and a sensor is not geometry,
    so the last impact-to-impact vector is the segment ARRIVING at that
    surface -- inside the glass for a refracting exit.
    """
    f = read(path)
    return (r.last_direction for r in f.rays), f.nb_traces
