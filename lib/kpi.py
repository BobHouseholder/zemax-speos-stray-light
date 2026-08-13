r"""kpi.py -- every reported metric carries its uncertainty, and every
comparison reports SIGNIFICANCE rather than a bare percentage.

Why this exists: "+7.9%" reads as a regression; "+7.9% +/- 6.3% (1.2 sigma)"
correctly reads as NO DETECTABLE CHANGE. That distinction decided the GPIM
verdict, and it was computed by hand after the fact. Here it is automatic.

Where sigma comes from, in order of preference:

  1. POISSON on a real count (ray counts from a .OptSequence path table or LPF
     trace count). Exact for equal-weight rays: sigma/x = 1/sqrt(N).

  2. EMPIRICAL repeat-run eta = |a-b| / (sqrt(2) * mean), measured by running
     the SAME configuration twice. Observed across this campaign at 1M rays:
         DG   30 deg no-vane 0.40% | vane 0.87% | seated 0.91%
         Cooke 30 deg no-vane 0.29% | vane 0.49%
     so 0.9% is used as a conservative default for a 1M-ray flux.

  3. NATIVE per-pixel Monte-Carlo error -- Speos computes this
     (BuildMapRelativeStandardError / GetValueRelativeStandardError) and it is
     strictly better than (2) because it accounts for ray WEIGHTS, not just
     counts. NOT AVAILABLE on our current results: the XMPs carry no error
     data because the sims were not configured to store it, and calling
     BuildMapRelativeStandardError on such a file hard-crashes the
     interpreter. Enabling it and re-running is the known upgrade path.
"""
import math

# Conservative default relative sigma for a 1M-ray flux, from the repeat runs
# above. Scale as 1/sqrt(rays) for other budgets.
ETA_1M = 0.009
REF_RAYS = 1_000_000


def eta_for(rays):
    """Relative sigma for a flux from an N-ray simulation."""
    if not rays or rays <= 0:
        return ETA_1M
    return ETA_1M * math.sqrt(REF_RAYS / float(rays))


class Measure:
    """A value with an uncertainty and a stated provenance for that uncertainty."""

    def __init__(self, value, sigma=None, unit="", method="unknown", n=None):
        self.value = value
        self.n = n
        self.method = method
        self.unit = unit
        if sigma is not None:
            self.sigma = sigma
        elif n:
            self.sigma = abs(value) / math.sqrt(n)
            self.method = "poisson-N=%d" % n
        else:
            self.sigma = abs(value) * ETA_1M
            self.method = "repeat-eta-default"

    @classmethod
    def from_count(cls, value, n, unit=""):
        return cls(value, n=n, unit=unit)

    @classmethod
    def from_rays(cls, value, rays, unit="W"):
        return cls(value, sigma=abs(value) * eta_for(rays), unit=unit,
                   method="repeat-eta@%drays" % rays)

    @property
    def rel(self):
        return self.sigma / abs(self.value) if self.value else float("inf")

    def __str__(self):
        if abs(self.value) >= 1e-3:
            return "%.5f +/- %.5f %s" % (self.value, self.sigma, self.unit)
        return "%.3e +/- %.1e %s" % (self.value, self.sigma, self.unit)

    def brief(self):
        return "%.5g +/-%.1f%%" % (self.value, 100 * self.rel)


def compare(before, after, what="", sigma_gate=2.0):
    """Compare two Measures. Returns a dict -- and NEVER hides a null."""
    if before.value == 0:
        raise ValueError("%s: zero baseline" % what)
    d = 100.0 * (after.value - before.value) / before.value
    # relative errors add in quadrature for a ratio
    comb = 100.0 * math.sqrt(before.rel ** 2 + after.rel ** 2)
    nsig = abs(d) / comb if comb else float("inf")
    if nsig < 1.0:
        verdict = "no change"
    elif nsig < sigma_gate:
        verdict = "not significant"
    elif nsig < 5.0:
        verdict = "significant"
    else:
        verdict = "decisive"
    return {"what": what, "before": before.value, "after": after.value,
            "delta_pct": d, "sigma_pct": comb, "n_sigma": nsig,
            "verdict": verdict, "significant": nsig >= sigma_gate}


def fmt(c, width=0):
    """One-line rendering of a comparison, uncertainty always shown."""
    s = "%+7.1f%% +/- %4.1f%% (%4.1f sig) %-15s" % (
        c["delta_pct"], c["sigma_pct"], c["n_sigma"], c["verdict"])
    return ("%-*s %s" % (width, c["what"], s)) if width else s


def acceptance(stage):
    """The CORRECT acceptance test per stage, stated up front.

    Twice in this campaign the wrong test was used: total flux for an operand
    that disperses foci, and a single wavelength for image quality that
    degraded 50% polychromatically.
    """
    return {
        "S0-ghost": "peak ghost IRRADIANCE on the isolated ghost system "
                    "(NOT total ghost flux -- GPIM disperses foci, it does not "
                    "remove Fresnel-fixed energy)",
        "S1-image": "POLYCHROMATIC spot/MTF at >=3 fields (never a single "
                    "wavelength)",
        "S2-import": "per-field centroid <= few um and RMS ratio 0.9-1.1 vs "
                     "the sequential baseline",
        "S4-stray": "imager flux from a collimated out-of-field source, "
                    "normalisation stated explicitly",
        "S6-verify": "all three: backward wall visibility, forward stray flux, "
                     "AND in-field throughput (which on wide fields may be the "
                     "headline, not the safety check)",
    }.get(stage, "undeclared -- state it before running")
