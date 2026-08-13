r"""outsel.py -- resolve WHICH simulation output a reader should open.

    import sys; sys.path.append(r"...\stray-light-loop\lib")
    import outsel

THE BUG THIS MODULE EXISTS TO PREVENT
-------------------------------------
A case directory holds ONE `SPEOS output files` folder and, inside it, one
subfolder per Speos run. The pipeline's own run is one of them; every side
experiment opens its own (`A01-band`, `A01-blast`, `A01-decode`, `A01-graz`,
`doubletstart5-screen`, ...). Which subfolder a reader gets is therefore a
CHOICE, and every place in this codebase that made that choice by directory
listing order has been wrong:

  * score-loop.py took `subs[0]`. Measured 2026-08-04: all 100 corpus cases
    silently re-scored as `complete-noresults` -- the whole suite losing its
    stray reductions with no error raised. Its fixed `outdir()` is the
    precedent this module generalises.
  * analyze-survey.py took `subs[0]`. Measured 2026-08-09: for
    `doubletstart5` that is `doubletstart5-blast`, a PST side run holding
    ZERO .lpf files, ahead of `doubletstart5-speos` which holds the real
    `SV_Back_doub_base.FrontCatch.lpf`. Every `back_traces` in
    survey-results.json came back null while the reader was provably fine
    (handed the file directly it returns 40882).
  * analyze-ghostdecode.py / analyze-straydecode.py kept the LAST glob match.
    A01 has three `.OptSequence` files from three different sims.

Listing order is not a fact about the data. It is an artefact of the
filesystem that happens to be stable enough to look like a rule, which is what
makes it dangerous: it works until a side experiment is added.

THE RULE
--------
Choose by CONTENT -- the files the caller has said it will actually open --
and when content does not single one out, REFUSE. A wrong folder read
successfully is indistinguishable from a genuine zero, and this codebase has
now paid for that three times. `pick_dir_by_files` returns None rather than
guessing; `resolve_one` raises rather than guessing.
"""
import glob
import os
import re


class OutputSelectionError(Exception):
    """Selection could not be made without guessing.

    Never catch this to fall back on a default -- the default IS the bug.
    Catch it only to report it and skip the case.
    """


def _subdirs(root):
    """Immediate subfolders of `root`, sorted by name.

    The sort is for REPRODUCIBILITY of diagnostics only. Nothing downstream
    may treat position in this list as meaning anything.
    """
    try:
        names = os.listdir(root)
    except OSError:
        return []
    return sorted(os.path.join(root, n) for n in names
                  if os.path.isdir(os.path.join(root, n)))


def pick_dir_by_files(root, wanted, label):
    """The subfolder of `root` holding the files the caller will read.

    `wanted` is the list of EXACT basenames this reader opens -- not a glob,
    not a prefix. Scoring on the caller's own read list is what makes the
    choice content-based: the winning folder is by definition the one that can
    answer the question being asked.

    Returns `(path, diag)`. `path` is None when NO candidate holds a single
    wanted file, so the caller reports "no results" instead of reading a
    folder full of some other experiment's output. `diag` is a list of
    `{"dir", "have", "want"}` -- always populated, structured rather than
    preformatted so callers can test it (`have < want` is a PARTIAL result and
    worth saying) instead of scraping their own log lines. Use `format_diag`
    to print it.

    Raises OutputSelectionError when two candidates tie on a non-zero score:
    that is genuine ambiguity and there is no safe way to break it here.
    """
    if not os.path.isdir(root):
        return None, []
    cands = _subdirs(root) or [root]
    scored = []
    for s in cands:
        try:
            names = set(os.listdir(s))
        except OSError:
            names = set()
        scored.append((sum(1 for w in wanted if w in names), s))
    scored.sort(key=lambda t: (-t[0], t[1]))
    diag = [{"dir": os.path.basename(p) or p, "have": n, "want": len(wanted)}
            for n, p in scored]
    best = scored[0][0]
    if best == 0:
        return None, diag
    tied = [p for n, p in scored if n == best]
    if len(tied) > 1:
        raise OutputSelectionError(
            "%s: %d folders hold the same %d of the %d wanted files (%s) -- "
            "content cannot single one out and listing order is not an "
            "answer. Remove or rename the stale run."
            % (label, len(tied), best, len(wanted),
               ", ".join(os.path.basename(t) for t in tied)))
    return scored[0][1], diag


def format_diag(diag):
    """`pick_dir_by_files` diagnostics as printable lines."""
    return ["%2d/%-2d wanted files in %s" % (d["have"], d["want"], d["dir"])
            for d in diag]


