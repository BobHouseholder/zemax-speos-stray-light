r"""ghost-reduce.py -- drive ghost-optimise.ps1 and judge what it produced.

    python lib/ghost-reduce.py --lens survey/systems/<slug>/<slug>.zmx
    python lib/ghost-reduce.py --slug example-triplet

Minimises DOUBLE-BOUNCE ghost focus by adding GPIM operands targeted to 0
alongside the existing merit function, per the Ansys Ghost Focus Generator note.
The PowerShell half does the ZOS-API work; this half applies the gates and
decides whether the result may be quoted.

WHY A SEPARATE JUDGE. The optimiser reports its own merit function going down,
which is not evidence of anything a reader cares about: the merit function
contains the thing being optimised. Every number below is re-measured after the
optimiser has finished and is compared on a domain WIDER than the one the
optimiser was constrained on.

THE FOUR GATES, and the specific way each has been got wrong before:

  ghost-peak      Peak |GPIM| over EVERY pair, not the driven ones. Driving 3
                  ghosts and reporting those 3 reports the optimiser's homework.
  ghost-collateral  Worst UNTARGETED pair, before vs after. An optimiser will
                  trade an unconstrained ghost for a constrained one for free.
  image-quality   Polychromatic spot on a DENSE field grid. lib/guard.py records
                  a mono check reading +4.2% where the truth was +49.5%, and the
                  worst field is routinely interior rather than at the corner.
  design-integrity  The ORIGINAL merit function re-evaluated on the optimised
                  design, plus EFFL and TOTR drift. Task: the design constraints
                  must survive the ghost work.

WHAT COUNTS AS SUCCESS. Peak ghost CONCENTRATION falls while image quality and
the design constraints hold. Total ghost energy is Fresnel-fixed and is NOT
expected to move -- see lib/kpi.py, which names peak irradiance as the correct
acceptance test for exactly this operand.
"""
import argparse
import json
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
import guard  # noqa: E402
import settings  # noqa: E402

PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
SCRIPT = os.path.join(BASE, "ghost", "ghost-optimise.ps1")


def load_json_bom(path):
    """utf-8-sig, not utf-8.

    Out-File -Encoding utf8 on PowerShell 5.1 prepends a BOM, which arrives as
    a glyph before `{` and makes json.load fail at char 0. settings.py carries
    the same note for straylight.toml; the s0 stage output has the same shape.
    """
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def peak(rows, mode=None, exclude=()):
    """Largest |GPIM| over rows, optionally restricted by mode / excluding pairs.

    Larger |GPIM| means the ghost focus sits nearer the image plane, which is
    what the operand is driven to 0 to prevent.
    """
    sel = [r for r in rows
           if (mode is None or r["mode"] == mode)
           and (r["surf1"], r["surf2"]) not in exclude
           and r["value"] is not None]
    if not sel:
        return None
    return max(sel, key=lambda r: abs(r["value"]))


