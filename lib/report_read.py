r"""report_read.py -- read measured flux out of a Speos HTML report.

Lifted out of survey/analyze-fleet.py so run-bsdf-band.py can report the wall
band without copying the extractor. Copying it was the obvious move and the
wrong one: `latex-report` and this repo's own history both record what happens
when one tool is duplicated to retarget it, and an extractor that disagrees
with the fleet table by a rounding rule would be worse than no band report.

Nothing here is new logic. `report_flux` and `find_report` are the analyze-fleet
functions unchanged; `change_pct` is the kpi.compare call the fleet table makes.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kpi  # noqa: E402

RAYS_STRAY, RAYS_INFIELD = 1_000_000, 200_000


def report_flux(path):
    """(flux_W, error_count) from a Speos .Report.html, or (None, None)."""
    try:
        t = open(path, encoding="utf-8", errors="replace").read()
    except IOError:
        return None, None
    m = re.search(r"<li>Flux: ([0-9.eE+-]+) W</li>", t)
    e = re.search(r"Total number of errors.{0,200}?([0-9]+)", t, re.S)
    return (float(m.group(1)) if m else None), (int(e.group(1)) if e else None)


def find_report(wd, name):
    """Locate a named report anywhere under the workdir's Speos output.

    Search by NAME rather than assuming a layout: taking subdirs[0] broke once
    other workstreams added more .scdocx documents to the same workdir.
    """
    d = os.path.join(wd, "SPEOS output files")
    if not os.path.isdir(d):
        return None
    for root, _dirs, files in os.walk(d):
        if name in files:
            return os.path.join(root, name)
    return None


def stray_pair(wd, prefix):
    """(before, after) stray flux for a simPrefix, or (None, None)."""
    fb = find_report(wd, "SV_Stray_%s_base.Report.html" % prefix)
    fa = find_report(wd, "SV_Stray_%s_redesign.Report.html" % prefix)
    if not fb or not fa:
        return None, None
    sb, _ = report_flux(fb)
    sa, _ = report_flux(fa)
    return sb, sa


def change_pct(before, after, rays=RAYS_STRAY, what=""):
    """The fleet table's own significance call, so the two cannot disagree."""
    if before is None or after is None or before <= 0:
        return None
    return kpi.compare(kpi.Measure.from_rays(before, rays),
                       kpi.Measure.from_rays(after, rays), what)
