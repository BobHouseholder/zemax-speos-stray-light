# wire-back-trace.py -- BACKWARD RUN (with mechanics) to choose the stray angle.
#
# Why this stage exists
# --------------------
# The stray angle used to be a bare heuristic, `maxField + 6 deg`. That is a
# guess, and it has already produced a wrong published number: on systems wider
# than 34 deg it placed the "stray" source INSIDE the design field.
#
# By optical reciprocity, the directions in which light escapes the front of the
# lens when the DETECTOR is made to emit are exactly the directions from which
# an outside source can deliver light to the detector. So one backward run
# yields the SHAPE of PST(theta), and its out-of-field peak is the angle worth
# simulating forward. Validated on the Double Gauss against a measured PST
# curve: 19 deg selected vs 20 deg measured.
#
# THE MECHANICS MUST BE PRESENT -- measured, not assumed
# ------------------------------------------------------
# The first version of this script ran OPTICS-ONLY, on the argument that the
# lens train dominates because "88.2% of out-of-field power reaches the sensor
# with no mechanical scatter". That argument is wrong: the statistic is about
# SCATTER, not about BLOCKING. The barrel still decides which angles get in,
# even when the light that survives never scatters off it.
#
# Measured on the Double Gauss, same lens, same method:
#     optics-only     -> 37 deg   (measured answer ranked 4th)
#     with mechanics  -> 19 deg   (measured PST peak: 20 deg)
# Strip the barrel and 35-40 deg looks wide open, so the histogram peaks there.
#
# This costs nothing in pipeline order: `strayDeg` is only ever written into
# params.json and never feeds any geometry, so this stage runs AFTER mech and
# patches the angle before the stray sims.
#
# Same 14-line config as wire-survey.py; MECH_STEP IS inserted.
import traceback
import sys

import os

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
MECH_STEP = f.readline().strip()          # IS inserted -- see header
SFX = f.readline().strip()
WALL_SOP = f.readline().strip()
Z_IMG = float(f.readline().strip())
R_DISC = float(f.readline().strip())
Z_CATCH = float(f.readline().strip())
STRAY_DEG = float(f.readline().strip())   # the OLD heuristic; being replaced
Z_SRC = float(f.readline().strip())
R_SRC = float(f.readline().strip())
WAVE = float(f.readline().strip())
EDGE_BLACK = f.readline().strip().upper() == "EDGEBLACK"
LOG = f.readline().strip()
f.close()
LOG = LOG.replace(".txt", "-backtrace.txt")

