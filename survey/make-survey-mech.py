# make-survey-mech.py — generic mechanicals for a survey system.
# Usage: python make-survey-mech.py <layout.json> <outdir> <slug>
# Emits <slug>-baseline.step (naive tubes: the "before"), <slug>-seated.step
# (prescription-driven seated barrel + housing + envelope-sized housing vane:
# the "after"), and <slug>-params.json (Speos run parameters).
#
# Generic seating rules (from the DG/Cooke validated pattern):
#   elements = runs of glass surfaces; cells split at the stop z (front cell
#   front-loads, rear cell rear-loads); each element gets a bore over its rim
#   span (+15 um radial), a knife-seat ring (0.8 mm) on its cell side, and
#   envelope-following reliefs that also guarantee the load path; entry/exit
#   reliefs sized from the ENTRY-EXTRAPOLATED beam envelope. Stop zone bore =
#   max(envelope+0.5, 2x stop sd). All gaps: 0.02 glass / 0.05 assembly.
import json
import math
import os
import sys

import cadquery as cq

LAYOUT, OUTDIR, SLUG = sys.argv[1], sys.argv[2], sys.argv[3]
CLEAR = 0.015
GAP = 0.02
RING_L = 0.8
ENTRY_Z = -4.0

txt = open(LAYOUT, encoding="utf-8-sig").read().replace("∞", "0")
raw = json.loads(txt)
SURF = raw["surfaces"]
IMGZ, IMGSD = raw["imgZ"], raw["imgSD"]
MAXFIELD = raw["maxField"]
STOP_SD = raw.get("stopSD", 0.0)
STOP_Z = None
if raw.get("stopSurf", -1) > 0:
    STOP_Z = SURF[raw["stopSurf"] - 1]["z"]

# A ray that never traced comes back with an EMPTY pts list. The layout only
# raises if EVERY ray fails, so partial vignetting reaches here intact, and
# env_at used to index pts[0] unconditionally -> IndexError, killing the stage
# with a traceback that named neither the ray nor the reason. Hit B17, B25,
# B32 and C30 on 2026-07-26 (5, 12, 2 and N of 105 rays empty) and became
# possible only when the fan was densified 15 -> 105 rays: the added extreme
# corners (field 1.0 x pupil +-1.0) are exactly the rays that vignette.
#
# Dropping them is correct: a ray that does not make it through the OPTICS is
# not part of the imaging beam, so it does not constrain the bore. But it must
# never be dropped SILENTLY -- the barrel is sized from whatever survives, and
# a bore fitted to a badly depleted sample is exactly how you get a barrel that
# clears every ray you kept and obstructs the beam you did not.
_all = raw.get("rays") or []
RAYS = [r for r in _all if r.get("pts")]
_dropped = len(_all) - len(RAYS)
if _dropped:
    print("%s: WARNING %d/%d rays did not trace and are excluded from the beam "
          "envelope" % (SLUG, _dropped, len(_all)))
if not RAYS:
    raise SystemExit("GUARD FAILED [mech] no ray in %s traced - there is no "
                     "beam envelope to fit a bore to" % LAYOUT)
if len(RAYS) < 0.5 * len(_all):
    raise SystemExit("GUARD FAILED [mech] only %d of %d rays traced (<50%%) - "
                     "the envelope would be fitted to a biased sample and the "
                     "bore could obstruct the beam that was dropped"
                     % (len(RAYS), len(_all)))

def sag(R, r):
    if not R:
        return 0.0
    c = 1.0 / R
    a = 1.0 - c * c * r * r
    if a <= 0:
        return 1.0 / c  # hemisphere edge; clamp
    return c * r * r / (1.0 + math.sqrt(a))

# elements from glass runs
elements = []
i = 0
while i < len(SURF) - 1:
    if SURF[i]["glass"] and SURF[i]["glass"].upper() != "MIRROR":
        j = i
        while j < len(SURF) - 1 and SURF[j]["glass"]:
            j += 1
        idxs = list(range(i, j + 1))
        edges = [SURF[k]["z"] + sag(SURF[k]["R"], SURF[k]["sd"]) for k in idxs]
        elements.append({
            "idxs": idxs, "r": max(SURF[k]["sd"] for k in idxs),
            "front": min(edges), "rear": max(edges),
            "zmid": 0.5 * (min(edges) + max(edges)),
        })
        i = j + 1
    else:
        i += 1