def candidates(root, pattern):
    """Every file matching `pattern` in any subfolder of `root`, and in `root`.

    Sorted by path so runs are reproducible. That sort carries NO meaning:
    callers must not take [0] or [-1] as "the" answer -- doing exactly that,
    with an unsorted glob, is what this module exists to stop.
    """
    if not os.path.isdir(root):
        return []
    esc = glob.escape(root)
    hits = sorted(glob.glob(os.path.join(esc, "*", pattern)))
    hits += sorted(glob.glob(os.path.join(esc, pattern)))
    return [h for h in hits if os.path.isfile(h)]


def sim_suffix(path, prefix):
    """The sim suffix in an output basename: `SD_Stray_<sfx>.<trailer>`.

    Speos appends its own trailer (`.Irradiance.1.Optical Design
    Exchange.1.30`), so the suffix is what sits between the sim-name prefix
    and the first dot.
    """
    b = os.path.basename(path)
    if not b.startswith(prefix):
        return None
    return b[len(prefix):].split(".")[0]


def wire_log_provenance(case_dir):
    """`{sfx: {"mech": step, "log": file}}` read from the wire scripts' logs.

    Every wire-*.py writes a first line of the form

        wire-straydecode start: odx=... mech=...\\A01-seated.step sfx=A01dec ...

    which is the only record of WHAT a given suffix was a simulation OF. Two
    candidates that differ only in suffix can be entirely different physics --
    A01's `A01dec` is the SEATED barrel and `A01decB` is the naive BASELINE
    tube -- so an ambiguity report that lists bare filenames is not actually
    telling the operator what they are choosing between.

    Best-effort: a missing or unparseable log costs an annotation, never the
    selection.
    """
    prov = {}
    for p in sorted(glob.glob(os.path.join(glob.escape(case_dir), "*.txt"))):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                first = f.readline()
        except IOError:
            continue
        if "wire-" not in first:
            continue
        m = re.search(r"\bsfx=(\S+)", first)
        if not m:
            continue
        mech = re.search(r"\bmech=(\S+)", first)
        prov[m.group(1)] = {
            "mech": (os.path.basename(mech.group(1).replace("\\", "/"))
                     if mech else None),
            "log": os.path.basename(p),
        }
    return prov


def resolve_one(cands, prefix, want, label, case_dir=None):
    """Exactly one file from `cands`, or an exception. Never a guess.

    `want` is an explicit suffix from the caller (the `<caseId>:<sfx>` CLI
    form); None means "there had better be only one". On more than one
    candidate with no `want`, this raises with the full table -- suffix, size,
    and the mechanics each sim was run on -- plus the argument that picks each
    one. That refusal is the point: the alternative is silently reporting one
    variant's physics under the other's name.
    """
    if not cands:
        raise OutputSelectionError(
            "%s: no file matching %s* under 'SPEOS output files'" % (label, prefix))

    by_sfx = {}
    for c in cands:
        by_sfx.setdefault(sim_suffix(c, prefix) or os.path.basename(c), []).append(c)

    if want is not None:
        hit = by_sfx.get(want)
        if not hit:
            raise OutputSelectionError(
                "%s: no %s%s among the %d candidate(s): %s"
                % (label, prefix, want, len(cands), ", ".join(sorted(by_sfx))))
        if len(hit) > 1:
            raise OutputSelectionError(
                "%s: suffix %r matches %d files in different folders: %s"
                % (label, want, len(hit), ", ".join(hit)))
        return hit[0]

    if len(cands) == 1:
        return cands[0]

    prov = wire_log_provenance(case_dir) if case_dir else {}
    rows = []
    for sfx in sorted(by_sfx):
        for c in by_sfx[sfx]:
            info = prov.get(sfx) or {}
            rows.append(
                "     %-14s %10d B  %-28s mech=%s"
                % ("%s:%s" % (label, sfx), os.path.getsize(c),
                   os.path.basename(os.path.dirname(c)),
                   info.get("mech") or "unrecorded"))
    raise OutputSelectionError(
        "%s: %d files match %s* -- these are DIFFERENT simulations, and which "
        "one a listing happens to yield first is not a reason to report its "
        "numbers as this case's. Name the one you mean as <caseId>:<sfx>:\n%s"
        % (label, len(cands), prefix, "\n".join(rows)))


def split_arg(arg):
    """`"A01:A01decB"` -> `("A01", "A01decB")`; `"A01"` -> `("A01", None)`."""
    if ":" in arg:
        cid, sfx = arg.split(":", 1)
        return cid, (sfx or None)
    return arg, None
