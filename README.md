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
actual stray-light number. It holds the HPC entitlement throughout, so nothing
else can solve while it runs. On the reference machine it takes **about nine
minutes** — the example is a small, fast design; your own lenses will generally
take longer, and forty minutes is a fairer figure for a typical one.

That run doubles as a known-answer check. On the reference machine the example
returns:

| | |
| --- | --- |
| Stray flux, naive tube | 0.08802 W |
| Stray flux, seated barrel | 0.00391 W |
| Change | **−95.6% ± 1.3%** (75.1σ, decisive) |
| Imaging throughput | −0.4%, no significant change |

If your figure is close to −95%, your installation is not merely running, it is
producing the right answer.

That −95.6% is measured at 15°, where the **baseline's** stray peaks. A forward
sweep (below) later showed the redesigned system's worst residual sits at 18°
instead, where the benefit is **−85.8%**. Both are true and neither is the
"right" number on its own — see *Where the stray peak moves*. The imaging row is the one that makes the stray row
mean anything: a barrel that simply blocked the lens would also remove stray
light, so throughput has to survive.

`--full` also reports the stray angle as **not resolved** for this example. That
is expected and is not a fault: the worst-case angle sits in the first bin the
search is permitted to consider, at the edge of the window rather than inside
it, so the measured reduction is real while the *angle* is a lower bound. The
pipeline says so rather than quietly picking a number, and you will see the same
disclosure on your own designs when it applies.

The example is **generated, not shipped**: `lib/first-run-lens.ps1` builds an
ordinary Cooke triplet — f/5, 50 mm focal length, ±14° field, catalog glasses —
from a fixed prescription of our own, and your OpticStudio writes the `.zmx`.
Nothing is redistributed, which is how this package can contain a working
example while containing no `.zmx` at all.

## The sample set — reproduce the range yourself

One example makes a misleading advertisement. The triplet returns −95.6%, and
the natural conclusion from a single data point is "this always gives you 95%."
It does not, and the honest claim is the whole reason to run a simulation
rather than consult a rule of thumb.

```bash
python lib/make-samples.py
```

generates four designs and their manifests; `python lib/run-fleet.py` then runs
them. **Measured on the reference machine, with the shipped synthetic BSDF:**

| design | f/# | field | track | stray before | stray after | change | imaging |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `example-triplet` | f/5 | ±14° | 62 mm | 0.08802 W | 0.00391 W | **−95.6%** decisive † | −0.4% |
| `wfov-30` | f/4 | ±30° | 38 mm | 0.06699 W | 0.05475 W | **−18.3%** decisive | −0.6% |
| `fast-f2p5` | f/2.5 | ±10° | 65 mm | 0.03773 W | 0.03671 W | **−2.7%** barely significant (2.1σ) | −0.5% |
| `longbore-f8` | f/8 | ±4° | 205 mm | 0.08126 W | 0.08068 W | **−0.7% — no change (0.6σ)** | +0.3% |

All four carry the same ±1.3% Monte-Carlo uncertainty, so `fast-f2p5`'s −2.7% is
only twice its own error bar, and **`longbore-f8` shows no effect at all**. That
last row is the most useful thing in this table. A workflow that always reports
a large improvement is a brochure; this one will tell you when the redesign is
not worth building, and that verdict is worth more than a flattering number
because it is the one that saves you the part.

The imaging column is what makes the stray column mean anything — a barrel that
simply blocked the lens would also remove stray light. Throughput survives
everywhere here, so these are baffling, not blocking.

**How the set was chosen, since it determines what it is evidence of.** The four
archetypes were fixed on optical grounds — f/number, field, track length,
element count — *before* any of them were run, and every result is published.
They were not selected for their numbers, and could not have been: nobody here
can predict which design gives a large benefit. Five separate proxies for that
were tested against measurement and all five failed, which is precisely why the
tool exists. The author's own prediction before running these was that
`longbore-f8`, a 205 mm tube at ±4°, would show the largest benefit. It came
last.

† `example-triplet` is measured at 15°; its worst *residual* angle is 18°, where
the benefit is −85.8%. See below.

Three of the four report their stray angle as **not resolved** — the peak sits
in the first bin the search may consider, so it is the edge of the search
window rather than a located maximum. That is disclosed rather than hidden, and
you will meet the same disclosure on your own designs.

### Where the stray peak moves

`resolved: false` is a **request for a forward sweep**, not a defect. We ran
those sweeps on all three flagged designs — the seated geometry, seven angles
each, plus a repeat angle for a run-to-run noise estimate:

| design | ranked | forward peak | flux at peak ÷ at ranked | repeat noise |
| --- | --- | --- | --- | --- |
| `wfov-30` | 31° | 31° | 1.00× | 0.5% |
| `longbore-f8` | 5° | 5° | 1.00× | 0.1% |
| `example-triplet` | 15° | **18°** | **2.94×** | 3.1% |

Two confirm exactly. The third is the interesting one, and it is **not** a case
of the selector picking a wrong angle — a baseline sweep over the same angles
shows the *naive tube's* stray peaks at 15°, precisely where it was ranked:

