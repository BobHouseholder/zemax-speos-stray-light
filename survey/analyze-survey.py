# analyze-survey.py — before/after metrics per survey system.
# Usage: python analyze-survey.py <slug> [<slug> ...]   (ANSYS CPython 3.10)
# For each slug reads SV_*_<slug>_base / _redesign results and emits a row:
# stray flux, in-field fluxes, error rays, backward wall-visibility traces.
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "lib"))
import lpf_read  # noqa: E402
import outsel  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(_ROOT, "survey", "systems")
OUT_JSON = os.path.join(_ROOT, "survey", "survey-results.json")

# Ray counts are OPTIONAL enrichment here -- every other figure in this report
# comes from the Speos HTML. The old code degraded silently when the Illumine
# bindings were absent (wrong interpreter); the same tolerance is kept, but the
# reason is now recorded rather than swallowed, because "no ray counts" looked
# identical to "zero rays" and that is this codebase's signature failure.
HAVE_LPF = True
_LPF_WHY = None


def report(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            t = f.read()
    except IOError:
        return None, None
    m = re.search(r"<li>Flux: ([0-9.eE+-]+) W</li>", t)
    e = re.search(r"Total number of errors.{0,200}?([0-9]+)", t, re.S)
    return (float(m.group(1)) if m else None), (int(e.group(1)) if e else None)


def lpf_traces(path):
    """Trace count for one .lpf, or None with the reason recorded in _LPF_WHY.

    ONE FAILURE IS NOT PROOF THE BACKEND IS GONE. lpf_read holds a single RPC
    server for the whole process, and over a full survey (40-odd files, some
    80 MB) that server is observed to drop mid-run: measured 2026-08-09, a
    21-system pass lost it after the second system and the one-strike latch
    below turned one transient disconnect into 34 permanent nulls -- while
    every one of those systems returns a real count when run on its own
    (gaussianquad14: 67693/22794). A null that means "the connection dropped"
    is indistinguishable from a null that means "no rays", which is the very
    confusion this report exists to avoid.

    So: drop the dead server and retry ONCE. Only a second consecutive failure
    latches the backend off, since that is what genuinely-absent looks like.
    """
    global HAVE_LPF, _LPF_WHY
    if not HAVE_LPF or not os.path.exists(path):
        return None
    try:
        return lpf_read.nb_traces(path)
    except Exception as exc:                                   # noqa: BLE001
        first = "%s: %s" % (type(exc).__name__, exc)
        try:
            lpf_read.close()          # force a fresh server on the next read
            n = lpf_read.nb_traces(path)
            sys.stderr.write("ray-count backend reconnected after: %s\n"
                             % first.splitlines()[0])
            return n
        except Exception as exc2:                              # noqa: BLE001
            HAVE_LPF = False
            _LPF_WHY = "%s: %s" % (type(exc2).__name__, exc2)
            sys.stderr.write("ray counts unavailable after a reconnect attempt, "
                             "continuing without them: %s\n" % _LPF_WHY)
            return None


def sim_prefix(slug):
    """The prefix the wire scripts actually stamped on this system's files.

    `<slug>.job.json` is the ONE authority -- it is what wire-survey.py was
    handed. The old first-four-characters guess (plus a one-entry override map
    for wideangle32) is a SECOND expression of the same convention, and the two
    had already drifted: measured 2026-08-09 it gives `scto` for sctole14 whose
    files on disk say `tole`, `scvf` for scvfac20 whose files say `vfac`, and
    `came` for cameralens14 whose manifest says `caml` -- and the SAME `scto`
    for both sctole14 and sctolcooke12. Every read on those systems missed, and
    a missed read here is a null, which is this codebase's signature
    indistinguishable-from-zero failure. Reading the manifest is also what
    score-loop.py does, and its comment there names the same drift.
    """
    p = os.path.join(BASE, slug, "%s.job.json" % slug)
    try:
        with open(p, encoding="utf-8-sig") as f:
            pre = (json.load(f) or {}).get("simPrefix")
    except (IOError, ValueError):
        pre = None
    if pre:
        return pre
    # No manifest: fall back to the old guess, but SAY SO. It is right for most
    # systems and wrong for at least three, and a silent fallback would restore
    # precisely the drift above.
    guess = {"wideangle32": "wa32"}.get(slug, slug[:4] if len(slug) > 6 else slug)
    sys.stderr.write("%s: no simPrefix in %s -- guessing %r from the slug; "
                     "verify the SV_* filenames match\n" % (slug, p, guess))
    return guess


def wanted_files(pre):
    """EXACTLY the files this script opens for one system, both variants.

    This list is the selection criterion (see outsel.pick_dir_by_files), so it
    must stay in step with what `variant()` below actually reads -- that is the
    whole mechanism: the folder that wins is the one that can answer the
    question being asked, rather than the one the filesystem lists first.
    """
    out = []
    for sfx in ("%s_base" % pre, "%s_redesign" % pre):
        out.append("SV_Stray_%s.Report.html" % sfx)
        out += ["SV_F%dv_%s.Report.html" % (i, sfx) for i in (1, 2, 3)]
        out.append("SV_Back_%s.FrontCatch.lpf" % sfx)
    return out


def outdir(slug, pre):
    """The Speos output folder holding THIS system's survey results.

    This used to return `subs[0]` -- whichever subfolder os.listdir yielded
    first. Measured 2026-08-09 on doubletstart5 that is `doubletstart5-blast`,
    a PST side run with zero .lpf files, ahead of `doubletstart5-speos` which
    holds the real SV_Back_doub_base.FrontCatch.lpf. lpf_traces() was handed a
    path that does not exist, returned None, and every back_traces field in
    survey-results.json was null -- while the reader itself was fine (handed
    the file directly it returns 40882). Same defect, same cause, as the one
    already fixed in testcases/score-loop.py.

    Returns (dir, diag). dir is None when no folder holds a single wanted file:
    say "no results" rather than read a side run and report its absence of
    SV_ files as a failed simulation.
    """
    root = os.path.join(BASE, slug, "SPEOS output files")
    return outsel.pick_dir_by_files(root, wanted_files(pre), slug)


def variant(od, sfx, tag):
    if od is None:
        return None
    v = {"tag": tag}
    fx, er = report(os.path.join(od, "SV_Stray_%s.Report.html" % sfx))
    v["stray_W"], v["errors"] = fx, er
    infield = []
    for i in (1, 2, 3):
        fi, _ = report(os.path.join(od, "SV_F%dv_%s.Report.html" % (i, sfx)))
        if fi is not None:
            infield.append(round(fi, 4))
    v["infield_W"] = infield

    # WHY back_traces IS NULL MUST TRAVEL WITH THE NULL. There are three
    # reasons and they call for three different actions -- the file was never
    # written (this run did not happen), the ray-count backend is unavailable
    # (wrong interpreter or no RPC server), or the sim genuinely caught nothing.
    # Reporting a bare None for the first two made a missing simulation look
    # exactly like a measured zero, which is how the outdir bug above stayed
    # invisible across a whole survey.
    lp = os.path.join(od, "SV_Back_%s.FrontCatch.lpf" % sfx)
    v["back_tracesFile"] = lp
    if not os.path.exists(lp):
        v["back_traces"] = None
        v["back_tracesNote"] = ("no %s in %s -- the backward trace was not run "
                                "(this is NOT a trace count of zero)"
                                % (os.path.basename(lp), os.path.basename(od)))
    else:
        v["back_traces"] = lpf_traces(lp)
        if v["back_traces"] is None:
            v["back_tracesNote"] = ("ray-count backend unavailable: %s (this is "
                                    "NOT a trace count of zero)" % _LPF_WHY)
    return v


results = {}
unresolved = []
for slug in sys.argv[1:]:
    pj = os.path.join(BASE, slug, slug + "-params.json")
    params = json.load(open(pj)) if os.path.exists(pj) else {}
    pre = sim_prefix(slug)
    print("== %s  (%s el, %.0f deg field, stray source %.0f deg)  prefix %s"
          % (slug, params.get("elements", "?"), params.get("maxField", 0),
             params.get("strayDeg", 0), pre))
    try:
        od, diag = outdir(slug, pre)
    except outsel.OutputSelectionError as exc:
        print("   SKIPPED: %s\n" % exc)
        unresolved.append(slug)
        continue
    if od is None:
        # loud, and with the evidence: which folders exist and how many of the
        # files this script reads each one holds
        print("   NO RESULTS: no folder under 'SPEOS output files' holds any "
              "SV_*_%s_* file this script reads" % pre)
        unresolved.append(slug)
    else:
        top = diag[0]
        print("   reading %s  (%d of %d wanted files)"
              % (top["dir"], top["have"], top["want"]))
        if top["have"] < top["want"]:
            print("   PARTIAL: %d wanted file(s) absent from the chosen folder"
                  % (top["want"] - top["have"]))
    for line in outsel.format_diag(diag):
        print("     %s" % line)
    b = variant(od, pre + "_base", "baseline")
    a = variant(od, pre + "_redesign", "redesign")
    row = {"params": params, "simPrefix": pre,
           "outdir": od, "outdirCandidates": diag,
           "baseline": b, "redesign": a}
    if b and a and b.get("stray_W") and a.get("stray_W"):
        row["delta_pct"] = round(100.0 * (a["stray_W"] - b["stray_W"]) / b["stray_W"], 1)
        if b.get("infield_W") and a.get("infield_W"):
            n = min(len(b["infield_W"]), len(a["infield_W"]))
            row["infield_delta_pct"] = [
                round(100.0 * (a["infield_W"][i] - b["infield_W"][i]) / b["infield_W"][i], 2)
                for i in range(n)]
    results[slug] = row
    for v in (b, a):
        if v is None:
            print("   %-9s MISSING (no output folder resolved)" % "?")
            continue
        print("   %-9s stray %s W | in-field %s | errors %s | back traces %s"
              % (v["tag"],
                 ("%.5f" % v["stray_W"]) if v["stray_W"] is not None else "----",
                 v["infield_W"], v["errors"],
                 v["back_traces"] if v["back_traces"] is not None else "null"))
        if v.get("back_tracesNote"):
            print("   %-9s   ^ %s" % ("", v["back_tracesNote"]))
    if "delta_pct" in row:
        print("   >> stray %+.1f%%   in-field delta %s%%"
              % (row["delta_pct"], row.get("infield_delta_pct")))
    print()

with open(OUT_JSON, "w") as f:
    json.dump(results, f, indent=1)
print("wrote %s" % OUT_JSON)

# A system whose output folder could not be resolved contributes nothing but a
# row of nulls, and a row of nulls reads like a measurement. Name them, and
# exit non-zero so a caller that chains off this script notices.
if unresolved:
    print("\n%d system(s) produced NO readable results: %s"
          % (len(unresolved), ", ".join(unresolved)))
    sys.exit(1)
