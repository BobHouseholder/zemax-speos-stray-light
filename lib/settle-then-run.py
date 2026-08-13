r"""settle-then-run.py -- wait for the gate scripts to stop changing, then
rerun the fleet.

`preflight.ps1` (and the mech generator) are being edited concurrently by the
injected-defect test-suite workstream. Running the fleet mid-edit produced
results against a MOVING gate: scgrin24 flipped NO-GO -> complete and two
other systems changed failure stage between passes.

"Settled" is defined concretely: the watched files' hashes unchanged for
QUIET_MIN minutes. If they are still churning after MAX_WAIT_MIN, give up
waiting and say so rather than blocking forever -- a stale answer reported
honestly beats an indefinite hang.
"""
import hashlib
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402

FLV = settings.PYTHON_EXE

WATCH = [
    os.path.join(BASE, "lib", "preflight.ps1"),
    os.path.join(BASE, "lib", "zos-guard.ps1"),
    os.path.join(BASE, "survey", "make-survey-mech.py"),
    os.path.join(BASE, "survey", "trace-layout-generic.ps1"),
]
QUIET_MIN = 10          # unchanged for this long => settled
MAX_WAIT_MIN = 40       # then proceed anyway, flagged
POLL_S = 60


def fingerprint():
    h = hashlib.sha256()
    for p in WATCH:
        h.update(p.encode())
        if os.path.exists(p):
            h.update(open(p, "rb").read())
            h.update(str(int(os.path.getmtime(p))).encode())
        else:
            h.update(b"<missing>")
    return h.hexdigest()[:12]


t0 = time.time()
fp = fingerprint()
stable_since = time.time()
print("watching %d gate file(s); need %d min quiet (max wait %d min)"
      % (len(WATCH), QUIET_MIN, MAX_WAIT_MIN), flush=True)
print("  fingerprint %s" % fp, flush=True)

settled = False
while True:
    time.sleep(POLL_S)
    cur = fingerprint()
    now = time.time()
    if cur != fp:
        print("  [%5.1f min] gate CHANGED %s -> %s; quiet timer reset"
              % ((now - t0) / 60, fp, cur), flush=True)
        fp = cur
        stable_since = now
    quiet = (now - stable_since) / 60.0
    if quiet >= QUIET_MIN:
        print("  [%5.1f min] settled: unchanged for %.0f min (fp %s)"
              % ((now - t0) / 60, quiet, fp), flush=True)
        settled = True
        break
    if (now - t0) / 60.0 >= MAX_WAIT_MIN:
        print("  [%5.1f min] STILL CHURNING after max wait -- proceeding anyway; "
              "results are against a moving gate (fp %s)"
              % ((now - t0) / 60, fp), flush=True)
        break

lock = os.path.join(BASE, "lib", ".runner.lock")
if os.path.exists(lock):
    try:
        os.remove(lock)
    except OSError:
        pass

print("\n=== launching fleet (--force preflight so every verdict re-evaluates) ===",
      flush=True)
log = os.path.join(BASE, "survey", "fleet-final.log")
with open(log, "w") as f:
    rc = subprocess.call([FLV, os.path.join(BASE, "lib", "run-fleet.py"),
                          "--force", "preflight"], stdout=f, stderr=subprocess.STDOUT)
print("fleet exit %s; log %s" % (rc, log), flush=True)
print("GATE_SETTLED=%s  FINGERPRINT=%s" % (settled, fp), flush=True)
