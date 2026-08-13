r"""guard.py -- fail-loud assertions for the analysis side of the workflow.

    import sys; sys.path.append(r"...\stray-light-loop\lib")
    from guard import *

Every check here exists because that failure occurred SILENTLY during
development and produced confident wrong output rather than an error.
"""
import json
import math
import os
import re


class GuardError(AssertionError):
    """A pipeline invariant was violated. Never catch this to continue."""


def _fail(check, detail):
    raise GuardError("GUARD FAILED [%s] %s" % (check, detail))


def warn(check, detail):
    print("GUARD WARN [%s] %s" % (check, detail))


def assert_file(path, what, min_bytes=32):
    """A stage produced its output file, with content."""
    if not os.path.exists(path):
        _fail("file-produced", "%s missing: %s" % (what, path))
    n = os.path.getsize(path)
    if n < min_bytes:
        _fail("file-produced", "%s is only %d bytes: %s" % (what, n, path))
    return path


def load_json_checked(path, what):
    """Load JSON, rejecting the empty-value corruption a clobbered PowerShell
    variable produces (`"key":,`) before it becomes an opaque parse error."""
    assert_file(path, what)
    raw = open(path, encoding="utf-8-sig").read()
    if re.search(r'":\s*[,}\]]', raw):
        _fail("json-complete",
              "%s has EMPTY values (a PowerShell variable was clobbered — "
              "names differing only by case). Re-run the producing stage." % what)
    if "System.Object" in raw or "System.Collections" in raw:
        _fail("json-complete", "%s contains a stringified .NET object" % what)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        _fail("json-parse", "%s is not valid JSON: %s" % (what, e))


def assert_speos_run(log_path, what, end_marker="end", fatal_status=False):
    """A Speos IronPython run completed. Stdout is invisible inside Speos, so
    the result log is the ONLY evidence: absent = crashed, never 'still slow'.

    `fatal_status=True` turns an ERROR inside StatusInfo into a halt instead of
    a warning. Use it for a run that JUST happened; leave it off for a resume
    check, where the log is history and halting would refuse work already
    recorded.

    Why the distinction earns its keep: B23's sim_redesign was stamped `ok` on
    2026-07-26 while its own log carried an Ansys OPTIS HPC licence failure on
    SV_F1v and SV_F3v. A licence-degraded stage STILL WRITES its report files,
    so nothing was missing, nothing raised, and the resume check's warning
    scrolled past. Two false "significant" in-field results (5 sigma and 9
    sigma) survived in the corpus for ten days and died on a clean re-run
    2026-08-05. Only an ERROR halts -- a non-empty StatusInfo that is merely
    informational still warns, because the point is to catch failures, not to
    refuse chatter.
    """
    assert_file(log_path, "%s result log" % what)
    txt = open(log_path, encoding="utf-8", errors="replace").read()
    if "FATAL" in txt:
        tail = txt[txt.index("FATAL"):][:400]
        _fail("speos-run", "%s raised:\n%s" % (what, tail))
    if end_marker not in txt:
        _fail("speos-run", "%s never reached its end marker (crashed mid-run)" % what)
    m = re.findall(r"StatusInfo=\[([^\]]+)\]", txt)
    bad = [s for s in m if s.strip()]
    if bad:
        errs = [s for s in bad if "error" in s.lower()]
        if errs and fatal_status:
            _fail("speos-run",
                  "%s reported %d SIMULATION ERROR(S) in StatusInfo -- the run "
                  "wrote its result files anyway, so this will NOT show up as a "
                  "missing output:\n%s"
                  % (what, len(errs), "\n".join(e[:300] for e in errs[:2])))
        warn("speos-run", "%s reported StatusInfo: %s" % (what, bad[:3]))
    return txt


def assert_flux(value, what, lo=0.0, hi=None):
    """A radiometric result is physical."""
    if value is None:
        _fail("flux", "%s not found in the report (regex missed or sim failed)" % what)
    if math.isnan(value):
        _fail("flux", "%s is NaN" % what)
    if value < lo:
        _fail("flux", "%s is negative (%g)" % (what, value))
    if hi is not None and value > hi:
        _fail("flux", "%s = %g exceeds the emitted flux %g — non-physical"
              % (what, value, hi))
    return value


def assert_rays(count, expected, what, min_frac=0.5):
    """A trace produced enough rays for its result to mean anything."""
    if count is None or count <= 0:
        _fail("ray-count", "%s recorded NO rays (expected ~%s)" % (what, expected))
    if expected and count < expected * min_frac:
        _fail("ray-count", "%s recorded %d of ~%d rays (>%.0f%% lost)"
              % (what, count, expected, 100 * (1 - min_frac)))
    return count


