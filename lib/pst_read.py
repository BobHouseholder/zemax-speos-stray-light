"""pst_read.py -- read fluxes out of Speos PST sweep reports.

ONE implementation, imported by both the standalone analyser and the confirm
step inside the runner. This codebase has shipped the same convention in two
places three times now (`min(3, n)`); a regex for a result value is exactly the
kind of thing that must not be duplicated.
"""
import glob
import os
import re

FLUX_RE = re.compile(r"<li>Flux: ([0-9.eE+-]+) W</li>")
ERR_RE = re.compile(r"Total number of errors.{0,200}?([0-9]+)", re.S)


def token_angle(tok):
    """'34b' -> 34.0. A trailing letter marks a repeat run at the same angle."""
    return float("".join(c for c in tok if c.isdigit() or c == "."))


def report_flux(path):
    """(flux_W, error_ray_count) from one Speos report, or (None, None)."""
    if not os.path.exists(path):
        return None, None
    txt = open(path, encoding="utf-8", errors="ignore").read()
    m, e = FLUX_RE.search(txt), ERR_RE.search(txt)
    return (float(m.group(1)) if m else None,
            int(e.group(1)) if e else None)


def collect_curve(wd, sfx):
    """All PST results under wd for suffix sfx -> [(angle, token, flux, err)]."""
    pat = os.path.join(wd, "**", "PST*_%s.Report.html" % sfx)
    rows = []
    for r in glob.glob(pat, recursive=True):
        m = re.search(r"PST(.+?)_%s\.Report\.html" % re.escape(sfx),
                      os.path.basename(r))
        if not m:
            continue
        fx, er = report_flux(r)
        if fx is None:
            continue
        rows.append((token_angle(m.group(1)), m.group(1), fx, er))
    rows.sort()
    return rows


def list_suffixes(wd):
    """Every PST result suffix present under wd, with its report count.

    Guessing a suffix is how a CONFIRM run's four reports once got analysed in
    place of a SWEEP's seven -- which would have presented the confirm sims as
    independent validation of themselves. Enumerate; never guess.
    """
    out = {}
    for r in glob.glob(os.path.join(wd, "**", "PST*.Report.html"), recursive=True):
        m = re.search(r"PST.+?_(.+)\.Report\.html", os.path.basename(r))
        if m:
            out[m.group(1)] = out.get(m.group(1), 0) + 1
    return out


def _export_survey_dir(base):
    """Tell the child Speos where survey/ is.

    A wire script needs this to import field_slots, and cannot work it out:
    inside Speos, IronPython sets __file__ to a GUID, so abspath() resolves it
    against the working directory and returns a plausible wrong folder rather
    than raising. Exported from the two functions below because every driver
    calls one of them before launching Speos -- putting it anywhere else means
    remembering it at eleven call sites.
    """
    os.environ["SL_SURVEY_DIR"] = os.path.join(base, "survey")
    os.environ["SL_ROOT"] = base


def private_config(tag):
    """Per-run PST config path, exported to the child Speos as SL_PST_CONFIG.

    `survey/pst-config.txt` is READ by wire-survey-pst.py and WRITTEN by six
    different drivers. That made it a global mutable, and on 2026-08-04 two
    drivers ran at once: a sweep launched for A01 read a wideangle32 config
    written 40 s later, so an A01 measurement executed wideangle32's geometry
    and wrote into wideangle32's output folder. Nothing raised -- the band
    driver just recorded "no flux" and moved on.

    Lives here for the same reason `field_slots.py` does: this codebase has
    shipped one convention as two expressions three times, and a config path
    every driver spells for itself is precisely that shape.
    """
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "survey", ".pstcfg")
    if not os.path.isdir(d):
        os.makedirs(d)
    path = os.path.join(d, "%s-%d.txt" % (tag, os.getpid()))
    os.environ["SL_PST_CONFIG"] = path
    _export_survey_dir(base)
    return path


def survey_config(tag):
    """Per-run survey config path, exported as SL_SURVEY_CONFIG.

    Sibling of private_config for the OTHER shared global: survey-config.txt is
    read by seven wire scripts and written by runner.py, screen-angles.py and
    run-backtrace.py. Both live here so there is one home for run-scoped config
    paths rather than a second module doing the same job.
    """
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, "survey", ".pstcfg")
    if not os.path.isdir(d):
        os.makedirs(d)
    path = os.path.join(d, "survey-%s-%d.txt" % (tag, os.getpid()))
    os.environ["SL_SURVEY_CONFIG"] = path
    _export_survey_dir(base)
    return path


