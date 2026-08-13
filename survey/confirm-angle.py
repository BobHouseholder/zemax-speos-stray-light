"""confirm-angle.py -- second half of the angle funnel.

    python survey/confirm-angle.py <slug> [ncand] [--dir DIR]

The backward trace RANKS candidate stray angles; it does not measure flux. Two
forward phases turn a ranking into a measurement:

  phase 1  short sim at each of the top candidates -> pick the best by flux
  phase 2  re-sim best-1, best, best+1             -> refine to 1 degree

Phase 2 is not optional padding. angle_select bins at 2 deg and reports bin
CENTRES, so its candidates are all odd; a peak at an even angle is unreachable
by phase 1 alone. Checked against the five systems with measured PST curves,
phase 2 lands on the measured peak for every one of them:

    system        rank1  phase1  phase2   measured
    dg              19     19      20        20
    cooke           23     23      22        22
    tessar25        27     27      26        26
    rearstop31      35     35      35        35
    wideangle32     33     35      35        35

Each phase repeats one angle to measure the Monte-Carlo noise floor, because a
difference between two candidates means nothing without it.
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
CFG = pst_read.private_config("confirm-angle")
SPEOS = settings.SPEOS_LAUNCHER
SCRIPT = os.path.join(BASE, "survey", "wire-survey-pst.py")
BSDF = os.path.join(BASE, "black-anodize-plausible.anisotropicbsdf").replace("\\", "/")

slug = sys.argv[1]
ncand = int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("-") else 3
wd = os.path.join(SYS, slug)
for i, a in enumerate(sys.argv):
    if a == "--dir" and i + 1 < len(sys.argv):
        wd = os.path.abspath(sys.argv[i + 1])

prm_path = os.path.join(wd, "%s-params.json" % slug)
prm = json.load(open(prm_path, encoding="utf-8-sig"))
sel = json.load(open(os.path.join(wd, "%s-strayangle.json" % slug),
                     encoding="utf-8-sig"))

EDGE_BLACK = prm.get("edgeBlack", True)   # redesign variant = seated + edge black
ODX_PATH = prm.get("odxPath") or os.path.join(wd, "%s.odx" % slug)
MECH_PATH = prm.get("mechPath") or os.path.join(wd, "%s-seated.step" % slug)
for _p in (ODX_PATH, MECH_PATH):
    if not os.path.exists(_p):
        raise SystemExit("missing artefact: %s" % _p)

if not sel.get("ok") or not sel.get("candidates"):
    print("[%s] no candidates to confirm (%s) -- leaving strayDeg at %s"
          % (slug, sel.get("reason", "?"), prm.get("strayDeg")))
    raise SystemExit(0)


def sweep(tokens, sfx):
    """Run one forward sweep; return {angle: mean flux} and the noise floor."""
    lines = [
        ODX_PATH.replace("\\", "/"),
        os.path.join(wd, "%s-confirm.scdocx" % slug).replace("\\", "/"),
        MECH_PATH.replace("\\", "/"),
        sfx,
        BSDF,
        str(prm["rSrc"]), str(prm["zSrc"]), str(prm["wave"]),
        ",".join(tokens),
        "EDGEBLACK" if EDGE_BLACK else "NONE",
        os.path.join(wd, "confirm-result-%s-%s.txt" % (slug, sfx)).replace("\\", "/"),
    ]
    backup = open(CFG, encoding="utf-8-sig").read() if os.path.exists(CFG) else None
    open(CFG, "w").write("\n".join(lines) + "\n")
    try:
        # Speos spawns OpticStudio via ComponentOpticStudio.Create, so this
        # contends for the single seat AND takes optishpc 10/10. Held per
        # launch, not per batch, so star-stop can interleave. See lib/seat.py.
        with seat.SeatLock('stray-light-loop/confirm-angle.py'):
            subprocess.call([SPEOS, "/RunScript=%s" % SCRIPT, "/Headless=True",
                             "/Splash=False", "/Welcome=False", "/ExitAfterScript=True"])
    finally:
        if backup is not None:
            open(CFG, "w").write(backup)
    rows = pst_read.read_sweep(
        wd, sfx, expect=tokens,
        log_path=os.path.join(wd, "confirm-result-%s-%s.txt" % (slug, sfx)),
        what="%s/%s" % (slug, sfx))
    agg = {}
    for a, tok, fx, er in rows:
        agg.setdefault(a, []).append(fx)
    return (dict((a, sum(v) / len(v)) for a, v in agg.items()),
            pst_read.noise_floor(rows))


def show(avg, floor, title):
    if not avg:
        print("  %s: no readable flux" % title)
        return None
    best = max(avg, key=lambda a: avg[a])
    print("  %s" % title)
    for a in sorted(avg):
        print("    %5.1f deg  %10.6f   %5.0f%%%s"
              % (a, avg[a], 100.0 * avg[a] / avg[best],
                 "  <--" if a == best else ""))
    for a, pct in floor.items():
        print("    noise floor at %.0f deg: %.2f%%" % (a, pct))
    return best


# ---- phase 1: rank-order candidates by measured flux -----------------------
cands = sel["candidates"][:ncand]
print("[%s] phase 1: candidates %s" % (slug, cands))
avg1, floor1 = sweep(["%g" % c for c in cands] + ["%gb" % cands[0]],
                     "%sc1" % slug)
best1 = show(avg1, floor1, "phase 1")
if best1 is None:
    print("[%s] phase 1 unreadable -- keeping %s" % (slug, prm.get("strayDeg")))
    raise SystemExit(0)

# ---- phase 2: refine +/-1 deg around the phase-1 winner --------------------
lo = max(best1 - 1.0, round(prm["maxField"], 1))
fine = sorted(set([lo, best1, best1 + 1.0]))
print("[%s] phase 2: refining %s" % (slug, fine))
avg2, floor2 = sweep(["%g" % a for a in fine] + ["%gb" % best1], "%sc2" % slug)
best2 = show(avg2, floor2, "phase 2")

final_avg = avg2 if best2 is not None else avg1
best = best2 if best2 is not None else best1
floor = floor2 if best2 is not None else floor1

# ---- is the winner separated from its runner-up? ---------------------------
ordered = sorted(final_avg, key=lambda a: final_avg[a], reverse=True)
margin_pct = None
if len(ordered) > 1 and final_avg[ordered[0]]:
    margin_pct = 100.0 * (final_avg[ordered[0]] - final_avg[ordered[1]]) / final_avg[ordered[0]]
worst_noise = max(floor.values()) if floor else 0.0
decisive = margin_pct is None or margin_pct >= 3.0 * worst_noise
if margin_pct is not None:
    print("  margin over runner-up: %.1f%%  vs noise %.1f%%  -> %s"
          % (margin_pct, worst_noise, "DECISIVE" if decisive else "NOT SEPARATED"))

rank1 = cands[0]
if not decisive:
    best = best1
    print("  top two within noise; falling back to the phase-1 winner")

# Preserve what the heuristic WOULD have said before overwriting strayDeg.
if prm.get("strayDegFallback") is None:
    prm["strayDegFallback"] = round(prm["maxField"] + 6.0, 1)

prm["strayDeg"] = round(float(best), 1)
prm["strayDefined"] = prm["strayDeg"] <= 85.0
prm["strayDegSource"] = ("inverse-trace+confirmed" if decisive
                         else "inverse-trace (confirm inconclusive)")
prm["strayDegCandidates"] = cands
prm["strayDegRank1"] = rank1
prm["strayDegPhase1"] = best1
prm["strayDegConfirmCurve"] = [{"angle": a, "flux": final_avg[a]}
                               for a in sorted(final_avg)]
prm["strayDegConfirmDecisive"] = bool(decisive)
prm["strayDegConfirmMarginPct"] = margin_pct
prm["strayDegConfirmNoisePct"] = worst_noise
json.dump(prm, open(prm_path, "w"), indent=1)

note = ""
if best != rank1:
    note = "  (rank 1 was %.1f -- confirm CHANGED it)" % rank1
print("[%s] strayDeg = %.1f deg%s" % (slug, prm["strayDeg"], note))
