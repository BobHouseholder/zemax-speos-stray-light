"""run-backtrace.py -- drive the back_trace stage for one survey system.

    python survey/run-backtrace.py <slug>

Renders the 14-line config from that system's params.json (so nothing is
transcribed by hand), runs the Speos backward trace, then runs the selector
under Ansys CPython and prints the measured angle beside the heuristic.

Standalone on purpose: lib/runner.py owns this inside the pipeline, but a
single-system driver is what you want when validating the stage itself.
"""
import glob
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
import pst_read  # noqa: E402
import seat  # noqa: E402
import settings  # noqa: E402

SYS = os.path.join(BASE, "survey", "systems")
CFG = pst_read.survey_config("run-backtrace")
SPEOS = settings.SPEOS_LAUNCHER
BSDF = os.path.join(BASE, "black-anodize-plausible.anisotropicbsdf").replace("\\", "/")
SCRIPT = os.path.join(BASE, "survey", "wire-back-trace.py")

slug = sys.argv[1]
wd = os.path.join(SYS, slug)
prm = json.load(open(os.path.join(wd, "%s-params.json" % slug), encoding="utf-8-sig"))

odx = os.path.join(wd, "%s.odx" % slug)
mech = os.path.join(wd, "%s-seated.step" % slug)
for p in (odx, mech):
    if not os.path.exists(p):
        raise SystemExit("missing %s" % p)

log = os.path.join(wd, "result-%s_backtrace.txt" % slug)
lines = [
    odx,
    os.path.join(wd, "%s-backtrace.scdocx" % slug),
    mech,
    "%s_backtrace" % slug,
    BSDF,
    str(prm["zImg"]),
    str(prm["rDisc"]),
    str(prm["zCatch"]),
    str(prm["strayDeg"]),          # the heuristic; unused by this script
    str(prm["zSrc"]),
    str(prm["rSrc"]),
    str(prm["wave"]),
    "NONE",
    log,
]

# the shared config is a single fixed path; save and restore it
backup = open(CFG, encoding="utf-8-sig").read()
open(CFG, "w").write("\n".join(lines) + "\n")

runlog = log.replace(".txt", "-backtrace.txt")
if os.path.exists(runlog):
    os.remove(runlog)

t0 = time.time()
try:
    # Speos spawns OpticStudio via ComponentOpticStudio.Create, so this
    # contends for the single seat AND takes optishpc 10/10. Held per
    # launch, not per batch, so star-stop can interleave. See lib/seat.py.
    with seat.SeatLock('stray-light-loop/run-backtrace.py'):
        subprocess.call([SPEOS, "/RunScript=%s" % SCRIPT, "/Headless=True",
                         "/Splash=False", "/Welcome=False", "/ExitAfterScript=True"])
finally:
    open(CFG, "w").write(backup)          # always restore, even on failure

dt = time.time() - t0
tail = ""
if os.path.exists(runlog):
    tail = open(runlog, encoding="utf-8", errors="ignore").read()
ok = "wire-back-trace end" in tail
print("[%s] speos %.0fs  end-marker=%s" % (slug, dt, ok))
if not ok:
    print(tail[-600:])
    raise SystemExit("back-trace did not complete for %s" % slug)

# GetResultFilePaths() does not list the .lpf even when written -- find it.
cands = glob.glob(os.path.join(wd, "**", "BT_*%s*FrontCatch.lpf" % slug),
                  recursive=True)
if not cands:
    cands = glob.glob(os.path.join(wd, "**", "BT_*.FrontCatch.lpf"), recursive=True)
if not cands:
    raise SystemExit("[%s] no backward LPF produced" % slug)
lpf = max(cands, key=os.path.getmtime)

out = os.path.join(wd, "%s-strayangle.json" % slug)
# The DRIVER interpreter, not ANSYS_PY: angle_select reads .lpf through
# lib/lpf_read.py (PySpeos lxp) as of 2026-08-09.
subprocess.call([settings.PYTHON_EXE,
                 os.path.join(BASE, "lib", "angle_select.py"),
                 lpf, str(prm["maxField"]), out])

r = json.load(open(out, encoding="utf-8-sig"))
print("[%s] field %.1f | heuristic %.1f | measured %s | candidates %s | "
      "escaping %d" % (slug, prm["maxField"], prm["strayDeg"],
                       r.get("strayDeg"), r.get("candidates"),
                       r.get("raysEscaping", 0)))
