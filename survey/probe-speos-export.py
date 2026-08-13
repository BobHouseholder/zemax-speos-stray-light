# probe-speos-export.py -- can the SpaceClaim side export a Speos LightBox?
#
# THE question for the PySpeos hybrid. PySpeos can only ingest a scene through
# SceneLink.load_file(), which takes a SpeosLightBox file. The pipeline today
# writes only .scdocx (SpaceClaim documents) via DocumentSave.Execute, and a
# scan of the whole tree finds zero .speos/.spslb/.lightbox files. So either
# the Speos scripting API can export one -- and the hybrid is real -- or it
# cannot, and geometry has to reach PySpeos as raw triangle meshes instead.
#
# Measure, do not guess: this project has shipped a wrong assumption about the
# Speos API more than once. Absolute output path for the usual reason (see
# probe-dunder-file.py): inside Speos nothing can be derived.
import sys

OUT = r"C:\Users\<user>\Dropbox\Optics\stray-light-loop\survey\probe-speos-export.txt"

lines = []


def note(s):
    lines.append(s)


def members(obj, label, pat=None):
    note("")
    note("--- %s ---" % label)
    try:
        names = sorted(dir(obj))
    except Exception as exc:
        note("   dir() failed: %s" % exc)
        return
    hits = [n for n in names
            if pat is None or any(t in n.lower() for t in pat)]
    note("   %d members, %d matching" % (len(names), len(hits)))
    for n in hits:
        note("     %s" % n)


PAT = ("save", "export", "lightbox", "light_box", "speos", "write", "pack",
       "bundle", "scene")

try:
    note("SpeosSim present: %s" % ("SpeosSim" in dir()))
    members(SpeosSim, "SpeosSim", PAT)          # noqa: F821
except Exception as exc:
    note("SpeosSim unavailable: %s: %s" % (type(exc).__name__, exc))

# The document/application level is where a "save as" would live if it is not
# on SpeosSim itself.
for name in ("DocumentSave", "DocumentInsert", "DocumentExport", "Document"):
    try:
        members(eval(name), name, PAT)          # noqa: S307
    except Exception as exc:
        note("")
        note("--- %s --- unavailable: %s" % (name, type(exc).__name__))

# Anything at global scope whose name smells like an exporter.
try:
    g = sorted(n for n in dir()
               if any(t in n.lower() for t in ("export", "lightbox", "save")))
    note("")
    note("--- globals matching export/lightbox/save ---")
    for n in g:
        note("     %s" % n)
except Exception as exc:
    note("globals scan failed: %s" % exc)

with open(OUT, "w") as f:
    f.write("\n".join(lines) + "\n")
