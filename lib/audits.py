r"""audits.py -- the checks a result must pass BEFORE it is allowed into a table.

Three times now a number reached a report and was withdrawn afterwards:

  "exists"      is not "succeeded"  -- a crashed Speos run leaves a result log
  "succeeded"   is not "MINE"       -- another run can write the same sim names
  "mine"        is not "current"    -- inputs get regenerated after the sim

Each was caught by a script written AFTER the table went out. Scripts you have
to remember to run are not a control. So the checks live here, as functions,
and `analyze-fleet.py` calls them as a gate: a row that cannot prove itself is
excluded and listed with its reason, never quietly printed.

Severity:
  BLOCK -- the number is not trustworthy; keep it out of the table entirely.
  WARN  -- the number is sound for this system's own before/after delta, but
           something about it does not generalise (e.g. F-labels that cannot
           be compared across systems).
"""
import datetime
import os
import re

BLOCK, WARN, OK = "BLOCK", "WARN", "OK"

PROV_TOL_MIN = 20      # artifact newer than its stage stamp by more than this
FRESH_SLACK_S = 120    # inputs written moments before the run consumes them

# What each sim variant consumes. Mirrors runner.DEPENDS; kept here so the
# report can audit a workdir without importing the runner.
SIM_INPUTS = {
    "base":     ["%(slug)s-baseline.step", "%(slug)s.odx"],
    "redesign": ["%(slug)s-seated.step", "%(slug)s.odx"],
}

_FIELDPAT = re.compile(r"SV_(F\d)v_\S+ <- imported field (\d+) of (\d+)")


def _mt(p):
    return os.path.getmtime(p) if os.path.exists(p) else None


def _find(wd, name):
    """Locate a named report anywhere under the workdir's Speos output.

    Never assume subdirs[0]: once a workdir holds more than one .scdocx, the
    first subdirectory is a DIFFERENT document's output.
    """
    d = os.path.join(wd, "SPEOS output files")
    if not os.path.isdir(d):
        return None
    for root, _dirs, files in os.walk(d):
        if name in files:
            return os.path.join(root, name)
    return None


def provenance(m):
    """Was this system's stray result produced by the run the manifest records?"""
    slug, wd, pre = m["slug"], m["workdir"], m["simPrefix"]
    art = _find(wd, "SV_Stray_%s_base.Report.html" % pre)
    if not art:
        return OK, ""                       # nothing to vouch for; other gates catch it
    stamp = ((m.get("stages", {}).get("sim_base", {}) or {})
             .get("provenance") or {}).get("stamped")
    if not stamp:
        return BLOCK, "no provenance stamp (resumed from a pre-manifest pipeline)"
    sdt = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S")
    drift = (datetime.datetime.fromtimestamp(_mt(art)) - sdt).total_seconds() / 60.0
    if drift > PROV_TOL_MIN:
        return BLOCK, "artifact written %.0f min after the stage stamp (another writer)" % drift
    return OK, ""


def staleness(m):
    """Did each sim run AFTER the geometry it claims to measure?"""
    slug, wd, pre = m["slug"], m["workdir"], m["simPrefix"]
    for variant, pats in SIM_INPUTS.items():
        log = os.path.join(wd, "result-%s_%s.txt" % (pre, variant))
        sim = _mt(log)
        if not sim:
            continue
        ins = [(p % {"slug": slug}, _mt(os.path.join(wd, p % {"slug": slug})))
               for p in pats]
        ins = [(n, t) for n, t in ins if t]
        if not ins:
            continue
        name, newest = max(ins, key=lambda x: x[1])
        if newest > sim + FRESH_SLACK_S:
            return BLOCK, "%s sim is %.0f h older than %s" % (
                variant, (newest - sim) / 3600.0, name)
    return OK, ""


def fieldmap(m):
    """Do base and redesign agree on what F1/F2/F3 DENOTE?

    The in-field selector forces the last field to be included, so 'SV_F3v' is
    whatever that run mapped it to. Equal mappings => comparable.
    """
    wd, pre = m["workdir"], m["simPrefix"]

    def mapping(path):
        if not os.path.exists(path):
            return None, "(no log)"
        out = {}
        for line in open(path, encoding="utf-8", errors="replace"):
            g = _FIELDPAT.search(line)
            if g:
                out[g.group(1)] = "%s/%s" % (g.group(2), g.group(3))
        return (out, "") if out else (None, "pre-logging run")

    b, bwhy = mapping(os.path.join(wd, "result-%s_base.txt" % pre))
    a, awhy = mapping(os.path.join(wd, "result-%s_redesign.txt" % pre))
    if not b or not a:
        return WARN, "F-labels unverifiable (%s) - own delta ok, not comparable across systems" % (bwhy or awhy)
    if b != a:
        diff = ",".join(sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k)))
        return BLOCK, "base and redesign disagree on %s - before/after compares different fields" % diff
    return OK, ""


def codeversion(m):
    """Is the code that produced this result still the code on disk?

    `provenance()` has hashed every stage's scripts from the beginning -- the
    manifest recorded them faithfully and nothing ever compared them. The
    staleness check covers DATA inputs (odx, STEP); a change to wire-survey.py
    is just as capable of invalidating a number and left no trace at all.

    WARN rather than BLOCK: an edit may be provably inert for a given system
    (the 106 deg source-placement fix reproduces the old point bit-for-bit
    below 85 deg, so no system in the table can move). That judgement needs a
    human, and a gate that cries wolf on every comment change teaches people
    to ignore it. So it names the script and the stage, and says nothing more.
    """
    import hashlib

    def sha(p):
        if not os.path.exists(p):
            return None
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:12]

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    drifted = []
    for stage, rec in sorted((m.get("stages") or {}).items()):
        for name, was in ((rec.get("provenance") or {}).get("scripts") or {}).items():
            hits = [os.path.join(d, name)
                    for d in (os.path.join(root, "survey"), os.path.join(root, "lib"),
                              os.path.join(root, "ghost"), root)
                    if os.path.exists(os.path.join(d, name))]
            if not hits:
                continue
            now = sha(hits[0])
            if now and was and now != was[:len(now)] and was[:12] != now:
                drifted.append("%s@%s (%s -> %s)" % (name, stage, was[:8], now[:8]))
    if drifted:
        return WARN, "produced by different code: " + "; ".join(drifted[:3])
    return OK, ""


CHECKS = [("provenance", provenance), ("staleness", staleness),
          ("fieldmap", fieldmap), ("codeversion", codeversion)]


def audit(m):
    """Run every check. Returns (severity, [(name, severity, reason), ...])
    where severity is the worst seen: BLOCK > WARN > OK."""
    rows, worst = [], OK
    for name, fn in CHECKS:
        sev, why = fn(m)
        if sev != OK:
            rows.append((name, sev, why))
            if sev == BLOCK:
                worst = BLOCK
            elif worst != BLOCK:
                worst = WARN
    return worst, rows