def poisson_rel_err(n):
    """Relative 1-sigma error on a count-based measurement."""
    return float("inf") if n <= 0 else 1.0 / math.sqrt(n)


def assert_significant(before, after, n_before, n_after, what, sigma=2.0):
    """Guard against reading Monte-Carlo noise as a result.

    Returns (delta_pct, combined_sigma, is_significant). Does NOT raise — a
    null is a legitimate finding; it must simply be REPORTED as one. The GPIM
    result (+7.9% +/- 6.3%, 1.2 sigma) reads as a regression without this.
    """
    if before == 0:
        _fail("significance", "%s has a zero baseline" % what)
    d = 100.0 * (after - before) / before
    comb = 100.0 * math.sqrt(poisson_rel_err(n_before) ** 2 +
                             poisson_rel_err(n_after) ** 2)
    sig = abs(d) / comb if comb else float("inf")
    if sig < sigma:
        warn("significance",
             "%s: %+.1f%% +/- %.1f%% (%.1f sigma) — NOT distinguishable from noise"
             % (what, d, comb, sig))
    return d, comb, sig >= sigma


def assert_traced(values, what, zero_tol=1e-9):
    """Every field actually produced a spot.

    TOLERANCE, NOT EQUALITY. A failed OpticStudio trace does not return 0, it
    returns something like 5.3e-14, which is strictly greater than zero and
    sails straight through `if v == 0` or `if v <= 0`. Two generated test cases
    were accepted as valid that way: on-axis 5.3e-14 mm with both off-axis
    fields at 0, i.e. a system in which no ray reaches the image, reported as a
    working lens. Same family as "a null read as a result".
    """
    if not values:
        _fail("traced", "%s produced no spot data at all" % what)
    dead = [i for i, v in enumerate(values)
            if v is None or v != v or abs(v) < zero_tol]
    if dead:
        _fail("traced", "%s: no ray reaches the image at field index %s "
                        "(values %s) -- the trace failed, it did not measure zero"
              % (what, dead, ["%.3g" % (v if v is not None else float('nan'))
                              for v in values]))
    return values


def assert_infield_metric_valid(flux_ratio, reli, what, tol=0.15):
    """The per-field flux ratio must agree with sequential relative illumination.

    The in-field metric is total detector flux from one imported ODX per-field
    source, reported relative to the axial field. That is only a throughput
    measure if the source couples into the pupil properly -- and it stops doing
    so when a system is driven well outside its design field.

    Measured 2026-07-26 on B01 (a Double Gauss widened 14 -> 26 deg):
        field 2 (18.6 deg): flux ratio 0.907 vs RELI 0.887  -- agrees
        field 3 (26.0 deg): flux ratio 0.076 vs RELI 0.905  -- 12x off
    Every system run AT its design field agrees (0.96-0.99); only the widened
    one collapses. Interpreting that 0.076 as throughput produced a confident,
    entirely wrong story about barrel vignetting.

    RELI columns are Param1=Samp, Param2=Wave, Param3=Field -- passing the field
    number positionally as arg 1 silently returns 1.0000 for every field.
    """
    if reli is None or flux_ratio is None:
        _fail("infield-metric", "%s: need both the flux ratio and RELI to "
                                "validate the in-field metric" % what)
    if reli <= 0:
        _fail("infield-metric", "%s: RELI is %g" % (what, reli))
    rel = abs(flux_ratio - reli) / reli
    if rel > tol:
        _fail("infield-metric",
              "%s: per-field flux ratio %.3f disagrees with RELI %.3f by %.0f%% "
              "-- the ODX source is not coupling into the pupil at this field, "
              "so in-field THROUGHPUT numbers here are meaningless (stray "
              "numbers, which use a separately built source, may still be fine)"
              % (what, flux_ratio, reli, 100 * rel))
    return rel


def assert_image_quality(before_um, after_um, what, max_growth_pct=10.0,
                         polychromatic=True):
    """Image quality was verified, and verified the RIGHT way.

    A single-wavelength check reported the GPIM-optimized DG edge spot as
    +4.2%; the polychromatic check on the same file showed +49.5%.
    """
    if not polychromatic:
        _fail("image-quality",
              "%s was verified MONOCHROMATICALLY — use wave=0; mono hid a "
              "49.5%% edge degradation once" % what)
    if not before_um or not after_um:
        _fail("image-quality", "%s has no spot data (did the trace return rays?)" % what)
    worst = None
    for i, (b, a) in enumerate(zip(before_um, after_um)):
        if b <= 0:
            _fail("image-quality", "%s field %d baseline is %g — trace failed" % (what, i, b))
        g = 100.0 * (a - b) / b
        if worst is None or g > worst[1]:
            worst = (i, g)
    if worst[1] > max_growth_pct:
        warn("image-quality", "%s: field %d spot grew %+.1f%% (limit %.0f%%)"
             % (what, worst[0], worst[1], max_growth_pct))
    return worst


