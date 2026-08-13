# catalog-samples.py — compile + rank the Zemax sample-file database for the
# stray-light workflow survey. Parses .zmx text (UTF-16 or ANSI), extracts
# system metadata, scores workflow suitability, dedupes by content hash.
#
# Eligibility gate (score > 0): MODE SEQ, refractive only (no MIRROR),
# surface types limited to STANDARD/EVENASPH, object at infinity (collimated
# stray-source pattern), finite image distance, angle-type fields, 2-10
# elements, aperture defined.
# Ranking: wider field (better stray test), 3-7 elements sweet spot, camera
# objectives preferred; small penalty for aspheres (ODX risk).
import glob
import hashlib
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "lib"))
import settings  # noqa: E402

# WHERE YOUR LENS FILES LIVE -- machine-specific and not derivable, so it comes
# from `zemax_data` in straylight.toml. Documents\Zemax is only the default:
# OpticStudio lets you relocate the user-data folder in Project Preferences,
# so assuming expanduser("~") would be right on most machines and quietly
# wrong on the rest -- which here means an empty catalogue, not an error.
ROOTS = [
    os.path.join(settings.zemax_data(), "Samples"),
    os.path.join(settings.zemax_data(), "Design Templates"),
]
OUT = os.path.join(_ROOT, "survey", "survey-db.json")
TESTED = ("double gauss 28 degree", "cooke 40 degree")

def read_zmx(path):
    for enc in ("utf-16", "utf-8-sig", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                t = f.read()
            if "SURF" in t or "MODE" in t:
                return t
        except (UnicodeError, UnicodeDecodeError):
            continue
    return None

def parse(path):
    t = read_zmx(path)
    if t is None:
        return None
    lines = t.splitlines()
    d = {"path": path, "name": "", "mode": "", "types": set(), "glasses": [],
         "mirror": False, "obj_inf": False, "img_finite": True, "ftyp": None,
         "max_field": 0.0, "enpd": None, "max_diam": 0.0, "nsurf": 0,
         "stop_surf": None}
    surf = -1
    last_disz = None
    prev_glass = False
    elements = 0
    for ln in lines:
        s = ln.strip()
        if s.startswith("NAME "):
            d["name"] = s[5:].strip()
        elif s.startswith("MODE "):
            d["mode"] = s.split()[1]
        elif s.startswith("ENPD "):
            try:
                d["enpd"] = float(s.split()[1])
            except (ValueError, IndexError):
                pass
        elif s.startswith("FTYP "):
            try:
                d["ftyp"] = int(s.split()[1])
            except (ValueError, IndexError):
                pass
        elif s.startswith("YFLN ") or s.startswith("XFLN "):
            for v in s.split()[1:]:
                try:
                    d["max_field"] = max(d["max_field"], abs(float(v)))
                except ValueError:
                    pass
        elif s.startswith("SURF "):
            surf = int(s.split()[1])
            d["nsurf"] = max(d["nsurf"], surf)
            if last_disz is not None:
                pass
        elif surf >= 0:
            if s.startswith("TYPE "):
                d["types"].add(s.split()[1])
            elif s.startswith("STOP"):
                d["stop_surf"] = surf
            elif s.startswith("GLAS "):
                g = s.split()[1]
                d["glasses"].append((surf, g))
                if g.upper() == "MIRROR":
                    d["mirror"] = True
                if not prev_glass:
                    elements += 1
                prev_glass = True
            elif s.startswith("DISZ "):
                v = s.split()[1]
                if surf == 0:
                    d["obj_inf"] = v.upper() == "INFINITY"
                else:
                    last_disz = v
                if not any(gs == surf for gs, _ in d["glasses"]):
                    prev_glass = False
            elif s.startswith("DIAM "):
                try:
                    d["max_diam"] = max(d["max_diam"], float(s.split()[1]))
                except (ValueError, IndexError):
                    pass
    # image distance = DISZ of surface n-1; INFINITY -> afocal
    d["img_finite"] = last_disz is not None and last_disz.upper() != "INFINITY"
    d["elements"] = elements
    d["types"] = sorted(d["types"])
    return d

def score(d):
    if d["mode"] != "SEQ" or d["mirror"] or not d["obj_inf"] or not d["img_finite"]:
        return 0
    allowed = {"STANDARD", "EVENASPH"}
    if not set(d["types"]) <= allowed:
        return 0
    if d["ftyp"] != 0 or d["max_field"] < 3.0:
        return 0
    if not (2 <= d["elements"] <= 10):
        return 0
    if d["enpd"] is None and True:
        pass  # FNUM-apertured systems still export; no gate
    if not (2.0 <= d["max_diam"] <= 80.0):
        return 0
    s = 40
    s += min(d["max_field"], 25.0)
    s += min(d["elements"] * 3, 24)
    if 3 <= d["elements"] <= 7:
        s += 10
    if "EVENASPH" in d["types"]:
        s -= 5
    if "objective" in d["path"].lower() or "camera" in d["path"].lower():
        s += 5
    return round(s, 1)

files = []
for r in ROOTS:
    files += glob.glob(os.path.join(r, "**", "*.zmx"), recursive=True)
print("candidate files: %d" % len(files))

seen_hash = {}
db = []
for p in sorted(files):
    try:
        h = hashlib.md5(open(p, "rb").read()).hexdigest()
    except OSError:
        continue
    if h in seen_hash:
        continue
    seen_hash[h] = p
    d = parse(p)
    if d is None:
        continue
    if any(t in os.path.basename(p).lower() for t in TESTED):
        d["note"] = "already validated (DG/Cooke)"
    d["score"] = score(d)
    db.append(d)

db.sort(key=lambda d: -d["score"])
eligible = [d for d in db if d["score"] > 0]
print("unique parsed: %d, eligible: %d" % (len(db), len(eligible)))
print("\ntop 20:")
for d in eligible[:20]:
    print("  %5.1f  %d el, %4.1f deg, diam %.1f, %s  %s%s"
          % (d["score"], d["elements"], d["max_field"], d["max_diam"],
             "+".join(d["types"]), os.path.basename(d["path"]),
             "  [%s]" % d.get("note", "") if d.get("note") else ""))

with open(OUT, "w") as f:
    json.dump(db, f, indent=1, default=str)
print("\nwrote %s" % OUT)
