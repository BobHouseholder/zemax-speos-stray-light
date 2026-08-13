"""stage-new.py -- add newly-eligible survey systems to the corpus.

Run after `catalog-samples.py` identifies a system the survey does not yet
cover. The catalog's "eligible" count is NOT the number of systems: on
2026-07-27 its 34 entries deduped to 24 unique files, of which 20 were already
staged, two were OpticStudio scratch copies of staged systems (ZMXtmp7A5C IS
wideangle32, ZMXtmp9537 IS rearstop31 - identical curvature lists) and one
shared cameralens14's prescription. Always compare PRESCRIPTIONS before
staging, not filenames or content hashes: the staged copies are re-saved
through OpticStudio, so their bytes differ from the originals.
"""
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "lib"))
import job as J  # noqa: E402
import settings  # noqa: E402

SYS = os.path.join(BASE, "survey", "systems")
_SC = os.path.join(settings.zemax_samples(), "Short course")

# (slug, source .zmx, simPrefix). simPrefix names every Speos artefact and must
# be unique across the corpus - it is explicit rather than derived because the
# historic artefacts used ad-hoc prefixes (wideangle32 -> wa32).
NEW = [
    ("scvfac20", os.path.join(_SC, "sc_vfac1.zmx"), "vfac"),
    ("sctole14", os.path.join(_SC, "sc_tole1.zmx"), "tole"),
]


def main():
    for slug, src, pre in NEW:
        if not os.path.exists(src):
            print("SKIP %s: source missing %s" % (slug, src))
            continue
        wd = os.path.join(SYS, slug)
        os.makedirs(wd, exist_ok=True)
        dst = os.path.join(wd, slug + ".zmx")
        if not os.path.exists(dst):
            shutil.copy2(src, dst)
        lens = os.path.abspath(dst).replace(os.sep, "/")
        wdp = os.path.abspath(wd).replace(os.sep, "/")
        mp = J.path_for(slug, os.path.abspath(wd), "manifest")
        m = J.load(mp) if os.path.exists(mp) else J.default_manifest(slug, lens, wdp)
        m["simPrefix"] = pre
        m["source"] = src
        J.save(m, mp)
        print("staged %-9s prefix=%-5s <- %s" % (slug, pre, os.path.basename(src)))


if __name__ == "__main__":
    main()
