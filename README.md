# Stray-light redesign loop — OpticStudio to Speos, headless

A closed-loop toolchain that takes a Zemax OpticStudio lens prescription,
builds a mechanical housing around it, exports the whole assembly to Ansys
Speos, measures stray light before and after adding baffles and vanes, and
reports how much the redesign removed. Every stage runs headless: there is no
GUI step anywhere in the loop.

**Start here:** `toolchain-install-a4.pdf`, in this folder. It is the complete
installation guide — twelve pages, five steps, with a verification section and
a troubleshooting table. This README is the summary; the guide is the
instructions.

---

## What you need before anything else

This toolchain drives commercial software. It cannot run without it, and no
part of it is optional:

| Requirement | Notes |
| --- | --- |
| Ansys Zemax OpticStudio | Provides the ZOS-API the loop scripts against |
| Ansys Speos | Same Ansys version as OpticStudio; the guide explains why |
| An OPTIS HPC entitlement | **One solve consumes the entire entitlement** |
| Python 3.10+ with CadQuery | For the mechanical housing; the guide pins versions |
| A lens prescription of your own | None ship with this package — see below |

The HPC entitlement is the constraint that surprises people. A single Speos
solve takes the whole thing, so **two runs cannot overlap** — not two of these
runs, and not this alongside any other Speos work on the same license. The
guide's section 7 covers what that looks like when you get it wrong (an HTTP
404 from the solver, which reads like a network fault and is not one).

## Install, in one paragraph

Install OpticStudio and Speos, create the CadQuery Python environment, copy
`straylight.toml.example` to `straylight.toml`, and edit it to point at your
own install paths. Then verify:

```bash
python lib/settings.py --check
```

That prints `CONFIG OK` or tells you exactly which key is wrong and why —
it distinguishes a path that does not exist from one that exists but is not
the kind of folder expected. Do not proceed past a failure here; every later
stage assumes this passed. Full detail is in the guide, steps 1–4.

## First run — prove the installation before supplying a design

No lens prescriptions ship with this package, so your first obstacle would
otherwise be supplying one before you have any evidence the installation
works. When something then fails, there is no way to tell whether the fault is
your config, your Ansys install, your lens, or the pipeline.

This separates those questions:

```bash
python lib/first-run.py
```

It generates a known-good example design, builds its job manifest, and runs
preflight on it — about ten seconds of OpticStudio. A `GO` means your
installation works end to end. Because the example is known to pass, a failure
here is your installation and not the design, and the tool says which of the
four candidates to look at first.

Add `--full` to take that same example through all eight stages and produce an
actual stray-light number (~40 minutes, and it holds the HPC entitlement
throughout).

The example is **generated, not shipped**: `lib/first-run-lens.ps1` builds an
ordinary Cooke triplet — f/5, 50 mm focal length, ±14° field, catalog glasses —
from a fixed prescription of our own, and your OpticStudio writes the `.zmx`.
Nothing is redistributed, which is how this package can contain a working
example while containing no `.zmx` at all.

## Running it

Stage each design in its own folder, where the folder name and file name
match — that name becomes the job's name everywhere afterwards:

    survey/systems/<name>/<name>.zmx

Then two commands. Generate the job manifests:

```bash
python lib/make-manifests.py
```

And run them:

```bash
python lib/run-fleet.py
```

The runner works through eight stages in order — `preflight`, `layout`, `odx`,
`mech`, `s0`, `sim_optics`, `sim_base`, `sim_redesign` — and is resumable: an
interrupted run restarts from the last completed stage. A full pass is roughly
forty minutes per design, nearly all of it simulation.

On a first batch, triage cheaply before committing hours of solver time:

```bash
python lib/run-fleet.py --only preflight
```

This tells you which of your designs the workflow can actually analyze. A
design that fails halts in about ten seconds with the failing check named.
**That is the intended behavior, not a defect** — the loop is built to refuse
rather than to guess, because a stray-light number produced from an unsuitable
model is worse than no number.

## Reading the result

The headline output is a percentage: how much less stray flux reaches the
detector after the redesign. Three things are worth knowing about that number
before you quote it to anyone.

**The benefit is large but not predictable in advance.** Across the reference
corpus it spans roughly −1% to −99%. Five different cheap proxies for
predicting where a given design will land — architecture class, field angle,
a flux-ratio spike, the mechanical scatter fraction, and median bore grazing
angle — were each tested against measurements and each failed. That is the
case for running the simulation rather than a limitation of it: if a proxy
worked, a lookup table would replace this toolchain.

**The wall scattering model matters, and the one shipped here is synthetic.**
`black-anodize-plausible.anisotropicbsdf` is a physically reasonable stand-in,
not measured data. Swapping it across a deliberately wide factor-of-four
reflectance span moves the headline number by anywhere from 0.2 to 28.6
percentage points depending on the design. If your housing's real surface
finish matters to your conclusion, measured BSDF data for your actual coating
is the single highest-value input you can add.

**It is Monte Carlo.** Repeat runs of the same design differ slightly. Small
differences between two designs may not be real; large ones are.

## What is in this package

    lib/          the pipeline: config, manifests, the fleet runner, geometry
    survey/       the Speos wire scripts and sweep drivers
    ghost/        the ghost-analysis stage
    testcases/    the regression suite (only if built with --with-testcases)

Your lens prescriptions are **not** included and none are supplied — the
reference designs used in development are Zemax's own sample files and are not
redistributable. You supply your own.

## License and attribution

See `LICENSE` and `NOTICE.md`. The reference material is intended to stand on
its own and is deliberately unattributed.
