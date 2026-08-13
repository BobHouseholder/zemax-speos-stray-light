r"""analyze-fleet.py -- consolidated before/after across every completed job,
with uncertainty and significance (kpi.py). Reads manifests, so it picks up
whatever the fleet has finished; incomplete jobs are listed, not guessed at.

The audits in lib/audits.py run FIRST and gate every row. Three separate times
a number was published and then withdrawn -- stale geometry, a foreign writer,
a renamed field -- each caught by a script run after the fact. A check you have
to remember to run is not a check, so a row that fails an audit cannot reach
the table: it is excluded and printed with its reason instead.
"""
import glob
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
import audits
import job as J
import kpi

RAYS_STRAY, RAYS_INFIELD = 1_000_000, 200_000


def report_flux(path):
    try:
        t = open(path, encoding="utf-8", errors="replace").read()
    except IOError:
        return None, None
    m = re.search(r"<li>Flux: ([0-9.eE+-]+) W</li>", t)
    e = re.search(r"Total number of errors.{0,200}?([0-9]+)", t, re.S)
    return (float(m.group(1)) if m else None), (int(e.group(1)) if e else None)


def find_report(wd, name):
    """Locate a named report anywhere under the workdir's Speos output.

    Taking subdirs[0] broke once other workstreams added more .scdocx
    documents to the same workdir: the first subdirectory was a different
    document's output, so every result read as 'missing' while 48 reports sat
    on disk. Search by NAME instead of assuming a layout.
    """
    d = os.path.join(wd, "SPEOS output files")
    if not os.path.isdir(d):
        return None
    for root, _dirs, files in os.walk(d):
        if name in files:
            return os.path.join(root, name)
    return None


rows, incomplete, blocked, warned = [], [], [], {}
unreadable = []
for mf in sorted(glob.glob(os.path.join(BASE, "survey", "systems", "*", "*.job.json"))):
    # J.load, not json.load: validates the schema and re-anchors `workdir` on
    # the manifest's own directory. Caught per manifest so one bad file does
    # not abandon the rest of the sweep.
    try:
        m = J.load(mf)
    except (ValueError, IOError) as exc:
        unreadable.append((os.path.basename(os.path.dirname(mf)), str(exc)))
        continue
    slug, wd, pre = m["slug"], m["workdir"], m["simPrefix"]
    st = m.get("stages", {})
    if any(st.get(k, {}).get("status") == "failed" for k in st):
        bad = [k for k in st if st[k].get("status") == "failed"][0]
        incomplete.append((slug, "failed@" + bad,
                           m.get("preflight", {}).get("verdict", "?")))
        continue
    # THE GATE. Runs before any number is read, not after the table ships.
    sev, findings = audits.audit(m)
    if sev == audits.BLOCK:
        blocked.append((slug, [f for f in findings if f[1] == audits.BLOCK]))
        continue
    if sev == audits.WARN:
        warned[slug] = findings
    fb = find_report(wd, "SV_Stray_%s_base.Report.html" % pre)
    fa = find_report(wd, "SV_Stray_%s_redesign.Report.html" % pre)
    if not fb or not fa:
        incomplete.append((slug, "no speos output", "-"))
        continue
    sb, eb = report_flux(fb)
    sa, ea = report_flux(fa)
    if sb is None or sa is None:
        incomplete.append((slug, "stray result missing", "-"))
        continue
    # An exact-zero flux is a wiring bug until proven otherwise (the source
    # never coupled into the system), not a spectacular result. Quarantine it
    # rather than reporting a -100% or dividing by zero.
    if sb <= 0 or sa < 0:
        incomplete.append((slug, "SUSPECT: baseline stray = %g" % sb, "investigate"))
        continue
    inf = []
    for i in (1, 2, 3):
        pb = find_report(wd, "SV_F%dv_%s_base.Report.html" % (i, pre))
        pa = find_report(wd, "SV_F%dv_%s_redesign.Report.html" % (i, pre))
        b = report_flux(pb)[0] if pb else None
        a = report_flux(pa)[0] if pa else None
        if b and a:
            inf.append((b, a))
    rows.append({"slug": slug, "el": m["optics"].get("elements"),
                 "field": m["optics"].get("maxField"), "sb": sb, "sa": sa,
                 "eb": eb, "ea": ea, "inf": inf})

