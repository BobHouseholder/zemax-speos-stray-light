# wire-survey.py — generic Speos quantification for a survey system.
# Config survey-config.txt (one value per line):
#   1 ODX path            2 SAVE .scdocx        3 mech STEP
#   4 suffix              5 wall SOP (path|MIRROR5)
#   6 zImg  7 rDisc  8 zCatch  9 strayDeg  10 zSrc  11 rSrc  12 wave
#   13 EDGEBLACK|NONE     14 result log path
# Sims: SV_Stray_<sfx> (1M) + SV_F<i>v_<sfx> (200k, per imported field) +
# SV_Back_<sfx> (300k, LXP).
import traceback
import math
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
# survey-config.txt is READ by seven wire scripts and WRITTEN
# by runner.py and two drivers -- a global mutable of exactly the kind that
# crossed an A01 PST sweep with a wideangle32 config on 2026-08-04.
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
MECH_STEP = f.readline().strip()
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

lines = []
def safe_str(s):
    try:
        s = str(s)
    except Exception:
        try:
            s = repr(s)
        except Exception:
            s = "<unprintable>"
    return "".join(c for c in s if c == "\n" or c == "\t" or ord(c) >= 32)

def log(s):
    lines.append(safe_str(s))

try:
    log("wire-survey start: odx=%s mech=%s sfx=%s edge=%s" % (ODX, MECH_STEP, SFX, EDGE_BLACK))
    DocumentSave.Execute(SAVE)

    odx = SpeosSim.ComponentOpticStudio.Create()
    odx.ComponentFile = ODX
    odx.Compute()
    log("ODX Compute: StatusInfo=[%s]" % odx.StatusInfo)
    # LOGGING IS NOT CHECKING. B25 on 2026-07-26 reported
    #   StatusInfo=[Error: Speos  Surface clear radius too large. ...]
    # and this code sailed past it into odx.Detectors.Item[0], which threw
    # "Property 'Detectors' (Sensors) is not defined for object 'Optical
    # Design Exchange.1' in its current state" -- an error that names neither
    # the surface nor the cause, while the real diagnosis sat one line above
    # in a string nobody tested. Same shape as "outputs exist is not stage
    # succeeded": a status field you print but never assert on is decoration.
    _status = "%s" % odx.StatusInfo
    if "Error" in _status or "error" in _status:
        raise RuntimeError(
            "ODX import FAILED - Speos reported: %s\n"
            "The detector and sources below do not exist when the import "
            "fails, so every later step would misreport the cause." % _status)

    det = odx.Detectors.Item[0]
    irr = None
    for g in SpeosSim.SensorIrradiance.FromSelection([det]):
        irr = g
    irr.SensorType = SpeosSim.SensorIrradiance.EnumSensorType.Radiometric
    irr.LayerType = getattr(SpeosSim.SensorIrradiance.EnumLayerType, "None")
    imported_sources = [odx.Sources.Item[i] for i in range(odx.Sources.Count)]
    log("imported sources: %d" % len(imported_sources))

    def sketch_disc(name, frame_origin, dir_x, dir_y, radius):
        prev = set(id(b) for b in GetRootPart().GetAllBodies())
        pl = Plane.Create(Frame.Create(frame_origin, dir_x, dir_y))
        ViewHelper.SetSketchPlane(pl)
        SketchCircle.Create(Point2D.Create(MM(0), MM(0)), MM(radius))
        ViewHelper.SetViewMode(InteractionMode.Solid)
        fresh = [b for b in GetRootPart().GetAllBodies() if id(b) not in prev]
        if len(fresh) != 1:
            fresh = [b for b in GetRootPart().GetAllBodies()
                     if b.GetName() == "Surface"]
        body = fresh[0]
        body.SetName(name)
        log("%s sketched (candidates=%d)" % (name, len(fresh)))
        return body

    back_disc = sketch_disc("ImageDisc",
                            Point.Create(MM(0), MM(0), MM(Z_IMG)),
                            Direction.DirX, Direction.Create(0, -1, 0), R_DISC)
    th = math.radians(STRAY_DEG)
    # Stray source standoff.
    #
    # The original form put the disc on the PLANE z = Z_SRC at
    # y = |Z_SRC|*tan(th). That is algebraically identical to a POLAR
    # placement at radius |Z_SRC|/cos(th) -- and it inherits tan's blow-up:
    # 457 mm off-axis at 85 deg, and a SIGN FLIP past 90 deg.
    #
    # wideanglelen100 is a 200 deg FOV lens, so its out-of-field angle is a
    # legitimate 106 deg. The plane form put the disc at y = -139 mm, on the
    # far side of the axis, with its normal (DirX x dir_y = (0,-sin,cos))
    # pointing AWAY from the lens. Collimated (IntensityTotalAngle = 0), not
    # one ray ever reached the system: stray flux was exactly 0 W in BOTH
    # variants while the in-field source read a healthy 0.2257 W.
    #
    # Polar placement is well defined for every angle. Below the clamp it
    # reproduces the old point bit-for-bit -- R*sin = |Z|tan and -R*cos =
    # Z_SRC -- so no existing result moves. Beyond it the plane form has no
    # solution at all, and a fixed standoff is used instead.
    TH_MAX = math.radians(85.0)
    r_stand = abs(Z_SRC) / math.cos(th) if th <= TH_MAX else abs(Z_SRC)
    sy, cy = math.sin(th), math.cos(th)
    stray_disc = sketch_disc("StrayDisc",
                             Point.Create(MM(0), MM(r_stand * sy),
                                          MM(-r_stand * cy)),
                             Direction.DirX,
                             Direction.Create(0, cy, sy), R_SRC)

    plane2 = Plane.Create(Frame.Create(
        Point.Create(MM(0), MM(0), MM(Z_CATCH)),
        Direction.DirX, Direction.DirY))
    ViewHelper.SetSketchPlane(plane2)
    SketchPoint.Create(Point2D.Create(MM(0), MM(0)))
    SketchLine.Create(Point2D.Create(MM(0), MM(0)), Point2D.Create(MM(10), MM(0)))
    ViewHelper.SetViewMode(InteractionMode.Solid)
    back_disc = [b for b in GetRootPart().GetAllBodies()
                 if b.GetName() == "ImageDisc"][0]
    stray_disc = [b for b in GetRootPart().GetAllBodies()
                  if b.GetName() == "StrayDisc"][0]
    log("discs resolved by name")

    n1 = len(list(GetRootPart().GetAllBodies()))
    DocumentInsert.Execute(MECH_STEP)
    mech = list(GetRootPart().GetAllBodies())[n1:]
    log("mech bodies: %d" % len(mech))
    curves = list(GetRootPart().Curves)

    back_src = SpeosSim.SourceSurface.Create()
    back_src.Name = "BackSource"
    back_src.EmissiveFaces.Set(back_disc.Faces[0])
    back_src.FluxType = SpeosSim.SourceSurface.EnumFluxType.RadiantFlux
    back_src.FluxValueRadiant = 1.0
    back_src.IntensityType = SpeosSim.SourceSurface.EnumIntensityType.Lambertian
    back_src.IntensityTotalAngle = 180.0
    back_src.SpectrumType = SpeosSim.SourceSurface.EnumSpectrumType.Monochromatic
    back_src.SpectrumValueWavelength = WAVE
    log("BackSource StatusInfo=[%s]" % back_src.StatusInfo)

    stray_src = SpeosSim.SourceSurface.Create()
    stray_src.Name = "StraySrc"
    stray_src.EmissiveFaces.Set(stray_disc.Faces[0])
    stray_src.FluxType = SpeosSim.SourceSurface.EnumFluxType.RadiantFlux
    stray_src.FluxValueRadiant = 1.0
    stray_src.IntensityType = SpeosSim.SourceSurface.EnumIntensityType.Lambertian
    try:
        stray_src.IntensityTotalAngle = 0.0
    except Exception:
        stray_src.IntensityTotalAngle = 0.5
    stray_src.SpectrumType = SpeosSim.SourceSurface.EnumSpectrumType.Monochromatic
    stray_src.SpectrumValueWavelength = WAVE
    log("StraySrc StatusInfo=[%s]" % stray_src.StatusInfo)

    mat = SpeosSim.Material.Create()
    mat.Name = "DiscAbsorber"
    mat.OpticalPropertiesType = SpeosSim.Material.EnumOpticalPropertiesType.Surfacic
    mat.SOPType = SpeosSim.Material.EnumSOPType.Mirror
    mat.SOPReflectance = 0.0
    mat.OrientedFaces.Set([back_disc, stray_disc])
    blk = SpeosSim.Material.Create()
    blk.Name = "BlackMech"
    blk.OpticalPropertiesType = SpeosSim.Material.EnumOpticalPropertiesType.Volumic
    blk.VOPType = SpeosSim.Material.EnumVOPType.Opaque
    # MIRROR<n> = specular walls at n% reflectance. Generalised from a hardcoded
    # MIRROR5 on 2026-07-27 so that MIRROR0 - fully absorbing walls - is a
    # first-class option. That is the null experiment for any "the mechanics
    # changed the flux" claim: with zero wall reflectance the mechanics can only
    # BLOCK, never add, so it separates obstruction from wall scatter.
    if WALL_SOP.upper().startswith("MIRROR"):
        try:
            refl = float(WALL_SOP[6:] or "5")
        except ValueError:
            refl = 5.0
        blk.SOPType = SpeosSim.Material.EnumSOPType.Mirror
        blk.SOPReflectance = refl
        log("wall SOP: Mirror %g%%" % refl)
    else:
        blk.SOPType = SpeosSim.Material.EnumSOPType.Library
        blk.SOPLibrary = WALL_SOP
        log("wall SOP: Library=%s" % WALL_SOP)
    blk.VolumeGeometries.Set(mech)
    log("materials OK; BlackMech StatusInfo=[%s]" % blk.StatusInfo)

    all_bodies = list(GetRootPart().GetAllBodies())
    lens_bodies = [b for b in all_bodies
                   if b.GetName().startswith("Lens_") or b.GetName().startswith("Stop_")]
    src_bodies = dict((b.GetName(), b) for b in all_bodies
                      if b.GetName().startswith("Source_"))
    log("lens bodies=%d mech=%d" % (len(lens_bodies), len(mech)))

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
            eb.OpticalPropertiesType = SpeosSim.Material.EnumOpticalPropertiesType.Surfacic
            # Must honour a MIRROR<n> token exactly as the wall material does.
            # This branch used to assign WALL_SOP as a library path
            # unconditionally, so any non-file token made EVERY simulation fail
            # with "File not found: MIRROR0" - while the wall itself had been
            # set correctly, which made the log read as if the run had worked.
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

    irr2 = SpeosSim.SensorIrradiance.Create()
    irr2.Name = "FrontCatch"
    for cand in curves:
        try:
            if irr2.OriginPoint.Set(cand):
                break
        except Exception:
            pass
    for cand in curves:
        try:
            if irr2.XDirection.Set(cand):
                break
        except Exception:
            pass
    irr2.XIsMirrored = True
    irr2.XEnd = 150.0
    irr2.YIsMirrored = True
    irr2.YEnd = 150.0
    irr2.XNbSamples = 100
    irr2.YNbSamples = 100
    irr2.SensorType = SpeosSim.SensorIrradiance.EnumSensorType.Radiometric
    irr2.LayerType = getattr(SpeosSim.SensorIrradiance.EnumLayerType, "None")

    def set_sensors(sim, sensor):
        try:
            sim.Sensors.Set([sensor])
        except Exception:
            try:
                sim.Sensors.Set(sensor)
            except Exception:
                sim.Sensors.Add(sensor)

    def run(name, geo, source_subject, sensor, nrays, lxp=False):
        sim = SpeosSim.SimulationDirect.Create()
        sim.Name = name
        sim.UsesLXP = lxp
        if lxp:
            sim.LXPMaxPath = 400000
        sim.HasRayNbLimit = True
        sim.NbRays = nrays
        sim.Geometries.Set(geo)
        sim.Sources.Set([source_subject])
        set_sensors(sim, sensor)
        sim.Compute()
        log("%s computed. StatusInfo=[%s]" % (name, sim.StatusInfo))
        for p in sim.GetResultFilePaths():
            log("  result: %s" % p)

    run("SV_Stray_" + SFX, lens_bodies + mech + [stray_disc],
        stray_src.Subject, irr, 1000000)
    # In-field runs. ALWAYS include the LAST field.
    #
    # This was `range(min(3, len(imported_sources)))`, which on a 4-field system
    # simulates fields 1-3 and silently never touches the real corner. tessar25
    # has 4 fields, so every "corner throughput" number reported for it was
    # actually its 17.5 deg field while its 25 deg corner went unmeasured.
    #
    # Keep the run count at 3 for cost, but pick axial / middle / MAX rather
    # than the first three. The simulation name still encodes the field INDEX,
    # so downstream parsing of SV_F<n>v is unchanged for 3-field systems.
    # IMPORTED, never re-derived - see survey/field_slots.py. A local copy of
    # this rule has shipped wrong twice.
    nsrc = len(imported_sources)
    field_idx = field_slots(nsrc)
    log("in-field runs: source indices %s of %d (last field ALWAYS included)"
        % ([i + 1 for i in field_idx], nsrc))
    for slot, i in enumerate(field_idx):
        geo = lens_bodies + mech
        bn = "Source_%d" % (i + 1)
        if bn in src_bodies:
            geo.append(src_bodies[bn])
        # slot number keeps the SV_F1v/F2v/F3v naming the analysers expect;
        # the log records which actual field each slot is
        log("  SV_F%dv_%s <- imported field %d of %d" % (slot + 1, SFX, i + 1, nsrc))
        run("SV_F%dv_" % (slot + 1) + SFX, geo, imported_sources[i], irr, 200000)
    run("SV_Back_" + SFX, lens_bodies + mech + [back_disc],
        back_src.Subject, irr2, 300000, lxp=True)

    DocumentSave.Execute(SAVE)
    log("wire-survey end")
except Exception:
    log("FATAL:")
    log(traceback.format_exc())
finally:
    fh = open(LOG, "w")
    fh.write("\n".join(lines) + "\n")
    fh.close()
