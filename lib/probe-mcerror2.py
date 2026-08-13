# probe-mcerror2.py -- is per-pixel Monte-Carlo error ALREADY in our XMPs?
#
# If it is, every past run gains a native sigma for free and the repeat-run
# convergence machinery (kpi.py's empirical eta) can be retired. If it is not,
# the sensors must be configured to store it and everything must be re-run -
# a very different cost, so establish which BEFORE changing any wiring.
#
# Signatures decoded by probe-mcerror.py (SWIG reports them on a type error):
#   BuildMapRelativeStandardError()          no args
#   GetNbPixelXRelativeStandardError(n)      1 arg
#   GetValueRelativeStandardError(a, b, c)   3 args
import glob
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402

SPEOS_BIN = settings.SPEOS_BIN
sys.path.append(SPEOS_BIN)
import IllumineCore_pywrap as core          # noqa: E402
import IllumineSpeos_pywrap as ips          # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATS = [os.path.join(BASE, "survey", "systems", "tessar25",
                     "SPEOS output files", "tessar25-speos",
                     "SV_Stray_tess_base*.xmp"),
        os.path.join(BASE, "testcases", "cases", "B01",
                     "SPEOS output files", "b01-speos",
                     "SV_F1v_b01_base*.xmp")]


def probe(xmp):
    print("=" * 70)
    print(xmp.rsplit("\\", 1)[-1])
    info = ips.COPTKernelResult_CreateXMPFileInfo(ips.COptString(core.String(xmp))).Value()
    for label, call in (("BuildMapRelativeStandardError()",
                         lambda: info.BuildMapRelativeStandardError()),):
        try:
            print("  %-34s -> %r" % (label, call()))
        except Exception as e:
            print("  %-34s %s: %s" % (label, type(e).__name__, str(e)[:110]))
    for n in (0, 1):
        try:
            print("  GetNbPixelXRelativeStandardError(%d) -> %r"
                  % (n, info.GetNbPixelXRelativeStandardError(n)))
        except Exception as e:
            print("  GetNbPixelXRelativeStandardError(%d) %s: %s"
                  % (n, type(e).__name__, str(e)[:110]))
    # sample a few (a, b, c) combinations - centre-ish pixel of layer 0
    for args in ((0, 0, 0), (0, 1, 1), (0, 10, 10)):
        try:
            print("  GetValueRelativeStandardError%-12s -> %r"
                  % (str(args), info.GetValueRelativeStandardError(*args)))
        except Exception as e:
            print("  GetValueRelativeStandardError%-12s %s: %s"
                  % (str(args), type(e).__name__, str(e)[:110]))


found = 0
for p in PATS:
    for xmp in glob.glob(p):
        probe(xmp)
        found += 1
        break
if not found:
    print("no XMP matched")
print("probe done")
