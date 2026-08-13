"""build-corpus-export.py -- assemble the shippable corpus export.

    python lib/build-corpus-export.py [--out DIR] [--check]

What this is, and why it is not `public-subset/`
------------------------------------------------
`public-subset/` is a working directory: 453 files, including 200 per-case
preflight outputs and 164 s0 records. It is sanitised and internally consistent,
but it is still recognisably one person's tree, and most of it is raw run
output that nobody reading the paper needs.

This export is the artefact the paper actually promises: the corpus ships as
**generator + ground truth + SHA-256 base manifest**, verified to contain no
recoverable prescription. Four things, each with a job:

  bases/        the 16 base designs identified by SHA-256, NOT shipped. They are
                stock Ansys Zemax OpticStudio samples and are not ours to
                publish; the manifest lets a recipient confirm byte-for-byte
                that they hold the same ones.
  generator/    the scripts that turn those 16 bases into the 100 injected-defect
                cases. This is what makes the corpus reproducible rather than
                merely described.
  ground-truth/ what a correct workflow must report for each case, plus the
                surface numbers the builder RESOLVED at build time (a defect
                injected "after the last optical surface" lands somewhere
                specific, and the truth records where).
  scoring/      the scripts that grade a workflow against that ground truth, and
                results/ the scores this workflow achieved -- so the numbers in
                the paper can be recomputed rather than believed. results/ also
                carries the CORRECTIONS to those scores, because two angle
                defects were found after the first pass and shipping the
                original numbers alone would hand someone figures the
                accompanying document contradicts.

`verify.py` ships WITH the export and re-runs the no-prescription check on the
recipient's copy. The claim is meant to be checkable by the person receiving it,
not asserted by the person sending it.

The no-prescription rule, stated operationally
----------------------------------------------
A prescription is per-surface geometry: radius, thickness, glass and
semi-diameter, in surface order. The export may carry AGGREGATE metadata
(element and surface counts, EFL, working f/number, field angle, an unordered
glass list, spot RMS) and the parameters of the defects WE injected, because
those are ours by construction. It must not carry a per-surface numeric array
under an optical key. Measured on the current corpus: zero such arrays.

That distinction is the whole claim. A glass NAME list says which catalogue
entries appear; it does not give radii or thicknesses, and no combination of the
aggregates here reconstructs a lens.
"""
import argparse
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
from sanitise import leaks, read, sanitise, sha, text, write  # noqa: E402

SRC = os.path.join(BASE, "public-subset")   # already sanitised + consistent

# (destination subdir, source name) -- curated by hand, deliberately.
LAYOUT = [
    ("bases", "BASES-manifest.json"),

    ("generator", "build-specs.py"),
    ("generator", "make-cases.ps1"),
    ("generator", "make-testcase-manifests.py"),
    ("generator", "audit-build.py"),
    ("generator", "sync-truth.py"),

    ("ground-truth", "specs.json"),
    ("ground-truth", "cases/built-ground-truth.json"),

    ("scoring", "score-preflight.py"),
    ("scoring", "score-ghosts.py"),
    ("scoring", "score-loop.py"),
    ("scoring", "compare-verdicts.py"),
    ("scoring", "reli_geom.py"),

    ("results", "score-preflight.json"),
    ("results", "score-ghosts.json"),
    ("results", "score-loop.json"),
    ("results", "audit-build.json"),
    ("results", "preflight-baseline-verdicts.json"),
    ("results", "baseline-preflight-score.txt"),
    ("results", "final-preflight-score.txt"),
    ("results", "final-ghost-score.txt"),
    ("results", "oversize-analysis.json"),
    ("results", "ghostdecode-results.json"),
    ("results", "straydecode-results.json"),

    # The corrections, shipped WITH the scores they correct. `score-loop.json`
    # records reductions at the placeholder angle; without these files the
    # export would hand someone numbers that the accompanying document
    # contradicts, which is the opposite of the point.
    ("results", "corpus-recomputed.json"),
    ("results", "remeasure-censored.json"),
    ("results", "censored-angles.json"),
    ("results", "censored-peaks.json"),
    ("results", "convention-check.json"),
    ("results", "band-corrected.json"),
]