def assert_envelope_agrees_with_transmission(worst_fail, t_redesign, what,
                                             floor=0.95):
    """Cross-validate the beam-envelope verdict against MEASURED transmission.

    The envelope check compares bore radii against a traced ray fan and reports
    `worstFail`. On 2026-07-28 it was found reporting worstFail = 0.00 on
    barrels that transmit as little as 3.3% of the corner beam -- 18 readings
    across the corpus, concentrated in the tessar family (median T = 0.213
    against 1.000 for dg/cooke/doublet). The loss is geometric, not scatter: a
    MIRROR0 absorbing-wall null reproduced it to four decimals.

    FIVE mechanisms have been eliminated -- sparse-ray statistics, tight
    clearances, an under-checked vane or housing, meridional-only Px=0 sampling
    (a true 2-D pupil grid exceeds it by 0.004 mm where it matters), and Speos
    volume-conflict errors (comparable counts on clean systems). The cause is
    NOT yet known, so the check cannot yet be made correct.

    It can be made HONEST. A guard that certifies bad geometry silently is worse
    than no guard, because downstream work treats worstFail = 0 as proof. This
    compares the geometric verdict against the one instrument that measures the
    thing it is trying to predict, and fails loudly when they disagree.

    Call it once transmission exists (after sim_optics + sim_redesign). It does
    NOT replace the envelope check -- it catches the case where the envelope
    check is wrong.
    """
    if t_redesign is None:
        return None
    if worst_fail is not None and worst_fail > 0:
        return None                 # envelope already failed; nothing hidden
    if t_redesign < floor:
        _fail("envelope-vs-transmission",
              "%s: the envelope check passed (worstFail=%s) but the barrel "
              "transmits only %.1f%% of this field's beam. The geometric check "
              "and the measured transmission disagree, so the barrel geometry "
              "is NOT validated -- see full-loop-sweep.md section 5e (cause "
              "unidentified, five mechanisms ruled out)."
              % (what, worst_fail, 100.0 * t_redesign))
    return t_redesign


def assert_transmission_denominator_valid(coupling, reli, what,
                                          lo=0.8, hi=1.25):
    """Transmission is only interpretable if its DENOMINATOR is imaging light.

    T = flux_with_mech / flux_optics_only. The denominator is a no-mechanics
    run, and it can fail in BOTH directions:

      * DEFICIENT - the imported per-field ODX source stops coupling into the
        pupil off-axis, so almost nothing arrives. B01 F3: coupling 0.051 while
        RELI says 0.890. Numerator and denominator are then both measuring
        stray, and their ratio (0.509) looks exactly like an obstruction.
      * INFLATED - with no barrel present, light flies past the lenses onto the
        detector. B32 F3: coupling 1.012 against RELI 0.133, so the denominator
        is ~8x the imaging flux. Any enclosure removes that light CORRECTLY,
        and T (0.487) again looks like an obstruction.

    The test is whether the no-mechanics run delivers what the optics can
    actually deliver: coupling = flux_oo(field)/flux_oo(axial) should equal
    RELI(field). Measured 2026-07-29: 35 of 118 readings fail this, and every
    severe "obstruction" in the corpus was a denominator failure, not a barrel.

    Call with coupling and the RELI for the SAME field.
    """
    if coupling is None or reli is None or reli <= 0:
        _fail("transmission-denominator",
              "%s: need both coupling and a positive RELI to validate the "
              "transmission denominator" % what)
    ratio = coupling / reli
    if not (lo <= ratio <= hi):
        kind = "DEFICIENT (source under-couples)" if ratio < lo else \
               "INFLATED (stray reaches the detector with no barrel)"
        _fail("transmission-denominator",
              "%s: optics-only denominator is %s - coupling %.3f vs RELI %.3f "
              "(ratio %.2f, want %.2f-%.2f). Transmission here is a ratio of "
              "two non-imaging measurements and must not be read as throughput."
              % (what, kind, coupling, reli, ratio, lo, hi))
    return ratio