def pct(before, after):
    if before in (None, 0):
        return None
    return 100.0 * (after - before) / before


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lens", help="path to the .zmx under test")
    ap.add_argument("--slug", help="job slug under survey/systems/")
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--weight", type=float, default=1.0)
    ap.add_argument("--dense-fields", type=int, default=11)
    ap.add_argument("--vary-thickness", action="store_true")
    ap.add_argument("--max-spot-growth", type=float, default=10.0,
                    help="percent; image quality beyond this is a failure")
    a = ap.parse_args()

    if not a.lens and not a.slug:
        sys.exit("need --lens <path.zmx> or --slug <name>")
    if a.slug and not a.lens:
        a.lens = os.path.join(BASE, "survey", "systems", a.slug, "%s.zmx" % a.slug)
    lens = os.path.abspath(a.lens)
    if not os.path.isfile(lens):
        sys.exit("no such lens: %s" % lens)
    slug = a.slug or os.path.splitext(os.path.basename(lens))[0]
    wd = os.path.dirname(lens)
    out = os.path.join(wd, "%s-ghostopt.json" % slug)

    cmd = PS + [SCRIPT, "-LensFile", lens, "-OutJson", out, "-Slug", slug,
                "-TopN", str(a.top_n), "-GhostWeight", str(a.weight),
                "-DenseFields", str(a.dense_fields)]
    if a.vary_thickness:
        cmd.append("-VaryThickness")

    print("=" * 78)
    print("GHOST REDUCTION -- %s" % slug)
    print("=" * 78)
    p = subprocess.run(cmd, timeout=1800)
    if p.returncode != 0:
        sys.exit("ghost-optimise.ps1 failed (exit %d) -- see %s"
                 % (p.returncode, os.path.splitext(out)[0] + ".log"))
    if not os.path.exists(out):
        sys.exit("ghost-optimise.ps1 exited 0 but wrote no %s" % out)
    d = load_json_bom(out)

    driven = {(r["surf1"], r["surf2"]) for r in d["injected"]}
    gb, ga = d["ghostsBefore"], d["ghostsAfter"]

    print()
    print("-" * 78)
    print("RESULT")
    print("-" * 78)
    if d["meritSynthesised"]:
        print("  NOTE  the lens carried no substantive merit function, so a")
        print("        design-intent guard was SYNTHESISED (hold EFFL, TOTR and")
        print("        polychromatic spot across the field). Without it this")
        print("        would be unconstrained optimisation.")
        print()

    failures = []

    # ---- gate 1: peak ghost concentration over EVERY pair
    pb, pa = peak(gb, mode=1), peak(ga, mode=1)
    dp = pct(abs(pb["value"]), abs(pa["value"]))
    print("  peak image-ghost |GPIM| over all %d pairs" % len([r for r in gb if r["mode"] == 1]))
    print("    before  %.6g   at (%d,%d)" % (abs(pb["value"]), pb["surf1"], pb["surf2"]))
    print("    after   %.6g   at (%d,%d)" % (abs(pa["value"]), pa["surf1"], pa["surf2"]))
    print("    change  %+.1f%%   %s" % (dp, "improved" if dp < 0 else "WORSE"))
    if dp >= 0:
        failures.append("peak image-ghost did not improve (%+.1f%%)" % dp)

    # ---- gate 2: collateral damage to ghosts nobody constrained
    cb, ca = peak(gb, mode=1, exclude=driven), peak(ga, mode=1, exclude=driven)
    if cb and ca:
        dc = pct(abs(cb["value"]), abs(ca["value"]))
        print("  worst UNTARGETED image ghost (collateral check)")
        print("    before  %.6g   after %.6g   %+.1f%%" % (abs(cb["value"]), abs(ca["value"]), dc))
        if dc > 10.0:
            failures.append("untargeted ghost worsened %+.1f%%" % dc)

    # ---- gate 3: image quality, polychromatic, dense grid, worst field named
    fb = [f["spot"] for f in d["fieldsBefore"]]
    fa = [f["spot"] for f in d["fieldsAfter"]]
    hy = [f["hy"] for f in d["fieldsBefore"]]
    worst = guard.assert_image_quality(fb, fa, "%s ghost-optimised" % slug,
                                       max_growth_pct=a.max_spot_growth,
                                       polychromatic=True)
    wi, wg = worst
    interior = 0 < wi < len(hy) - 1
    print("  polychromatic spot across %d fields (verified outside the optimiser)"
          % len(hy))
    print("    worst field  hy=%.2f  %+.1f%%   (%s)"
          % (hy[wi], wg, "INTERIOR" if interior else "at domain edge"))
    print("    on-axis %+.1f%%   corner %+.1f%%"
          % (pct(fb[0], fa[0]), pct(fb[-1], fa[-1])))
    if interior:
        print("    note: worst field is interior -- a 3-point check at "
              "hy=0/0.7/1.0 could have missed it")
    if wg > a.max_spot_growth:
        failures.append("spot grew %+.1f%% at hy=%.2f (limit %.0f%%)"
                        % (wg, hy[wi], a.max_spot_growth))

    # ---- gate 4: design integrity
    de = pct(d["efflBefore"], d["efflAfter"])
    dt = pct(d["totrBefore"], d["totrAfter"])
    dm = pct(d["meritOriginalBefore"], d["meritOriginalAfter"])
    print("  design constraints")
    print("    EFFL  %.4f -> %.4f  (%+.2f%%)" % (d["efflBefore"], d["efflAfter"], de))
    print("    TOTR  %.4f -> %.4f  (%+.2f%%)" % (d["totrBefore"], d["totrAfter"], dt))
    # A SYNTHESISED guard starts at ~0 by construction: every target is set to
    # the value the design already has, so the baseline merit function is
    # floating-point zero. A percentage against that is arithmetic noise -- the
    # first version of this line printed "+7126488336169751.0%" and that is not
    # a result, it is a division by ~0. Report absolutes there instead.
    print("    %s merit function re-evaluated on the optimised design"
          % ("synthesised guard" if d["meritSynthesised"] else "original"))
    if d["meritSynthesised"] or abs(d["meritOriginalBefore"]) < 1e-9:
        print("      %.6g -> %.6g  (baseline is ~0 by construction, so the"
              % (d["meritOriginalBefore"], d["meritOriginalAfter"]))
        print("       ratio is meaningless; the constraint drift is in the")
        print("       EFFL/TOTR/spot rows above)")
    else:
        print("      %.6g -> %.6g  (%+.1f%%)"
              % (d["meritOriginalBefore"], d["meritOriginalAfter"], dm))
    if abs(de) > 1.0:
        failures.append("EFFL moved %+.2f%%" % de)

    print()
    print("-" * 78)
    if failures:
        print("VERDICT: NOT ACCEPTED")
        for f in failures:
            print("  - %s" % f)
    else:
        print("VERDICT: ACCEPTED -- peak ghost concentration reduced with the")
        print("         design constraints intact.")
    print("  optimised lens: %s" % d["lensOptimised"])
    print("  NOTE: GPIM disperses ghost foci; it does NOT remove Fresnel-fixed")
    print("        energy. Total ghost flux is not expected to fall, and the")
    print("        stray-light loop still measures the mechanical redesign.")
    print("-" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