| angle | naive tube | seated barrel | reduction |
| --- | --- | --- | --- |
| **15°** | **0.0882 W** ← baseline peaks | 0.00385 W | **−95.6%** |
| **18°** | 0.0795 W | **0.0113 W** ← residual peaks | **−85.8%** |
| 22° | 0.0393 W | 0.00721 W | −81.6% |
| 26° | 0.0199 W | 0.000265 W | −98.7% |

**The redesign moves where the residual comes in.** The barrel is extremely
effective at 15° and less so at 18°, so the worst remaining stray after the
redesign is not at the angle that was worst before it. Quote −95.6% as the
reduction at the baseline's worst angle, and −85.8% as the benefit at the
redesigned system's worst angle. The second is the more conservative number and
the more useful one if you are sizing a margin.

This generalises, and it is worth knowing before you trust any single-angle
result: **the worst angle before a redesign is not necessarily the worst angle
after it.** That is exactly what `resolved: false` exists to make you check.

## How much does the wall model move the answer?

`black-anodize-plausible.anisotropicbsdf` is **synthetic** — a physically
reasonable stand-in, not measured data — and it is one of the largest
uncertainties in the workflow. Replacing a specular black wall with it moved
20° stray flux by +22% and grew the measured vane benefit from −15.7% to
−24.6%. A stray-light number quoted without reference to the wall model is
quoting an unstated assumption.

So the model is bracketed rather than trusted. Two more copies of the same
physical model ship alongside it — a Lambertian floor plus a specular lobe
rising steeply toward grazing, the characteristic black-surface behaviour —
with only the level scaled:

| | normal-incidence TIS | at 75° | represents |
| --- | --- | --- | --- |
| low | 2.2% | 12.5% | a good black coating |
| **mid** | **4.5%** | **25%** | **the shipped model** |
| high | 9.0% | 50% | a poor or aged surface |

A factor of four in reflectance brackets the range of real black-surface data
(Fest, *Stray Light Analysis and Control*). The band is deliberately wide: its
job is to bound the answer, not to flatter it.

The headline reduction is a **ratio** of two runs sharing the same walls, so it
should be far more stable than either absolute flux. That was a hypothesis.
Measured across the four sample designs:

| design | field | low (TIS 12.5%) | mid (shipped) | high (TIS 50%) | spread |
| --- | --- | --- | --- | --- | --- |
| `example-triplet` ‡ | ±14° | −96.5% | −95.6% | −94.3% | 2.2 pp |
| `wfov-30` | ±30° | −12.7% | −18.3% | −25.5% | **12.7 pp** |
| `fast-f2p5` | ±10° | −2.4% | −2.7% | −2.7% | 0.3 pp |
| `longbore-f8` | ±4° | −1.4% | −0.7% | −0.4% | 0.9 pp |

‡ Each design's band is measured at its own ranked angle, so
`example-triplet`'s band is the spread at 15°. The band and the angle are
independent questions; the 18° residual peak discussed above has not been
banded.

Reproduce it — both band models ship, so this needs nothing else:

```bash
python run-bsdf-band.py --samples
```

**The hypothesis holds.** A fourfold swing in wall reflectance moves the
reduction by 0.3 to 12.7 pp, on benefits spanning −0.7% to −95.6%, and for
three of the four designs the *conclusion* does not change at all:

- `example-triplet` stays decisively beneficial at every wall model (2.2 pp).
- `wfov-30` stays decisively beneficial, but the **size** of the benefit
  roughly doubles across the span, −12.7% to −25.5%. This is the caveat case:
  quote it as "−18%, and between −13% and −26% depending on your surface
  finish", never as a bare number. Wide fields have moved most throughout this
  work.
- **`longbore-f8` is null at every wall model** — not statistically significant
  at any of the three. "This design does not benefit from vanes" survives a
  fourfold reflectance swing, which makes it a far stronger recommendation than
  a single run could support.
- `fast-f2p5` is the one whose verdict shifts, clearing significance at two of
  three wall models. What changes is "marginal" versus "negligible" — every
  value sits between −2.4% and −2.7% — so nothing anyone would act on
  differently.

The direction is **not** consistent: worse walls give `wfov-30` and `fast-f2p5`
*more* benefit and `example-triplet` and `longbore-f8` *less*. Four points is
far too few to build a rule on, and this project has already refuted five
plausible-looking predictors of exactly this kind, so it is recorded as an
observation and nothing is inferred from it.

All four spreads fall inside the 0.2–28.6 pp band this README quotes from the
development corpus, so that figure is now corroborated by designs you can run
yourself rather than asserted from files that cannot ship.

**What this does not settle.** A band that moves the magnitude is a caveat you
state and move on from. A band that spans "clear benefit" to "no benefit" means
the simulation does not answer the question for that design without measured
BSDF data — and that case is real: for designs with a window close to the
sensor, the corpus band contains zero, so the *sign* is undetermined. None of
the four designs here is in that class, which is a fact about this sample set
and not a general reassurance. If your housing's real surface finish matters to
your conclusion, measured BSDF data for your actual coating at 60–75° angles of
incidence remains the highest-value input you can add.

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
