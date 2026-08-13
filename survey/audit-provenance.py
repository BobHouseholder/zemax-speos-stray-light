r"""audit-provenance.py -- which results were actually produced by THIS job?

Resume trusts an artifact because it exists and reports success. It does not
check that the artifact is the one this job produced. This script compares
each artifact's mtime against the manifest's recorded stage completion.

WHY IT EXISTS (and what it disproved). tessar25's in-field F3 read 0.0409
where an earlier pass had 0.7252, and I called that cross-workstream
contamination. It was not. The audit cleared it -- artifact 07-26 04:16,
stage stamped 07-26 04:17, a consistent pair -- and the real cause was that
the in-field selector had changed to "last field ALWAYS included", so F3 was
renumbered from field 3 to field 4 of 4. Same physical run, different label;
0.7252 still sits in SV_F2v. I compared a relabeled field against memory.

Two lessons, both kept:
  1. Report NAMES are not stable identities across pipeline revisions. The
     sim logs record the mapping ("SV_F3v <- imported field 4 of 4") -- read
     it before comparing a number to an older number.
  2. Before blaming another writer, check whether the artifact and its stamp
     agree. They did. The audit is worth keeping for the case where they do
     not: "exists" is not "succeeded", and not "MINE" either.
"""
import datetime
import glob
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
import job as J  # noqa: E402
TOL_MIN = 20        # artifact newer than the stage stamp by more than this => replaced


def mtime(p):
    return datetime.datetime.fromtimestamp(os.path.getmtime(p)) if os.path.exists(p) else None


def find(wd, name):
    d = os.path.join(wd, "SPEOS output files")
    if not os.path.isdir(d):
        return None
    for root, _d, files in os.walk(d):
        if name in files:
            return os.path.join(root, name)
    return None


print("%-16s %-19s %-19s %s" % ("system", "stage stamped", "artifact written", "verdict"))
clean = suspect = unreadable = 0
for mf in sorted(glob.glob(os.path.join(BASE, "survey", "systems", "*", "*.job.json"))):
    # J.load, not json.load: validates the schema and re-anchors `workdir` on
    # the manifest's own directory. Caught per manifest so one bad file does
    # not abandon the rest of the sweep.
    try:
        m = J.load(mf)
    except (ValueError, IOError) as exc:
        print("%-16s %-19s %-19s %s"
              % (os.path.basename(os.path.dirname(mf)), "-", "-",
                 "UNREADABLE MANIFEST: %s" % exc))
        unreadable += 1
        continue
    slug, wd, pre = m["slug"], m["workdir"], m["simPrefix"]
    st = m.get("stages", {}).get("sim_base", {})
    stamp = (st.get("provenance") or {}).get("stamped")
    art = find(wd, "SV_Stray_%s_base.Report.html" % pre)
    if not art:
        continue
    amt = mtime(art)
    if not stamp:
        print("%-16s %-19s %-19s %s" % (slug, "(resumed, no stamp)",
                                        amt.strftime("%m-%d %H:%M:%S"),
                                        "UNKNOWN provenance"))
        suspect += 1
        continue
    sdt = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S")
    drift = (amt - sdt).total_seconds() / 60.0
    if drift > TOL_MIN:
        verdict = "REPLACED after this job (+%.0f min) -- NOT ours" % drift
        suspect += 1
    else:
        verdict = "consistent"
        clean += 1
    print("%-16s %-19s %-19s %s" % (slug, sdt.strftime("%m-%d %H:%M:%S"),
                                    amt.strftime("%m-%d %H:%M:%S"), verdict))

print("\n%d consistent, %d suspect/unknown%s"
      % (clean, suspect,
         ", %d unreadable manifest(s)" % unreadable if unreadable else ""))
print("\n'UNKNOWN provenance' = artifact predates provenance stamping, so the")
print("runner resumed it from a pipeline revision this manifest never saw.")
print("Those rows are STALE, not stolen: re-run them with --force before")
print("presenting the fleet as one dataset.")