print("%s: %d elements" % (SLUG, len(elements)))

def env_at(zq):
    worst = 0.0
    for ray in RAYS:
        th = math.tan(ray["hy"] * MAXFIELD * math.pi / 180.0)
        p0 = ray["pts"][0]
        pts = [[ENTRY_Z - 1.0, p0[1] - th * (p0[0] - (ENTRY_Z - 1.0))]] + ray["pts"]
        for k in range(len(pts) - 1):
            (z1, y1), (z2, y2) = pts[k], pts[k + 1]
            if (z1 - zq) * (z2 - zq) <= 0 and z1 != z2:
                worst = max(worst, abs(y1 + (y2 - y1) * (zq - z1) / (z2 - z1)))
    return worst

def env_span(za, zb):
    return max(env_at(za + (zb - za) * k / 8.0) for k in range(9))

# cells: split at stop z (elements strictly behind the stop rear-load)
front_cell = [e for e in elements if STOP_Z is None or e["zmid"] < STOP_Z]
rear_cell = [e for e in elements if e not in front_cell]
print("cells: front %d, rear %d (stop z=%s)" % (len(front_cell), len(rear_cell), STOP_Z))

sections = []   # (za, zb, r, kind)
rings = set()

def add(za, zb, r, kind):
    if zb - za > 1e-6:
        sections.append((za, zb, round(r, 3), kind))

lens_end = max(e["rear"] for e in elements)
# entry relief: pass every front-cell bore + entering beam
front_bores = [e["r"] + CLEAR for e in front_cell]
entry_r = max([env_span(ENTRY_Z, front_cell[0]["front"]) + 0.3] + [b + 0.05 for b in front_bores]) if front_cell else env_span(ENTRY_Z, 0) + 0.3
cursor = ENTRY_Z
if front_cell:
    add(cursor, front_cell[0]["front"] - GAP, entry_r, "entry")
    cursor = front_cell[0]["front"] - GAP
    for n, e in enumerate(front_cell):
        # bore must clear the BEAM even if that exceeds the tolerance fit
        # (elements whose clear aperture == mechanical rim)
        bore = max(e["r"] + CLEAR, env_span(cursor, e["rear"] + GAP) + 0.02)
        add(cursor, e["rear"] + GAP, bore, "bore")
        # seat ring only if the airgap behind the element has room for it
        nxt_limit = (front_cell[n + 1]["front"] - GAP if n + 1 < len(front_cell)
                     else (STOP_Z if STOP_Z else e["rear"] + 5.0))
        if nxt_limit - (e["rear"] + GAP) >= RING_L + 0.2:
            ring_id = max(env_span(e["rear"] + GAP, e["rear"] + GAP + RING_L) + 0.1, e["r"] - 0.5)
            ring_id = min(ring_id, e["r"] - 0.05)
            add(e["rear"] + GAP, e["rear"] + GAP + RING_L, ring_id, "ring")
            rings.add(round(ring_id, 3))
            cursor = e["rear"] + GAP + RING_L
        else:
            cursor = e["rear"] + GAP
        if n + 1 < len(front_cell):
            nxt = front_cell[n + 1]
            relief = max(env_span(cursor, nxt["front"] - GAP) + 0.4,
                         max(x["r"] + CLEAR + 0.05 for x in front_cell[n + 1:]))
            add(cursor, nxt["front"] - GAP, relief, "relief")
            cursor = nxt["front"] - GAP