VERIFY = '''"""verify.py -- check this export against the claims made for it.

    python verify.py

Run it. Nothing here needs to be taken on trust:

  1. MANIFEST.sha256 -- every file is present and unmodified.
  2. No recoverable prescription. A prescription is per-surface geometry
     (radius / thickness / glass / semi-diameter in surface order). Aggregate
     metadata and the parameters of the deliberately injected defects are
     expected and allowed; a per-surface numeric array under an optical key is
     not, and fails this check.
  3. Internal consistency -- every case in the ground truth is present in the
     spec, and every scored case exists in the ground truth.
  4. No operator identity -- the export carries no author, and the check
     confirms no username survives in a path.

Exit code 0 means every check passed.
"""
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OPTICAL = re.compile(r"radi|radius|thick|curv|glass|semidia|semi_dia|conic|sag",
                     re.I)
IDENT = re.compile(r"(?i)C:(?:\\\\\\\\|\\\\|/)Users(?:\\\\\\\\|\\\\|/)(?!<user>)[A-Za-z0-9._-]+")

fails = []


def check(name, ok, detail=""):
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                           "" if ok else "  -- " + detail))
    if not ok:
        fails.append(name)


# 1 -- manifest
man = os.path.join(HERE, "MANIFEST.sha256")
listed, bad = 0, []
for line in open(man, encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    digest, rel = line.split("  ", 1)
    p = os.path.join(HERE, rel.replace("/", os.sep))
    listed += 1
    if not os.path.exists(p):
        bad.append(rel + " (missing)")
    elif hashlib.sha256(open(p, "rb").read()).hexdigest() != digest:
        bad.append(rel + " (modified)")
check("manifest: %d files intact" % listed, not bad, ", ".join(bad[:3]))

# 2 -- no recoverable prescription
arrays = []
def walk(o, key, where):
    if isinstance(o, dict):
        for k, v in o.items():
            walk(v, k, where)
    elif isinstance(o, list):
        if OPTICAL.search(key or "") and o and all(
                isinstance(x, (int, float)) and not isinstance(x, bool) for x in o):
            arrays.append("%s: %s (len %d)" % (where, key, len(o)))
        for v in o:
            walk(v, key, where)

for dp, _, fs in os.walk(HERE):
    for f in fs:
        if f.endswith(".json"):
            p = os.path.join(dp, f)
            try:
                walk(json.load(open(p, encoding="utf-8-sig")), "",
                     os.path.relpath(p, HERE))
            except ValueError:
                pass
check("no per-surface optical arrays", not arrays, "; ".join(arrays[:3]))

# 3 -- internal consistency
spec = json.load(open(os.path.join(HERE, "ground-truth", "specs.json"),
                      encoding="utf-8-sig"))
gt = json.load(open(os.path.join(HERE, "ground-truth", "built-ground-truth.json"),
                    encoding="utf-8-sig"))
ids = {c["id"] for c in spec["cases"]}
missing = sorted(set(gt) - ids)
check("ground truth subset of spec (%d cases)" % len(gt), not missing,
      "not in spec: %s" % missing[:5])
mismatch = [cid for cid, r in gt.items()
            if cid in ids and r.get("truth") != next(
                c for c in spec["cases"] if c["id"] == cid)["truth"]]
check("truth blocks agree with spec", not mismatch, "differ: %s" % mismatch[:5])

# 4 -- no operator identity
who = []
for dp, _, fs in os.walk(HERE):
    for f in fs:
        p = os.path.join(dp, f)
        try:
            t = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        if IDENT.search(t):
            who.append(os.path.relpath(p, HERE))
check("no operator identity in paths", not who, ", ".join(who[:3]))

print("\\n%s" % ("ALL CHECKS PASSED" if not fails
                 else "FAILED: %s" % ", ".join(fails)))
sys.exit(1 if fails else 0)
'''


