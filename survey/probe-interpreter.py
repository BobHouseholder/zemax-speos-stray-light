# probe-interpreter.py -- WHICH Python actually runs a Speos /RunScript= script?
#
# The distinction matters for everything else in this tree: IronPython 2.7
# embedded in SpaceClaim has no pip, no C extensions, no f-strings and no
# pathlib, whereas PySpeos (ansys-speos-core) is an ordinary CPython gRPC
# client with none of those limits. Guessing which one you are in is how you
# write code that fails only in production.
#
# Sibling of probe-dunder-file.py, and the absolute output path below is
# correct for the same reason: this runs where nothing can be derived.
import sys

OUT = r"C:\Users\<user>\Dropbox\Optics\stray-light-loop\survey\probe-interpreter.txt"

lines = []


def note(k, v):
    lines.append("%-24s %s" % (k, v))


note("sys.version", repr(getattr(sys, "version", "?")))
note("sys.platform", repr(getattr(sys, "platform", "?")))
note("sys.executable", repr(getattr(sys, "executable", "?")))
note("sys.version_info", repr(tuple(sys.version_info)))
note("sys.implementation", repr(getattr(sys, "implementation", "ABSENT (py<3.3)")))
note("sys.winver", repr(getattr(sys, "winver", "ABSENT")))

# The decisive tells. `clr` imports only under .NET-hosted Python; `maxint`
# exists only in Python 2.
for mod in ("clr", "System"):
    try:
        __import__(mod)
        note("import %s" % mod, "OK -- .NET host")
    except Exception as exc:                                  # noqa: BLE001
        note("import %s" % mod, "fails (%s)" % type(exc).__name__)

note("sys.maxint (py2 only)", repr(getattr(sys, "maxint", "ABSENT -- py3")))

# Is the modern gRPC client reachable from in here?
for mod in ("ansys.speos.core", "grpc"):
    try:
        __import__(mod)
        note("import %s" % mod, "OK")
    except Exception as exc:                                  # noqa: BLE001
        note("import %s" % mod, "fails (%s)" % type(exc).__name__)

# What the SpaceClaim host injects without any import at all.
for name in ("GetRootPart", "DocumentSave", "Point", "SpaceClaim"):
    note("global %s" % name, name in dir(__builtins__) or name in globals())

with open(OUT, "w") as f:
    f.write("\n".join(lines) + "\n")
