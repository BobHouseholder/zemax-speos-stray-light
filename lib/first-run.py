"""first-run.py -- take a fresh installation to its first result.

    python lib/first-run.py            generate the example, then preflight it
    python lib/first-run.py --full     ...and run the whole eight-stage loop
    python lib/first-run.py --rebuild  regenerate the example lens first

WHY THIS EXISTS
---------------
The pipeline analyses lens prescriptions, and none ship with it -- the designs
it was developed against are stock OpticStudio sample files and are not ours to
redistribute. That is correct, and it also means a new user's very first
obstacle is supplying a design, before they have any evidence the installation
works at all. When something then fails, they cannot tell whether the fault is
their config, their Ansys install, their lens, or the pipeline.

This separates those questions. It generates a known-good example, runs it, and
tells you which of the four is wrong when something breaks. A first success
needs nothing from you but a working installation.

The example is GENERATED, not shipped: `lib/first-run-lens.ps1` builds an
ordinary Cooke triplet from a fixed prescription of our own and saves it with
your OpticStudio. No .zmx is redistributed, which is what keeps `.zmx` in
build-distribution.py's BANNED_EXT with no exceptions.

WHAT IT COSTS
-------------
The default stops after `preflight`: about ten seconds of OpticStudio, plus a
few for the lens itself. It answers "is this installation wired up correctly"
and nothing else -- deliberately, because the answer is worth having before
anyone commits an hour of solver time.

`--full` runs all eight stages, roughly forty minutes, almost all of it Speos.
It takes the OPTIS HPC entitlement for the duration; nothing else can solve
while it runs. See section 7 of the install guide.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402  -- validates the whole config at import
import job as J  # noqa: E402

BASE = J.BASE
LIB = os.path.join(BASE, "lib")
PS = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]

# The example's name is its folder name, its file name and its job slug, exactly
# as for a design you stage yourself -- there is no special case for it anywhere
# in the pipeline. Four characters of it become the Speos artefact prefix
# ("exam"), which is why the name is not something like "example" that a user's
# own second design might collide with.
SLUG = "example-triplet"
WORKDIR = os.path.join(BASE, "survey", "systems", SLUG)
LENS = os.path.join(WORKDIR, SLUG + ".zmx")


def step(n, total, title):
    print("\n[%d/%d] %s" % (n, total, title))
    print("-" * 72)
    sys.stdout.flush()


def run(cmd, what, timeout):
    """Run a child so the user SEES it, and never wait forever.

    Two deliberate choices, both learned the hard way while building this:

    Output is NOT captured. The child writes straight to the console, so a
    forty-minute `--full` run shows its stages as they happen. Capturing and
    replaying at the end means the tool whose job is to prove the installation
    works sits silent for the entire time it is working.

    Every child has a TIMEOUT. The first version of this file had none, and the
    lens generator hung on a contended licence seat: no output, no error, no
    exit, for as long as anyone was willing to wait. A first-run tool that can
    hang silently is worse than no first-run tool, because a new user cannot
    tell it apart from slowness and has no basis to judge either.
    """
    # FLUSH FIRST. The child writes to the same console, but Python
    # block-buffers its own stdout whenever that console is not a terminal --
    # so `python lib/first-run.py > log.txt`, which is exactly what someone
    # does to send us a failure, produces a file where every heading appears
    # AFTER the output it introduces. The log then reads as though the run
    # happened in an order it did not.
    sys.stdout.flush()
    try:
        p = subprocess.run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("\n  %s TIMED OUT after %d s and was stopped." % (what, timeout))
        print("  The usual cause is the licence seat: another OpticStudio or")
        print("  Speos instance holds it, or a previous run leaked one. Close")
        print("  them (or reboot) and try again -- install guide, section 7.")
        return 124
    if p.returncode != 0:
        print("\n  %s FAILED (exit %d)" % (what, p.returncode))
    return p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="continue past preflight through all eight stages "
                         "(~40 min, takes the HPC entitlement throughout)")
    ap.add_argument("--rebuild", action="store_true",
                    help="regenerate the example lens even if it already exists")
    a = ap.parse_args()

    total = 4
    print("=" * 72)
    print("FIRST RUN -- generate a known-good example and put it through the loop")
    print("=" * 72)

    # 1. configuration. Importing settings already validated it or raised, so
    #    reaching this line IS the check; printing the values makes the report
    #    useful when a later stage fails for a reason that traces back here.
    step(1, total, "Configuration")
    print("  ansys_root  %s" % settings.ANSYS_ROOT)
    print("  version     %s" % settings.ANSYS_VERSION)
    print("  python_exe  %s" % settings.PYTHON_EXE)
    print("  CONFIG OK")

    # 2. the example lens
    step(2, total, "Example design")
    if os.path.isfile(LENS) and not a.rebuild:
        print("  already present: %s" % LENS)
        print("  (pass --rebuild to regenerate it)")
    else:
        rc = run(PS + [os.path.join(LIB, "first-run-lens.ps1"), "-OutZmx", LENS],
                 "lens generation", 600)
        if rc != 0:
            print("\n  Could not build the example. This is an OpticStudio/ZOS-API")
            print("  problem, not a pipeline one -- the lens is written by your")
            print("  OpticStudio. Check steps 1 and 4 of the install guide.")
            return 1
        if not os.path.isfile(LENS):
            print("\n  The generator reported success but wrote no file at")
            print("  %s" % LENS)
            return 1

    # 3. the manifest
    step(3, total, "Job manifest")
    rc = run([settings.PYTHON_EXE, os.path.join(LIB, "make-manifests.py")],
             "make-manifests", 180)
    if rc != 0:
        return 1
    mpath = J.path_for(SLUG, WORKDIR, "manifest")
    if not os.path.isfile(mpath):
        print("\n  No manifest was written for '%s'. If make-manifests reported")
        print("  a simPrefix collision above, rename a folder under")
        print("  survey/systems/ so the first four characters differ.")
        return 1

    # 4. run it
    stages = "all eight stages" if a.full else "preflight only"
    step(4, total, "Running the example (%s)" % stages)
    if a.full:
        print("  This takes roughly forty minutes and holds the HPC")
        print("  entitlement throughout. It is resumable: if it is interrupted,")
        print("  running it again picks up from the last completed stage.\n")
    cmd = [settings.PYTHON_EXE, os.path.join(LIB, "run-fleet.py"),
           "--manifests", mpath]
    if not a.full:
        cmd += ["--only", "preflight"]
    # preflight is ~10 s of OpticStudio; the full loop is ~40 min of Speos and
    # the runner already bounds each stage, so this is a backstop against a
    # hung child rather than a schedule.
    rc = run(cmd, "the run", 4 * 3600 if a.full else 900)

    # verdict, read from the manifest rather than from the child's exit code --
    # preflight returning NO-GO is a CORRECT outcome for an unsupported design,
    # so exit status alone cannot distinguish "the pipeline works and refused
    # this lens" from "the pipeline is broken". For the bundled example the
    # distinction matters: a NO-GO here means the installation is wrong, because
    # this design is known to pass.
    print("\n" + "=" * 72)
    verdict = "?"
    try:
        verdict = J.load(mpath).get("preflight", {}).get("verdict", "?")
    except Exception:                                             # noqa: BLE001
        pass
    if verdict in ("GO", "GO-WITH-WARNINGS") and rc == 0:
        print("FIRST RUN OK -- preflight returned %s" % verdict)
        print("=" * 72)
        print("\nYour installation works end to end. What to do next:\n")
        if not a.full:
            print("  Run the example the whole way (~40 min):")
            print("      python lib/first-run.py --full\n")
        print("  Then stage a design of your own -- folder name and file name")
        print("  must match, and that name becomes the job's name everywhere:\n")
        print("      survey/systems/<name>/<name>.zmx\n")
        print("      python lib/make-manifests.py")
        print("      python lib/run-fleet.py --only preflight\n")
        print("  Read README.md on interpreting the result before quoting a")
        print("  number: the wall scattering model shipped here is synthetic,")
        print("  and it moves the answer by 0.2 to 28.6 percentage points")
        print("  depending on the design.")
        return 0

    print("FIRST RUN FAILED -- preflight verdict: %s" % verdict)
    print("=" * 72)
    print("\nThis example is known to pass, so a failure here is your")
    print("installation and not the design. In order of likelihood:\n")
    print("  * The licence seat. A Speos or OpticStudio instance already")
    print("    holds it, or an earlier run left one behind. Section 7 of the")
    print("    install guide covers this; it is the most common cause.")
    print("  * Ansys sign-in expired -- look for a 499 in the output above.")
    print("  * straylight.toml points at the wrong install. Re-run")
    print("    `python lib/settings.py --check`.")
    print("  * A glass catalog is missing, in which case the generator")
    print("    warned about elements above.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