import os as _os
# 300k leaves enough escaping rays for the selector on most systems, but a
# tight barrel absorbs almost everything: B32/C22/C25 returned 556-1598 and
# were refused by the 2000-ray guard. They have out-of-field bins (27-32), so
# the refusal is a BUDGET limit, not a physical one. Raise it for those.
NRAYS = int(_os.environ.get("BACKTRACE_NRAYS") or 300000)
CATCH_HALF = 150.0        # generous: never clip the escaping cone

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
    log("wire-back-trace start: odx=%s sfx=%s mech=%s" % (ODX, SFX, MECH_STEP))
    log("z_img=%.4f r_disc=%.4f z_catch=%.4f" % (Z_IMG, R_DISC, Z_CATCH))
    DocumentSave.Execute(SAVE)

    odx = SpeosSim.ComponentOpticStudio.Create()
    odx.ComponentFile = ODX
    odx.Compute()
    log("ODX Compute: StatusInfo=[%s]" % odx.StatusInfo)

    # --- mechanics ----------------------------------------------------------
    # Present on purpose: they set the out-of-field angular acceptance. Without
    # them this run answers 37 deg instead of 19 on the Double Gauss.
    n1 = len(list(GetRootPart().GetAllBodies()))
    DocumentInsert.Execute(MECH_STEP)
    mech = list(GetRootPart().GetAllBodies())[n1:]
    log("mech bodies inserted: %d" % len(mech))

    all_before = set(b.GetName() for b in GetRootPart().GetAllBodies())

    # --- emitting disc AT the image plane -----------------------------------
    # The frame is Y-FLIPPED so the face normal points at the lens (-z).
    # This is the documented workaround for gotcha 13: ExitanceReverse is not
    # settable on a constant-exitance source and raises
    #   "Property ExitanceReverse ... is not defined for object in its current
    #    state"
    # which is exactly what a first run of this script hit. Orient the geometry
    # instead of trying to reverse the emission.
    plane = Plane.Create(Frame.Create(
        Point.Create(MM(0), MM(0), MM(Z_IMG)),
        Direction.DirX, Direction.Create(0, -1, 0)))
    ViewHelper.SetSketchPlane(plane)
    SketchCircle.Create(Point2D.Create(MM(0), MM(0)), MM(R_DISC))
    ViewHelper.SetViewMode(InteractionMode.Solid)

    after = list(GetRootPart().GetAllBodies())
    new_bodies = [b for b in after if b.GetName() not in all_before]
    if not new_bodies:
        raise RuntimeError("emitting disc was not created")
    disc = new_bodies[0]
    # rename IMMEDIATELY: every sketched body is called "Surface", so anything
    # resolved by name later is ambiguous otherwise (gotcha 12).
    disc.SetName("ImageDisc")
    log("disc body renamed to ImageDisc")

    # --- catch-plane reference curves ---------------------------------------
    # SensorIrradiance.OriginPoint.Set(Point) is NOT supported by this Script
    # API version; it takes sketch curves. Sketch a point+line at z=Z_CATCH and
    # hand those over, which is how the working DG script positions it.
    plane2 = Plane.Create(Frame.Create(
        Point.Create(MM(0), MM(0), MM(Z_CATCH)),
        Direction.DirX, Direction.DirY))
    ViewHelper.SetSketchPlane(plane2)
    SketchPoint.Create(Point2D.Create(MM(0), MM(0)))
    SketchLine.Create(Point2D.Create(MM(0), MM(0)), Point2D.Create(MM(10), MM(0)))
    ViewHelper.SetViewMode(InteractionMode.Solid)
    curves = list(GetRootPart().Curves)   # NOT GetAllCurves(); no such method
    log("catch reference curves: %d" % len(curves))

    # re-resolve by NAME after that regeneration -- stale body refs are a known
    # trap in this API.
    disc = [b for b in GetRootPart().GetAllBodies()
            if b.GetName() == "ImageDisc"][0]

    # --- backward Lambertian source on the disc -----------------------------
    src = SpeosSim.SourceSurface.Create()
    src.Name = "BackSource"
    face = disc.Faces[0]
    for setter in ("Set", "Add"):
        try:
            getattr(src.EmissiveFaces, setter)(face)
            break
        except Exception:
            pass
    try:
        names = [n for n in dir(SpeosSim.SourceSurface.EnumFluxType)
                 if not n.startswith("_")]
        for nm in ("RadiantFlux", "Radiant", "FluxRadiant"):
            if nm in names:
                src.FluxType = getattr(SpeosSim.SourceSurface.EnumFluxType, nm)
                break
        src.FluxValueRadiant = 1.0
    except Exception as e:
        log("flux set failed: %s" % safe_str(e))
    src.IntensityType = SpeosSim.SourceSurface.EnumIntensityType.Lambertian
    src.IntensityTotalAngle = 180.0
    # Best-effort only. The direction is already handled by the flipped sketch
    # frame above; on a constant-exitance source this property does not exist
    # and must not be fatal (gotcha 13).
    try:
        src.ExitanceReverse = True
        log("ExitanceReverse set (in addition to the flipped frame)")
    except Exception as e:
        log("ExitanceReverse unavailable as expected (%s) -- relying on the "
            "flipped sketch frame for direction" % safe_str(e)[:80])
    src.SpectrumType = SpeosSim.SourceSurface.EnumSpectrumType.Monochromatic
    src.SpectrumValueWavelength = WAVE
    log("back source: Lambertian 180, reversed, mono %.1f nm" % WAVE)

    # --- the disc needs a MATERIAL ------------------------------------------
    # Every new body must carry an optical property before ANY sim runs, or
    # Speos aborts with "No optical properties was found on 'Surface'"
    # (gotcha 8) -- which is exactly what the first run of this script hit.
    # Black (0% mirror) so the disc emits but never reflects rays back.
    discmat = SpeosSim.Material.Create()
    discmat.Name = "DiscAbsorber"
    discmat.OpticalPropertiesType = \
        SpeosSim.Material.EnumOpticalPropertiesType.Surfacic
    discmat.SOPType = SpeosSim.Material.EnumSOPType.Mirror
    discmat.SOPReflectance = 0.0
    discmat.OrientedFaces.Set([disc])
    log("disc material assigned: Surfacic Mirror 0%%  StatusInfo=[%s]"
        % discmat.StatusInfo)

    # --- mech material ------------------------------------------------------
    # ODX recompute wipes optical properties, so these are assigned in code
    # every run, never by hand.
    if mech:
        blk = SpeosSim.Material.Create()
        blk.Name = "BlackMech"
        blk.OpticalPropertiesType = \
            SpeosSim.Material.EnumOpticalPropertiesType.Volumic
        blk.VOPType = SpeosSim.Material.EnumVOPType.Opaque
        # any MIRROR<n>, not just MIRROR5 -- MIRROR0 is the no-scatter control
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
            log("wall SOP: Library=%s" % WALL_SOP)
        blk.VolumeGeometries.Set(mech)
        log("mech material assigned  StatusInfo=[%s]" % blk.StatusInfo)

    # --- catch sensor in front ----------------------------------------------
    # LXP only records rays that CONTRIBUTE to a sensor, so without this the
    # backward run produces no ray file at all.
    catch = SpeosSim.SensorIrradiance.Create()
    catch.Name = "FrontCatch"
    for cand in curves:
        try:
            if catch.OriginPoint.Set(cand):
                break
        except Exception:
            pass
    for cand in curves:
        try:
            if catch.XDirection.Set(cand):
                break
        except Exception:
            pass
    catch.XIsMirrored = True
    catch.XEnd = CATCH_HALF
    catch.YIsMirrored = True
    catch.YEnd = CATCH_HALF
    catch.XNbSamples = 100
    catch.YNbSamples = 100
    catch.SensorType = SpeosSim.SensorIrradiance.EnumSensorType.Radiometric
    catch.LayerType = getattr(SpeosSim.SensorIrradiance.EnumLayerType, "None")
    log("catch sensor: +/-%.0f mm at z=%.3f" % (CATCH_HALF, Z_CATCH))

    # --- geometry: lens bodies + the disc, NO mechanics ----------------------
    bodies = list(GetRootPart().GetAllBodies())
    lens_bodies = [b for b in bodies
                   if b.GetName().startswith("Lens_")
                   or b.GetName().startswith("Stop_")]
    # --- edge blackening ----------------------------------------------------
    # This script READ the EDGE_BLACK flag and never acted on it, so every
    # backward trace ran on a barrel that was not the redesign it claimed to
    # represent. Ported verbatim from wire-survey.py.
    if EDGE_BLACK:
        rim_faces = []
        for b in bodies:
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
        log("edge blackening OFF")

    geo = list(lens_bodies) + list(mech) + [disc]
    log("geometry: %d lens/stop + %d mech + emitting disc"
        % (len(lens_bodies), len(mech)))

    sim = SpeosSim.SimulationDirect.Create()
    sim.Name = "BT_%s" % SFX
    sim.HasRayNbLimit = True
    sim.NbRays = NRAYS
    sim.UsesLXP = True
    sim.LXPMaxPath = 400000
    sim.Geometries.Set(geo)
    sim.Sources.Set([src.Subject if hasattr(src, "Subject") else src])
    try:
        sim.Sensors.Set([catch])
    except Exception:
        try:
            sim.Sensors.Set(catch)
        except Exception:
            sim.Sensors.Add(catch)
    sim.Compute()
    log("BT_%s computed. StatusInfo=[%s]" % (SFX, sim.StatusInfo))
    for p in sim.GetResultFilePaths():
        log("  result: %s" % p)

    log("wire-back-trace end")
except Exception:
    log("FATAL\n" + traceback.format_exc())

out = open(LOG, "w")
out.write("\n".join(lines))
out.close()
