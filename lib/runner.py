"""runner.py -- stage orchestrator for a stray-light job.

    python runner.py <manifest.job.json> [--dry-run] [--force STAGE] [--only STAGE]

Replaces hand-driving ~30 runs per system. Provides what that lacked:

  * RESUME      -- a stage whose outputs exist is skipped, so an interrupted
                   job restarts where it stopped instead of from zero.
  * TIMEOUTS    -- every stage is bounded. A hung ZOS-API call previously ate
                   3.5 h before anyone noticed.
  * ISOLATION   -- one process per OpticStudio session, always waited on.
                   Looping sessions inside one shell leaks licence seats.
  * HYGIENE     -- seat holders (zemax-mcp) and stale help viewers are cleared
                   before any OpticStudio stage.
  * PROVENANCE  -- each stage stamps the hashes of the scripts and inputs that
                   produced its outputs into the manifest.
  * HALT ON NO-GO -- preflight blocks the rest of the pipeline.

Config paths are PER-RUN as of 2026-08-04 (pst_read.survey_config): the wire
scripts read SL_SURVEY_CONFIG / SL_PST_CONFIG, falling back to the old fixed
paths. The previous note here said Speos stages "must stay serialised" because
of that fixed path -- which was true of the CONFIG but was never enforced by
anything, and a fleet run and a band driver duly ran together and crossed
configs. The remaining reason to serialise is the LICENCE, not the file: a
second live Speos makes the Ansys OPTIS HPC checkout return a LaaS 404, and
Speos reports that inside StatusInfo rather than as a failure.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402
import job as J
import seat  # noqa: E402 -- for OWNER_ENV only; the lock itself is re-implemented below
from guard import GuardError, assert_file, assert_speos_run, load_json_checked

BASE = J.BASE
LIB = os.path.join(BASE, "lib")
PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
SPEOS = settings.SPEOS_LAUNCHER
FLV = settings.PYTHON_EXE
# The Illumine SWIG bindings that read .lpf ray files load ONLY under the
# CPython that ships inside Speos. A python.org 3.10 will not do.
# ANSYS_PY (Ansys bundled CPython 3.10) is no longer needed by any stage: the
# only consumer was angle_select, which now reads .lpf via lib/lpf_read.py.
# It remains in settings for the three operator-run analysers that still use
# the Illumine bindings -- see lpf_read's header.
# Per-run, not the shared survey/survey-config.txt. The runner's own LOCK keeps
# two runners apart, but it does not keep a runner apart from run-pst.py,
# run-backtrace.py, screen-angles.py or the band driver -- and on 2026-08-04 a
# fleet run and a band run were live together while both config globals were
# being rewritten every few minutes. The lock was never the whole guard.
import pst_read  # noqa: E402
SPEOS_CFG = pst_read.survey_config("runner")


# THE SHARED ANSYS SEAT LOCK. Was BASE/lib/.runner.lock -- private to this
# toolchain, so it kept two runners apart and was BLIND to star-stop, which
# drives the same single OpticStudio seat from Dropbox\Optics\star-stop and had
# no guard at all. On 2026-08-10 a star-stop compensator run was terminated
# 25 s into its C6 set (no CLIENT_EXIT, no licence check-in, no WER dump on a
# day WER demonstrably worked) at the exact second a Speos run started here.
# A guard aimed at the wrong population is the defect. Machine-local, NOT under
# Dropbox: the seat is a property of this machine and a synced lock would
# generate conflict copies and let another machine's lock masquerade as ours.
# Format is key=value, shared with star-stop\lib\fixture.ps1 -- change one, change both.
LOCK = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                    "ansys-seat", "seat.lock")


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

    PID REUSE is real on Windows, and a recycled pid would make a dead lock look
    live FOREVER -- the failure mode of a lock is that it never opens. The
    recorded process start time settles it.
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
    info = read_lock()
    return info.get("pid") == str(os.getpid())
FORCE_PREFLIGHT = False   # set from --force; bypasses the verdict cache too


class RunnerLock:
    """Only ONE process on this machine may hold the OpticStudio licence seat.

    Two concurrent runners deadlock each other: both call clear_seat(), each
    killing the other's helper processes, and neither finishes. Observed for
    real -- a shell quirk launched a duplicate runner on the same manifest and
    the pair span forever. A stale lock (dead PID) is reclaimed automatically.

    Since 2026-08-10 the lock is SHARED with star-stop (see the LOCK comment):
    the holder may be a toolchain this module has never heard of, so the error
    names whatever wrote the file rather than assuming "another runner".
    """

    def __enter__(self):
        if os.path.exists(LOCK):
            info = read_lock()
            if lock_holder_alive(info):
                raise GuardError(
                    "the OpticStudio seat is held by pid %s (%s) since %s. "
                    "Starting now would force-kill its run. Wait for it, or "
                    "remove %s if you are sure it is dead."
                    % (info.get("pid", "?"), info.get("holder", "unknown"),
                       info.get("at", "?"), LOCK))
            print("  (reclaiming stale lock from dead pid %s)"
                  % info.get("pid", "?"))
        os.makedirs(os.path.dirname(LOCK), exist_ok=True)
        start = _ps("(Get-Process -Id %d).StartTime.ToString('o')" % os.getpid())
        with open(LOCK, "w") as f:
            f.write("pid=%d start=%s at=%s holder=stray-light-loop/runner.py"
                    % (os.getpid(), start, J.now().replace(" ", "T")))
        # PUBLISH OURSELVES AS THE OWNER so the guarded drivers this runner
        # launches -- confirm-angle.py above all -- re-enter instead of
        # deadlocking against their own parent. See seat.OWNER_ENV for the
        # failure this repairs: the forward confirm raised RuntimeError on every
        # fleet run from the day the guard landed until 2026-08-13, and the
        # error went to a stage log nobody reads.
        #
        # NOTE THE DUPLICATION. This class re-implements lib/seat.py's lock
        # rather than importing it, so the two must agree on the marker name --
        # which is why it is imported from there and not spelled again here.
        # Unifying them is the real fix and is deliberately not attempted in
        # the same change as repairing a live defect.
        os.environ[seat.OWNER_ENV] = str(os.getpid())
        return self

    def __exit__(self, *exc):
        # Only ever remove OUR OWN lock. Now that the file is shared, an
        # unconditional remove here would silently unlock a star-stop run that
        # had reclaimed it after we were reaped -- handing the seat to the next
        # comer while a live process is mid-sweep.
        if not hold_lock():
            return
        try:
            os.remove(LOCK)
        except OSError:
            pass
        os.environ.pop(seat.OWNER_ENV, None)


def clear_seat():
    """No other process may hold the single OpticStudio licence seat.

    An ORPHANED OpticStudio counts. A leaked headless session does not always
    hang the next call outright -- observed 2026-07-25, it degraded a sweep from
    ~80 s to ~5 min per case while sitting at 1.1 s of CPU and 132 MB resident.
    Killing it restored throughput immediately. The diagnostic either way is a
    live OpticStudio process that is not using any CPU.
    """
    # THE KILL IS MACHINE-WIDE AND PID-BLIND, so it is gated on OWNING the seat.
    # Unguarded, this reaches into any other toolchain's OpticStudio: on
    # 2026-08-10 a star-stop compensator run was terminated mid-sweep with no
    # crash dump and no licence check-in, and the only evidence it had ever run
    # was a licence CHECKOUT with no matching CLIENT_EXIT. Refusing to fire
    # without the lock makes that impossible rather than merely unlikely.
    if not hold_lock():
        raise GuardError(
            "clear_seat() refused: this process does not hold the shared seat "
            "lock (%s). It force-kills EVERY OpticStudio on the machine, so it "
            "must only run inside a RunnerLock." % LOCK)
    for name in ("ZemaxMCP.Server", "ANSYSHelpViewer", "OpticStudio"):
        # DEVNULL, not pipes: see run() for why pipes deadlock around here
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-Process -Name '%s' -ErrorAction SilentlyContinue | "
                        "Stop-Process -Force" % name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, timeout=60)


def run(cmd, timeout, label, needs_seat=False):
    """Run a stage, capturing output to FILES -- never to pipes.

    capture_output=True deadlocks here: a stage launches PowerShell, which
    launches OpticStudio as a GRANDCHILD that inherits the stdout pipe handle.
    On timeout Python kills PowerShell but not OpticStudio, then calls
    communicate() again during cleanup -- which blocks forever waiting for an
    EOF the surviving grandchild never sends. Observed for real: preflight
    takes 8 s standalone and hung ~52 min under the runner, with the child
    already gone. File redirection removes the inherited pipe entirely, so the
    timeout can actually fire.
    """
    if needs_seat:
        clear_seat()
    logdir = os.path.join(BASE, "lib", ".stagelogs")
    os.makedirs(logdir, exist_ok=True)
    op = os.path.join(logdir, "%s.out.txt" % label)
    ep = os.path.join(logdir, "%s.err.txt" % label)
    t0 = time.time()
    with open(op, "w") as fo, open(ep, "w") as fe:
        proc = subprocess.Popen(cmd, stdout=fo, stderr=fe, stdin=subprocess.DEVNULL)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=30)
            raise GuardError(
                "STAGE TIMEOUT [%s] exceeded %ss -- likely a hung ZOS-API call. "
                "Stage output: %s" % (label, timeout, op))
    dt = time.time() - t0

    class R:
        pass
    r = R()
    r.returncode = proc.returncode
    r.stdout = open(op, errors="replace").read()
    r.stderr = open(ep, errors="replace").read()
    return r, dt


# ---------------------------------------------------------------- stages
def st_preflight(m, wd, slug):
    """ALWAYS enforced, never skipped.

    A cached verdict is reused (no OpticStudio launch), but the verdict itself
    is always applied. Treating preflight as an ordinary resumable stage let a
    cached NO-GO be skipped, and the pipeline sailed on to burn a Speos run on
    a system already known to be unimportable -- precisely the waste the gate
    exists to prevent.
    """
    out = J.path_for(slug, wd, "preflight")
    script = os.path.join(LIB, "preflight.ps1")
    # CACHE KEY MUST COVER THE CODE, NOT JUST THE DATA. Keyed on the lens hash
    # alone, a verdict computed BEFORE a gate existed was replayed after it was
    # added -- schamm110 sailed through a gate written specifically to reject
    # it, even under --force. Hash the gate script into the key so any change
    # to the rules invalidates every cached verdict.
    key = J.sha(m["lens"]) + ":" + J.sha(script)
    dt = 0.0
    if os.path.exists(out) and key == m.get("preflightKey") and not FORCE_PREFLIGHT:
        print("    (reusing cached verdict: lens and gate rules both unchanged)")
    else:
        p, dt = run(PS + [script, "-LensFile", m["lens"], "-OutJson", out],
                    300, "preflight", needs_seat=True)
        m["preflightKey"] = key
        m.pop("lensHash", None)          # superseded by preflightKey
    assert_file(out, "preflight verdict")
    v = load_json_checked(out, "preflight verdict")
    m["preflight"] = {"verdict": v["verdict"], "blocks": v["blocks"],
                      "warnings": v["warnings"]}
    if v["verdict"] == "NO-GO":
        raise GuardError("PREFLIGHT NO-GO for %s:\n  - %s"
                         % (slug, "\n  - ".join(v["blocks"])))
    return [out], [os.path.join(LIB, "preflight.ps1")], [m["lens"]], dt


def st_layout(m, wd, slug):
    out = J.path_for(slug, wd, "layout")
    script = os.path.join(BASE, "survey", "trace-layout-generic.ps1")
    p, dt = run(PS + [script, "-LensFile", m["lens"], "-OutJson", out],
                420, "layout", needs_seat=True)
    assert_file(out, "layout")
    d = _check_layout(load_json_checked(out, "layout"), slug)
    m["optics"].update({"imgZ": d["imgZ"], "imgSD": d["imgSD"],
                        "maxField": d["maxField"], "primaryWave": d["primaryWave"]})
    return [out], [script], [m["lens"]], dt


def _check_layout(d, slug):
    """A parseable layout file is not a VALID prescription.

    zmxtmpfacb23's layout stage 'succeeded' while writing zero surfaces,
    imgZ=0 and stopSurf=-1; the emptiness only surfaced two stages later as
    'max() iterable argument is empty' inside the mech generator. Validate
    content at the stage that produces it, where the error is actionable.
    """
    n = len(d.get("surfaces") or [])
    if n < 3:
        raise GuardError("layout for %s has only %d surface(s) -- the trace "
                         "produced an empty prescription (unloadable or "
                         "template .zmx?)" % (slug, n))
    if not d.get("imgZ"):
        raise GuardError("layout for %s has imgZ=0 -- no image distance" % slug)
    if not any(s.get("glass") for s in d["surfaces"]):
        raise GuardError("layout for %s has no glass on any surface -- "
                         "materials did not resolve (missing catalog?)" % slug)
    return d


def st_odx(m, wd, slug):
    out = J.path_for(slug, wd, "odx")
    script = os.path.join(BASE, "export-odx.ps1")
    p, dt = run(PS + [script, "-LensFile", m["lens"]], 420, "odx", needs_seat=True)
    assert_file(out, "ODX export", min_bytes=1024)
    return [out], [script], [m["lens"]], dt


def st_mech(m, wd, slug):
    script = os.path.join(BASE, "survey", "make-survey-mech.py")
    lay = J.path_for(slug, wd, "layout")
    p, dt = run([FLV, script, lay, wd, slug], 600, "mech")
    outs = [J.path_for(slug, wd, "mech_base"), J.path_for(slug, wd, "mech_seat"),
            J.path_for(slug, wd, "params")]
    for o in outs:
        assert_file(o, "mechanics %s" % os.path.basename(o))
    prm = load_json_checked(outs[2], "mech params")
    m["sim"].update({"strayDeg": prm["strayDeg"], "zSrc": prm["zSrc"],
                     "rSrc": prm["rSrc"], "rDisc": prm["rDisc"],
                     "zCatch": prm["zCatch"], "waveNm": prm["wave"]})
    m["optics"]["elements"] = prm["elements"]
    if prm.get("worstFail", 0) > 0:
        # HALT, do not warn. A blocked beam still yields a beautiful stray
        # number (schamm110: -91% stray, in-field EXACTLY ZERO) -- the barrel
        # was blocking the signal too. A geometry that fails its own envelope
        # check must never reach the Speos stage and become a "result".
        raise GuardError(
            "mech envelope FAILS by %.2f mm for %s -- the barrel obstructs the "
            "imaging beam. Any stray reduction from this geometry is an "
            "artefact of blocking the signal." % (prm["worstFail"], slug))
    return outs, [script], [lay], dt


def st_ghost_opt(m, wd, slug):
    """Minimise double-bounce ghost focus on a COPY of the lens. OPT-IN.

    Off unless the manifest carries `ghost: {"optimise": true}`. It is off by
    default because it produces a DIFFERENT lens, and a stage that silently
    changes the optics would change what every stray-light number downstream
    means without changing its name.

    It also does not repoint `m["lens"]`. The optimised design is measured by
    staging `<slug>-ghost.zmx` as its own job -- two jobs through one pipeline,
    which is comparable, rather than one job whose input moved.
    """
    if not (m.get("ghost") or {}).get("optimise"):
        return [], [], [], 0.0
    out = J.path_for(slug, wd, "ghostopt")
    script = os.path.join(BASE, "ghost", "ghost-optimise.ps1")
    g = m.get("ghost") or {}
    cmd = PS + [script, "-LensFile", m["lens"], "-OutJson", out, "-Slug", slug,
                "-TopN", str(g.get("topN", 3)),
                "-GhostWeight", str(g.get("weight", 1.0)),
                "-DenseFields", str(g.get("denseFields", 11))]
    p, dt = run(cmd, 1800, "ghost_opt", needs_seat=True)
    assert_file(out, "ghost optimisation")
    load_json_checked(out, "ghost optimisation")
    assert_file(J.path_for(slug, wd, "lens_ghost"), "ghost-optimised lens")
    return ([out, J.path_for(slug, wd, "lens_ghost")], [script], [m["lens"]], dt)


def st_s0(m, wd, slug):
    out = J.path_for(slug, wd, "s0")
    script = os.path.join(BASE, "ghost", "s0-ghosts.ps1")
    p, dt = run(PS + [script, "-LensFile", m["lens"], "-OutJson", out, "-Slug", slug],
                420, "s0", needs_seat=True)
    assert_file(out, "S0 ghost enumeration")
    load_json_checked(out, "S0 ghost enumeration")   # catches empty-value corruption
    return [out], [script], [m["lens"]], dt


def _sim(m, wd, slug, variant, script_name="wire-survey.py", tag=None):
    cfg = J.render_speos_config(m, variant, J.path_for(slug, wd, "config", variant))
    # wire-survey.py reads the per-run path pst_read exported; copy it there
    with open(cfg) as f:
        body = f.read()
    with open(SPEOS_CFG, "w") as f:
        f.write(body)
    script = os.path.join(BASE, "survey", script_name)
    if not os.path.exists(script):
        script = os.path.join(BASE, "testcases", script_name)
    log = body.strip().split("\n")[-1]
    if tag:
        # the optics-only run writes its own log so it cannot be mistaken for,
        # or overwrite, the with-mechanics result for the same variant
        base, ext = os.path.splitext(log)
        log = "%s-%s%s" % (base, tag, ext)
    if os.path.exists(log):
        os.remove(log)                       # force a fresh, unambiguous result
    label = "sim-" + (tag or variant)
    p, dt = run([SPEOS, "/RunScript=%s" % script, "/Headless=True", "/Splash=False",
                 "/Welcome=False", "/ExitAfterScript=True"], 1800, label)
    end = "wire-optics-only end" if tag == "opticsonly" else "wire-survey end"
    assert_speos_run(log, "sim %s" % (tag or variant), end_marker=end,
                     fatal_status=True)
    return [log, cfg], [script], [J.path_for(slug, wd, "odx")], dt


def st_back_trace(m, wd, slug):
    """MEASURE the stray angle instead of guessing it.

    Runs a backward trace -- the detector emits, and the directions in which
    rays escape the front are, by reciprocity, the directions from which an
    external source can reach the detector. The out-of-field peak of that
    distribution is the angle worth simulating forward.

    Replaces `strayDeg = maxField + 6`, a placeholder that had already put the
    "stray" source INSIDE the design field on systems wider than 34 deg.

    THE MECHANICS MUST BE PRESENT. An optics-only version of this stage was
    built first, on the argument that the lens train dominates because "88.2%
    of out-of-field power arrives with no mechanical scatter". That conflates
    SCATTER with BLOCKING: the barrel sets which angles get in regardless of
    whether the surviving light scatters off it. Measured on the Double Gauss:
        optics-only    -> 37 deg  (the right answer ranked 4th)
        with mechanics -> 19 deg  (measured forward PST peak: 20 deg)

    Runs AFTER mech, which costs nothing: `strayDeg` is only ever written into
    params.json and feeds no geometry, so this stage patches the angle in place
    before the stray sims consume it.

    A failure here is NOT fatal -- the heuristic value written by mech stands,
    and `strayDegSource` records which was used.
    """
    # mech has run, so the standard renderer has everything. The "redesign"
    # variant points at the seated barrel, which is the geometry whose angular
    # acceptance we want to measure.
    cfg = J.render_speos_config(m, "redesign", J.path_for(slug, wd, "config", "backtrace"))
    with open(cfg) as f:
        body = f.read()
    with open(SPEOS_CFG, "w") as f:
        f.write(body)

    log = body.strip().split("\n")[-1]
    base, ext = os.path.splitext(log)
    log = "%s-backtrace%s" % (base, ext)
    if os.path.exists(log):
        os.remove(log)

    script = os.path.join(BASE, "survey", "wire-back-trace.py")
    p, dt = run([SPEOS, "/RunScript=%s" % script, "/Headless=True",
                 "/Splash=False", "/Welcome=False", "/ExitAfterScript=True"],
                1800, "back-trace")
    assert_speos_run(log, "back-trace", end_marker="wire-back-trace end",
                     fatal_status=True)

    # The LPF reader loads ONLY under Ansys CPython, so selection runs as a
    # subprocess and hands back JSON rather than being imported here.
    # GetResultFilePaths() does NOT list the .lpf even when it is written, so
    # find it on disk rather than trusting the reported result list.
    out = J.path_for(slug, wd, "strayangle")
    cands = glob.glob(os.path.join(wd, "**", "BT_*.FrontCatch.lpf"),
                      recursive=True)
    if cands:
        lpf = max(cands, key=os.path.getmtime)
        # FLV, not ANSYS_PY: angle_select reads .lpf through lib/lpf_read.py
        # (PySpeos lxp) as of 2026-08-09 and no longer needs Ansys's bundled
        # CPython 3.10. Verified identical on 19 stored answers.
        run([FLV, os.path.join(BASE, "lib", "angle_select.py"),
             lpf, str(m["optics"]["maxField"]), out], 600, "angle-select")
    else:
        json.dump({"ok": False, "reason": "no backward LPF produced",
                   "strayDeg": None}, open(out, "w"), indent=1)

    # --- patch the angle into params.json AND the live manifest -------------
    # Both: params.json is what the wire scripts read, m["sim"] is what later
    # stages in THIS process read. Updating one and not the other is exactly
    # the kind of split-source bug this codebase has shipped before.
    sel = json.load(open(out, encoding="utf-8-sig"))
    prm_path = J.path_for(slug, wd, "params")
    prm = json.load(open(prm_path, encoding="utf-8-sig"))
    if sel.get("ok") and sel.get("strayDeg"):
        prm["strayDeg"] = round(float(sel["strayDeg"]), 1)
        prm["strayDefined"] = prm["strayDeg"] <= 85.0
        prm["strayDegSource"] = "inverse-trace"
        prm["strayDegCandidates"] = sel.get("candidates")
        # Carry the selector's own verdict on whether it RESOLVED a peak or
        # merely stopped at the edge of its search window. Writing the flag and
        # leaving it unread is the failure this codebase has already shipped
        # twice, so it is consumed three lines further down and again in
        # score-loop.
        prm["strayDegRankResolved"] = bool(sel.get("resolved", True))
        prm["strayDegCensored"] = bool(sel.get("censored", False))
        with open(prm_path, "w") as f:
            json.dump(prm, f, indent=1)
        print("      stray angle RANKED %.1f deg (heuristic said %s)"
              % (prm["strayDeg"], prm.get("strayDegFallback")))

        # --- CONFIRM: the backward trace ranks, it does not measure ---------
        # On wideangle32 rank 1 was 33 deg and the measured peak was 35 deg
        # (candidate #2), on a peak only ~4 deg wide -- rank 1 sat on 37% of
        # the available signal. A short forward sim per candidate settles it.
        # Non-fatal: on failure the ranked angle stands.
        #
        # THE CONFIRM IS THE ESCALATION THE `resolved` FLAG ASKS FOR. When the
        # rank is boundary-censored the histogram has not located a peak at all
        # (petzval4: ranked 5 deg, forward peak 14 deg on the same seated
        # geometry, 5.6x the flux), so a forward measurement is the only thing
        # that can settle the angle -- and if it does not run, that has to be
        # visible downstream rather than assumed away.
        if not prm["strayDegRankResolved"]:
            print("      !! rank is BOUNDARY-CENSORED (first admissible bin) "
                  "- the forward confirm is what settles this one")
        try:
            run([FLV, os.path.join(BASE, "survey", "confirm-angle.py"), slug],
                2400, "confirm-angle")
            prm = json.load(open(prm_path, encoding="utf-8-sig"))
        except Exception as e:
            print("      confirm step failed (%s); keeping the ranked angle" % e)

        # Resolved either because the histogram located an interior peak, or
        # because the forward confirm measured one decisively. Neither => the
        # angle is a lower bound on the peak, not the peak, and every number
        # measured at it inherits that.
        prm["strayDegResolved"] = bool(prm.get("strayDegRankResolved")
                                       or prm.get("strayDegConfirmDecisive"))
        with open(prm_path, "w") as f:
            json.dump(prm, f, indent=1)
        m["sim"]["strayDeg"] = prm["strayDeg"]
        m["sim"]["strayDegResolved"] = prm["strayDegResolved"]
        # README's "check it actually ran" instruction tells the reader to look
        # for `strayDegSource` IN THE MANIFEST, and it lived only in
        # <slug>-params.json -- so following the documented check on a fully
        # confirmed angle found nothing and, by the README's own words, meant
        # "the angle is a ranking and nothing more". A false alarm on the one
        # check guarding the failure mode that already cost a full re-measure.
        # Carried here so the documented check works where it is documented.
        m["sim"]["strayDegSource"] = prm.get("strayDegSource")
        print("      stray angle FINAL %.1f deg [%s]%s"
              % (prm["strayDeg"], prm.get("strayDegSource"),
                 "" if prm["strayDegResolved"]
                 else "  ** UNRESOLVED - neither the rank nor the confirm "
                      "located a peak; treat this angle as a lower bound **"))
    else:
        print("      stray angle NOT measured (%s); keeping heuristic %s deg"
              % (sel.get("reason", "?"), prm.get("strayDeg")))

    return [log, cfg, out, prm_path], [script], [J.path_for(slug, wd, "odx")], dt


def st_angle_gate(m, wd, slug):
    """ENFORCE `strayDefined`. Always runs; never resumed. Costs milliseconds.

    `strayDefined = strayDeg <= 85` has been written into params.json since the
    40-degree cap was replaced, with the derivation recorded beside it: the
    source disc sits at z = -40 ahead of the entrance, so at 90 deg it is
    edge-on to the entrance plane and beyond that it is BEHIND it, where no
    front-facing barrel can baffle it. Past that limit there is no stray
    measurement to make -- only a source that cannot illuminate the system.

    Until now the only consumer was score-loop.py. The survey path computed the
    flag correctly, stored it faithfully, and simulated anyway: wideanglelen100
    carried `strayDefined: false` with strayDeg 106 the whole way to a
    published table, where its 0 W read as a suspicious -100%.

    A SEPARATE STAGE WITH NO RESUME KINDS, for the same reason preflight has
    none: a verdict that can be skipped is not a gate. Folding this into
    st_back_trace made it invisible to any job whose back_trace resumed -- and
    a hydrator is the wrong tool too, since a GuardError there means "artifact
    unusable, re-run", not "halt". Placed after back_trace because that is
    where the FINAL angle appears (the confirm step can move it), and before
    s0 and all three sims.
    """
    prm_path = J.path_for(slug, wd, "params")
    prm = load_json_checked(prm_path, "mech params")
    deg, src = prm.get("strayDeg"), prm.get("strayDegSource", "?")
    if prm.get("strayDefined") is False:
        raise GuardError(
            "stray angle %s deg is OUTSIDE the measurable range (> 85 deg). "
            "The source disc would sit at or behind the entrance plane, where "
            "no front-facing barrel can baffle it -- so a 'stray reduction' "
            "here would compare two numbers that are not stray light. This "
            "system needs a different archetype, not a measurement. "
            "(maxField %s deg, angle from %s)"
            % (deg, prm.get("maxField"), src))
    print("      stray angle %s deg is measurable [%s]" % (deg, src))
    return [], [os.path.abspath(__file__)], [prm_path], 0.0


def st_sim_optics(m, wd, slug):
    """In-field flux with NO MECHANICS - the denominator that makes in-field
    throughput measurable at all.

    The old in-field metric was flux(field i) / flux(axial), both with
    mechanics. That is only throughput while the imported per-field ODX source
    couples into the pupil, and it stops doing so off-axis: on B01 the corner
    source delivers 0.044 W against 0.873 W axial with NO mechanics present, so
    ~95% of the "loss" is the source, not the barrel. Dividing by axial buries
    the mechanics in a coupling artefact.

    Dividing by the SAME FIELD with no mechanics cancels the coupling exactly -
    it is identical in numerator and denominator - and leaves only what the
    mechanics do:

        T(field) = flux_with_mech(field) / flux_optics_only(field)

        T ~ 1  mechanics neither block nor add
        T < 1  genuine obstruction (vignetting)
        T > 1  the mechanics ADD non-imaging light - measured 1.515 on B01's
               naive tube, which is the withdrawn "+91%/+92% corner recovery"
               seen directly

    Runs once per system: with no mechanics inserted there is nothing to
    distinguish a baseline from a redesign, so both variants share it.

    It is also the COUPLING CHECK, which is why it is ordered first -- and
    that check was never actually written. wideanglelen100 (200 deg FOV) got
    all the way to a published table before anyone read this stage's own
    output: with NO mechanics present its 50 deg and 100 deg fields deliver
    0 W against 0.379 W on axis. The ODX model simply does not propagate them.
    Every downstream number was then a ratio against zero -- the stray sim
    read 0 W and was quarantined as "suspect baseline", which sent the
    investigation to the barrel and the source placement, neither of which
    was at fault.

    A denominator of zero is not a measurement. Halt here.
    """
    out = _sim(m, wd, slug, "base", script_name="wire-optics-only.py",
               tag="opticsonly")
    log = os.path.join(wd, "result-%s_base-opticsonly.txt" % m["simPrefix"])
    dead, unclean = [], []
    for tag, field, flux, status in _optics_only_fluxes(m, wd, log):
        if status:
            # A sim that errored did not measure anything. Its report is
            # whatever the PREVIOUS run left behind -- reading that as a
            # result is the staleness bug all over again, and it nearly got
            # in here: a licence-failed 50 deg field left a stale 0 W report
            # that this check would have scored as a coupling failure.
            unclean.append("%s: %s" % (tag, status[:90]))
        elif flux is not None and flux <= 0.0:
            dead.append("%s (imported field %s) = 0 W" % (tag, field))
    if unclean:
        raise GuardError(
            "optics-only did not complete cleanly, so coupling is UNPROVEN "
            "(not disproven): %s. Re-run once the tool is healthy."
            % "; ".join(unclean))
    if dead:
        raise GuardError(
            "optics-only coupling failed: %s. With no mechanics present the "
            "optical model does not deliver these fields at all, so every "
            "in-field ratio and the stray baseline would divide by zero. "
            "This system is outside the archetype -- not a mechanics result."
            % "; ".join(dead))
    return out


def _optics_only_fluxes(m, wd, log):
    """(sim tag, imported field, flux) for each optics-only in-field run.

    The mapping lives in the sim log ('OO_F2v_x <- imported field 3 of 5'), so
    the field is REPORTED rather than guessed -- the same renaming that once
    made an F3 look like an 18x drop.

    CAVEAT: a log written before mapping-logging existed yields no pairs, and
    the caller then passes VACUOUSLY -- no fields checked reads the same as no
    fields dead. Any system re-run under the current pipeline gets real
    coverage; one resumed from an older log gets none. Not made fatal, because
    that would reject legitimately old runs, but it is why the audit layer
    tracks code versions separately.
    """
    import re as _re
    pairs, status = [], {}
    if os.path.exists(log):
        for line in open(log, encoding="utf-8", errors="replace"):
            g = _re.search(r"(OO_F\d+v_\S+) <- imported field (\d+) of (\d+)", line)
            if g:
                pairs.append((g.group(1), g.group(2)))
            # "TAG computed. StatusInfo=[]" is clean; anything else is not.
            c = _re.search(r"(OO_F\d+v_\S+) computed\. StatusInfo=\[(.*)$", line)
            if c:
                status[c.group(1)] = c.group(2).rstrip("]").strip()
    for tag, field in pairs:
        rep = None
        d = os.path.join(wd, "SPEOS output files")
        for root, _dirs, files in os.walk(d) if os.path.isdir(d) else []:
            if "%s.Report.html" % tag in files:
                rep = os.path.join(root, "%s.Report.html" % tag)
                break
        flux = None
        if rep:
            import re as _re2
            t = open(rep, encoding="utf-8", errors="replace").read()
            mm = _re2.search(r"<li>Flux: ([0-9.eE+-]+) W</li>", t)
            flux = float(mm.group(1)) if mm else None
        yield tag, field, flux, status.get(tag, "")


def st_sim_base(m, wd, slug):
    return _sim(m, wd, slug, "base")


def st_sim_redesign(m, wd, slug):
    return _sim(m, wd, slug, "redesign")


def hy_layout(m, wd, slug):
    d = load_json_checked(J.path_for(slug, wd, "layout"), "layout")
    m["optics"].update({"imgZ": d["imgZ"], "imgSD": d["imgSD"],
                        "maxField": d["maxField"], "primaryWave": d["primaryWave"]})


def hy_mech(m, wd, slug):
    prm = load_json_checked(J.path_for(slug, wd, "params"), "mech params")
    m["sim"].update({"strayDeg": prm["strayDeg"], "zSrc": prm["zSrc"],
                     "rSrc": prm["rSrc"], "rDisc": prm["rDisc"],
                     "zCatch": prm["zCatch"], "waveNm": prm["wave"]})
    m["optics"]["elements"] = prm["elements"]


# HYDRATORS: a resumed stage must still re-populate the manifest state that
# LATER stages read. Skipping a stage skipped its side effect too, so a job
# resumed after a mid-run crash reached the Speos stage with empty sim
# parameters and failed with "config line 7 is empty". Resume = re-hydrate,
# not merely skip.
HYDRATE = {"layout": hy_layout, "mech": hy_mech}

STAGES = [
    # preflight has NO resume kinds on purpose: it must run (or re-apply its
    # cached verdict) every time, so a NO-GO always halts the pipeline.
    ("preflight", st_preflight, []),
    ("layout",    st_layout,    ["layout"]),
    ("odx",       st_odx,       ["odx"]),
    ("mech",      st_mech,      ["mech_base", "mech_seat", "params"]),
    # MEASURES the stray angle and patches params.json. AFTER mech because the
    # mechanics are what set the out-of-field angular acceptance -- measured,
    # not assumed: optics-only answers 37 deg where the truth is 20. Costs
    # nothing in ordering, since the angle feeds no geometry.
    ("back_trace", st_back_trace, ["strayangle"]),
    # No resume kinds ON PURPOSE (see st_angle_gate): a verdict that can be
    # skipped is not a gate. Milliseconds, and it stands between the measured
    # angle and every expensive stage after it.
    ("angle_gate", st_angle_gate, []),
    ("s0",        st_s0,        ["s0"]),
    # OPT-IN, and a no-op unless the manifest sets ghost.optimise. Placed after
    # s0 because s0's exhaustive GPIM enumeration is the "before" picture this
    # stage is judged against, and before the sim stages so the optimised lens
    # exists by the time anyone wants to stage it as its own job.
    ("ghost_opt", st_ghost_opt, ["ghostopt", "lens_ghost"]),
    # optics-only first: it is the DENOMINATOR both sim variants are measured
    # against, and running it up front means a system whose per-field source
    # does not couple is visible before either mechanics run costs ~150 s.
    ("sim_optics", st_sim_optics, []),
    ("sim_base",  st_sim_base,  []),
    ("sim_redesign", st_sim_redesign, []),
]

# Stages that only exist when the manifest asks for them. Keyed by stage name;
# the predicate reads the manifest. An opt-in stage is skipped entirely rather
# than run as a no-op, so it never appears in a summary it did no work for.
OPTIONAL = {
    "ghost_opt": lambda m: bool((m.get("ghost") or {}).get("optimise")),
}


# What each stage CONSUMES. A stage whose output predates any of these is
# describing geometry that has since been replaced, so it is not 'done'.
# Regenerating the STEP files on 07-27 left 26 of 38 sim results measuring
# barrels that no longer existed on disk -- every one of them resumed clean,
# because existence was the only question being asked.
DEPENDS = {
    "layout":       ["lens"],
    "odx":          ["lens"],
    "mech":         ["layout"],
    "back_trace":   ["odx", "mech_base"],
    "s0":           ["lens"],
    "sim_optics":   ["odx"],
    "sim_base":     ["odx", "mech_base"],
    "sim_redesign": ["odx", "mech_seat"],
}
FRESH_SLACK_S = 120     # inputs written moments before the sim consumes them


def _input_paths(m, slug, wd, stage):
    out = []
    for kind in DEPENDS.get(stage, []):
        p = m["lens"] if kind == "lens" else J.path_for(slug, wd, kind)
        if os.path.exists(p):
            out.append(p)
    return out


def stale_against_inputs(m, slug, wd, stage, out_paths):
    """Newest input vs oldest output. Returns a reason string, or ''."""
    ins = _input_paths(m, slug, wd, stage)
    outs = [p for p in out_paths if os.path.exists(p)]
    if not ins or not outs:
        return ""
    newest = max(ins, key=os.path.getmtime)
    oldest = min(os.path.getmtime(p) for p in outs)
    lead = os.path.getmtime(newest) - oldest
    if lead > FRESH_SLACK_S:
        return "%s is %.0f h newer than this stage's output" % (
            os.path.basename(newest), lead / 3600.0)
    return ""


def outputs_exist(m, slug, wd, kinds, stage):
    """Resume predicate.

    A file existing is NOT proof of success: a crashed Speos run still leaves a
    result log (cameralens14's contains a FATAL traceback). Resuming on mere
    existence would silently accept a failed stage as complete -- exactly the
    class of bug the guards exist to kill. So sim stages are only 'done' if
    their log actually reports success.

    Nor is success proof of CURRENCY: a sim that ran before its geometry was
    regenerated succeeded against inputs that no longer exist. Both questions
    are asked here.
    """
    def current(paths):
        """Complete AND not superseded. Announces staleness rather than
        silently re-running, so the log says why the work is happening."""
        why = stale_against_inputs(m, slug, wd, stage, paths)
        if why:
            print("  %-13s STALE: %s -- re-running" % (stage, why), flush=True)
            return False
        return True

    if kinds:
        paths = [J.path_for(slug, wd, k) for k in kinds]
        if not all(os.path.exists(p) for p in paths):
            return False
        return current(paths)
    if stage.startswith("sim_"):
        v = stage[4:]
        if v == "optics":
            # optics-only runs under the base variant but writes its own log
            log = os.path.join(wd, "result-%s_base-opticsonly.txt" % m["simPrefix"])
            if not os.path.exists(log):
                return False
            try:
                assert_speos_run(log, "resume check optics",
                                 end_marker="wire-optics-only end")
            except GuardError:
                return False
            return current([log])
        log = os.path.join(wd, "result-%s_%s.txt" % (m["simPrefix"], v))
        if not os.path.exists(log):
            return False
        try:
            assert_speos_run(log, "resume check %s" % v, end_marker="wire-survey end")
        except GuardError:
            return False        # failed previously -> re-run it
        return current([log])
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", default="", help="comma-separated stages to re-run")
    ap.add_argument("--only", default="", help="comma-separated stages to run")
    a = ap.parse_args()

    m = J.load(a.manifest)
    slug, wd = m["slug"], m["workdir"]
    force = set(x for x in a.force.split(",") if x)
    global FORCE_PREFLIGHT
    FORCE_PREFLIGHT = "preflight" in force
    only = set(x for x in a.only.split(",") if x)

    print("JOB %s  (%s)" % (slug, os.path.basename(m["lens"])))
    print("  workdir: %s" % wd)
    rc = 0
    if a.dry_run:
        return _loop(m, slug, wd, force, only, a)
    with RunnerLock():
        return _loop(m, slug, wd, force, only, a)


def _loop(m, slug, wd, force, only, a):
    rc = 0
    for name, fn, kinds in STAGES:
        if only and name not in only:
            continue
        # OPT-IN stages vanish entirely when not enabled, rather than running
        # and recording a no-op. A stage that reports "ok" having done nothing
        # is indistinguishable in the summary from one that worked.
        if name in OPTIONAL and not OPTIONAL[name](m):
            continue
        done = outputs_exist(m, slug, wd, kinds, name) and name not in force
        if a.dry_run:
            print("  %-13s %s" % (name, "SKIP (outputs present)" if done else "would RUN"))
            continue
        if done:
            if name in HYDRATE:
                try:
                    HYDRATE[name](m, wd, slug)
                except GuardError as e:
                    print("  %-13s resume artifact unusable, re-running: %s"
                          % (name, str(e)[:90]))
                    done = False
            if done:
                print("  %-13s skip (resume, state re-hydrated)"
                      % name if name in HYDRATE else "  %-13s skip (resume)" % name)
                m["stages"].setdefault(name, {})["status"] = "skipped-resume"
                continue
        print("  %-13s running..." % name, flush=True)
        try:
            # Retry transient failures once. The fleet run showed ODX exports
            # failing to produce a file, then succeeding on a manual retry --
            # a race (licence hand-off / file lock), not a property of the
            # lens. A NO-GO verdict or a real Speos FATAL is NOT transient and
            # must not be retried.
            try:
                outs, scripts, inputs, dt = fn(m, wd, slug)
            except GuardError as e:
                transient = ("file-produced" in str(e) or "TIMEOUT" in str(e))
                if not transient:
                    raise
                print("  %-13s transient failure, retrying once..." % name, flush=True)
                time.sleep(3)
                outs, scripts, inputs, dt = fn(m, wd, slug)
            m["stages"][name] = {
                "status": "ok", "seconds": round(dt, 1),
                "outputs": [os.path.basename(o) for o in outs],
                "provenance": J.provenance(scripts, inputs),
            }
            print("  %-13s ok (%.0fs)" % (name, dt))
        except GuardError as e:
            m["stages"][name] = {"status": "failed", "error": str(e), "at": J.now()}
            # Drop every LATER stage's record. They describe a previous run, and
            # once this stage halts they cannot be reached now, so leaving them
            # makes the manifest describe a run that did not happen: scdoublet15
            # halted at preflight in 11 s yet the summary still announced
            # "halted at layout" from a stale entry written before the gate
            # existed. A stored result is only true of the code that produced
            # it - the same trap as a stale regression baseline, one layer down.
            later = [s for s, _, _ in STAGES[[x[0] for x in STAGES].index(name) + 1:]]
            for s in later:
                m["stages"].pop(s, None)
            print("  %-13s FAILED\n%s" % (name, e))
            rc = 2
            break
        finally:
            J.save(m, a.manifest)
    J.save(m, a.manifest)
    print("  manifest updated: %s" % a.manifest)
    return rc


if __name__ == "__main__":
    sys.exit(main())
