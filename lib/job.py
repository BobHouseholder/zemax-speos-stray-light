"""job.py -- typed job manifest for a stray-light run, with provenance.

Replaces the positional `.txt` configs (the Speos runner reads 14 unlabelled
lines) that were hand-edited ~15 times in one session; a line-order slip is
silent and produces a valid-looking WRONG run.

One manifest per system is the single source of truth. The runner RENDERS the
legacy positional config from it, so the proven Speos scripts stay untouched
while nothing is ever hand-edited again.

Every artefact is stamped with the hashes of the scripts and inputs that made
it, so "which wall model produced this -94%?" is answerable from the file
rather than from memory.
"""
import datetime
import hashlib
import json
import os

SCHEMA = "straylight-job/1"
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sha(path, n=12):
    """Short content hash of a file, or 'missing'."""
    if not os.path.exists(path):
        return "missing"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:n]


def now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def provenance(scripts, inputs):
    """Hash the scripts and inputs behind a stage so results are traceable."""
    return {
        "stamped": now(),
        "scripts": {os.path.basename(p): sha(p) for p in scripts},
        "inputs": {os.path.basename(p): sha(p) for p in inputs},
    }


def default_manifest(slug, lens, workdir):
    """Skeleton. Numeric fields are filled in by the layout stage -- they are
    DERIVED from the prescription, never hand-set."""
    return {
        "schema": SCHEMA,
        "slug": slug,
        # explicit, not derived: Speos sim names are built from this. Deriving
        # it (slug[:4]) silently disagreed with existing artefacts for two
        # systems, which would have re-run finished work.
        "simPrefix": slug[:4],
        "lens": lens.replace("\\", "/"),
        "workdir": workdir.replace("\\", "/"),
        "created": now(),
        "preflight": {"verdict": None, "blocks": [], "warnings": []},
        # filled by the layout stage
        "optics": {"imgZ": None, "imgSD": None, "maxField": None,
                   "primaryWave": None, "elements": None},
        # simulation setup, derived in the mech stage
        "sim": {"strayDeg": None, "zSrc": -40.0, "rSrc": None, "rDisc": None,
                "zCatch": None, "waveNm": 550.0,
                "rays": {"stray": 1000000, "infield": 200000, "backward": 300000}},
        "materials": {
            "wallBsdf": os.path.join(BASE, "black-anodize-plausible.anisotropicbsdf").replace("\\", "/"),
            "wallBsdfNote": "SYNTHETIC black-anodize model, not a measurement -- "
                            "swap for customer data (LightTec guideline) when available",
        },
        "variants": [
            {"name": "base", "mechSuffix": "-baseline.step", "edgeBlack": False,
             "note": "naive placeholder tube -- the 'before'"},
            {"name": "redesign", "mechSuffix": "-seated.step", "edgeBlack": True,
             "note": "prescription-driven seated barrel + edge blackening"},
        ],
        "stages": {},          # stage -> {status, started, finished, outputs, provenance}
    }


def path_for(slug, workdir, kind, variant=None):
    """Canonical artefact paths -- one place, so no stage guesses."""
    p = {
        "manifest":  os.path.join(workdir, "%s.job.json" % slug),
        "preflight": os.path.join(workdir, "%s-preflight.json" % slug),
        "layout":    os.path.join(workdir, "%s-layout.json" % slug),
        "odx":       os.path.join(workdir, "%s.odx" % slug),
        "params":    os.path.join(workdir, "%s-params.json" % slug),
        "mech_base": os.path.join(workdir, "%s-baseline.step" % slug),
        "mech_seat": os.path.join(workdir, "%s-seated.step" % slug),
        "s0":        os.path.join(workdir, "%s-s0.json" % slug),
        # double-bounce ghost minimisation (opt-in). The optimised lens is a
        # SEPARATE file on purpose: repointing the job's own `lens` would
        # silently change what every downstream stray-light number refers to.
        # To measure the ghost-optimised design, stage `<slug>-ghost.zmx` as its
        # own job and run the fleet on it -- then the comparison is two jobs
        # through one pipeline rather than one job that quietly changed.
        "ghostopt":   os.path.join(workdir, "%s-ghostopt.json" % slug),
        "lens_ghost": os.path.join(workdir, "%s-ghost.zmx" % slug),
        # the measured stray angle from the optics-only backward trace
        "strayangle": os.path.join(workdir, "%s-strayangle.json" % slug),
        "speosdoc":  os.path.join(workdir, "%s-speos.scdocx" % slug),
    }
    if variant:
        p["result"] = os.path.join(workdir, "result-%s.txt" % variant)
        p["config"] = os.path.join(workdir, "%s-%s.cfg.txt" % (slug, variant))
    return p[kind]


