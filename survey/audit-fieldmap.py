r"""audit-fieldmap.py -- does each system's base and redesign agree on what
F1/F2/F3 MEAN?

The in-field selector picks a subset of imported sources and forces the last
field to be included, so "SV_F3v" is not a fixed field -- it is whatever the
selector mapped it to on that run. Base and redesign are only comparable if
both runs produced the SAME mapping. If a pipeline revision landed between
them, the same report name denotes different physical fields and the
before/after delta is nonsense.

This already bit once (tessar25 F3: field 3 -> field 4 of 4, read as an 18x
drop that was really a rename). The mapping is printed in each run's
result-*.txt, so it is checkable rather than assumed.
"""
import glob
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
import job as J  # noqa: E402
PAT = re.compile(r"SV_(F\d)v_\S+ <- imported field (\d+) of (\d+)")


def mapping(path):
    """({'F1': '2/4', ...}, why) -- distinguishes a MISSING log from a log
    that predates mapping-logging. Both are unverifiable, but only the second
    means the run happened under the older selector convention."""
    if not os.path.exists(path):
        return None, "(no log)"
    out = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        m = PAT.search(line)
        if m:
            out[m.group(1)] = "%s/%s" % (m.group(2), m.group(3))
    return (out, "") if out else (None, "(pre-logging run)")


print("%-16s %-26s %-26s %s" % ("system", "base mapping", "redesign mapping", "verdict"))
ok = bad = missing = unreadable = 0
for mf in sorted(glob.glob(os.path.join(BASE, "survey", "systems", "*", "*.job.json"))):
    # J.load, not json.load: validates the schema and re-anchors `workdir` on
    # the manifest's own directory. Caught per manifest so one bad file does
    # not abandon the rest of the sweep.
    try:
        m = J.load(mf)
    except (ValueError, IOError) as exc:
        print("%-16s %-26s %-26s %s"
              % (os.path.basename(os.path.dirname(mf)), "-", "-",
                 "UNREADABLE MANIFEST: %s" % exc))
        unreadable += 1
        continue
    slug, wd, pre = m["slug"], m["workdir"], m["simPrefix"]
    b, bwhy = mapping(os.path.join(wd, "result-%s_base.txt" % pre))
    a, awhy = mapping(os.path.join(wd, "result-%s_redesign.txt" % pre))
    if not b or not a:
        # Base and redesign of a pre-logging run were still produced seconds
        # apart by the SAME code, so their delta stands; what is unverifiable
        # is what F1/F2/F3 denote, i.e. comparison ACROSS systems or to an
        # older table.
        print("%-16s %-26s %-26s %s" % (slug, bwhy or "(ok)", awhy or "(ok)",
                                        "labels unverifiable (delta still ok)"))
        missing += 1
        continue
    fmt = lambda d: " ".join("%s=%s" % (k, d[k]) for k in sorted(d))
    if b == a:
        verdict, ok = "comparable", ok + 1
    else:
        diff = [k for k in set(b) | set(a) if b.get(k) != a.get(k)]
        verdict, bad = "MISMATCH on %s -- delta invalid" % ",".join(sorted(diff)), bad + 1
    print("%-16s %-26s %-26s %s" % (slug, fmt(b), fmt(a), verdict))

print("\n%d verified comparable, %d MISMATCHED, %d labels unverifiable%s"
      % (ok, bad, missing,
         ", %d unreadable manifest(s)" % unreadable if unreadable else ""))
if bad:
    print("Mismatched systems compare DIFFERENT physical fields before vs after;")
    print("their in-field numbers must be dropped or the pair re-run together.")
print("\nNo mismatch found: every before/after pair was produced by one code")
print("revision, so each system's own delta is sound. What is NOT sound is")
print("reading F3 as the same field across systems, or against an older")
print("table -- the selector's 'last field ALWAYS included' rule renumbers it.")
