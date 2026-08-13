# angle_select.py -- choose the stray-light angle from a BACKWARD run.
#
# Replaces the heuristic `strayDeg = maxField + 6`, which is a guess and which
# put the "stray" source inside the design field on systems wider than 34 deg.
#
# Method: optical reciprocity. The backward run makes the detector emit; the
# directions in which rays escape the front are the directions from which an
# external source can reach the detector. The out-of-field peak of that
# distribution is the angle worth simulating forward.
#
# Validated on the Double Gauss against a MEASURED forward PST curve:
#   inverse peak 19 deg vs measured 20 deg (|error| 1.0 deg).
#
# Runs under the ORDINARY driver interpreter as of 2026-08-09. It used to
# require Ansys's bundled CPython 3.10, because the Illumine SWIG bindings load
# nowhere else; ray reading now goes through lib/lpf_read.py (PySpeos `lxp`),
# which is ordinary Python. The two backends were verified to agree on 30 files
# / 1.39 M rays -- every histogram bin identical -- before the swap.
#
# Consumers (make-survey-mech.py, under a different interpreter) read the JSON
# this writes; they never import this module.
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lpf_read  # noqa: E402

BIN_DEG = 2.0
MIN_ESCAPING = 2000        # below this the histogram is too sparse to trust
# A bin more than this many times its outward neighbour is on the decaying
# shoulder of the imaging cone, not on the stray distribution. Measured
# separation is wide: petzval4 51x and C25 81x on the contaminated side,
# against 0.99-1.03x for every wide-field system, so the threshold is not
# delicately placed.
SHOULDER = 5.0


def escape_angles(lpf):
    """Angle from the optical axis of each escaping ray.

    Uses the ray's NATIVE escape direction. Do not derive this from impacts:
    the final impact is the last GEOMETRY interaction and the sensor is not
    geometry, so the last impact-to-impact vector is the segment arriving at
    that surface -- inside the glass for a refracting exit.

    The classification below is unchanged from the Illumine implementation it
    replaced, deliberately and to the character: same order of tests, same
    treatment of a zero direction as BAD rather than as an angle of zero, same
    atan2 form. Only where the directions come from changed (see lpf_read),
    and the two backends were checked to agree on 30 files before the swap.
    """
    dirs, n = lpf_read.last_directions(lpf)
    angles, bad, inbound = [], 0, 0
    for d in dirs:
        if d is None or len(d) < 3:
            bad += 1
            continue
        dx, dy, dz = d[0], d[1], d[2]
        if not all(map(math.isfinite, (dx, dy, dz))):
            bad += 1
            continue
        if dx == 0.0 and dy == 0.0 and dz == 0.0:
            bad += 1
            continue
        if dz >= 0.0:                       # not heading out of the front
            inbound += 1
            continue
        r = math.sqrt(dx * dx + dy * dy)
        angles.append(math.degrees(math.atan2(r, abs(dz))))
    return angles, n, bad, inbound


