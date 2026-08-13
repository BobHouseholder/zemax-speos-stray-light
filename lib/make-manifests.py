"""make-manifests.py -- generate a job manifest for every staged system.

    python lib/make-manifests.py

Systems are DISCOVERED, not listed. Put your design at

    survey/systems/<name>/<name>.zmx

and it is picked up; `<name>` is yours to choose and becomes the job's slug.
Nothing here needs editing to add a design.

That was not always true. Until 2026-08-09 this file carried a hardcoded list
of the five systems on the reference machine, so a customer who staged their
own lens correctly got "0 manifests written" and no reason why -- found by
running the install guide's Step 5 literally against a fresh distribution.

Manifests are GENERATED, never hand-written: the numeric simulation parameters
are back-filled from artefacts that already exist (layout, mech params), so
nothing is transcribed by hand.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import job as J

BASE = J.BASE
SYS = os.path.join(BASE, "survey", "systems")

# simPrefix names EVERY Speos artefact a run produces, so it must be stable
# and unique. It is derived (slug[:4]) except where history fixed it
# otherwise -- those artefacts already exist under the older spelling and
# renaming them would orphan the results.
LEGACY_PREFIX = {
    "tessar25": "tess", "rearstop31": "rear", "petzval4": "petz",
    "wideangle32": "wa32", "cameralens14": "caml",
}


def prefix_for(slug, wd):
    """HISTORY WINS. A system that has already run owns its prefix.

    Every Speos artefact on disk is named with it, so re-deriving would orphan
    them. Only a system with no manifest yet gets a derived prefix -- which is
    also why the collision check below can be strict without breaking the
    existing corpus, where `wideangle32`/`wideanglelen100` and
    `sctolcooke12`/`sctole14` both collide at four characters and have run
    happily for weeks under explicitly-assigned prefixes.
    """
    mpath = J.path_for(slug, wd, "manifest")
    if os.path.exists(mpath):
        existing = (J.load(mpath) or {}).get("simPrefix")
        if existing:
            return existing
    prm = J.path_for(slug, wd, "params")
    if os.path.exists(prm):
        import json as _pj
        p = _pj.load(open(prm, encoding="utf-8-sig"))
        if p.get("simPrefix"):
            return p["simPrefix"]
    return LEGACY_PREFIX.get(slug, slug[:4])


def discover():
    if not os.path.isdir(SYS):
        return []
    out = []
    for slug in sorted(os.listdir(SYS)):
        lens = os.path.join(SYS, slug, slug + ".zmx")
        if os.path.isfile(lens):
            out.append((slug, lens, prefix_for(slug, os.path.dirname(lens))))
    return out


SYSTEMS = discover()

# plus anything staged by survey/stage-remaining.py (design-deduped)
_staged = os.path.join(BASE, "survey", "staged-remaining.json")
if os.path.exists(_staged):
    import json as _j
    _known = set(s[0] for s in SYSTEMS)
    for row in _j.load(open(_staged, encoding="utf-8-sig")):
        if row["slug"] not in _known:
            SYSTEMS.append((row["slug"], row["lens"],
                            prefix_for(row["slug"],
                                       os.path.dirname(row["lens"]))))

# A prefix collision would make two systems write over each other's Speos
# artefacts silently -- exactly the failure class this project keeps paying
# for -- so refuse rather than proceed. Only reachable via slug[:4], and only
# for names a user chose, which is precisely when nobody would suspect it.
_seen = {}
for _slug, _lens, _pre in SYSTEMS:
    if _pre in _seen:
        raise SystemExit(
            "FATAL: '%s' and '%s' both derive simPrefix '%s'. Every Speos\n"
            "artefact is named with it, so they would overwrite each other.\n"
            "Rename one of the survey/systems/ folders so the first four\n"
            "characters differ." % (_seen[_pre], _slug, _pre))
    _seen[_pre] = _slug

if not SYSTEMS:
    print("no systems found under %s\n" % SYS)
    print("Stage a design first -- the folder name is the job name:")
    print("    survey/systems/<name>/<name>.zmx")

made = []
for slug, lens, prefix in SYSTEMS:
    wd = os.path.dirname(lens)
    if not os.path.exists(lens):
        print("skip %s (lens missing)" % slug)
        continue
    mpath = J.path_for(slug, wd, "manifest")
    m = J.load(mpath) if os.path.exists(mpath) else J.default_manifest(slug, lens, wd)
    m["simPrefix"] = prefix

    # back-fill from artefacts that already exist
    lay = J.path_for(slug, wd, "layout")
    if os.path.exists(lay):
        d = J.load_raw(lay) if hasattr(J, "load_raw") else None
        import json
        d = json.load(open(lay, encoding="utf-8-sig"))
        m["optics"].update({"imgZ": d["imgZ"], "imgSD": d["imgSD"],
                            "maxField": d["maxField"],
                            "primaryWave": d.get("primaryWave")})
    prm = J.path_for(slug, wd, "params")
    if os.path.exists(prm):
        import json
        p = json.load(open(prm, encoding="utf-8-sig"))
        m["sim"].update({"strayDeg": p["strayDeg"], "zSrc": p["zSrc"],
                         "rSrc": p["rSrc"], "rDisc": p["rDisc"],
                         "zCatch": p["zCatch"], "waveNm": p["wave"]})
        m["optics"]["elements"] = p["elements"]

    J.save(m, mpath)
    made.append((slug, mpath))
    print("manifest: %-14s prefix=%-5s optics=%s" % (
        slug, prefix,
        "filled" if m["optics"]["imgZ"] else "PENDING (layout stage)"))

print("\n%d manifests written" % len(made))
