# wire-survey-pst.py -- generic PST(theta) sweep for a survey system.
#
# Generalises cooke/wire-cooke-pst.py (which is Cooke-hardcoded). One
# collimated disc per angle, one sim each, ALL IN ONE SPEOS SESSION so the
# ~40 s startup is paid once rather than per angle.
#
# Exists to test the inverse-trace angle selector against forward measurement
# on a system where the selector and the old heuristic DISAGREE.
#
# Config (survey/pst-config.txt), one item per line:
#   1 ODX  2 SAVE  3 MECH step  4 suffix  5 wall SOP
#   6 R_SRC  7 Z_SRC  8 WAVE  9 angle tokens (comma; letter suffix = repeat)
#   10 EDGEBLACK|NONE   11 LOG
#
# EDGEBLACK is not optional decoration. The pipeline's `redesign` variant is
# seated barrel PLUS edge blackening; a sweep that omits it measures a
# DIFFERENT MECHANICAL DESIGN. Omitting it here made a re-measurement of B15 at
# its own published angle return -89.3% against a published -98.9%, because the
# un-blackened lens rims passed ~10x more stray light. Any comparison against a
# published figure must set this to match the variant it claims to reproduce.
import math
import os
import traceback

# The config path is per-run, taken from the environment. It used to be this
# one hardcoded file, which made it a GLOBAL MUTABLE shared by six drivers
# (band-at-correct-angle, blast-top, test-grazing, discriminate-grazing,
# run-pst, confirm-angle). On 2026-08-04 two of them ran concurrently and the
# collision fired: the band driver launched a sweep for A01 and the Speos
# child read a wideangle32 config written 40 s later by the other driver, so
# an A01 measurement executed wideangle32's geometry and wrote into
# wideangle32's output folder. Nothing raised; the band simply recorded
# "no flux" for A01's low band and carried on. Each driver now writes its own
# file and names it here.
# No fallback path: inside Speos, IronPython sets __file__ to a GUID, so
# this script cannot locate a config file on its own. The driver that
# launches it exports this -- see speos_env() in lib/settings.py.
CFG = os.environ.get("SL_PST_CONFIG")
if not CFG:
    raise SystemExit(
        "FATAL: SL_PST_CONFIG is not set. This script is launched by the\n"
        "pipeline, which exports it; it cannot be run directly.")

f = open(CFG)
ODX = f.readline().strip()
SAVE = f.readline().strip()
MECH_STEP = f.readline().strip()
SFX = f.readline().strip()
WALL_SOP = f.readline().strip()
R_SRC = float(f.readline().strip())
Z_SRC = float(f.readline().strip())
WAVE = float(f.readline().strip())
TOKENS = [t.strip() for t in f.readline().strip().split(",") if t.strip()]
EDGE_BLACK = f.readline().strip().upper() == "EDGEBLACK"
LOG = f.readline().strip()
f.close()

NRAYS = 500000

lines = []


def safe_str(s):
    try:
        s = str(s)
    except Exception:
        s = "<unprintable>"
    return "".join(c for c in s if c == "\n" or c == "\t" or ord(c) >= 32)


def log(s):
    lines.append(safe_str(s))


def token_angle(tok):
    return float("".join(c for c in tok if c.isdigit() or c == "."))


# --- status assertions ------------------------------------------------------
# StatusInfo was LOGGED and never tested, which is the same defect B25 taught
# this pipeline on the ODX side ("Property Detectors is not defined", five
# stages and four minutes downstream of the real cause). On the SIM side it is
# worse than a wasted run: a failed sim writes no Report.html, so collect_curve
# simply does not see that angle. A `17,17b` pair whose primary fails silently
# degrades to a ONE-SAMPLE measurement whose repeat-pair noise floor is then
# reported as 0.0 -- a confident number built from half the data with its own
# error check switched off. That is exactly how A01 produced "+24.8%" on
# 2026-08-04 while every one of its Ansys OPTIS HPC licence checkouts was
# returning 404.
sim_errors = []


def status_error(what, status):
    """Record a non-empty StatusInfo carrying an error. Returns True if bad."""
    s = safe_str(status).strip()
    if not s or "error" not in s.lower():
        return False
    first = [ln for ln in s.splitlines() if ln.strip()]
    sim_errors.append("%s: %s" % (what, first[0] if first else s))
    log("  !! STATUS ERROR on %s" % what)
    return True


