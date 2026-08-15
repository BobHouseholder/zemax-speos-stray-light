"""run-bsdf-band.py -- how much does the WALL BSDF assumption move the answer?

The workflow's wall material is synthetic (`black-anodize-plausible`); real
measured data is blocked on a customer supplying it. The wall model is not a
minor detail - swapping specular-black for the synthetic BSDF moved 20 deg
stray flux +22% and grew the vane benefit from -15.7% to -24.6%. Leaving that
unbounded means every stray number carries an unstated uncertainty.

So bound it. Re-run the loop's stray measurement at three reflectance levels
(make-bsdf-band.py builds them; a factor of 4 span, deliberately wide):

    low  TIS 2.2% -> 12.5%   a good black coating
    mid  TIS 4.5% -> 25%     the shipped model
    high TIS 9.0% -> 50%     a poor or aged surface

and report how far the headline stray REDUCTION moves. The reduction is a
ratio of two runs that share the same walls, so it should be far more stable
than either absolute flux - that is the hypothesis being tested, and if it
holds the workflow's conclusions are robust to the missing measurement.

Systems are chosen as unmodified real designs at OPPOSITE ends of the measured
benefit range, so the band is bracketed rather than sampled at one point.

Usage:  C:\\flv\\Scripts\\python.exe run-bsdf-band.py [--dry-run]
Needs the OpticStudio/Speos seat - do not run alongside a fleet sweep.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "lib"))
import job as J  # noqa: E402
import report_read  # noqa: E402
import pst_read  # noqa: E402
import seat  # noqa: E402
import settings  # noqa: E402

# Both of these were wrong until 2026-08-10 and neither was caught by the
# 2026-08-08 settings conversion, which scanned lib/, survey/ and testcases/
# only -- this file sits at the repo root.
#   * the launcher was a hardcoded v261 path, so this driver alone would have
#     kept pointing at an old install after an Ansys upgrade;
#   * the config was `survey/survey-config.txt`, the SHARED global that
#     pst_read.survey_config replaced after a band run and a PST sweep crossed
#     configs on 2026-08-04 and an A01 measurement executed wideangle32's
#     geometry. A band run is precisely the long job that collision hit.
SPEOS = settings.SPEOS_LAUNCHER
WIRE = os.path.join(BASE, "survey", "wire-survey.py")
SPEOS_CFG = pst_read.survey_config("bsdf-band")

BANDS = [("low", os.path.join(BASE, "black-anodize-low.anisotropicbsdf")),
         ("high", os.path.join(BASE, "black-anodize-high.anisotropicbsdf"))]

# unmodified real designs at opposite ends of the benefit range, plus a
# wide-field case. mid (the shipped BSDF) is already measured for all three.
SYSTEMS = [
    ("a37", os.path.join(BASE, "testcases", "cases", "A37", "a37.job.json"), -7.1),
    ("b31", os.path.join(BASE, "testcases", "cases", "B31", "b31.job.json"), -90.9),
    ("wideangle32", os.path.join(BASE, "survey", "systems", "wideangle32",
                                 "wideangle32.job.json"), -79.5),
]

# WIDE-FIELD EXTENSION, added 2026-07-28. The first three showed the reduction
# is stable to a 4x wall-reflectance swing on a narrow-field system (A37, 3.0
# pp) and a near-saturated one (B31, 7.1 pp) - but wideangle32 moved 27.5 pp,
# from -60.6% to -88.1%. That is one measurement, and it is the one that
# matters, because grazing incidence is exactly where the TIS rise is steepest
# and wide fields are where grazing dominates. Three more wide-field systems
# decide whether +/-14 pp is a general wide-field figure or a wideangle32
# peculiarity.
WIDE = [
    ("rearstop31", os.path.join(BASE, "survey", "systems", "rearstop31",
                                "rearstop31.job.json"), -87.4),   # 31 deg
    ("tessar25", os.path.join(BASE, "survey", "systems", "tessar25",
                              "tessar25.job.json"), -94.8),       # 25 deg
    ("b32", os.path.join(BASE, "testcases", "cases", "B32", "b32.job.json"), -99.1),  # 34 deg
]
if "--wide" in sys.argv:
    SYSTEMS = WIDE

# HIGH-GRAZING EXTENSION, added 2026-08-10. Median bore grazing angle predicts
# band spread at r = +0.813 over the 9 systems measured so far -- but those
# span only 49.1-59.0 deg, and a corpus-wide scan then found 60 of 86 systems
# AT OR ABOVE the level that produced >=11.5 pp, with twelve of them beyond
# wideangle32's 59.0 deg. Those sit OUTSIDE the range where the correlation was
# established, so the published "+/-11 pp typical" cannot be restated on
# extrapolation. These three sample 65.0, 66.7 and 70.0 deg and decide whether
# the relationship continues, saturates, or breaks.
# NOT the top two by grazing: C09 (70.0 deg) is `stray-undefined` -- a 0-degree
# field, the same class as the withdrawn C25 -- and B13 (69.6 deg) was itself
# withdrawn on 2026-08-10 for sitting at the noise floor. That the two HIGHEST
# grazing systems in the corpus are both unmeasurable is a result in its own
# right, and it is why the set below starts at 66.7 deg.
HIGHGRAZE = [
    ("vacuumcell15", os.path.join(BASE, "survey", "systems", "vacuumcell15",
                                  "vacuumcell15.job.json"), -75.3),                       # 66.7 deg
    ("a08", os.path.join(BASE, "testcases", "cases", "A08", "a08.job.json"), -14.6),      # 65.6 deg
    ("doubletstart5", os.path.join(BASE, "survey", "systems", "doubletstart5",
                                   "doubletstart5.job.json"), -11.0),                     # 65.0 deg
]
if "--highgraze" in sys.argv:
    SYSTEMS = HIGHGRAZE

# SAMPLE SET, added 2026-08-13. The four BUNDLED designs (lib/make-samples.py),
# the only ones a public reader can reproduce -- every other system named in
# this file is a stock OpticStudio sample and cannot ship.
#
# This is the same argument that produced the sample set itself, applied to the
# other unverifiable claim. The README states the benefit range AND the wall
# band (0.2-28.6 pp); the range is now reproducible and the band is not, because
# it rests entirely on the unshippable corpus. Measuring it here closes that.
#
# `longbore-f8` is the interesting one: it shows NO benefit at all (-0.7%,
# 0.6 sigma). A band on a null is a different question from a band on -95% --
# it asks whether the null is robust to the wall model or an artefact of this
# particular BSDF, and only one of those answers is safe to publish.
SAMPLES = [
    ("example-triplet", os.path.join(BASE, "survey", "systems", "example-triplet",
                                     "example-triplet.job.json")),
    ("wfov-30", os.path.join(BASE, "survey", "systems", "wfov-30",
                             "wfov-30.job.json")),
    ("fast-f2p5", os.path.join(BASE, "survey", "systems", "fast-f2p5",
                               "fast-f2p5.job.json")),
    ("longbore-f8", os.path.join(BASE, "survey", "systems", "longbore-f8",
                                 "longbore-f8.job.json")),
]
if "--samples" in sys.argv:
    SYSTEMS = SAMPLES


# ---- reading results back, so no number in this file is a literal ----------
# The four SAMPLES entries used to carry a hardcoded "shipped-BSDF result" each.
# They were the PRE-CONFIRMATION-ANGLE values: c93073b re-measured every
# published number at a forward-confirmed angle, touched README.md,
# lib/first-run.py, lib/runner.py and lib/seat.py, and missed this file -- the
# second repo-wide sweep to skip it, for the same reason recorded at the top of
# this file (it sits at the repo root). A literal that a sweep can miss is a
# literal that will be missed again, so the value is read back instead.
def _mid(mpath):
    """(before, after, kpi-comparison) for the shipped BSDF, from the artifacts."""
    try:
        m = J.load(mpath)
    except Exception:                                          # noqa: BLE001
        return None
    sb, sa = report_read.stray_pair(m["workdir"], m["simPrefix"])
    c = report_read.change_pct(sb, sa, what=m["slug"])
    return None if c is None else (sb, sa, c)


def _mid_str(mpath):
    r = _mid(mpath)
    return "not yet measured" if r is None else "%+.1f%%" % r[2]["delta_pct"]


def report_band():
    """Print the low/mid/high/spread table the README promises.

    run-bsdf-band.py ran the 16 simulations and stopped, so the table the README
    presents as reproducible by one command could not actually be obtained --
    the .Report.html files landed under the band simPrefix and nothing read
    them. Extraction and significance come from lib/report_read.py, which is
    analyze-fleet.py's own code, so this table and the fleet table cannot
    disagree about what a flux is.
    """
    print("=" * 78)
    print("WALL-BSDF BAND -- reduction at each reflectance level")
    print("=" * 78)
    print("%-16s %5s %9s %9s %9s %9s" % ("design", "angle", "low", "mid", "high", "spread"))
    print("-" * 78)
    rows = 0
    for entry in SYSTEMS:
        slug, mpath = entry[0], entry[1]
        if not os.path.exists(mpath):
            print("%-16s  (no manifest)" % slug)
            continue
        m = J.load(mpath)
        wd, pre = m["workdir"], m["simPrefix"]
        vals = {}
        for tag in ("low", "", "high"):
            sb, sa = report_read.stray_pair(wd, pre + tag)
            c = report_read.change_pct(sb, sa, what=slug)
            vals[tag or "mid"] = None if c is None else c["delta_pct"]
        got = [v for v in vals.values() if v is not None]
        spread = (max(got) - min(got)) if len(got) == 3 else None
        f = lambda v: "  MISSING" if v is None else "%+.1f%%" % v
        print("%-16s %4.0f%s %9s %9s %9s %9s"
              % (slug, m["sim"].get("strayDeg") or 0, chr(176),
                 f(vals["low"]), f(vals["mid"]), f(vals["high"]),
                 "n/a" if spread is None else "%.1f pp" % spread))
        rows += 1
    print("-" * 78)
    print("  %d design(s) reported. Each is banded AT ITS OWN forward-confirmed" % rows)
    print("  angle: measuring one degree off understated example-triplet's wall")
    print("  sensitivity sevenfold (2.2 pp at 15 deg against 16.2 pp at 16 deg).")


def run_one(mpath, variant, bsdf, tag, dry):
    m = J.load(mpath)
    slug = m["slug"]
    wd = m["workdir"]
    orig = m["materials"]["wallBsdf"]
    m["materials"]["wallBsdf"] = bsdf
    # a distinct simPrefix so band results never overwrite the shipped ones
    orig_pre = m["simPrefix"]
    m["simPrefix"] = "%s%s" % (orig_pre, tag)
    cfg = J.render_speos_config(m, variant, os.path.join(wd, "%s-%s-%s.cfg.txt"
                                                         % (slug, tag, variant)))
    m["materials"]["wallBsdf"] = orig          # never persist the override
    m["simPrefix"] = orig_pre
    with open(cfg) as f:
        body = f.read()
    log = body.strip().split("\n")[-1]
    if dry:
        print("    would run %-8s %-9s -> %s" % (tag, variant, os.path.basename(log)))
        return None
    shutil.copyfile(cfg, SPEOS_CFG)
    if os.path.exists(log):
        os.remove(log)
    t0 = time.time()
    # Speos spawns OpticStudio via ComponentOpticStudio.Create, so this
    # contends for the single seat AND takes optishpc 10/10. Held per
    # launch, not per batch, so star-stop can interleave. See lib/seat.py.
    with seat.SeatLock('stray-light-loop/run-bsdf-band.py'):
        subprocess.run([SPEOS, "/RunScript=%s" % WIRE, "/Headless=True",
                        "/Splash=False", "/Welcome=False", "/ExitAfterScript=True"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, timeout=2400)
    dt = time.time() - t0
    ok = os.path.exists(log) and "wire-survey end" in open(
        log, encoding="utf-8", errors="replace").read()
    print("    %-8s %-9s %s (%.0fs)" % (tag, variant, "ok" if ok else "FAILED", dt))
    return log if ok else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true",
                    help="print the low/mid/high/spread table from results "
                         "already on disk and exit -- the step the README's "
                         "reproduce instruction was missing")
    ap.add_argument("--wide", action="store_true",
                    help="run the wide-field extension set instead")
    ap.add_argument("--samples", action="store_true",
                    help="run the four BUNDLED sample designs -- the only "
                         "systems a public reader can reproduce, so the wall "
                         "band becomes checkable rather than asserted")
    ap.add_argument("--highgraze", action="store_true",
                    help="run the high-grazing extension set (65-70 deg) "
                         "instead -- tests whether the grazing/band "
                         "correlation holds beyond the 49-59 deg it was "
                         "measured over")
    a = ap.parse_args()
    if a.report:
        report_band()
        return
    for tag, bsdf in BANDS:
        if not os.path.exists(bsdf):
            sys.exit("missing %s - run make-bsdf-band.py first" % bsdf)
    for entry in SYSTEMS:
        slug, mpath = entry[0], entry[1]
        if not os.path.exists(mpath):
            print("SKIP %s (no manifest)" % slug)
            continue
        print("%s  (shipped-BSDF result: %s)" % (slug, _mid_str(mpath)))
        for tag, bsdf in BANDS:
            for variant in ("base", "redesign"):
                run_one(mpath, variant, bsdf, tag, a.dry_run)
    print("band runs done")


if __name__ == "__main__":
    main()
