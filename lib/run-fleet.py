"""run-fleet.py -- drive every job manifest through the runner, sequentially.

    python run-fleet.py [--dry-run] [--force STAGE]

Each job is a separate runner process, so one job's failure cannot corrupt
another, and the runner's lock guarantees only one holds the OpticStudio seat
at a time. This is the unattended-overnight entry point: the survey's
remaining eligible systems become a queue rather than a supervised day.

Exit codes per job: 0 = all stages ok/resumed, 2 = a stage failed or preflight
returned NO-GO (which is a CORRECT outcome for an unsupported system, not an
error in the pipeline).
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402
import job as J

BASE = J.BASE
FLV = settings.PYTHON_EXE
RUNNER = os.path.join(BASE, "lib", "runner.py")


DEFAULT_PATS = [os.path.join(BASE, "survey", "systems", "*", "*.job.json"),
                os.path.join(BASE, "*.job.json"),
                os.path.join(BASE, "cooke", "*.job.json")]
# the injected-defect regression suite is NOT in the default set: it is 100
# jobs and would silently multiply an ordinary survey run by ~17x. Ask for it.
TESTCASE_PAT = os.path.join(BASE, "testcases", "cases", "*", "*.job.json")


def find_manifests(pats):
    out = []
    for p in pats:
        out.extend(glob.glob(p))
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", default="")
    ap.add_argument("--only", default="",
                    help="run only these stages (e.g. 'preflight' to triage a "
                         "batch cheaply before committing hours of Speos)")
    ap.add_argument("--testcases", action="store_true",
                    help="run the injected-defect suite in testcases/cases/ "
                         "instead of the survey systems")
    ap.add_argument("--manifests", default="",
                    help="explicit glob of manifests, overriding both defaults")
    ap.add_argument("--skip", default="",
                    help="comma-separated job slugs to leave out")
    a = ap.parse_args()

    if a.manifests:
        pats = [p.strip() for p in a.manifests.split(",") if p.strip()]
    elif a.testcases:
        pats = [TESTCASE_PAT]
    else:
        pats = DEFAULT_PATS
    manifests = find_manifests(pats)
    skip = set(s.strip().lower() for s in a.skip.split(",") if s.strip())
    if skip:
        manifests = [mf for mf in manifests
                     if os.path.basename(mf).replace(".job.json", "").lower()
                     not in skip]
    print("FLEET: %d job manifest(s)\n" % len(manifests))
    results = []
    for mf in manifests:
        cmd = [FLV, RUNNER, mf]
        if a.dry_run:
            cmd.append("--dry-run")
        if a.force:
            cmd += ["--force", a.force]
        if a.only:
            cmd += ["--only", a.only]
        t0 = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True)
        dt = time.time() - t0
        slug = os.path.basename(mf).replace(".job.json", "")
        print(p.stdout.rstrip())
        if p.stderr.strip():
            tail = [l for l in p.stderr.strip().split("\n")
                    if "SyntaxWarning" not in l and "\"\"\"" not in l]
            if tail:
                print("  stderr: %s" % tail[-1][:160])
        results.append((slug, p.returncode, dt, mf))
        print("")

    print("=" * 82)
    print("FLEET SUMMARY")
    print("=" * 82)
    print("%-14s %-8s %8s  %s" % ("job", "exit", "seconds", "stage status"))
    for slug, rc, dt, mf in results:
        try:
            m = J.load(mf)
            st = m.get("stages", {})
            bits = []
            for name in ("preflight", "layout", "odx", "mech", "s0",
                         "sim_base", "sim_redesign"):
                s = st.get(name, {}).get("status")
                bits.append({"ok": "+", "skipped-resume": ".", "failed": "X"}
                            .get(s, "-"))
            status = "".join(bits)
            verdict = m.get("preflight", {}).get("verdict", "?")
        except Exception:
            status, verdict = "???????", "?"
        print("%-14s %-8s %8.0f  %s  (%s)" % (slug, rc, dt, status, verdict))
    print("\n  legend: + ran   . resumed   X failed   - not reached")
    print("  order : preflight layout odx mech s0 sim_base sim_redesign")
    bad = [r for r in results if r[1] not in (0,)]
    print("\n  %d/%d jobs completed cleanly" % (len(results) - len(bad), len(results)))
    for slug, rc, dt, mf in bad:
        try:
            m = J.load(mf)
            for name, s in m.get("stages", {}).items():
                if s.get("status") == "failed":
                    print("    %s halted at %s: %s" % (slug, name,
                          s.get("error", "").split("\n")[0][:110]))
        except Exception:
            pass
    # PROPAGATE the verdict this function already computed. Returning 0 here
    # unconditionally made every caller's exit-code check useless, and the
    # summary two lines up disagreed with the process status in the same run:
    # a fresh clone halted at odx, printed "0/1 jobs completed cleanly", and
    # exited 0 -- so lib/first-run.py, which correctly gates on `rc == 0`, went
    # on to print "FIRST RUN OK -- the loop closed end to end" over a run that
    # simulated nothing. Anything wrapping this in CI failed open.
    #
    # 2, not 1, to match the per-job convention in this module's docstring.
    # Note a preflight NO-GO also reaches here as a non-zero job exit: that IS
    # a correct outcome for an unsupported design, and the distinction is made
    # by READING THE VERDICT (first-run.py does exactly that), not by the fleet
    # pretending every run succeeded.
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