SWEEP_OK_RE = re.compile(r"^SWEEP OK", re.M)
SWEEP_BAD_RE = re.compile(r"^SWEEP INCOMPLETE -- (\d+) failed", re.M)
SIMERR_RE = re.compile(r"^\s*SIMERROR (.+)$", re.M)


def sweep_status(log_path):
    """('ok'|'incomplete'|'unknown', [errors]) from a wire-survey-pst log.

    'unknown' is returned for logs written before the marker existed, so this
    is safe to call on historical runs -- pair it with missing_tokens(), which
    catches the same failure from the result side.
    """
    if not log_path or not os.path.exists(log_path):
        return "unknown", []
    txt = open(log_path, encoding="utf-8", errors="ignore").read()
    errs = SIMERR_RE.findall(txt)
    if SWEEP_BAD_RE.search(txt):
        return "incomplete", errs
    if SWEEP_OK_RE.search(txt):
        return "ok", []
    # legacy log: fall back to the raw signature the wire script always wrote
    if "StatusInfo=[Error" in txt:
        return "incomplete", ["legacy log: StatusInfo carried an error"]
    return "unknown", []


def missing_tokens(rows, tokens):
    """Requested angle tokens with no result row. Empty list means complete.

    A failed sim writes NO Report.html, so collect_curve cannot see it and a
    `17,17b` pair silently degrades to one sample -- whose repeat-pair noise
    floor then reads 0.0. Callers must compare against what they ASKED for.
    """
    got = set(r[1] for r in rows)
    return [t for t in tokens if t not in got]


def assert_sweep(rows, tokens, log_path=None, what=""):
    """Raise unless every requested angle produced a result and no sim errored."""
    miss = missing_tokens(rows, tokens)
    state, errs = sweep_status(log_path)
    if miss or state == "incomplete":
        raise RuntimeError(
            "incomplete PST sweep %s: missing %s; log=%s%s"
            % (what or "", miss or "none", state,
               ("; " + "; ".join(errs[:3])) if errs else ""))
    return True


_UNGUARDED_WARNED = set()


def read_sweep(wd, sfx, expect=None, log_path=None, what=""):
    """THE entry point for reading a PST sweep. collect_curve + verification.

    Raises RuntimeError unless every requested token produced a result and the
    wire log reports no failed sim. Pass `expect=None` when analysing results
    that were produced by an earlier run whose angle list is not available here;
    the log is still checked.

    Why this exists rather than each driver checking for itself: on 2026-08-04 a
    Speos HPC licence dropped out for roughly half the sims in a batch. A failed
    sim logs `computed. StatusInfo=[Error: Licensing error ...]` and writes NO
    Report.html, so collect_curve simply returned fewer rows and the caller
    averaged them -- a baseline over one repeat against a seated over two,
    reported as `+24.8%`. Every consumer in this codebase had the same shape.
    Worse, `assert_sweep` and the SWEEP OK/SIMERROR markers already existed and
    NOTHING CALLED THEM, which is the failure this function is meant to end:
    a validity check is not a safeguard until the read path goes through it.

    Note the aggregation hazard this also closes: callers group rows by angle
    and average, so a lost repeat both halves the sample AND makes noise_floor()
    report 0.0 for that angle -- a partial run that claims to be perfectly
    repeatable.
    """
    rows = collect_curve(wd, sfx)
    if expect is None:
        state, errs = sweep_status(log_path)
        if state == "incomplete":
            raise RuntimeError(
                "PST sweep %s reported failed sims: %s"
                % (what or sfx, "; ".join(errs[:3]) or "unspecified"))
        if state == "unknown":
            import sys
            sys.stderr.write(
                "WARNING pst_read: %s read WITHOUT an expected angle list and "
                "with no usable log -- a sim that failed and wrote no report is "
                "undetectable here. Pass expect=[...] from the driver that ran "
                "it.\n" % (what or sfx))
        return rows
    assert_sweep(rows, list(expect), log_path, what or sfx)
    return rows


# NOTE, 2026-08-05: an expectation-free completeness test was attempted here and
# REMOVED, because it cannot work on this codebase's sweeps. A PST sweep repeats
# exactly ONE angle for the noise floor -- measured shape `[2,1,1,1,1,1,1]` on
# wideangle32's 7-angle run -- so losing a single-shot angle leaves the
# distribution of repeat counts unchanged and is invisible from the results
# alone. Only the requested angle list (expect=) or the wire log (SWEEP OK /
# SIMERROR) can detect it. A check that silently cannot fire is worse than none,
# because it reads as coverage.