def histogram(angles, width=BIN_DEG):
    """Counts per bin and counts per steradian.

    Per-steradian is the one to rank on: a raw count histogram rises with theta
    purely because the bin solid angle 2*pi*sin(theta)*dtheta grows, which would
    put the 'peak' at large angles for any optic whatsoever.

    LXP bundle rays carry equal weight and COptRayPath exposes no energy field,
    so counts are already proportional to energy. If true energy weighting is
    ever needed it must come from the .OptSequence sidecar, which carries power
    in watts per path family.
    """
    raw, per_sr = {}, {}
    for a in angles:
        b = int(a // width) * width
        raw[b] = raw.get(b, 0) + 1
    for b, c in raw.items():
        lo, hi = math.radians(b), math.radians(b + width)
        omega = 2.0 * math.pi * (math.cos(lo) - math.cos(hi))
        per_sr[b] = c / omega if omega > 0 else 0.0
    return raw, per_sr


def rank(per_sr, max_field_deg, width=BIN_DEG, ncand=4):
    """Rank the out-of-field bins. Split out from select() so it can be tested
    against a stored histogram without a licence or an .lpf.

    Returns (ranked_edges, oof, first_edge, notes).
    """
    # Admit bins outside the design field, then WALK OFF THE IMAGING CONE'S
    # SHOULDER before ranking.
    #
    # A fixed clearance does not work, and the histograms say why. On a wide
    # field the out-of-field distribution has no peak at all -- tessar25 runs
    # 41847 / 41862 / 40616 / 39690 per steradian, a flat monotonic decline --
    # so the "peak" is simply the lowest admissible angle, and excluding a whole
    # bin throws away the real answer (tessar25's true peak is 26 deg, in the
    # first admissible bin).
    #
    # On a NARROW field the in-field bins are a spike: petzval4 carries 955704
    # and 944766 per steradian inside a 4 deg field against ~10-20k outside, and
    # C25 839702 against 10368. That is the imaging cone, and its shoulder
    # spills into the bins just outside the field -- petzval4's first admissible
    # bin reads 109028, still 51x its neighbour, which is why the old selector
    # returned 5 deg when the forward peak of the same seated geometry is 14.
    #
    # So the discriminator is a SPIKE, not the field width: step outward while
    # the current bin towers over the next one, which only happens on a decaying
    # cone shoulder. On the flat wide-field profiles the ratio is ~1.0 and
    # nothing is excluded.
    first = width * math.ceil(max_field_deg / width)
    edges = sorted(k for k in per_sr if k >= first)
    dropped = []
    while len(edges) >= 2 and per_sr[edges[0]] > SHOULDER * per_sr[edges[1]]:
        dropped.append(edges.pop(0))
    if edges:
        first = edges[0]
    oof = dict((k, per_sr[k]) for k in edges)
    ranked = sorted(oof, key=lambda k: oof[k], reverse=True)[:ncand]
    return ranked, oof, first, dropped


def select(lpf, max_field_deg, width=BIN_DEG, ncand=4):
    """Return the recommended stray angle and the evidence behind it."""
    angles, n, bad, inbound = escape_angles(lpf)
    raw, per_sr = histogram(angles, width)

    ranked, oof, first, dropped = rank(per_sr, max_field_deg, width, ncand)
    result = {
        "lpf": lpf, "binDeg": width, "maxFieldDeg": max_field_deg,
        "raysTotal": n, "raysEscaping": len(angles),
        "raysInbound": inbound, "raysUnusable": bad,
        "firstAdmissibleEdge": first,
        "droppedConeShoulderEdges": dropped,
        "rawCounts": dict((str(k), v) for k, v in raw.items()),
        "perSteradian": dict((str(k), v) for k, v in per_sr.items()),
    }

    # A design field at or below one bin cannot be separated from the stray
    # region at all: every bin either overlaps it or sits immediately beside
    # it, and the histogram is then dominated by imaging light. Refuse rather
    # than return the imaging cone dressed as a stray angle (C25).
    if max_field_deg <= width:
        result.update(strayDeg=None, ok=False, resolved=False, censored=False,
                      candidates=[],
                      reason="design field %.1f deg is within one %.0f deg bin "
                             "- in-field and stray light cannot be separated by "
                             "this histogram" % (max_field_deg, width))
        return result

    if len(angles) < MIN_ESCAPING or not oof:
        result.update(strayDeg=None, ok=False, resolved=False, censored=False,
                      candidates=[],
                      reason="only %d escaping rays / %d out-of-field bins"
                             % (len(angles), len(oof)))
        return result

    peak = ranked[0] + width / 2.0

    # BOUNDARY CENSORING. If the winning bin is the first one admitted, the
    # search stopped at the edge of its own window instead of resolving an
    # interior maximum. That is sometimes legitimate -- stray light really can
    # fall monotonically from the field edge -- but it is NOT a resolved peak,
    # and on 2026-08-06 petzval4 showed how badly it can miss: ranked 5.0 deg,
    # while a forward sweep of the SAME seated geometry peaks at 14 deg with
    # 5.6x the flux (and the naive tube at 16 deg with 11x). The angle is still
    # returned so nothing downstream breaks, but `resolved` is False and the
    # caller is expected to escalate to a forward sweep before quoting a
    # reduction against it.
    censored = abs(ranked[0] - first) < 1e-9
    result.update(
        strayDeg=round(peak, 1), ok=True,
        censored=censored, resolved=not censored,
        candidates=[round(b + width / 2.0, 1) for b in ranked],
        reason=("UNRESOLVED: peak sits in the first admissible bin (%.0f-%.0f "
                "deg), so this is the edge of the search window, not a located "
                "maximum - confirm with a forward sweep before quoting a "
                "reduction at it" % (first, first + width)) if censored
               else "out-of-field per-steradian peak")
    return result


def main():
    if len(sys.argv) < 4:
        raise SystemExit("usage: angle_select.py <backward.lpf> <maxFieldDeg> <out.json>")
    lpf, mf, out = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    if not os.path.exists(lpf):
        raise SystemExit("missing LPF: %s" % lpf)
    r = select(lpf, mf)
    json.dump(r, open(out, "w"), indent=1)
    if r["ok"]:
        print("stray angle SELECTED: %.1f deg  (candidates %s)  from %d escaping rays"
              % (r["strayDeg"], r["candidates"], r["raysEscaping"]))
    else:
        print("stray angle NOT selected: %s" % r["reason"])
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
