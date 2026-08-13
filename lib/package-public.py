"""package-public.py -- build `public-subset/` from the live tree, sanitised.

    python lib/package-public.py [--check] [--verbose]

      --check    report what WOULD change and exit non-zero if anything would;
                 nothing is written. Use this in a pre-publication check.

Why this exists as a TOOL and not as a one-off edit
---------------------------------------------------
The public subset is a CURATED artefact, not a mirror, and it is sanitised --
every `C:\\Users\\<user>` is rewritten to `C:\\Users\\<user>` so the material can
stand on its own without naming anyone. Both of those properties are destroyed
by an ordinary copy. On 2026-08-05 a hand-run sync copied 22 files across and
would silently have re-imported the username on the next pass, because the live
tree legitimately keeps real paths. Sanitisation done once, in place, regresses
the first time anyone syncs. Done here, it cannot.

The three jobs, in order
------------------------
1. SYNC   live -> public, for files the subset already carries. The subset's
          file list is curated by hand; this never ADDS files, it only refreshes
          what is there (new candidates are reported, not imported).
2. REBUILD `cases/built-ground-truth.json`, which exists only in the subset and
          is an aggregate of the 100 per-case `built.json` files. It cannot be
          copied, and it goes stale silently: after the 2026-08-05 gate-4c
          correction it still recorded C10 as expectPreflight=GO while the
          scripts shipped beside it said NO-GO.
3. SANITISE everything written.

Comparison is against the SANITISED live file
---------------------------------------------
Public is sanitised and live is not, so a naive hash comparison reports every
single file as drifted, forever. Every comparison here is
`sanitise(live) == public`, which is the only form that converges.

Where the live file is WORSE
----------------------------
Two classes, both encoded as RULES rather than a list of filenames, so they keep
working as the corpus changes:

  * `lens`-only drift. A live `preflight-bases/*.json` often records the scratch
    file OpticStudio actually had open (`ZMXtmp7A5C.zmx`, `Petzval.zmx`,
    `1- Initial Camera Lens.ZMX`) while the public copy names the curated corpus
    file. If `lens` is the ONLY differing key, the public copy is the better
    record and is kept.
  * Run noise in `*.preflight.txt` -- a `GUARD WARN [licence-seat]` line, or a
    `PREFLIGHT: <name>.zmx` header naming that same scratch file. And if the
    live file has strictly fewer content lines, it has LOST something (observed
    on C13, whose live copy is missing its whole header block), so it is never
    allowed to overwrite a fuller public one.

Anything that differs for a reason NOT covered by a rule is copied, and named in
the report. Silence means nothing happened; every decision is printed.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PS = os.path.join(BASE, "public-subset")
LIVE = os.path.join(BASE, "testcases")

# --- sanitisation -----------------------------------------------------------
# Three encodings occur in the tree and all three must be handled: forward
# slash (the bulk, ~519), single backslash (in .ps1 and Python raw strings),
# and JSON-escaped double backslash. Written as one pattern per separator so
# the separator STYLE is preserved -- rewriting `C:\Users\<user>` to a forward
# slash form would corrupt PowerShell string literals.
# The substitution itself lives in lib/sanitise.py -- ONE implementation, shared
# with build-corpus-export.py, because a byte-exact rule spread across two
# publishing paths is how a username eventually ships from the one that was not
# updated.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitise import IDENT, read, sanitise, sha, text, write  # noqa: E402,F401


# --- "is the live file worse?" rules ----------------------------------------
NOISE = (
    re.compile(r"GUARD WARN \[licence-seat\]"),
    # `.+` not `\S+`: the sample lenses have SPACES in their names
    # ("1- Initial Camera Lens.ZMX", "Cooke 40 degree field.zmx"), and \S+
    # silently failed to match them -- so two files were being refreshed from
    # a live copy naming the scratch file, which is the exact regression these
    # rules exist to prevent.
    re.compile(r"^\s*PREFLIGHT:\s*.+\.zmx\s*$", re.I),
    re.compile(r"^\s*lens\s*:", re.I),
)


def keep_public_json(pub, live):
    """True when `lens` is the only key that differs -- scratch-path drift."""
    try:
        a, b = json.loads(text(pub)), json.loads(text(live))
    except ValueError:
        return None
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    diff = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    if diff and diff <= {"lens"}:
        return "live differs only in `lens` (scratch path); public names the corpus file"
    return None


def keep_public_txt(pub, live):
    """True for licence-seat noise, scratch-path headers, or lost content."""
    pa = [l for l in text(pub).splitlines() if l.strip()]
    pb = [l for l in text(live).splitlines() if l.strip()]
    if len(pb) < len(pa):
        return ("live has %d fewer content lines -- it has LOST something"
                % (len(pa) - len(pb)))
    changed = [x for x in difflib.unified_diff(pa, pb, n=0, lineterm="")
               if x[:1] in "+-" and not x.startswith(("---", "+++"))]
    if changed and all(any(rx.search(x[1:]) for rx in NOISE) for x in changed):
        return "live differs only in run noise (licence-seat warning / scratch-path header)"
    return None


def keep_public(rel, pub, live):
    ext = os.path.splitext(rel)[1].lower()
    if ext == ".json":
        return keep_public_json(pub, live)
    if ext == ".txt":
        return keep_public_txt(pub, live)
    return None


# --- the three jobs ---------------------------------------------------------
def public_files():
    for dp, _, fs in os.walk(PS):
        for f in sorted(fs):
            p = os.path.join(dp, f)
            yield p, os.path.relpath(p, PS).replace(os.sep, "/")


# Files the subset ships from the REPO ROOT rather than from testcases/: the
# shared modules the published scripts import, and the config template that
# settings.py tells the reader to fill in. Everything else mirrors testcases/.
#
# This is an explicit allowlist and not a "try testcases/, then try BASE/"
# fallback ON PURPOSE. A blanket fallback would silently start refreshing any
# public file whose name happens to match a root-level one -- README.md being
# the obvious hazard, where the curated public text would be overwritten by an
# internal document the first time someone added one.
#
# A value is the path RELATIVE TO THE REPO ROOT that the subset entry mirrors.
# wire-survey.py is shipped flat (matching the other wire-*.py in the subset)
# but lives in survey/, so its source has to be spelled out or it would look
# like a curated public-only file and never refresh.
ROOT_SOURCED = {
    "straylight.toml.example": "straylight.toml.example",
    "wire-survey.py": os.path.join("survey", "wire-survey.py"),
}


def live_for(rel):
    """Where `rel` comes from in the live tree, or None if the subset curates it.

    None means "no live counterpart", which package-public treats as curated
    and never refreshes. That is right for genuinely public-only files and
    WRONG for a copy of a live module: it would be sanitised forever and
    updated never, drifting silently out of step with the code it mirrors.
    So anything shipped from lib/ has to be mapped here.
    """
    if rel in ROOT_SOURCED:
        p = os.path.join(BASE, ROOT_SOURCED[rel])
    else:
        root = BASE if rel.startswith("lib/") else LIVE
        p = os.path.join(root, rel.replace("/", os.sep))
    return p if os.path.isfile(p) else None


def build_ground_truth():
    """The public-only aggregate of the 100 per-case built.json files."""
    cdir = os.path.join(LIVE, "cases")
    agg = {}
    for cid in sorted(os.listdir(cdir)):
        p = os.path.join(cdir, cid, "%s.built.json" % cid)
        if os.path.isfile(p):
            agg[cid] = json.loads(text(read(p)))
    return sanitise(json.dumps(agg, indent=1).encode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report only; exit 1 if anything would change")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    copied, kept, cleaned, orphan, unchanged = [], [], [], [], 0

    for path, rel in public_files():
        if rel == "cases/built-ground-truth.json":
            continue                                   # rebuilt below
        pub = read(path)
        pub_s = sanitise(pub)
        live = live_for(rel)

        # A file the subset curates with no live counterpart still has to be
        # sanitised -- it is published like everything else.
        if live is None:
            orphan.append(rel)
            if pub != pub_s:
                cleaned.append((rel, sha(pub), sha(pub_s)))
                if not a.check:
                    write(path, pub_s)
            continue

        target = sanitise(read(live))

        # EVERY comparison below is sanitised-vs-sanitised. Comparing the raw
        # public file against a sanitised live one makes the sanitisation itself
        # look like content drift: the `lens` key differs on all ~200 preflight
        # files purely because one says `bob` and the other `<user>`, the
        # lens-only rule fires on every one of them, and the sanitisation can
        # then never actually be applied. Measured on the first dry run: 207
        # files "kept" for a difference that was not a difference.
        if pub_s == target:
            if pub == pub_s:
                unchanged += 1
            else:
                cleaned.append((rel, sha(pub), sha(pub_s)))
                if not a.check:
                    write(path, pub_s)
            continue

        why = keep_public(rel, pub_s, target)
        if why:
            kept.append((rel, why))
            if pub != pub_s:            # keep public's CONTENT, still sanitised
                cleaned.append((rel, sha(pub), sha(pub_s)))
                if not a.check:
                    write(path, pub_s)
            continue

        copied.append((rel, sha(pub), sha(target)))
        if not a.check:
            write(path, target)

    # the aggregate
    gt_path = os.path.join(PS, "cases", "built-ground-truth.json")
    gt_new = build_ground_truth()
    gt_old = read(gt_path) if os.path.exists(gt_path) else ""
    gt_changed = gt_new != gt_old
    if gt_changed and not a.check:
        write(gt_path, gt_new)

    # --- report -------------------------------------------------------------
    print("=== package-public %s ===" % ("(check only)" if a.check else ""))
    print("  already correct      %d" % unchanged)
    print("  sanitised only       %d" % len(cleaned))
    for rel, before, after in (cleaned if a.verbose else cleaned[:6]):
        print("      %-46s %s -> %s" % (rel, before, after))
    if not a.verbose and len(cleaned) > 6:
        print("      ... %d more (--verbose to list)" % (len(cleaned) - 6))
    print("  refreshed from live  %d" % len(copied))
    for rel, before, after in copied:
        print("      %-46s %s -> %s" % (rel, before, after))
    print("  kept (live is worse) %d" % len(kept))
    for rel, why in (kept if a.verbose else kept[:6]):
        print("      %-46s %s" % (rel, why))
    if not a.verbose and len(kept) > 6:
        print("      ... %d more (--verbose to list)" % (len(kept) - 6))
    print("  public-only, no live counterpart: %d" % len(orphan))
    for rel in orphan:
        print("      %s" % rel)
    print("  cases/built-ground-truth.json: %s"
          % ("REBUILT %s -> %s" % (sha(gt_old), sha(gt_new)) if gt_changed
             else "unchanged"))

    # --- verification -------------------------------------------------------
    print("\n=== verification ===")
    leaks = []
    for path, rel in public_files():
        if IDENT.search(read(path)):
            leaks.append(rel)
    print("  identifier occurrences: %s"
          % ("NONE" if not leaks else "%d FILES STILL LEAK: %s"
             % (len(leaks), ", ".join(leaks[:5]))))

    bad_syntax = []
    for path, rel in public_files():
        if rel.endswith(".py"):
            try:
                compile(text(read(path)), rel, "exec")
            except SyntaxError as e:
                bad_syntax.append("%s: %s" % (rel, e))
    print("  python files parse:     %s"
          % ("all OK" if not bad_syntax else "FAILURES: %s" % bad_syntax))

    gt = json.loads(text(read(gt_path)))
    spec = {c["id"]: c
            for c in json.loads(text(read(os.path.join(PS, "specs.json"))))["cases"]}
    mism = [cid for cid, r in gt.items()
            if cid in spec and r.get("truth") != spec[cid]["truth"]]
    print("  ground truth vs specs:  %s"
          % ("consistent" if not mism else "MISMATCH on %s" % mism))

    # Judged by the suite's OWN rule (score-preflight.py): BINARY -- did the
    # gate proceed or halt -- is the verdict that costs money, and it is the one
    # that must hold. A strict GO vs GO-WITH-WARNINGS difference is a severity
    # call, a property of the base design; the suite scores 100/100 binary and
    # 99/100 strict, so a single strict miss is the DOCUMENTED result, not a
    # packaging failure. Reported, never fatal.
    def proceeds(v):
        return v in ("GO", "GO-WITH-WARNINGS", "PROCEED")

    vmism, strict_only = [], []
    for cid, r in gt.items():
        want = (r.get("truth") or {}).get("expectPreflight")
        pf = os.path.join(PS, "preflight", "%s.preflight.json" % cid)
        if not want or not os.path.exists(pf):
            continue
        got = json.loads(text(read(pf))).get("verdict")
        if proceeds(want) != proceeds(got):
            vmism.append("%s want %s got %s" % (cid, want, got))
        elif want != "PROCEED" and want != got:
            strict_only.append("%s %s vs %s" % (cid, want, got))
    print("  preflight vs expected:  %s%s"
          % ("binary consistent" if not vmism else "BINARY MISMATCH: %s" % vmism[:4],
             "" if not strict_only
             else "  (%d strict-only, expected: %s)"
                  % (len(strict_only), ", ".join(strict_only[:3]))))

    failed = bool(leaks or bad_syntax or mism or vmism)
    pending = len(copied) + len(cleaned) + int(gt_changed)
    if a.check and pending:
        print("\n  --check: %d file(s) would change" % pending)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