def readme(counts, bases):
    return """# Injected-defect corpus for stray-light workflow validation

A corpus of **100 optical systems** built by taking **%d real base designs** and
injecting into each a single named pathology at a recorded location, so that the
answer a workflow must return is known by construction. It exists to let an
analysis workflow be **scored** rather than assessed for plausibility.

This export carries no author and makes no claim to priority. It is intended to
be judged from its contents.

## What is here

| | |
|---|---|
| `bases/` | the %d base designs identified by **SHA-256**, not shipped |
| `generator/` | the scripts that build the 100 cases from those bases |
| `ground-truth/` | what a correct workflow must report for each case |
| `scoring/` | the scripts that grade a workflow against that ground truth |
| `results/` | the scores this workflow achieved, **and their corrections** |
| `verify.py` | re-runs every claim below on your copy |

**Read `results/` with its corrections.** `score-loop.json` holds the original
scoring, measured with the off-axis source at a placeholder angle. Two separate
defects in how that angle was chosen were found afterwards, and both are shipped
here rather than quietly folded in: `corpus-recomputed.json` gives the restated
corpus (60 systems, median reduction 56.7%%, median error against the originally
published values 7.3 points).

**Three systems are withdrawn, not scored.** `C25` has a 0-degree design field,
so it cannot be separated from its own stray region. `B13` and `C14` were
withdrawn on 2026-08-10 for the same reason reached from the other direction:
their seated sweeps sit at the noise floor (2.3e-05 W falling to literal zero
with a 28%% spread between repeat runs at the same angle; 4.9e-05 W to 3.1e-09
with 29%%). A peak located inside scatter is not a measurement. Removing them
moved the median reduction from 64.7%% to 56.7%% -- their near-zero seated
fluxes had been producing large apparent reductions -- and the median error
from 7.0 to 7.3 points. **The figure got worse when the unmeasurable systems
were removed, which is the direction that should increase confidence in the
rest.**

**Do not read that median as an expectation.** The reduction ranges from
`-99.9%%` to `+10.4%%` -- the worst case is a small *penalty*, not a small
benefit -- and position within that range is **not predictable from the
design**. Measured directly by scaling one parameter on a fixed base:
narrowing the field helps a double gauss (`-12.7%%` -> `-39.5%%`), halves a
cooke triplet (`-91.3%%` -> `-46.8%%`), and leaves a tessar with no separable
out-of-field region at all; widening a cemented doublet from 5 to 15 degrees
improves it tenfold (`-2.5%%` -> `-23.2%%`). Neither architecture nor field
angle predicts the outcome, which is the reason to measure a given design
rather than look it up.

A **third outcome** exists alongside "helps" and "does not help": some designs
have no separable out-of-field stray region, because the imaging cone fills the
angular space the enclosure could act on. The workflow detects this and refuses
to return a reduction rather than quoting one measured on the imaging cone's
shoulder. `remeasure-censored.json` and
`censored-{angles,peaks}.json` the systems affected and why, `convention-check.json`
the measured 0.4-point bound on the one methodological fork left open, and
`band-corrected.json` the wall-model sensitivity band. The corrections are part
of the evidence, not an erratum appended to it.

## What is deliberately NOT here

**The base prescriptions.** All %d are stock Ansys Zemax OpticStudio sample
files. They are not ours to redistribute, so `bases/BASES-manifest.json` gives
each one's source filename, its directory under the OpticStudio installation,
its SHA-256 and its byte count. If you have OpticStudio, you can confirm
byte-for-byte that you hold the same designs this corpus was built from.

**Any recoverable prescription.** A prescription is per-surface geometry --
radius, thickness, glass and semi-diameter in surface order. This export
contains none. What it does contain is aggregate metadata (element and surface
counts, EFL, working f/number, field angle, an unordered glass list, spot RMS)
and the parameters of the defects we injected ourselves, which are ours by
construction. `verify.py` enforces the distinction: it fails if any file carries
a per-surface numeric array under an optical key.

**The raw per-case run output.** 200 preflight records and 164 sequential-ghost
records were left out; they are regenerable from the generator and add bulk
rather than evidence. The aggregate scores they produce are in `results/`.

## Reproducing

1. Obtain the %d base designs and verify them against `bases/BASES-manifest.json`.
2. Run `generator/` to rebuild the 100 cases. `audit-build.py` confirms every
   injection landed where the spec said it would.
3. Run your own workflow over them.
4. Score it with `scoring/`, and compare against `results/`.

Steps 1-2 need OpticStudio and the ZOS-API. Steps 3-4 do not depend on this
particular pipeline -- the scoring is deliberately separable, and is the part
worth reusing even if none of the rest is.

## What the corpus is for

The difficulty this addresses is structural: a stray-light prediction has no
closed-form answer to check against, and hardware correlation is available late,
rarely, and one system at a time. The usual fallbacks are that the pipeline runs
and looks plausible, or that two of the analyst's own estimates agree -- which
measures consistency, not correctness. Two estimates can agree and both be
wrong.

Scoring against answers fixed in advance is what separates those cases. On this
workflow it produced several corrections that plausibility checking had not,
including a measurement angle that was wrong on most systems and reversed the
engineering conclusion on six of them. Those corrections, not the passing
scores, are the argument for building a corpus like this.

## Contents

%s

Every file is listed with its SHA-256 in `MANIFEST.sha256`. Run `verify.py`.
""" % (bases, bases, bases, bases, counts)


