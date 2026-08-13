# probe-dunder-file.py -- is __file__ available to a script run inside Speos?
#
# The three wire-*.py scripts hardcode an absolute repo path because they run
# under SpaceClaim's IronPython via /RunScript=, not as ordinary modules. Whether
# that literal can be replaced by a path derived from __file__ depends entirely
# on whether IronPython defines __file__ in that context -- and guessing is how
# this project has repeatedly shipped a wrong assumption. So: measure it.
#
# ANSWER, 2026-08-08: __file__ is DEFINED but holds a GUID
# ('8d1fa36f-24b2-4c0e-ba29-7bfe42898217'), so abspath resolves it against the
# working directory and returns a real, plausible, WRONG folder rather than
# raising. The wire scripts therefore take their paths from environment
# variables the launching driver exports -- see speos_env() in lib/settings.py.
#
# The literal below is the one absolute path in this tree that is CORRECT to
# hardcode: this script runs where nothing can be derived, and hardcoding is
# precisely what it exists to demonstrate. Edit it if you re-run the probe
# elsewhere.
import os

OUT = r"C:\Users\<user>\Dropbox\Optics\stray-light-loop\survey\probe-dunder-file.txt"

lines = []


def note(k, v):
    lines.append("%-22s %s" % (k, v))


try:
    note("__file__", repr(__file__))
    note("dirname(__file__)", repr(os.path.dirname(os.path.abspath(__file__))))
except NameError:
    note("__file__", "NOT DEFINED (NameError)")
except Exception as exc:
    note("__file__", "raised %s: %s" % (type(exc).__name__, exc))

try:
    note("sys.argv[0]", repr(__import__("sys").argv[0]))
except Exception as exc:
    note("sys.argv[0]", "unavailable (%s)" % exc)

note("cwd", repr(os.getcwd()))

with open(OUT, "w") as f:
    f.write("\n".join(lines) + "\n")
