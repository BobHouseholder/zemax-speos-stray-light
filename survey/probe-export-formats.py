# probe-export-formats.py -- WHICH formats can the SpaceClaim side write?
#
# probe-speos-export.py established that SpeosSim exposes no exporter (5
# members, none matching save/export/lightbox). What it did find were
# SpaceClaim's own export enums. If one of them is a Speos scene / LightBox,
# the PySpeos hybrid has its bridge. If they are all CAD formats (STEP, STL,
# IGES...), then PySpeos can only be fed raw triangle meshes and the lens's
# per-surface optical materials cannot cross that boundary.
import sys

OUT = r"C:\Users\<user>\Dropbox\Optics\stray-light-loop\survey\probe-export-formats.txt"
lines = []


def note(s):
    lines.append(s)


def enum_values(name):
    note("")
    note("--- %s ---" % name)
    try:
        obj = eval(name)                        # noqa: S307
    except Exception as exc:
        note("   unavailable: %s" % type(exc).__name__)
        return
    vals = [n for n in sorted(dir(obj)) if not n.startswith("_")]
    note("   %d values" % len(vals))
    for v in vals:
        note("     %s" % v)


for n in ("ExportFormatType", "PartExportFormat", "WindowExportFormat",
          "PartWindowExportFormat", "StlExportFormat"):
    enum_values(n)

# SpeosSim's five members, named explicitly -- confirms what the API surface is
note("")
note("--- SpeosSim members ---")
try:
    for n in sorted(dir(SpeosSim)):             # noqa: F821
        note("     %s" % n)
except Exception as exc:
    note("   failed: %s" % exc)

# Does Document.SaveAs accept an arbitrary extension? Record its signature.
note("")
note("--- Document.SaveAs ---")
try:
    note("     %r" % (Document.SaveAs,))        # noqa: F821
except Exception as exc:
    note("     failed: %s" % exc)

with open(OUT, "w") as f:
    f.write("\n".join(lines) + "\n")
