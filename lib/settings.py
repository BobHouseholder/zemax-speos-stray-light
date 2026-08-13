"""settings.py -- THE machine-specific paths. One definition, imported by all.

Usage:
    python lib/settings.py --check

Everything the pipeline needs falls into exactly two classes, and this module
exists to keep them apart:

  DERIVED   Where the code, the systems and the results live. Always a fixed
            offset from this file, so it is computed, never configured. Editing
            a derived path is always a mistake.

  CONFIGURED  Where Ansys and Python are installed. Cannot be derived from
            anything -- it genuinely differs per machine. Four values, in
            `straylight.toml` at the pipeline root.

Before this module the second class was 46 absolute literals across 40 files,
so moving the pipeline to another machine meant 46 correct edits with no way to
tell you had missed one. The failure mode was never a clear error: a wrong
Speos path launches nothing and leaves an empty log, which reads exactly like a
simulation that found no flux.

THREE RULES, each bought with a specific failure:

1.  FAIL AT IMPORT, NOT AT USE. A configured path that does not exist raises
    here, naming the key and the file to edit. This codebase's signature bug is
    a wrong path surfacing 200 lines downstream as a missing output file.

2.  REPORT EVERY PROBLEM AT ONCE. A user with three stale paths should learn
    that in one run, not three.

3.  NO tomllib. The parser below is hand-written and deliberately minimal
    because this module must import under Ansys's bundled CPython **3.10**,
    which predates tomllib (3.11+). angle_select.py and the .lpf readers run
    only there. A `import tomllib` here would work perfectly under the driver
    interpreter and fail on exactly the scripts that matter most.

NOT USABLE FROM A SPEOS WIRE SCRIPT. Inside Speos, IronPython sets `__file__`
to a GUID, so a wire script cannot locate this module or the TOML beside it.
Those scripts take their paths from environment variables exported by whichever
driver launched them -- see `speos_env()` below and the note in field_slots.py.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.environ.get("SL_CONFIG") or os.path.join(ROOT, "straylight.toml")
EXAMPLE = os.path.join(ROOT, "straylight.toml.example")

# key -> (required, what it is, what it must contain to be the right folder)
KEYS = {
    "ansys_root": (True, "Ansys unified install root",
                   os.path.join("Optical Products", "Speos", "bin")),
    "opticstudio_root": (False, "OpticStudio install directory",
                         "ZOSAPI_NetHelper.dll"),
    "python_exe": (True, "driver CPython interpreter", None),
    "zemax_data": (False, "OpticStudio user-data folder", "Samples"),
}


class SettingsError(Exception):
    """Raised at import when the configuration cannot be trusted."""


def _parse(text):
    """Minimal reader for a flat TOML of `key = "value"` under [tables].

    Handles what straylight.toml actually is and nothing more: comments,
    blank lines, table headers, and double-quoted string values. Anything
    else raises rather than being silently skipped -- a config parser that
    ignores what it does not understand is how a typo becomes a default.
    """
    out = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line or (line.startswith("[") and line.endswith("]")):
            continue
        if "=" not in line:
            raise SettingsError("%s line %d: not `key = \"value\"`: %s"
                                % (CONFIG, n, raw.strip()))
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) < 2 or v[0] != '"' or v[-1] != '"':
            raise SettingsError('%s line %d: value must be double-quoted: %s'
                                % (CONFIG, n, raw.strip()))
        out[k.strip()] = v[1:-1]
    return out


def _load():
    if not os.path.isfile(CONFIG):
        raise SettingsError(
            "no configuration file at\n    %s\n"
            "Copy the example beside it and edit the four paths:\n"
            "    copy \"%s\" \"%s\"\n"
            "then verify with:  python lib\\settings.py --check"
            % (CONFIG, EXAMPLE, CONFIG))
    # utf-8-sig, not utf-8: Notepad and PowerShell's `Out-File -Encoding utf8`
    # both prepend a BOM on Windows, which arrives as a ﻿ glued to the
    # front of `[paths]` and makes line 1 unparseable. Caught by the parser's
    # own failure test, where every case reported a line-1 syntax error
    # instead of the stale path it was meant to be testing.
    with open(CONFIG, encoding="utf-8-sig") as fh:
        raw = _parse(fh.read())

    bad = [k for k in raw if k not in KEYS]
    if bad:
        raise SettingsError(
            "%s: unknown key(s) %s. Known keys: %s"
            % (CONFIG, ", ".join(sorted(bad)), ", ".join(sorted(KEYS))))

    vals, problems = {}, []
    for key, (required, what, marker) in KEYS.items():
        # An environment override exists so a test or a second install can
        # point elsewhere without editing the shared file.
        v = os.environ.get("SL_" + key.upper()) or raw.get(key, "")
        v = os.path.normpath(v) if v else ""
        vals[key] = v
        if not v:
            if required:
                problems.append("  %-17s MISSING   %s -- required"
                                % (key, what))
            continue
        if not os.path.exists(v):
            problems.append("  %-17s NOT FOUND %s\n%s%s"
                            % (key, what, " " * 22, v))
        elif marker and not os.path.exists(os.path.join(v, marker)):
            problems.append(
                "  %-17s WRONG     %s exists but has no %s, so it is not the "
                "%s\n%s%s" % (key, what, marker, what, " " * 22, v))
    if problems:
        raise SettingsError(
            "configuration in %s does not match this machine:\n%s\n"
            "Edit that file, then re-check with:  python lib\\settings.py "
            "--check" % (CONFIG, "\n".join(problems)))
    return vals


def _excepthook(kind, exc, tb):
    """Print a SettingsError as an instruction, not as a stack trace.

    The traceback is noise here: it points at this module's internals when
    the thing that needs fixing is one line of straylight.toml. Only this one
    exception type is intercepted; everything else keeps the default hook,
    because a real crash still needs its trace.
    """
    if isinstance(exc, SettingsError):
        sys.stderr.write("\nCONFIGURATION ERROR\n\n%s\n\n" % exc)
    else:
        sys.__excepthook__(kind, exc, tb)


sys.excepthook = _excepthook

_V = _load()


def _need(key):
    v = _V[key]
    if not v:
        raise SettingsError(
            "this step needs `%s` (%s), which is blank in %s"
            % (key, KEYS[key][1], CONFIG))
    return v


ANSYS_ROOT = _V["ansys_root"]
PYTHON_EXE = _V["python_exe"]

# Derived from ansys_root -- the layout inside an Ansys install is fixed, so
# these are not four more things to configure.
SPEOS_BIN = os.path.join(ANSYS_ROOT, "Optical Products", "Speos", "bin")
SPEOS_LAUNCHER = os.path.join(SPEOS_BIN, "AnsysSpeosLauncher.exe")
ANSYS_PY = os.path.join(ANSYS_ROOT, "commonfiles", "CPython", "3_10",
                        "winx64", "Release", "python", "python.exe")
SPEOS_RPC = os.path.join(ANSYS_ROOT, "Optical Products", "SPEOS_RPC",
                         "SpeosRPC_Server.exe")
# PySpeos identifies an install by bare release digits ("261"), which is the
# configured root's own folder name with the "v" stripped -- derived, so an
# upgrade to v262 needs no second edit.
ANSYS_VERSION = os.path.basename(ANSYS_ROOT).lstrip("vV")


def opticstudio_root():
    """The ZOS-API install dir. Raises by name if OpticStudio is not set up."""
    return _need("opticstudio_root")


def zemax_data():
    """The OpticStudio user-data folder. Raises by name if unset."""
    return _need("zemax_data")


def zemax_samples():
    """`Samples` inside the OpticStudio user-data folder."""
    return os.path.join(_need("zemax_data"), "Samples")


def speos_env(**extra):
    """Environment for launching a Speos wire script.

    A wire script cannot find this module, the TOML, or even its own directory
    -- IronPython inside Speos sets `__file__` to a GUID, so `abspath` returns
    the working directory and the script proceeds confidently down a wrong
    path. Everything it needs must therefore be handed to it here.
    """
    env = dict(os.environ)
    env["SL_SURVEY_DIR"] = os.path.join(ROOT, "survey")
    env["SL_ROOT"] = ROOT
    for k, v in extra.items():
        env[k] = v
    return env


def _check():
    print("pipeline root (derived)   %s" % ROOT)
    print("configuration             %s" % CONFIG)
    print("")
    rows = [("ansys_root", ANSYS_ROOT), ("python_exe", PYTHON_EXE),
            ("opticstudio_root", _V["opticstudio_root"] or "(not set)"),
            ("zemax_data", _V["zemax_data"] or "(not set)"),
            ("  -> speos launcher", SPEOS_LAUNCHER),
            ("  -> speos bin", SPEOS_BIN),
            ("  -> speos rpc server", SPEOS_RPC),
            ("  -> ansys cpython", ANSYS_PY)]
    worst = 0
    for name, p in rows:
        if p.startswith("("):
            mark = "  --"
        elif os.path.exists(p):
            mark = "  ok"
        else:
            mark, worst = "FAIL", 1
        print("%s  %-18s %s" % (mark, name, p))

    # Paths are not the whole story any more. Reading .lpf ray files goes
    # through PySpeos as of 2026-08-09, so a correct straylight.toml on a
    # machine without the package produces a configuration that checks out
    # clean and then fails with ImportError partway through the first
    # analysis. That is the same class of late, misleading failure this
    # module exists to prevent, so the dependency is checked HERE.
    print("")
    for mod, why, pipname in (
            ("ansys.speos.core", "reads .lpf ray files", "ansys-speos-core"),
            ("cadquery", "builds the mechanical barrel", "cadquery"),
    ):
        try:
            __import__(mod)
            mark = "  ok"
        except ImportError:
            mark, worst = "FAIL", 1
        print("%s  %-18s %s (pip install %s)"
              % (mark, mod, why, pipname))

    print("")
    print("CONFIG OK" if not worst else
          "CONFIG BROKEN -- fix the FAIL lines above. A path that does not "
          "exist,\nor a package that will not import, stops the pipeline "
          "later and less clearly.")
    return worst


if __name__ == "__main__":
    sys.exit(_check() if "--check" in sys.argv else _check())
