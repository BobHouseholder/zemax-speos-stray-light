"""Replay the envelope check offline: for one system, test every traced ray
polyline against the recorded seated-barrel section table, and report the
minimum clearance per field.

Purpose (2026-08-05): wideanglelen100's seated barrel took in-field SV_F1v
0.2268 -> 0.0123 while the checker reported worstFail 0.0. Two candidate
stories: (a) the checker missed a real obstruction of the imaging beam;
(b) the barrel never obstructed the imaging beam, and the SV collapse is an
INFLATED-DENOMINATOR artefact -- non-imaging light reaching the detector in
the optics-only run -- which the coupling/RELI gate cannot catch at FIELD 1,
because field 1 IS the gate's own reference. This discriminates them.

Usage: python replay-envelope.py <slug>
"""
import json
import os
import sys

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "systems")
slug = sys.argv[1] if len(sys.argv) > 1 else "wideanglelen100"
d = os.path.join(BASE, slug)

lay = json.load(open(os.path.join(d, "%s-layout.json" % slug), encoding="utf-8-sig"))
prm = json.load(open(os.path.join(d, "%s-params.json" % slug), encoding="utf-8-sig"))

sections = prm.get("sections") or prm.get("sectionTable") or []
if not sections:
    raise SystemExit("no section table recorded in %s-params.json (keys: %s)"
                     % (slug, sorted(prm.keys())))

rays = [r for r in (lay.get("rays") or []) if r.get("pts")]
print("%s: %d traced rays, %d sections, recorded worstFail=%s"
      % (slug, len(rays), len(sections), prm.get("worstFail")))
dropped = len(lay.get("rays") or []) - len(rays)
if dropped:
    print("  NOTE: %d rays in the layout did NOT trace (excluded, as the "
          "generator excludes them)" % dropped)

def ray_r_at(pts, z):
    """Radial height of a polyline at axial position z (linear interp);
    None outside the polyline's z range (entry extrapolation NOT applied --
    matching what a non-extrapolated check would see; the generator
    extrapolates entry segments, noted separately below)."""
    for (z1, r1), (z2, r2) in zip(pts, pts[1:]):
        if (z1 - z) * (z2 - z) <= 0 and abs(z2 - z1) > 1e-12:
            t = (z - z1) / (z2 - z1)
            return r1 + t * (r2 - r1)
    return None

# normalise ray points to (z, |r|) pairs
def pts_of(ray):
    out = []
    for p in ray["pts"]:
        if isinstance(p, dict):
            z, y = p.get("z"), p.get("y", p.get("r", 0.0))
        else:
            z, y = p[0], p[1]
        out.append((float(z), abs(float(y))))
    return out

by_field = {}
for ray in rays:
    f = ray.get("field", ray.get("f", "?"))
    by_field.setdefault(f, []).append(pts_of(ray))

NPROBE = 40
print("\nper-field minimum clearance to any section (negative = ray INSIDE the metal):")
overall = {}
for f in sorted(by_field, key=str):
    worst = None
    for pts in by_field[f]:
        for (za, zb, r, kind) in [tuple(s) for s in sections]:
            for i in range(NPROBE + 1):
                z = za + (zb - za) * i / NPROBE
                w = ray_r_at(pts, z)
                if w is None:
                    continue
                c = r - w
                if worst is None or c < worst[0]:
                    worst = (c, kind, z, w, r)
    overall[f] = worst
    if worst:
        c, kind, z, w, r = worst
        verdict = "CLEARS" if c > 0 else "OBSTRUCTED"
        print("  field %-3s: min clearance %+8.4f mm  (%s at z=%.2f, ray r=%.3f vs bore r=%.3f)  -> %s"
              % (f, c, kind, z, w, r, verdict))
    else:
        print("  field %-3s: no overlap between rays and sections AT ALL "
              "(rays outside every section's z-span)" % f)

print("\nfields present in the traced envelope: %s" % sorted(by_field, key=str))
print("rays per field: %s" % {f: len(v) for f, v in by_field.items()})
