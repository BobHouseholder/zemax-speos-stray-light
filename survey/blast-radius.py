"""blast-radius.py -- does the stray angle change the published REDUCTION?

    python survey/blast-radius.py <slug> <seatedSfx> <baseSfx>

Every stray-light reduction published from this pipeline was measured at
`maxField + 6`, a heuristic now known to sit off-peak -- on tessar25 at 12% of
peak flux, on wideangle32 at 23%.

But the published number is a RATIO of naive tube to seated barrel at the same
angle, so it may be stable even at the wrong angle. This compares the reduction
computed at the heuristic angle against the reduction at the measured peak.

  reductions agree  -> the published figures stand; the selector only improves
                       future work
  reductions differ -> a correction affecting every scored system
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
import pst_read  # noqa: E402

slug, seated_sfx, base_sfx = sys.argv[1], sys.argv[2], sys.argv[3]
wd = os.path.join(BASE, "survey", "systems", slug)
prm = json.load(open(os.path.join(wd, "%s-params.json" % slug),
                     encoding="utf-8-sig"))


def curve(sfx):
    # expect=None: this reads results an earlier run produced, so the requested
    # angle list is not available here. read_sweep still refuses an uneven
    # sweep, which is what a licence-failed sim leaves behind.
    rows = pst_read.read_sweep(wd, sfx, what="%s/%s" % (slug, sfx))
    agg = {}
    for a, tok, fx, er in rows:
        agg.setdefault(a, []).append(fx)
    return (dict((a, sum(v) / len(v)) for a, v in agg.items()),
            pst_read.noise_floor(rows))


seated, nf_s = curve(seated_sfx)
base, nf_b = curve(base_sfx)
shared = sorted(set(seated) & set(base))
if not shared:
    raise SystemExit("no angles common to %s and %s (seated=%s base=%s)"
                     % (seated_sfx, base_sfx, sorted(seated), sorted(base)))

heur = prm.get("strayDegFallback")
peak = prm.get("strayDeg")
noise = max(list(nf_s.values()) + list(nf_b.values()) + [0.0])

print("=== %s (field %.1f deg) ===" % (slug, prm["maxField"]))
print("  angle   naive tube    seated     reduction")
red = {}
for a in shared:
    r = 100.0 * (base[a] - seated[a]) / base[a] if base[a] else float("nan")
    red[a] = r
    tag = []
    if heur is not None and abs(a - heur) < 0.51:
        tag.append("heuristic")
    if peak is not None and abs(a - peak) < 0.51:
        tag.append("measured peak")
    print("  %5.1f  %10.6f  %10.6f   %+7.1f%%   %s"
          % (a, base[a], seated[a], -r, ", ".join(tag)))

print("\n  MC noise floor across both runs: %.2f%%" % noise)

if heur is not None and peak is not None:
    ah = min(shared, key=lambda a: abs(a - heur))
    ap = min(shared, key=lambda a: abs(a - peak))
    if abs(ah - ap) > 0.51:
        d = abs(red[ap] - red[ah])
        print("\n  reduction at heuristic %.0f deg : %+.1f%%" % (ah, -red[ah]))
        print("  reduction at measured  %.0f deg : %+.1f%%" % (ap, -red[ap]))
        print("  DIFFERENCE                     : %.1f percentage points" % d)
        verdict = ("STABLE -- the published reduction does not depend on the "
                   "angle within noise" if d <= max(3.0, 3.0 * noise) else
                   "SHIFTS -- the reduction is angle-dependent; published "
                   "figures were computed at the wrong angle")
        print("  VERDICT: %s" % verdict)
    else:
        print("\n  heuristic and measured peak coincide -- nothing to compare")

out = os.path.join(wd, "%s-blastradius.json" % slug)
json.dump({"slug": slug, "maxField": prm["maxField"],
           "heuristicDeg": heur, "measuredPeakDeg": peak,
           "noisePct": noise,
           "rows": [{"angle": a, "baseFlux": base[a], "seatedFlux": seated[a],
                     "reductionPct": -red[a]} for a in shared]},
          open(out, "w"), indent=1)
print("\nwrote %s" % out)