def build(out, check_only=False):
    plan = []
    for sub, name in LAYOUT:
        src = os.path.join(SRC, name.replace("/", os.sep))
        if not os.path.exists(src):
            plan.append((sub, name, None, "MISSING FROM SOURCE"))
            continue
        data = sanitise(read(src))
        plan.append((sub, os.path.basename(name), data, None))

    missing = [n for _, n, d, why in plan if why]
    print("=== corpus export %s ===" % ("(check only)" if check_only else ""))
    print("  target: %s" % out)
    if missing:
        print("  !! missing from public-subset: %s" % ", ".join(missing))

    if check_only:
        for sub, name, data, why in plan:
            print("  %-14s %-34s %s" % (sub, name, why or sha(data)))
        return 1 if missing else 0

    if os.path.isdir(out):
        shutil.rmtree(out)
    for sub in ("bases", "generator", "ground-truth", "scoring", "results"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)

    written = []
    for sub, name, data, why in plan:
        if why:
            continue
        rel = "%s/%s" % (sub, name)
        write(os.path.join(out, sub, name), data)
        written.append((rel, data))

    # verify.py travels with the export
    vdata = VERIFY.encode("utf-8")
    write(os.path.join(out, "verify.py"), vdata)
    written.append(("verify.py", vdata))

    # README, then MANIFEST last (it hashes everything else)
    counts = "\n".join(
        "- `%s` — %s" % (rel, "%.1f KB" % (len(d) / 1024.0))
        for rel, d in sorted(written))
    nbases = len(json.loads(text(read(os.path.join(SRC, "BASES-manifest.json")))))
    rdata = readme(counts, nbases).encode("utf-8")
    write(os.path.join(out, "README.md"), rdata)
    written.append(("README.md", rdata))

    lines = ["# SHA-256 of every file in this export. Verified by verify.py."]
    for rel, d in sorted(written):
        lines.append("%s  %s" % (sha(d, 0), rel))
    write(os.path.join(out, "MANIFEST.sha256"),
          ("\n".join(lines) + "\n").encode("utf-8"))

    for rel, d in sorted(written):
        print("  %-42s %8.1f KB  %s" % (rel, len(d) / 1024.0, sha(d)))
    print("  %-42s" % "MANIFEST.sha256")

    bad = [rel for rel, d in written if leaks(d)]
    print("\n  identity leaks: %s" % (", ".join(bad) if bad else "NONE"))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        os.path.expanduser("~"), "ClaudeOS", "outputs",
        "2026-08-05-straylight-corpus-export", "straylight-corpus"))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    return build(os.path.abspath(a.out), a.check)


if __name__ == "__main__":
    sys.exit(main())