# stop / mid zone
if rear_cell:
    mid_end = rear_cell[0]["front"] - GAP - RING_L
    if mid_end > cursor:
        mid_r = max(env_span(cursor, mid_end) + 0.5, 2.0 * STOP_SD)
        add(cursor, mid_end, mid_r, "mid")
        cursor = mid_end
    # rear cell processed front-to-back; rings sit AHEAD of each element
    for n, e in enumerate(rear_cell):
        ring_start = e["front"] - GAP - RING_L
        if ring_start >= cursor - 1e-6:
            if ring_start > cursor + 1e-6:
                add(cursor, ring_start,
                    max(env_span(cursor, ring_start) + 0.4,
                        max(x["r"] + CLEAR + 0.05 for x in rear_cell[n:])), "relief")
            ring_id = max(env_span(ring_start, e["front"] - GAP) + 0.1, e["r"] - 0.5)
            ring_id = min(ring_id, e["r"] - 0.05)
            add(ring_start, e["front"] - GAP, ring_id, "ring")
            rings.add(round(ring_id, 3))
            cursor = e["front"] - GAP
        bore = max(e["r"] + CLEAR, env_span(cursor, e["rear"] + GAP) + 0.02)
        add(cursor, e["rear"] + GAP, bore, "bore")
        cursor = e["rear"] + GAP
barrel_end = lens_end + 1.5
exit_r = max(env_span(cursor, barrel_end) + 0.3,
             max((e["r"] + CLEAR + 0.05 for e in rear_cell), default=0))
add(cursor, barrel_end, exit_r, "exit")

# A seat ring is a KNIFE EDGE: it may clip the beam by a hair. Exempting
# "ring" sections from the FAIL check WITHOUT BOUNDING the intrusion let
# schamm110 through with 13.5 mm and 11.7 mm intrusions into a ~27 mm beam --
# plugs, not knife edges. Its in-field flux went to EXACTLY ZERO while the
# stray metric read a triumphant -91%. Bound it: at most 0.5 mm AND 5% of the
# local beam radius, otherwise it is a FAIL like any other section.
RING_MAX_ABS = 0.5
RING_MAX_FRAC = 0.05

# ---- CLEARANCE MARGIN on envelope-derived sections -------------------------
#
# "bore radius >= sampled beam radius" is not a sufficient test. The envelope
# comes from 15 meridional rays (3 fields x 5 pupil points); the real beam is a
# continuum, so the sampled maximum is a LOWER BOUND on the true one.
#
# Measured on B01 (a Double Gauss run at 26 deg): every section reported PASS,
# the tightest corner ray cleared its bore by 0.049 mm, and the seated barrel
# still cost 66% of the corner illumination. At a heavily vignetted field the
# surviving bundle is a thin crescent hugging the aperture edge, so shaving
# even a few tenths off its outer radius removes most of it.
#
# Sections that exist to SEAT an element ("bore") or to form a knife edge
# ("ring") are set by the element rim and must not be grown -- widening them
# breaks the seat. Sections whose radius is purely envelope-derived (entry,
# relief, mid, exit) get a real margin.
CLEAR_ABS = 0.5
CLEAR_FRAC = 0.02
GROWABLE = ("entry", "relief", "mid", "exit")
grown = []
for i, (za, zb, r, kind) in enumerate(sections):
    if kind not in GROWABLE:
        continue
    w = env_mech_span(za, zb) if "env_mech_span" in dir() else env_span(za, zb)
    need = w + max(CLEAR_ABS, CLEAR_FRAC * w)
    if r < need:
        sections[i] = (za, zb, need, kind)
        grown.append((kind, za, r, need))
if grown:
    print("clearance margin applied (envelope-derived sections):")
    for kind, za, old, new in grown:
        print("  %-7s at z%8.2f : r %.3f -> %.3f (+%.3f)" % (kind, za, old, new, new - old))

