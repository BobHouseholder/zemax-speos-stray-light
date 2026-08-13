"""run-pst.py -- drive a forward PST sweep for one system.

    python survey/run-pst.py <slug> <angles> [--dir DIR]

    <angles>  comma list; a trailing letter marks a repeat run at the same
              angle, which is the free Monte-Carlo noise estimate.

Renders the config from that system's params.json so nothing is transcribed by
hand. Independent ground truth for the angle selector -- a forward measurement,
not the backward ranking.

The config is now PER-RUN (pst_read.private_config). It used to be the shared
survey/pst-config.txt, saved and restored around the run; that made it a global
mutable, and on 2026-08-04 a wideangle32 sweep from this script overwrote the
config a concurrent band driver had just written for A01. Restoring the file
afterwards does not help -- the damage is done while the run is in flight.
"""
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
import pst_read  # noqa: E402
import seat  # noqa: E402
import settings  # noqa: E402

SYS = os.path.join(BASE, "survey", "systems")
CFG = pst_read.private_config("run-pst")
SPEOS = settings.SPEOS_LAUNCHER
SCRIPT = os.path.join(BASE, "survey", "wire-survey-pst.py")
BSDF = os.path.join(BASE, "black-anodize-plausible.anisotropicbsdf").replace("\\", "/")

slug, angles = sys.argv[1], sys.argv[2]
wd = os.path.join(SYS, slug)
for i, a in enumerate(sys.argv):
    if a == "--dir" and i + 1 < len(sys.argv):
        wd = os.path.abspath(sys.argv[i + 1])

prm = json.load(open(os.path.join(wd, "%s-params.json" % slug),
                     encoding="utf-8-sig"))
odx = prm.get("odxPath") or os.path.join(wd, "%s.odx" % slug)
mech = prm.get("mechPath") or os.path.join(wd, "%s-seated.step" % slug)
# --mech swaps the mechanics without touching params.json: needed to compare
# the SAME angle across the naive-tube baseline and the seated barrel.
for i, a in enumerate(sys.argv):
    if a == "--mech" and i + 1 < len(sys.argv):
        mech = os.path.abspath(sys.argv[i + 1])
for p in (odx, mech):
    if not os.path.exists(p):
        raise SystemExit("missing artefact: %s" % p)

# FULL slug, not slug[:8]. The truncated form collides outright for any two
# systems sharing an 8-character prefix, and it already made the suffix
# unguessable ("rearstop31" -> "rearstop"), which caused a confirm run's
# reports to be analysed in place of a sweep's.
edge_black = "--edgeblack" in sys.argv
sfx = "%spst" % slug
for i, a in enumerate(sys.argv):
    if a == "--sfx" and i + 1 < len(sys.argv):
        sfx = sys.argv[i + 1]
log = os.path.join(wd, "pst-result-%s.txt" % sfx)
lines = [
    odx.replace("\\", "/"),
    os.path.join(wd, "%s-pst.scdocx" % slug).replace("\\", "/"),
    mech.replace("\\", "/"),
    sfx,
    BSDF,
    str(prm["rSrc"]), str(prm["zSrc"]), str(prm["wave"]),
    angles,
    "EDGEBLACK" if edge_black else "NONE",
    log.replace("\\", "/"),
]

backup = open(CFG, encoding="utf-8-sig").read() if os.path.exists(CFG) else None
open(CFG, "w").write("\n".join(lines) + "\n")
if os.path.exists(log):
    os.remove(log)
print("[%s] sweeping %s  (mech=%s)" % (slug, angles, os.path.basename(mech)))
# HOLD THE SHARED ANSYS SEAT. Speos spawns OpticStudio through
# ComponentOpticStudio.Create, so this contends for the single seat and for the
# whole optishpc pool -- a single solve checks out 10/10, and a second one gets
# the LaaS 404 that Speos reports inside StatusInfo rather than as a failure.
# Until 2026-08-10 only runner.py took the lock, so every sweep launched from
# here was invisible to the neighbouring star-stop toolchain that drives the
# same seat. See lib/seat.py.
try:
    with seat.SeatLock("stray-light-loop/run-pst.py"):
        subprocess.call([SPEOS, "/RunScript=%s" % SCRIPT, "/Headless=True",
                         "/Splash=False", "/Welcome=False",
                         "/ExitAfterScript=True"])
finally:
    if backup is not None:
        open(CFG, "w").write(backup)

tail = open(log, encoding="utf-8", errors="ignore").read() if os.path.exists(log) else ""
ok = "wire-survey-pst end" in tail
print("[%s] sweep complete=%s  (suffix %s)" % (slug, ok, sfx))
if not ok:
    print(tail[-800:])
    raise SystemExit(1)