try:
    log("wire-survey-pst start: sfx=%s angles=%s" % (SFX, ",".join(TOKENS)))
    DocumentSave.Execute(SAVE)

    odx = SpeosSim.ComponentOpticStudio.Create()
    odx.ComponentFile = ODX
    odx.Compute()
    log("ODX Compute: StatusInfo=[%s]" % odx.StatusInfo)
    status_error("ODX Compute", odx.StatusInfo)

    det = odx.Detectors.Item[0]
    irr = None
    for g in SpeosSim.SensorIrradiance.FromSelection([det]):
        irr = g
    irr.SensorType = SpeosSim.SensorIrradiance.EnumSensorType.Radiometric
    irr.LayerType = getattr(SpeosSim.SensorIrradiance.EnumLayerType, "None")

    # --- one collimated disc per angle -- sketch ONE body at a time and rename
    # immediately; batched sketches come back in arbitrary order (gotcha 12).
    def sketch_disc(name, origin, dx, dy, radius):
        prev = set(id(b) for b in GetRootPart().GetAllBodies())
        pl = Plane.Create(Frame.Create(origin, dx, dy))
        ViewHelper.SetSketchPlane(pl)
        SketchCircle.Create(Point2D.Create(MM(0), MM(0)), MM(radius))
        ViewHelper.SetViewMode(InteractionMode.Solid)
        fresh = [b for b in GetRootPart().GetAllBodies() if id(b) not in prev]
        if len(fresh) != 1:
            fresh = [b for b in GetRootPart().GetAllBodies()
                     if b.GetName() == "Surface"]
        fresh[0].SetName(name)
        return fresh[0]

    for tok in TOKENS:
        th = math.radians(token_angle(tok))
        y0 = abs(Z_SRC) * math.tan(th)
        sketch_disc("StrayDisc_" + tok,
                    Point.Create(MM(0), MM(y0), MM(Z_SRC)),
                    Direction.DirX,
                    Direction.Create(0, math.cos(th), math.sin(th)), R_SRC)

    by_name = dict((b.GetName(), b) for b in GetRootPart().GetAllBodies())
    discs = dict((t, by_name["StrayDisc_" + t]) for t in TOKENS)
    log("discs resolved by name: %d" % len(discs))

    n1 = len(list(GetRootPart().GetAllBodies()))
    DocumentInsert.Execute(MECH_STEP)
    mech = list(GetRootPart().GetAllBodies())[n1:]
    log("mech bodies: %d" % len(mech))

    sources = {}
    for tok in TOKENS:
        src = SpeosSim.SourceSurface.Create()
        src.Name = "Stray_" + tok
        src.EmissiveFaces.Set(discs[tok].Faces[0])
        src.FluxType = SpeosSim.SourceSurface.EnumFluxType.RadiantFlux
        src.FluxValueRadiant = 1.0
        src.IntensityType = SpeosSim.SourceSurface.EnumIntensityType.Lambertian
        try:
            src.IntensityTotalAngle = 0.0      # collimated
        except Exception:
            src.IntensityTotalAngle = 0.5
        src.SpectrumType = SpeosSim.SourceSurface.EnumSpectrumType.Monochromatic
        src.SpectrumValueWavelength = WAVE
        sources[tok] = src

    mat = SpeosSim.Material.Create()
    mat.Name = "DiscAbsorber"
    mat.OpticalPropertiesType = SpeosSim.Material.EnumOpticalPropertiesType.Surfacic
    mat.SOPType = SpeosSim.Material.EnumSOPType.Mirror
    mat.SOPReflectance = 0.0
    mat.OrientedFaces.Set([discs[t] for t in TOKENS])

    blk = SpeosSim.Material.Create()
    blk.Name = "BlackMech"
    blk.OpticalPropertiesType = SpeosSim.Material.EnumOpticalPropertiesType.Volumic
    blk.VOPType = SpeosSim.Material.EnumVOPType.Opaque
    # Must honour a MIRROR<n> token, exactly as wire-survey.py does. Assigning
    # WALL_SOP as a library path unconditionally makes every sim fail with
    # "File not found: MIRROR0" -- which is documented in a comment in
    # wire-survey.py and was reproduced here anyway when only the EDGE-BLACK
    # branch got the handling. MIRROR0 (perfectly absorbing) is the control
    # that isolates wall scatter, so this path is load-bearing.
    if WALL_SOP.upper().startswith("MIRROR"):
        try:
            _wr = float(WALL_SOP[6:] or "5")
        except ValueError:
            _wr = 5.0
        blk.SOPType = SpeosSim.Material.EnumSOPType.Mirror
        blk.SOPReflectance = _wr
        log("wall SOP: Mirror %.1f%%" % _wr)
    else:
        blk.SOPType = SpeosSim.Material.EnumSOPType.Library
        blk.SOPLibrary = WALL_SOP
    blk.VolumeGeometries.Set(mech)
    log("materials assigned; BlackMech StatusInfo=[%s]" % blk.StatusInfo)

    all_bodies = list(GetRootPart().GetAllBodies())
    lens_bodies = [b for b in all_bodies
                   if b.GetName().startswith("Lens_")
                   or b.GetName().startswith("Stop_")]
    log("lens bodies: %d" % len(lens_bodies))

    # --- edge blackening ----------------------------------------------------
    # Ported verbatim from wire-survey.py so the two cannot diverge in what
    # "redesign" means. Cylindrical faces of lens bodies ARE the element rims;
    # blackening them removes rim TIR, which on the Cooke was 61% of the
    # residual stray.
    if EDGE_BLACK:
        rim_faces = []
        for b in all_bodies:
            if not b.GetName().startswith("Lens_"):
                continue
            for fc in b.Faces:
                try:
                    gname = fc.Shape.Geometry.GetType().Name
                except Exception:
                    gname = "?"
                if gname == "Cylinder":
                    rim_faces.append(fc)
        if rim_faces:
            eb = SpeosSim.Material.Create()
            eb.Name = "BlackEdges"
            eb.OpticalPropertiesType = \
                SpeosSim.Material.EnumOpticalPropertiesType.Surfacic
            # Must honour a MIRROR<n> token exactly as the wall material does,
            # or a non-file token makes every sim fail with "File not found".
            if WALL_SOP.upper().startswith("MIRROR"):
                try:
                    _r = float(WALL_SOP[6:] or "5")
                except ValueError:
                    _r = 5.0
                eb.SOPType = SpeosSim.Material.EnumSOPType.Mirror
                eb.SOPReflectance = _r
            else:
                eb.SOPType = SpeosSim.Material.EnumSOPType.Library
                eb.SOPLibrary = WALL_SOP
            eb.OrientedFaces.Set(rim_faces)
            log("EDGE BLACK: BSDF on %d rim faces; StatusInfo=[%s]"
                % (len(rim_faces), eb.StatusInfo))
        else:
            log("EDGE BLACK: no cylindrical rim faces found")
    else:
        log("edge blackening OFF (baseline variant)")

    def set_sensors(sim, sensor):
        try:
            sim.Sensors.Set([sensor])
        except Exception:
            try:
                sim.Sensors.Set(sensor)
            except Exception:
                sim.Sensors.Add(sensor)

    for tok in TOKENS:
        sim = SpeosSim.SimulationDirect.Create()
        sim.Name = "PST%s_%s" % (tok, SFX)
        sim.HasRayNbLimit = True
        sim.NbRays = NRAYS
        # ONE source body per sim: overlapping discs cause volume conflicts.
        sim.Geometries.Set(lens_bodies + mech + [discs[tok]])
        sim.Sources.Set([sources[tok].Subject])
        set_sensors(sim, irr)
        sim.Compute()
        log("PST%s_%s computed. StatusInfo=[%s]" % (tok, SFX, sim.StatusInfo))
        status_error("PST%s_%s" % (tok, SFX), sim.StatusInfo)
        for p in sim.GetResultFilePaths():
            log("  result: %s" % p)

    log("wire-survey-pst end")
except Exception:
    log("FATAL\n" + traceback.format_exc())
    sim_errors.append("FATAL: unhandled exception")

# Machine-readable verdict on the LAST line the reader looks for. A driver
# must be able to tell "this sweep is incomplete" from the log alone, without
# re-deriving it from which report files happen to exist.
if sim_errors:
    log("SWEEP INCOMPLETE -- %d failed" % len(sim_errors))
    for e in sim_errors:
        log("  SIMERROR %s" % e)
else:
    log("SWEEP OK -- %d sims, 0 failed" % len(TOKENS))

out = open(LOG, "w")
out.write("\n".join(lines))
out.close()