print("section table + envelope check:")
worst_fail = 0.0
tight = []
for za, zb, r, kind in sections:
    w = env_span(za, zb)
    if kind == "ring" and w >= r:
        intr = w - r
        if intr <= min(RING_MAX_ABS, RING_MAX_FRAC * max(w, 1e-9)):
            tag = "RING (intrusion %.2f, within knife-edge limit)" % intr
        else:
            tag = ("RING BLOCKS BEAM by %.2f mm (%.0f%% of beam radius)"
                   % (intr, 100.0 * intr / max(w, 1e-9)))
            worst_fail = max(worst_fail, intr)
    elif w < r:
        # PASS, but say so honestly: a seating bore that clears the SAMPLED
        # envelope by less than the margin is clipping the real beam, and
        # cannot be widened without breaking the element seat. That is a
        # design constraint to report, not a silent pass.
        margin = r - w
        need = max(CLEAR_ABS, CLEAR_FRAC * max(w, 1e-9))
        if margin < need:
            tag = "PASS but TIGHT (%.3f mm clearance, want %.2f)" % (margin, need)
            tight.append((za, zb, kind, margin, need))
        else:
            tag = "PASS"
    else:
        tag = "FAIL by %.2f" % (w - r)
        worst_fail = max(worst_fail, w - r)
    print("  %7.2f..%7.2f r%7.3f %-7s beam %6.2f  %s" % (za, zb, r, kind, w, tag))

barrel_od = max(r for _, _, r, _ in sections) + 2.0
housing_id = max(IMGSD + 0.8, exit_r + 0.3)
housing_od = housing_id + 2.0


# ---- REAR CONE: what the rear mechanics must clear to serve the DETECTOR ----
#
# The bores and the vane were sized from the TRACED RAY ENVELOPE, while the
# Speos detector is sized from IMGSD (OpticStudio's image semi-diameter). Those
# are two different rules and nothing compared them. On B01 the 15-ray fan only
# reached r=40.8 at the image while IMGSD was 43.96, so the vane was cut to
# r=42.07 -- INSIDE the detector it is supposed to serve. Result: 20 deg stray
# fell 72.5% and the corner lost 66.5% of its illumination, with the envelope
# check reporting worstFail = 0.0.
#
# Any mechanical radius below the straight line from the exit-aperture rim
# (barrel_end, exit_r) to the detector corner (IMGZ, IMGSD) blocks a ray that
# the detector is entitled to receive. That line is the floor.
def rear_floor(z):
    if z <= barrel_end or IMGZ <= barrel_end:
        return 0.0
    f = (z - barrel_end) / (IMGZ - barrel_end)
    return exit_r + (IMGSD - exit_r) * f


def env_mech(z):
    """Envelope for MECHANICAL sizing: sampled rays, floored by the rear cone."""
    return max(env_at(z), rear_floor(z))


def env_mech_span(za, zb):
    return max(env_mech(za + (zb - za) * k / 8.0) for k in range(9))


# Does the traced fan actually span the detector? If not, every rear dimension
# derived from it is an under-estimate.
img_ray_max = env_at(IMGZ)
if IMGSD > img_ray_max + 0.25:
    print("  WARNING: traced fan reaches r=%.2f at the image but IMGSD=%.2f "
          "(%.2f mm short) -- rear mechanics floored by the detector cone"
          % (img_ray_max, IMGSD, IMGSD - img_ray_max))

vane_z = barrel_end + 0.65 * (IMGZ - barrel_end)
vane_id = env_mech_span(vane_z, vane_z + 1.0) + 1.2
vane_ok = (housing_id - 0.05) - vane_id >= 1.0

# The vane is a real aperture in the imaging path: check it like any bore.
vane_need = rear_floor(vane_z + 0.5)
if vane_ok and vane_id < vane_need:
    print("  VANE BLOCKS BEAM: inner r=%.2f but the detector cone needs %.2f "
          "at z=%.2f (short by %.2f)" % (vane_id, vane_need, vane_z,
                                         vane_need - vane_id))
    worst_fail = max(worst_fail, vane_need - vane_id)

# The housing bore spans barrel exit -> image and was never checked at all.
hou_need = max(rear_floor(barrel_end + (IMGZ - barrel_end) * k / 8.0)
               for k in range(9))
if housing_id < hou_need:
    print("  HOUSING BLOCKS BEAM: id=%.2f but the detector cone needs %.2f "
          "(short by %.2f)" % (housing_id, hou_need, hou_need - housing_id))
    worst_fail = max(worst_fail, hou_need - housing_id)

