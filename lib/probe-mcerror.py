# probe-mcerror.py -- provoke SWIG's prototype listing with a deliberately
# wrong argument. SWIG raises TypeError with the full C++ signature BEFORE
# executing anything, so this is safe where a blind call crashed (exit 5).
import glob
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import settings  # noqa: E402

SPEOS_BIN = settings.SPEOS_BIN
sys.path.append(SPEOS_BIN)
import IllumineCore_pywrap as core
import IllumineSpeos_pywrap as ips

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XMP = glob.glob(os.path.join(
    BASE, "survey", "systems", "tessar25", "SPEOS output files",
    "tessar25-speos", "SV_Stray_tess_base*.xmp"))[0]
info = ips.COPTKernelResult_CreateXMPFileInfo(ips.COptString(core.String(XMP))).Value()

for m in ("BuildMapRelativeStandardError", "GetValueRelativeStandardError",
          "GetNbPixelXRelativeStandardError", "ExportXmpFileRelativeStandardError"):
    f = getattr(info, m)
    try:
        f("bogus", "bogus", "bogus", "bogus", "bogus")     # wrong types on purpose
        print("%-36s accepted 5 bogus args (?)" % m)
    except TypeError as e:
        s = str(e).replace("\n", " ")
        i = s.find("Possible")
        print("%-36s %s" % (m, (s[i:i + 260] if i >= 0 else s[:200])))
    except Exception as e:
        print("%-36s %s: %s" % (m, type(e).__name__, str(e)[:150]))
