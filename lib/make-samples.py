"""make-samples.py -- generate the bundled sample designs and their manifests.

    python lib/make-samples.py            generate any that are missing
    python lib/make-samples.py --rebuild  regenerate all of them

Then run them exactly as you would your own designs:

    python lib/run-fleet.py --only preflight     triage, ~10 s each
    python lib/run-fleet.py                      the full loop

WHY A SET AND NOT JUST THE ONE
------------------------------
`first-run.py` generates ONE design, the triplet, because its job is to prove
an installation works with the least possible ceremony. This generates four,
because one example makes a misleading advertisement.

The headline claim about this workflow is that the benefit is large but NOT
predictable in advance -- it spans roughly -1% to -99% across the development
corpus, and five separate proxies for guessing where a given design lands were
each tested against measurement and each refuted. That claim rests on a corpus
of stock OpticStudio sample files which can never ship. So without these, a
reader has exactly one reproducible data point, and it sits at the strong end:
"this always gives you 95%" is the natural and wrong conclusion.

These four exist so the range can be reproduced rather than taken on trust.

HOW THEY WERE CHOSEN, AND THE TRAP THAT WOULD HAVE RUINED IT
------------------------------------------------------------
On OPTICAL grounds only: f/number, field angle, track length, element count.
NOT on the stray-light answer they were expected to produce.

The trap is selection: design several, ship the ones with impressive numbers,
and the set becomes an advertisement that a customer's first real lens will
contradict. It cannot be dodged by designing toward a spread either, because
the five refuted proxies mean nobody here can predict which archetype gives a
large benefit. So the archetypes were fixed first, all four were run, and every
result is published -- including any that are unimpressive, which are the ones
that make the honest claim credible.

    example-triplet   f/5,   50 mm,  +/-14 deg,  3 elements,  62 mm track
    fast-f2p5         f/2.5, 50 mm,  +/-10 deg,  3 elements,  65 mm track
    longbore-f8       f/8,  200 mm,  +/-4 deg,   2 elements, 205 mm track
    wfov-30           f/4,   20 mm,  +/-30 deg,  3 elements,  38 mm track

Names differ in their first FOUR characters on purpose: those four become the
Speos artefact prefix, and two designs sharing them would silently overwrite
each other's results. make-manifests.py refuses that rather than proceeding.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402
import job as J  # noqa: E402

BASE = J.BASE
LIB = os.path.join(BASE, "lib")
PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]

# (slug, -Design argument). The slug is the folder name, the file name and the
# job name, exactly as for a design you stage yourself.
SAMPLES = [
    ("example-triplet", "triplet"),
    ("fast-f2p5", "fast"),
    ("longbore-f8", "longbore"),
    ("wfov-30", "widefov"),
]


def lens_path(slug):
    return os.path.join(BASE, "survey", "systems", slug, slug + ".zmx")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="regenerate designs that already exist")
    a = ap.parse_args()

    made, kept, failed = [], [], []
    for slug, design in SAMPLES:
        out = lens_path(slug)
        if os.path.isfile(out) and not a.rebuild:
            kept.append(slug)
            print("  %-16s already present" % slug)
            continue
        # Each generator call takes the OpticStudio seat for a few seconds and
        # releases it. Bounded, because a contended seat otherwise hangs with
        # no output at all -- see first-run.py's run().
        try:
            p = subprocess.run(
                PS + [os.path.join(LIB, "first-run-lens.ps1"),
                      "-OutZmx", out, "-Design", design],
                timeout=600)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            print("  %-16s TIMED OUT -- the licence seat is probably held "
                  "elsewhere" % slug)
            failed.append(slug)
            continue
        if rc != 0 or not os.path.isfile(out):
            print("  %-16s FAILED (exit %s)" % (slug, rc))
            failed.append(slug)
            continue
        made.append(slug)

    print("\n%d generated, %d already present, %d failed"
          % (len(made), len(kept), len(failed)))
    if failed:
        print("A generator failure is an OpticStudio/ZOS-API problem, not a")
        print("pipeline one -- the .zmx is written by your OpticStudio.")
        return 1

    print("\nWriting manifests...")
    rc = subprocess.run([settings.PYTHON_EXE,
                         os.path.join(LIB, "make-manifests.py")],
                        timeout=180).returncode
    if rc != 0:
        return 1

    print("\nNext:")
    print("    python lib/run-fleet.py --only preflight   triage, ~10 s each")
    print("    python lib/run-fleet.py                    the full loop")
    print("\nREADME.md carries the measured result for each of these on the")
    print("reference machine, so you can check your numbers against them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