def build_barrel(secs, od):
    b = cq.Workplane("XY", origin=(0, 0, ENTRY_Z)).circle(od).extrude(barrel_end - ENTRY_Z)
    for za, zb, r, _ in secs:
        b = b.cut(cq.Workplane("XY", origin=(0, 0, za - 0.01)).circle(r).extrude(zb - za + 0.02))
    return b

def build_housing():
    return (cq.Workplane("XY", origin=(0, 0, barrel_end + 0.05))
            .circle(housing_od).circle(housing_id).extrude(IMGZ - barrel_end - 0.05))

# seated assembly
asm = cq.Assembly()
asm.add(build_barrel(sections, barrel_od), name="Barrel")
asm.add(build_housing(), name="Housing")
if vane_ok:
    asm.add(cq.Workplane("XY", origin=(0, 0, vane_z))
            .circle(housing_id - 0.05).circle(vane_id).extrude(1.0), name="HousingVane")
seated_step = os.path.join(OUTDIR, SLUG + "-seated.step")
asm.save(seated_step)

# baseline: naive open tubes (the "before")
base_id = max(e["r"] for e in elements) + 1.5
basm = cq.Assembly()
basm.add(cq.Workplane("XY", origin=(0, 0, ENTRY_Z))
         .circle(base_id + 2.0).circle(base_id).extrude(barrel_end - ENTRY_Z), name="Barrel")
basm.add(build_housing(), name="Housing")
base_step = os.path.join(OUTDIR, SLUG + "-baseline.step")
basm.save(base_step)

# STRAY SOURCE ANGLE. The whole point is a source OUTSIDE the design field, so
# the angle must track MAXFIELD - it used to be min(MAXFIELD + 6, 40), and that
# 40 degree cap silently put the "stray" source INSIDE the field of any system
# wider than 34 degrees. On a 100 degree fisheye it was measuring imaging light
# and calling it stray.
#
# Uncapped it now, but past roughly 85 degrees the test stops being physical
# rather than merely awkward: the source disc sits at z=-40 in front of the
# entrance, so at 90 degrees it is edge-on to the entrance plane and beyond that
# it is BEHIND it, where no front-facing barrel can baffle it. For those systems
# there is no out-of-field stray measurement to make, and saying so is better
# than reporting whatever number a degenerate geometry produces.
#
# Scope check before changing this: exactly 3 of 100 cases had the cap bind
# (C10, C12 at 100 deg, B03 at 37 deg) and NONE of them has a published stray
# number - C10/C12 are already stray-undefined and B03 failed at mech. So no
# reported result changes; this only stops future wide systems being scored on
# an in-field source.
#
# ---------------------------------------------------------------------------
# THE ANGLE IS NOW MEASURED, NOT GUESSED (2026-07-31).
#
# `MAXFIELD + 6` was only ever a placeholder: it assumes the worst angle sits
# just outside the field, which is not generally true. The optics-only backward
# run (survey/wire-back-optics.py -> lib/angle_select.py) measures where the
# detector can actually see out to, by reciprocity, and writes its choice to
# <slug>-strayangle.json. Validated on the Double Gauss against a measured
# forward PST curve: 19 deg selected vs 20 deg measured.
#
# The heuristic remains as the FALLBACK, and the source of the number is
# recorded in params.json so no result is ever ambiguous about which was used.
# ---------------------------------------------------------------------------
_stray_fallback = round(MAXFIELD + 6.0, 1)
_stray_deg = _stray_fallback
_stray_source = "heuristic:maxField+6"
_stray_candidates = None
print("  stray angle provisionally %.1f deg (heuristic). The back_trace stage "
      "runs next and overwrites this with the MEASURED angle." % _stray_deg)

_stray_defined = _stray_deg <= 85.0
if not _stray_defined:
    print("  STRAY TEST UNDEFINED: field %.1f deg needs a source at %.1f deg, "
          "which is at or behind the entrance plane - no front-facing barrel "
          "can baffle that, so there is no out-of-field stray to measure"
          % (MAXFIELD, _stray_deg))