print("=" * 100)
print("AUDIT GATE (runs before any number is reported)")
print("=" * 100)
print("  %-14s %d rows admitted" % ("PASS", len(rows)))
# Only warn about rows that actually reached the table; a system dropped later
# for a zero baseline is reported there, not here, or the counts contradict
# each other (17 warnings over 16 admitted rows).
admitted = set(r["slug"] for r in rows)
warned = {s: f for s, f in warned.items() if s in admitted}
if warned:
    print("  %-14s of them carry a caveat" % ("WARN %d" % len(warned)))
    for slug, findings in sorted(warned.items()):
        for name, _sev, why in findings:
            print("      %-16s %-11s %s" % (slug, name, why))
if blocked:
    print("  %-14s %d rows EXCLUDED -- not trustworthy, not shown below"
          % ("BLOCK", len(blocked)))
    for slug, findings in sorted(blocked):
        for name, _sev, why in findings:
            print("      %-16s %-11s %s" % (slug, name, why))
if not blocked and not warned:
    print("  every admitted row is stamped, current, and label-consistent")

print("\n" + "=" * 100)
print("FLEET RESULTS -- naive placeholder barrel vs prescription-driven seated barrel")
print("=" * 100)
print("%-16s %4s %6s %11s %11s   %s" % ("system", "el", "field", "stray before",
                                        "stray after", "change (sigma)"))
wins = 0
for r in sorted(rows, key=lambda r: r["sb"] - r["sa"], reverse=True):
    c = kpi.compare(kpi.Measure.from_rays(r["sb"], RAYS_STRAY),
                    kpi.Measure.from_rays(r["sa"], RAYS_STRAY), r["slug"])
    if c["delta_pct"] < 0 and c["significant"]:
        wins += 1
    print("%-16s %4s %5s° %11.5f %11.5f   %+7.1f%% (%4.1f sig) %s"
          % (r["slug"], r["el"], r["field"], r["sb"], r["sa"],
             c["delta_pct"], c["n_sigma"], c["verdict"]))

print("\n%d of %d systems show a SIGNIFICANT stray reduction" % (wins, len(rows)))

print("\n--- In-field throughput: only changes clearing 2 sigma are claimed ---")
claimed = 0
for r in rows:
    # A system whose F-labels are unverifiable still has a valid delta -- base
    # and redesign ran under one code revision -- but the field INDEX printed
    # here would be a guess. Mark it rather than implying F3 means the same
    # thing it means for its neighbours.
    # Key off the FIELDMAP check specifically. Keying off "has any warning"
    # mislabelled codeversion warnings as unverified field indices -- these
    # systems were re-run under the current selector and their mappings are
    # logged and checked.
    tag = ("  [field index unverified]"
           if any(n == "fieldmap" for n, _s, _w in warned.get(r["slug"], ()))
           else "")
    for i, (b, a) in enumerate(r["inf"]):
        c = kpi.compare(kpi.Measure.from_rays(b, RAYS_INFIELD),
                        kpi.Measure.from_rays(a, RAYS_INFIELD), r["slug"])
        if c["significant"]:
            claimed += 1
            print("  %-16s field %d  %8.4f -> %8.4f  %+7.1f%% (%4.1f sig) %s%s"
                  % (r["slug"], i + 1, b, a, c["delta_pct"], c["n_sigma"],
                     c["verdict"], tag))
if not claimed:
    print("  (none)")
print("  all other field points: no detectable change (<2 sigma) -- the intended result")

if incomplete:
    print("\n--- Not included (%d) ---" % len(incomplete))
    for s, why, v in incomplete:
        print("  %-16s %-22s %s" % (s, why, v))

# A manifest that could not be read is a system MISSING FROM THIS REPORT, not a
# system with nothing to say. Name them, or the fleet summary silently covers
# fewer systems than the reader assumes.
if unreadable:
    print("\n--- Unreadable manifests (%d, EXCLUDED from every count above) ---"
          % len(unreadable))
    for s, why in unreadable:
        print("  %-16s %s" % (s, why))