def collect_curve_unverified(wd, sfx, why=""):
    """Escape hatch for reads that genuinely cannot state what they expected.

    Warns once per suffix so an unguarded read is visible rather than silent.
    Prefer read_sweep(); this exists so that choosing to skip verification is a
    deliberate, greppable act.
    """
    if sfx not in _UNGUARDED_WARNED:
        _UNGUARDED_WARNED.add(sfx)
        import sys
        sys.stderr.write(
            "WARNING pst_read: UNVERIFIED read of %s (%s) -- a failed sim is "
            "indistinguishable from a missing angle here\n" % (sfx, why or "no reason given"))
    return collect_curve(wd, sfx)


# --- interpreting a sweep: where is the peak, and does the difference matter --
# These live here rather than in each driver because the THRESHOLD IS THE
# DEFINITION OF THE FINDING, not a detail of the caller. It was re-derived per
# script three times on 2026-08-06 and was wrong twice, both times by judging in
# DEGREES.
MATERIAL_RATIO = 1.5


def curve_from(rows):
    """[(angle, token, flux, err)] -> {angle: mean flux}, averaging repeats."""
    agg = {}
    for a, tok, fx, er in rows:
        agg.setdefault(a, []).append(fx)
    return dict((a, sum(v) / len(v)) for a, v in agg.items())


def peak_of(curve):
    """(angle, flux, censored) of the maximum.

    `censored` means the maximum sits at the TOP of the sampled range, so it is
    the largest value measured rather than a located maximum -- extend the sweep
    until the curve turns over before calling it a peak. petzval4's first sweep
    stopped at 16 deg with the curve still rising; extending to 28 deg is what
    made 16 an interior peak rather than an edge value.
    """
    if not curve:
        return None, None, False
    pk = max(curve, key=lambda a: curve[a])
    return pk, curve[pk], pk == max(curve.keys())


def materiality(curve, reference_deg, threshold=MATERIAL_RATIO):
    """Does the peak differ from `reference_deg` by enough to change an answer?

    JUDGE ON FLUX, NEVER ON DEGREES. Measured counter-examples that fix the
    rule, both from 2026-08-06:

        B14  peak 3 deg from the reference, 1.02x the flux  -> immaterial;
             the curve is simply flat across its top.
        A06  peak 3 deg from the reference, 12.7x the flux  -> severe.

    An angle-error threshold calls those two the same and is useless. What
    propagates into a reduction is the flux at the angle you measured, so the
    ratio is the statistic. Returns a dict; `material` is the verdict.
    """
    pk, pf, censored = peak_of(curve)
    ref = curve.get(reference_deg)
    ratio = (pf / ref) if (ref and pf is not None) else None
    material = bool(ratio is not None and ratio >= threshold)
    if pk is None:
        verdict = "no curve"
    elif censored:
        verdict = ("UNRESOLVED: peak at %.0f deg is the top of the sampled "
                   "range -- extend the sweep" % pk)
    elif ratio is None:
        verdict = "reference angle %.0f deg not in the curve" % reference_deg
    elif material:
        verdict = ("MATERIAL: %.0f deg carries %.1fx the flux of the reference "
                   "%.0f deg" % (pk, ratio, reference_deg))
    else:
        verdict = ("immaterial: peak %.0f deg carries %.2fx the reference "
                   "%.0f deg" % (pk, ratio, reference_deg))
    return {"peakDeg": pk, "peakFlux": pf, "refDeg": reference_deg,
            "refFlux": ref, "ratio": ratio, "material": material,
            "censored": censored, "verdict": verdict}


def noise_floor(rows):
    """Percent spread between repeat runs at the same angle, if any.

    A repeat token is a free Monte-Carlo noise estimate: same angle, same
    geometry, different random seed. Without it there is no way to tell a real
    difference between two candidates from sampling scatter.
    """
    by = {}
    for a, tok, fx, er in rows:
        by.setdefault(a, []).append(fx)
    out = {}
    for a, v in by.items():
        if len(v) > 1:
            mean = sum(v) / len(v)
            out[a] = abs(max(v) - min(v)) / mean * 100.0 if mean else 0.0
    return out
