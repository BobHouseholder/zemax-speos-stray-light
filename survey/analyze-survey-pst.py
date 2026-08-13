"""analyze-survey-pst.py -- read a generic PST sweep and score the angle selector.

    python survey/analyze-survey-pst.py <slug> <sfx>

Extracts detector flux per angle from the Speos report HTML, finds the measured
peak, and compares it against BOTH the inverse-trace selection and the old
`maxField + 6` heuristic -- which is the whole point of running the sweep.
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
import pst_read  # noqa: E402  -- the ONE flux reader, shared with the runner

slug = sys.argv[1]
wd = os.path.join(BASE, "survey", "systems", slug)
for i, a in enumerate(sys.argv):
    if a == "--dir" and i + 1 < len(sys.argv):
        wd = os.path.abspath(sys.argv[i + 1])

avail = pst_read.list_suffixes(wd)
if not avail:
    raise SystemExit("no PST reports found under %s" % wd)

if len(sys.argv) > 2 and not sys.argv[2].startswith("-"):
    sfx = sys.argv[2]
    if sfx not in avail:
        raise SystemExit("suffix '%s' not present. Available: %s"
                         % (sfx, ", ".join("%s (%d)" % kv for kv in
                                           sorted(avail.items()))))
else:
    # default to the SWEEP, not a confirm run -- and say what was chosen
    sweeps = {k: v for k, v in avail.items() if k.endswith("pst")}
    pool = sweeps or avail
    sfx = max(pool, key=lambda k: pool[k])
    print("suffixes present: %s"
          % ", ".join("%s (%d)" % kv for kv in sorted(avail.items())))
    print("analysing: %s\n" % sfx)

# expect=None: the suffix is auto-discovered above, so the angle list this run
# asked for is not available here. read_sweep still refuses an uneven sweep --
# the shape a licence-failed sim leaves, since it writes no report at all.
try:
    rows = pst_read.read_sweep(wd, sfx, what=sfx)
except RuntimeError as exc:
    raise SystemExit("REFUSING to analyse %s: %s" % (sfx, exc))
if not rows:
    raise SystemExit("no readable flux for suffix %s" % sfx)

prm = json.load(open(os.path.join(wd, "%s-params.json" % slug),
                     encoding="utf-8-sig"))
selp = os.path.join(wd, "%s-strayangle.json" % slug)
sel = json.load(open(selp, encoding="utf-8-sig")) if os.path.exists(selp) else {}

peak = max(rows, key=lambda t: t[2])
top = peak[2]

print("=== PST sweep: %s (field %.1f deg) ===" % (slug, prm["maxField"]))
print("  angle   flux (W)     errors")
for a, tok, fx, er in rows:
    bar = "#" * int(round(46 * fx / top))
    mark = "  <-- MEASURED PEAK" if fx == top else ""
    print("  %5s  %10.6f  %7s  %s%s" % (tok, fx, er, bar, mark))

# repeat tokens (letter suffix) give a convergence estimate for free
floor = pst_read.noise_floor(rows)
if floor:
    print()
    for a, pct in floor.items():
        print("  repeat at %.0f deg -> spread %.2f%% (MC noise floor)" % (a, pct))

heur = prm.get("strayDegFallback") or prm.get("strayDeg")
selected = sel.get("strayDeg")
print()
print("  measured peak        : %.1f deg" % peak[0])
if selected:
    print("  inverse-trace picked : %.1f deg   -> error %.1f deg"
          % (selected, abs(selected - peak[0])))
print("  heuristic maxField+6 : %.1f deg   -> error %.1f deg"
      % (heur, abs(heur - peak[0])))

# how much signal does each choice actually see?
def flux_at(target):
    if not rows:
        return None
    return min(rows, key=lambda t: abs(t[0] - target))


if selected:
    fs, fh = flux_at(selected), flux_at(heur)
    print()
    print("  flux at the selected angle  (%.0f deg): %.6f W  (%.0f%% of peak)"
          % (fs[0], fs[2], 100.0 * fs[2] / top))
    print("  flux at the heuristic angle (%.0f deg): %.6f W  (%.0f%% of peak)"
          % (fh[0], fh[2], 100.0 * fh[2] / top))

out = os.path.join(wd, "%s-pst-results.json" % slug)
json.dump({"slug": slug, "maxField": prm["maxField"],
           "curve": [{"angle": a, "token": t, "flux": f, "errors": e}
                     for a, t, f, e in rows],
           "measuredPeakDeg": peak[0], "measuredPeakFlux": peak[2],
           "inverseSelectedDeg": selected, "heuristicDeg": heur},
          open(out, "w"), indent=1)
print("\nwrote %s" % out)