def load(path):
    """Read a manifest and anchor it to WHERE IT ACTUALLY IS.

    `workdir` is stored absolute and every artefact path is built from it
    (see path_for and render_speos_config), so a manifest that moves keeps
    pointing at the machine it was written on. That is not hypothetical: the
    published subset's a03.job.json still named
    `C:/Users/<user>/Dropbox/.../testcases/cases/A03`, so a reader's run
    resolved every input to a directory that does not exist on their disk --
    and did it silently, because a wrong absolute path looks exactly like a
    right one until something opens it.

    A manifest lives IN its own workdir. That is the invariant this relies on,
    and it is not an assumption: checked 2026-08-09 across all 121 manifests
    in the tree (100 test cases + 21 survey systems), `workdir` equals the
    manifest's own directory in every single one. So re-anchoring is a no-op
    where the file has not moved, and the correction only ever fires on a copy
    that has -- which is exactly when the stored value is wrong.

    Only `workdir` is re-anchored. `lens` and `materials.wallBsdf` are also
    absolute, but they name a prescription and a repo-level BSDF rather than
    case-local artefacts; neither can be derived from the manifest's location.
    """
    with open(path, encoding="utf-8-sig") as f:
        m = json.load(f)
    if m.get("schema") != SCHEMA:
        raise ValueError("%s is schema '%s', expected '%s'"
                         % (path, m.get("schema"), SCHEMA))

    here = os.path.dirname(os.path.abspath(path))
    stored = (m.get("workdir") or "").replace("\\", "/")
    if os.path.normcase(os.path.normpath(stored)) != os.path.normcase(here):
        # Say it. A silently corrected path is still a surprise, and the
        # stored value is evidence about where this manifest came from.
        import sys
        sys.stderr.write(
            "%s: workdir re-anchored to this copy\n    was: %s\n    now: %s\n"
            % (os.path.basename(path), stored or "(unset)",
               here.replace("\\", "/")))
        m["workdir"] = here.replace("\\", "/")
    return m


def save(m, path, attempts=12):
    """Atomic manifest write, resilient to transient file locks.

    These workdirs live under Dropbox; a sync grabbing the file makes
    os.replace raise PermissionError [WinError 5] and killed a mid-fleet job
    outright. Retry with backoff rather than losing the run.

    Backoff budget matters. The first version gave up after 5 tries with a
    linear 0.4 s step -- about 4 s total -- and that was not enough once the
    suite put 100 case directories under the same synced folder: B01 lost its
    whole job to a denied rename AFTER every stage had already succeeded.
    Capped exponential backoff now spans ~45 s, and a failure reports what it
    was competing with instead of just re-raising.
    """
    import time
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(m, f, indent=1)
    delay = 0.4
    for i in range(attempts):
        try:
            os.replace(tmp, path)   # atomic: never a half-written manifest
            return
        except PermissionError:
            if i == attempts - 1:
                raise RuntimeError(
                    "could not replace %s after %d attempts over ~%.0f s -- a "
                    "sync client or editor is holding it. The staged content is "
                    "in %s and can be renamed by hand."
                    % (path, attempts, 45.0, tmp))
            time.sleep(delay)
            delay = min(delay * 1.6, 8.0)


def render_speos_config(m, variant_name, out_path):
    """Render the legacy 14-line positional config FROM the manifest.

    This is the whole point: the ordering lives in ONE tested function instead
    of in fifteen hand-edits.
    """
    v = next(v for v in m["variants"] if v["name"] == variant_name)
    wd = m["workdir"]
    slug = m["slug"]
    s = m["sim"]
    mech = os.path.join(wd, slug + v["mechSuffix"])
    lines = [
        path_for(slug, wd, "odx"),                       # 1 ODX
        path_for(slug, wd, "speosdoc"),                  # 2 save doc
        mech,                                            # 3 mechanics
        "%s_%s" % (m["simPrefix"], v["name"]),           # 4 suffix
        m["materials"]["wallBsdf"],                      # 5 wall SOP
        str(m["optics"]["imgZ"]),                        # 6
        str(s["rDisc"]),                                 # 7
        str(s["zCatch"]),                                # 8
        str(s["strayDeg"]),                              # 9
        str(s["zSrc"]),                                  # 10
        str(s["rSrc"]),                                  # 11
        str(s["waveNm"]),                                # 12
        "EDGEBLACK" if v["edgeBlack"] else "NONE",       # 13
        os.path.join(wd, "result-%s_%s.txt" % (m["simPrefix"], v["name"])),  # 14
    ]
    for i, ln in enumerate(lines, 1):
        if ln in (None, "None", ""):
            raise ValueError("config line %d for %s/%s is empty -- the layout "
                             "stage must run first" % (i, slug, variant_name))
    with open(out_path, "w") as f:
        f.write("\n".join(str(x) for x in lines) + "\n")
    return out_path
