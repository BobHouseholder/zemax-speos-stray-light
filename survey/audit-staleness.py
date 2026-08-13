r"""audit-staleness.py -- did each sim run AFTER the geometry it claims to measure?

Resume asks "does the output exist?", never "is it newer than its inputs?". A
system whose seated STEP was regenerated after its sim still looks complete:
the sim result is present and reports success, but it measured a barrel that
no longer exists on disk.

Spotted on defaultoptic20 -- seated STEP 07-27 07:21, redesign sim 07-25
09:04. The reported -90.9% was produced by a superseded barrel.

Compares each sim's mtime against the mech/ODX artifacts it consumed.
"""
import glob
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
import job as J  # noqa: E402

# (label, manifest-relative filename pattern) consumed by each sim variant
INPUTS = {
    "base": ["%(slug)s-baseline.step", "%(slug)s.odx"],
    "redesign": ["%(slug)s-seated.step", "%(slug)s.odx"],
}


def mt(p):
    return os.path.getmtime(p) if os.path.exists(p) else None


def stamp(t):
    import datetime
    return datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M") if t else "--"


print("%-16s %-9s %-13s %-13s %s" % ("system", "variant", "sim", "newest input", "verdict"))
stale = fresh = skipped = unreadable = 0
for mf in sorted(glob.glob(os.path.join(BASE, "survey", "systems", "*", "*.job.json"))):
    # J.load, not json.load: it validates the schema and re-anchors `workdir`
    # on the manifest's own directory, so a relocated copy is audited against
    # the files it actually sits next to rather than against the machine it was
    # written on. Caught per manifest -- an audit that dies on system 3 leaves
    # 18 unaudited and reports nothing, which is the failure mode audits exist
    # to catch.
    try:
        m = J.load(mf)
    except (ValueError, IOError) as exc:
        print("%-16s %-9s UNREADABLE MANIFEST: %s"
              % (os.path.basename(os.path.dirname(mf)), "-", exc))
        unreadable += 1
        continue
    slug, wd, pre = m["slug"], m["workdir"], m["simPrefix"]
    for variant, pats in INPUTS.items():
        sim = mt(os.path.join(wd, "result-%s_%s.txt" % (pre, variant)))
        if not sim:
            skipped += 1
            continue
        ins = [(p % {"slug": slug}, mt(os.path.join(wd, p % {"slug": slug})))
               for p in pats]
        ins = [(n, t) for n, t in ins if t]
        if not ins:
            skipped += 1
            continue
        newest_name, newest = max(ins, key=lambda x: x[1])
        # Tolerate a small lead: the runner writes inputs moments before the
        # sim consumes them within one job.
        if newest > sim + 120:
            verdict = "STALE - %s is %.0f h newer" % (newest_name,
                                                      (newest - sim) / 3600.0)
            stale += 1
        else:
            verdict = "ok"
            fresh += 1
        print("%-16s %-9s %-13s %-13s %s"
              % (slug, variant, stamp(sim), stamp(newest), verdict))

print("\n%d up to date, %d STALE, %d not run%s"
      % (fresh, stale, skipped,
         ", %d unreadable manifest(s)" % unreadable if unreadable else ""))
if stale:
    print("STALE = the sim measured geometry that has since been replaced.")
    print("Its number does not describe any artifact currently on disk.")