params = {
    "slug": SLUG, "zImg": IMGZ, "rDisc": IMGSD + 0.5, "zCatch": ENTRY_Z - 1.0,
    "strayDeg": _stray_deg, "strayDefined": _stray_defined, "zSrc": -40.0,
    # provenance of the angle: a result must never be ambiguous about whether
    # its stray angle was measured or guessed.
    "strayDegSource": _stray_source,
    "strayDegFallback": _stray_fallback,
    "strayDegCandidates": _stray_candidates,
    "rSrc": round(min(1.3 * entry_r, 60.0), 1), "wave": 550.0,
    "maxField": MAXFIELD, "elements": len(elements),
    "vane": {"z": round(vane_z, 2), "id": round(vane_id, 2)} if vane_ok else None,
    "worstFail": worst_fail,
    # sections that pass only by a hair. These cannot be widened (they seat an
    # element) but they ARE clipping the real beam, so downstream in-field
    # comparisons on this system are suspect. Surfacing them is the difference
    # between "worstFail 0.0, all good" and the truth.
    "tightSections": [{"z0": round(a, 3), "z1": round(b, 3), "kind": k,
                       "clearance": round(m, 4), "wanted": round(n, 3)}
                      for a, b, k, m, n in tight],
    # scale sanity: the shared rule in mech_scale.py (also evaluated by
    # audit-build against truth.expectMechWarning). Producer added 2026-08-05;
    # the expectation had existed unproduced AND unchecked since the suite
    # was built.
    "unitToMm": float(raw.get("unitToMm") or 1.0),
    "mechWarnings": __import__("mech_scale").scale_warnings(raw),
}

# ---- BASELINE OVERSIZE: forecast the benefit BEFORE paying for the sims ----
# Isolated 2026-07-26 over 63 systems. The seated barrel's stray-light benefit
# is driven by how much of the naive tube's cross-section stands EMPTY, not by
# the base design and not by anything absolute:
#
#   spearman(oversize, stray reduction) = -0.777      n=63
#   within a single design (the confound-free test): dg -0.672 (n=23),
#     cooke -0.690 (n=13), zebase -0.790, tessar -0.584
#   once oversize is regressed out, the residual correlates with field angle at
#     -0.075 and f/number at -0.096, i.e. those rivals were oversize leaking
#   ABSOLUTE measures do not predict it at all: wall area +0.195, empty volume
#     +0.088, base_id +0.076. The effect is SCALE-INVARIANT - a 5 mm Cooke and
#     a 180 mm tessar at the same oversize get the same benefit.
#
# The Double Gauss's poor showing (median -17.3%) is positional, not intrinsic:
# most DG cases sit at that design's natural oversize ~0.49 where the naive
# tube already hugs the beam. The DG case whose injection inflated the tube
# (B15, oversize 0.835) reached -98.9%, matching any Cooke.
#
# Fit: stray% = 76.32 - 233.22 * oversize, R^2 = 0.558, residual sd 22.9 pp.
# R^2 = 0.558 means 44% of the variance is NOT explained - this is a screening
# forecast, never a substitute for the run. It is clamped because the line goes
# unphysical (-110%) past oversize ~0.75, and refuses to extrapolate outside
# the 0.377..0.835 range it was fitted on.
# DEFINITION MATTERS, and the obvious definition is not the best one. Measured
# both ways over the same 63 systems:
#   * over the OPTICS SPAN (entry -> last surface), reference radius
#     max(semi-diameter)+1.5 : spearman -0.777, R^2 0.558
#   * over the AS-BUILT barrel (entry -> barrel_end), reference radius
#     max(element rim)+1.5   : spearman -0.569, R^2 0.187
# The as-built version is the real tube, yet it predicts far worse, because it
# averages in the rear extent where there is no beam left to compare against
# and every system looks empty. The stray-relevant region is where the beam
# actually is. So this is an explicit SCREENING STATISTIC over the optics span,
# not a description of the manufactured part -- do not "correct" it to use
# barrel_end without refitting the constants below.
_ref_r = max([s["sd"] for s in SURF if s.get("sd")] or [base_id]) + 1.5
_z_last = max(s["z"] for s in SURF)
_fills = []
for _i in range(41):
    _zq = ENTRY_Z + (_z_last - ENTRY_Z) * _i / 40.0
    _rb = env_at(_zq)
    if _rb > 0:
        _fills.append(min(1.0, (_rb / _ref_r) ** 2))
