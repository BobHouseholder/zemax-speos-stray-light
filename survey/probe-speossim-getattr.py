# probe-speossim-getattr.py -- enumerate SpeosSim PROPERLY.
#
# probe-speos-export.py concluded "SpeosSim has no exporter" from dir(), and
# that conclusion was unsound. dir(SpeosSim) returns 5 names -- Command,
# InverseSimulationSettings, Options, SimulationSettings, Specific -- and NONE
# of them is ComponentOpticStudio, Material, SensorIrradiance,
# SimulationDirect or SourceSurface, all of which the wire scripts call
# successfully on every run. So IronPython resolves .NET namespace members
# LAZILY and dir() under-reports. Any "the API lacks X" claim built on dir()
# is worthless.
#
# Two sound methods instead:
#   1. getattr() by name -- a lazily-resolved member answers even when unlisted
#   2. CLR reflection over loaded assemblies -- the authoritative type list
#
# The controls matter as much as the candidates: if getattr finds the five
# known-good classes that dir() missed, the method is validated and a negative
# result on the exporter names means something.
import sys

OUT = r"C:\Users\<user>\Dropbox\Optics\stray-light-loop\survey\probe-speossim-getattr.txt"
lines = []


def note(s):
    lines.append(s)


CONTROLS = ["ComponentOpticStudio", "Material", "SensorIrradiance",
            "SimulationDirect", "SourceSurface"]
CANDIDATES = ["SaveLightBox", "ExportLightBox", "LightBox", "SpeosLightBox",
              "Export", "Save", "SaveAs", "ExportScene", "Scene", "Project",
              "SpeosProject", "Pack", "Bundle", "ExportSpeos", "SpeosExport",
              "LightBoxExport", "SaveSpeos", "SpeosFile", "Simulation",
              "Sensor", "Source", "Job", "Compute", "Solve"]

note("=== 1. dir(SpeosSim) -- the unreliable view ===")
try:
    note("   " + ", ".join(sorted(dir(SpeosSim))))          # noqa: F821
except Exception as exc:
    note("   failed: %s" % exc)

note("")
note("=== 2. getattr CONTROLS (known to work; dir() does not list them) ===")
for n in CONTROLS:
    try:
        o = getattr(SpeosSim, n)                            # noqa: F821
        note("   %-24s FOUND  %r" % (n, o))
    except Exception as exc:
        note("   %-24s absent (%s)" % (n, type(exc).__name__))

note("")
note("=== 3. getattr CANDIDATES (does an exporter exist?) ===")
for n in CANDIDATES:
    try:
        o = getattr(SpeosSim, n)                            # noqa: F821
        note("   %-24s FOUND  %r" % (n, o))
    except Exception:
        pass                        # only report what exists
note("   (only found names listed)")

note("")
note("=== 4. walk the 5 members dir() DID report ===")
for n in ("Command", "Options", "Specific", "SimulationSettings",
          "InverseSimulationSettings"):
    try:
        o = getattr(SpeosSim, n)                            # noqa: F821
        subs = [s for s in sorted(dir(o)) if not s.startswith("_")]
        hits = [s for s in subs
                if any(t in s.lower() for t in
                       ("save", "export", "lightbox", "scene", "write", "pack"))]
        note("   %-26s %3d members, save/export-ish: %s"
             % (n, len(subs), hits if hits else "none"))
    except Exception as exc:
        note("   %-26s failed: %s" % (n, type(exc).__name__))

note("")
note("=== 5. CLR reflection -- the authoritative type list ===")
try:
    import clr                                              # noqa: F401
    from System import AppDomain
    found = []
    for asm in AppDomain.CurrentDomain.GetAssemblies():
        try:
            for t in asm.GetTypes():
                ns = t.Namespace or ""
                if "speos" in ns.lower() or "speos" in (t.Name or "").lower():
                    found.append("%s.%s" % (ns, t.Name))
        except Exception:
            continue
    note("   %d Speos-namespaced types visible" % len(found))
    interesting = sorted(set(x for x in found
                             if any(t in x.lower() for t in
                                    ("save", "export", "lightbox", "scene",
                                     "pack", "bundle", "write"))))
    note("   matching save/export/lightbox/scene: %d" % len(interesting))
    for x in interesting[:40]:
        note("     %s" % x)
    note("")
    note("   -- a sample of all Speos types, for calibration --")
    for x in sorted(set(found))[:30]:
        note("     %s" % x)
except Exception as exc:
    note("   reflection failed: %s: %s" % (type(exc).__name__, exc))

with open(OUT, "w") as f:
    f.write("\n".join(lines) + "\n")
