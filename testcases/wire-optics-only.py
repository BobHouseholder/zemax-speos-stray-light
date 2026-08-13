# wire-optics-only.py -- in-field flux with NO MECHANICS AT ALL.
#
# Settles B01: the seated barrel takes corner relative illumination from 7.6%
# (naive tube) to 2.6%. Either the barrel vignettes, or the naive tube was
# delivering non-imaging light to the detector and the barrel correctly removes
# it. The arbiter is the flux with lens bodies only -- no barrel, no housing,
# no vane, nothing to scatter off.
#
#   near 2.6%  -> the barrel is right and the baseline was inflated by stray
#   near 7.6%  -> a real vignetting bug remains to be found
#
# Measured in SPEOS rather than via OpticStudio's RELI so the comparison is
# like-for-like with the numbers it is being compared against (and because the
# OpticStudio licence seat has been unreliable).
#
# Same 14-line config as wire-survey.py.
import traceback
import sys

import os

SURVEY_DIR = os.environ.get("SL_SURVEY_DIR")
if not SURVEY_DIR:
    raise SystemExit(
        "FATAL: SL_SURVEY_DIR is not set. This script is launched by the\n"
        "pipeline, which exports it; it cannot be run directly.")
if SURVEY_DIR not in sys.path:
    sys.path.append(SURVEY_DIR)
from field_slots import field_slots   # THE slot mapping - never re-derive it

# Per-run config, exported by the caller as SL_SURVEY_CONFIG.
# See wire-survey.py for why.
# No fallback path: inside Speos, IronPython sets __file__ to a GUID, so
# this script cannot locate a config file on its own. The driver that
# launches it exports this -- see speos_env() in lib/settings.py.
CFG = os.environ.get("SL_SURVEY_CONFIG")
if not CFG:
    raise SystemExit(
        "FATAL: SL_SURVEY_CONFIG is not set. This script is launched by the\n"
        "pipeline, which exports it; it cannot be run directly.")

f = open(CFG)
ODX = f.readline().strip()
SAVE = f.readline().strip()
MECH_STEP = f.readline().strip()          # deliberately NOT inserted
SFX = f.readline().strip()
WALL_SOP = f.readline().strip()
Z_IMG = float(f.readline().strip())
R_DISC = float(f.readline().strip())
Z_CATCH = float(f.readline().strip())
STRAY_DEG = float(f.readline().strip())
Z_SRC = float(f.readline().strip())
R_SRC = float(f.readline().strip())
WAVE = float(f.readline().strip())
EDGE_BLACK = f.readline().strip().upper() == "EDGEBLACK"
LOG = f.readline().strip()
f.close()
LOG = LOG.replace(".txt", "-opticsonly.txt")

lines = []


def safe_str(s):
    try:
        s = str(s)
    except Exception:
        s = "<unprintable>"
    return "".join(c for c in s if c == "\n" or c == "\t" or ord(c) >= 32)


def log(s):
    lines.append(safe_str(s))


try:
    log("wire-optics-only start: odx=%s sfx=%s  (NO mechanics inserted)" % (ODX, SFX))
    DocumentSave.Execute(SAVE)

    odx = SpeosSim.ComponentOpticStudio.Create()
    odx.ComponentFile = ODX
    odx.Compute()
    log("ODX Compute: StatusInfo=[%s]" % odx.StatusInfo)

    det = odx.Detectors.Item[0]
    irr = None
    for g in SpeosSim.SensorIrradiance.FromSelection([det]):
        irr = g
    irr.SensorType = SpeosSim.SensorIrradiance.EnumSensorType.Radiometric
    irr.LayerType = getattr(SpeosSim.SensorIrradiance.EnumLayerType, "None")

    imported_sources = [odx.Sources.Item[i] for i in range(odx.Sources.Count)]
    log("imported sources: %d" % len(imported_sources))

    all_bodies = list(GetRootPart().GetAllBodies())
    lens_bodies = [b for b in all_bodies
                   if b.GetName().startswith("Lens_") or b.GetName().startswith("Stop_")]
    src_bodies = dict((b.GetName(), b) for b in all_bodies
                      if b.GetName().startswith("Source_"))
    log("lens bodies=%d  source discs=%d  (mech bodies: 0 by design)"
        % (len(lens_bodies), len(src_bodies)))

    def set_sensors(sim, sensor):
        try:
            sim.Sensors.Set([sensor])
        except Exception:
            try:
                sim.Sensors.Set(sensor)
            except Exception:
                sim.Sensors.Add(sensor)

    # One source body per sim: the imported per-field discs overlap and would
    # otherwise produce volume-conflict error rays.
    # THE DENOMINATOR MUST BE THE SAME FIELD AS THE NUMERATOR. This loop used
    # `range(min(3, len(imported_sources)))` - copied from the pre-2026-07-26
    # wire-survey.py - while wire-survey picks axial/middle/MAX. On any system
    # with more than three fields the two disagreed, so transmission divided
    # the 25 deg flux by the 17.5 deg flux and invented an obstruction that
    # survived six eliminated hypotheses. Import the mapping; never restate it.
    nsrc = len(imported_sources)
    field_idx = field_slots(nsrc)
    log("optics-only runs: source indices %s of %d (must match wire-survey)"
        % ([i + 1 for i in field_idx], nsrc))
    for slot, i in enumerate(field_idx):
        geo = list(lens_bodies)
        bn = "Source_%d" % (i + 1)
        if bn in src_bodies:
            geo.append(src_bodies[bn])
        sim = SpeosSim.SimulationDirect.Create()
        sim.Name = "OO_F%dv_%s" % (slot + 1, SFX)
        log("  OO_F%dv_%s <- imported field %d of %d" % (slot + 1, SFX, i + 1, nsrc))
        sim.HasRayNbLimit = True
        sim.NbRays = 200000
        sim.Geometries.Set(geo)
        sim.Sources.Set([imported_sources[i].Subject
                         if hasattr(imported_sources[i], "Subject")
                         else imported_sources[i]])
        set_sensors(sim, irr)
        sim.Compute()
        log("OO_F%dv_%s computed. StatusInfo=[%s]" % (slot + 1, SFX, sim.StatusInfo))
        for p in sim.GetResultFilePaths():
            log("  result: %s" % p)

    log("wire-optics-only end")
except Exception:
    log("FATAL\n" + traceback.format_exc())

out = open(LOG, "w")
out.write("\n".join(lines))
out.close()
