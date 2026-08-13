"""stage-remaining.py -- stage the eligible systems not yet run.

The 34 eligible files include many NEAR-duplicates (the same Double Gauss or
Cooke appearing as tolerancing/vignetting variants across short-course
folders). Content-hash dedupe in the catalog only removes byte-identical
files, so these survive. Collapsing them by DESIGN signature avoids spending
~15 min of Speos per redundant copy.

Signature = (surfaces, elements, field, max diameter) -- coarse enough to
collapse tolerance variants, specific enough that distinct designs do not
collide. Groups are printed so the collapsing is auditable.
"""
import json
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "survey", "survey-db.json")
SYS = os.path.join(BASE, "survey", "systems")

# already run (or already rejected) -- identified by design signature below
DONE_NAMES = ("double gauss", "cooke 40", "tessar lens using vignetting",
              "zmxtmp7a5c", "zmxtmp9537", "petzval", "eye_retinal",
              "1- initial camera lens")

db = json.load(open(DB, encoding="utf-8-sig"))
elig = [d for d in db if d.get("score", 0) > 0]
print("eligible in database: %d" % len(elig))


def sig(d):
    return (d["nsurf"], d["elements"], round(d["max_field"], 1),
            round(d["max_diam"], 1))


def already_done(d):
    b = os.path.basename(d["path"]).lower()
    return any(k in b for k in DONE_NAMES)


groups = {}
for d in elig:
    groups.setdefault(sig(d), []).append(d)

print("distinct design signatures: %d\n" % len(groups))

def slugify(name, s):
    base = re.sub(r"[^a-z0-9]+", "", os.path.basename(name).lower().replace(".zmx", ""))[:12]
    return "%s%d" % (base or "sys", int(s[2]))


staged, skipped_done, collapsed = [], 0, 0
for s, members in sorted(groups.items(), key=lambda kv: -kv[1][0]["score"]):
    rep = max(members, key=lambda d: d["score"])
    names = [os.path.basename(m["path"]) for m in members]
    if any(already_done(m) for m in members):
        skipped_done += 1
        print("  skip (already run)  %-46s  [%d file(s)]" % (names[0][:46], len(members)))
        continue
    collapsed += len(members) - 1
    slug = slugify(rep["path"], s)
    wd = os.path.join(SYS, slug)
    os.makedirs(wd, exist_ok=True)
    dst = os.path.join(wd, slug + ".zmx")
    if not os.path.exists(dst):
        shutil.copy2(rep["path"], dst)
    staged.append((slug, dst, rep))
    extra = ("  (+%d dupe%s)" % (len(members) - 1, "s" if len(members) > 2 else "")
             if len(members) > 1 else "")
    print("  STAGE %-14s %-42s %d el %5.1f deg%s"
          % (slug, names[0][:42], rep["elements"], rep["max_field"], extra))

print("\nstaged %d new systems; %d signature(s) already run; "
      "%d near-duplicate file(s) collapsed" % (len(staged), skipped_done, collapsed))
json.dump([{"slug": s, "lens": p, "elements": r["elements"],
            "field": r["max_field"], "score": r["score"]}
           for s, p, r in staged],
          open(os.path.join(BASE, "survey", "staged-remaining.json"), "w"), indent=1)
