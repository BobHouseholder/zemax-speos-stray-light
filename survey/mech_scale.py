"""mech_scale.py -- THE scale-sanity rule for the mechanical generator.

One definition, imported by make-survey-mech.py (producer: writes
params["mechWarnings"]) AND testcases/audit-build.py (checker: compares the
rule to truth.expectMechWarning) -- never restate it (the min(3,n) lesson).

The generator's assembly constants are fixed in MM (assembly gap 0.05, seat
radial clearance 0.010-0.025, knife ring 0.8, envelope margin 0.5, Speos mesh
sag 0.005). They are sane for elements roughly 1-40 mm in semi-diameter and
silently change meaning outside that band. Thresholds set 2026-08-05 from the
measured corpus distribution (117 layouts): the small side separates C22
(0.19) and the miniature retrofoc family (0.44-0.47) from C23 (1.90); the
large side sits below C24 (47.5) and above the regular corpus mid-range.
NOTE: C26 (x0.1 DG, elements 2.88 mm; gap = 1.7% of the element) measured
BUILDABLE -- the old factor-based expectation for it was a guess and is
corrected to no-warning.

All corpus files are UNIT MM (verified 2026-08-05 -- including the Inverse
telephoto, a genuinely MINIATURE mm-lens once misread as an inch file).
unit_to_mm exists so a future non-mm customer file stays correct once
trace-layout writes `unitToMm`; absent means 1.0.
"""

SMALL_MM = 1.0    # assembly gap 0.05 >= 5% of the largest element
LARGE_MM = 40.0   # seat clearance ceiling 0.025 < 0.06% of the element


def scale_stat_mm(layout):
    """max glass semi-diameter in mm, or None if no glass surface has an sd."""
    u = float(layout.get("unitToMm") or 1.0)
    sds = [s["sd"] for s in (layout.get("surfaces") or [])
           if s.get("glass") and s.get("sd")]
    return (max(sds) * u) if sds else None


def scale_warnings(layout):
    """List of warning strings; empty when the scale is inside the sane band."""
    s = scale_stat_mm(layout)
    if s is None:
        return ["no glass semi-diameters in layout - scale sanity unverifiable"]
    if s < SMALL_MM:
        return ["largest element semi-diameter %.3f mm: the fixed assembly "
                "constants (0.05 mm gap, 10-25 um seats) are no longer small "
                "against the elements - the generated mechanics are not "
                "buildable at this scale" % s]
    if s > LARGE_MM:
        return ["largest element semi-diameter %.1f mm: the fixed assembly "
                "constants (25 um seat clearance, 5 um mesh sag) are "
                "unrealistically tight/fine at this scale - clearances need "
                "re-deriving before trusting the mechanics" % s]
    return []
