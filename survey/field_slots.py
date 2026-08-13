"""field_slots.py -- THE field-slot mapping. One definition, imported by all.

Which imported ODX per-field source feeds each SV_F<n>v / OO_F<n>v slot.

This exists because the same bug has now shipped THREE times:

  2026-07-26  wire-survey.py used `range(min(3, n))`, so any system with more
              than three fields never had its real corner simulated. tessar25's
              "corner" was 17.5 deg when its corner is 25 deg; a published
              +91%/+92% corner-recovery claim was withdrawn over it.
  2026-07-28  wire-optics-only.py was written from the OLD template and
              reproduced `min(3, n)` verbatim. Transmission then divided the
              25 deg field by the 17.5 deg field on every 4-field system,
              manufacturing an "18 systems where the barrel obstructs the beam"
              finding that survived SIX eliminated hypotheses before the
              irradiance maps showed the two runs were imaging different
              fields.

Both times the fix was correct and local, and both times it failed to reach the
other copy. So the rule is: NOBODY re-derives this. Import it.

    import os, sys
    sys.path.append(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "survey"))
    from field_slots import field_slots

(Derive the path; do not hardcode one. The exception is a script run INSIDE
Speos via /RunScript=, where IronPython sets __file__ to a GUID rather than a
path -- os.path.abspath then resolves it against the working directory and
returns a plausible wrong answer instead of raising. Those scripts take their
root from an environment variable instead; see wire-survey.py.)

(The absolute path matches how these scripts already locate their config, and
survives the Speos IronPython host having no useful __file__ or cwd.)
"""


def field_slots(nsrc):
    """Zero-based imported-source indices for slots F1, F2, F3.

    ALWAYS includes the LAST field: the corner is the whole point of the
    measurement. Fewer than three sources means every source gets a slot.
    """
    if nsrc <= 3:
        return list(range(nsrc))
    return [0, nsrc // 2, nsrc - 1]