if len(_fills) >= 10:
    _ov = 1.0 - sum(_fills) / len(_fills)
    params["oversize"] = round(_ov, 4)
    # the fit spans 0.377..0.835; widen by a hair so a system sitting exactly
    # on the boundary is not excluded by floating-point noise (B15 is 0.835)
    # ---- THE NUMERIC FORECAST IS SUSPENDED (2026-08-02) --------------------
    # The fit `76.32 - 233.22 * oversize` was regressed against 63 reductions
    # that were ALL measured at `strayDeg = maxField + 6` -- an angle that is
    # off-peak on most systems. Re-measured at the correct angle on 20 of those
    # systems the fit becomes `95.52 - 251.74 * oversize`: same direction, but
    # a 32% steeper slope and a very different intercept.
    #
    # The RELATIONSHIP survives correction (spearman -0.609 corrected vs -0.569
    # published on the identical 20 systems), so oversize is still the right
    # screening variable and the regime warning below still stands. The
    # COEFFICIENTS do not, and emitting a number from them would put a stale
    # figure into params.json and into every report downstream.
    #
    # Re-derive from the full corrected set once the re-measurement finishes;
    # 20 systems chosen for large angle gaps is a biased sample and a worse
    # basis than the 63-system fit it would replace.
    # RE-DERIVED 2026-08-03 on 60 systems re-measured at their MEASURED stray
    # angles (was: 63 systems at `maxField + 6`). The relationship barely moved
    # -- spearman -0.755 corrected vs -0.774 published on the same 60 -- and
    # the fit is TIGHTER (R2 0.599 vs 0.545). The coefficients moved a lot:
    #     old  stray% =  76.32 - 233.22 * oversize   R2 0.545  sd 22.9 pp
    #     new  stray% = 128.81 - 299.44 * oversize   R2 0.599  sd 25.7 pp
    # NOTE the forecast may now be POSITIVE at low oversize, and that is a real
    # prediction, not a clamp artefact: six systems (all window-near-sensor at
    # oversize 0.493) measured a stray INCREASE from the seated barrel.
    params["forecastSuspended"] = None
    if 0.37 <= _ov <= 0.84:
        _fc = max(-99.0, 128.81 - 299.44 * _ov)
        params["forecastStrayPct"] = round(_fc, 1)
        params["forecastSdPct"] = 25.7
        print("  baseline oversize %.3f -> forecast stray %+.0f%% +/- 26 "
              "(R^2=0.60, n=60, screening only)" % (_ov, _fc))
        # The regime call is ordinal, not numeric, so it survives the
        # correction and is still worth making.
        if _ov < 0.55:
            print("  REGIME: the naive tube already hugs the beam here. A "
                  "seated barrel has little wall to reclaim, and near the "
                  "field edge it can be NET NEGATIVE -- A01/A11 (oversize "
                  "0.493) measured +10% MORE stray light than the naive tube, "
                  "from grazing-incidence scatter off the close-fitting bores.")
        elif _ov > 0.70:
            print("  REGIME: substantial empty wall -- large benefit likely.")
    else:
        print("  baseline oversize %.3f is outside the fitted range "
              "0.377..0.835" % _ov)

with open(os.path.join(OUTDIR, SLUG + "-params.json"), "w") as f:
    json.dump(params, f, indent=1)
print("wrote %s, %s, params (stray %.1f deg, rSrc %.1f, vane %s)"
      % (os.path.basename(seated_step), os.path.basename(base_step),
         params["strayDeg"], params["rSrc"], params["vane"]))
if worst_fail > 0:
    print("WARNING: envelope FAIL present (%.2f) — review before running" % worst_fail)
