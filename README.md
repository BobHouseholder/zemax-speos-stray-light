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
| Worst stray angle | 16°, forward-confirmed |
| Stray flux, naive tube | 0.10957 W |
| Stray flux, seated barrel | 0.02267 W |
| Change | **−79.3% ± 1.3%**, decisive |
| Imaging throughput | −0.4%, no significant change |

If your figure is close to −79%, your installation is not merely running, it is
producing the right answer.

The angle matters as much as the number. The backward trace *ranks* a candidate
angle; a forward sweep then *measures* it. Here the rank was 17° and the
measurement settles at 16° — and at 15°, one degree away, the same design reads
−95.6%. Always quote the confirmed angle alongside the reduction. The imaging row is the one that makes the stray row
mean anything: a barrel that simply blocked the lens would also remove stray
light, so throughput has to survive.

`--full` also reports how the angle was settled. For this example the backward
trace ranks 17° and the forward confirm measures 16°; the run reports
`inverse-trace+confirmed`. If a design of yours comes back **not resolved**,
the ranking alone could not settle it — see *How the angle is settled*.

The example is **generated, not shipped**: `lib/first-run-lens.ps1` builds an
ordinary Cooke triplet — f/5, 50 mm focal length, ±14° field, catalog glasses —
from a fixed prescription of our own, and your OpticStudio writes the `.zmx`.
Nothing is redistributed, which is how this package can contain a working
example while containing no `.zmx` at all.

## The sample set — reproduce the range yourself

One example makes a misleading advertisement. The triplet returns −79.3%, and
the natural conclusion from a single data point is "this always gives you 80%."
It does not — one of the four below shows no benefit at all — and the honest
claim is the whole reason to run a simulation rather than consult a rule of
thumb.

```bash
python lib/make-samples.py
```

generates four designs and their manifests; `python lib/run-fleet.py` then runs
them. **Measured on the reference machine, with the shipped synthetic BSDF:**

| design | f/# | field | track | angle | stray before | stray after | change | imaging |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `example-triplet` | f/5 | ±14° | 62 mm | 16° | 0.10957 W | 0.02267 W | **−79.3%** decisive | −0.4% |
| `wfov-30` | f/4 | ±30° | 38 mm | 30° | 0.07721 W | 0.06175 W | **−20.0%** decisive | −0.9% |
| `fast-f2p5` | f/2.5 | ±10° | 65 mm | 15° | 0.03768 W | 0.03707 W | **−1.6% — not significant** | −0.2% |
| `longbore-f8` | f/8 | ±4° | 205 mm | 4° | 0.41190 W | 0.41283 W | **+0.2% — no change** | −0.5% |

Every angle above is **forward-confirmed**, not merely ranked — see *How the
angle is settled*. All four carry the same ±1.3% Monte-Carlo uncertainty, so
`fast-f2p5`'s −1.6% does not clear its own error bar and **`longbore-f8` shows
no effect at all**.

Those last two rows are the most useful thing in this table. A workflow that
always reports a large improvement is a brochure; this one will tell you when a
redesign is not worth building, and that verdict is worth more than a
flattering number because it is the one that saves you the part.

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

### How the angle is settled

A stray-light reduction is meaningless without the angle it was measured at,
and finding that angle takes two steps.

The **backward trace** ranks candidates cheaply: it traces rays out from the
detector and histograms where they escape. It bins at 2°, so it can only ever
report odd-numbered bin centres — an even-angle peak is unreachable by ranking
alone. The **forward confirm** then measures: a short simulation at each top
candidate, then a ±1° refinement around the winner.

Ranked against confirmed, for the four bundled designs:

| design | ranked | **confirmed** |
| --- | --- | --- |
| `example-triplet` | 17° | **16°** |
| `wfov-30` | 31° | **30°** |
| `longbore-f8` | 5° | **4°** |
| `fast-f2p5` | 15° | **15°** |

Three of four moved by a degree, and a degree is not a rounding detail:
`example-triplet` reads −79.3% at its confirmed 16° and −95.6% at 15°.
`longbore-f8` carries **five times** the stray flux at 4° that it does at 5°.
Whenever a run reports its angle as **not resolved**, it is telling you the
ranking alone could not settle it — that is a request for the forward
measurement, not a defect, and the runner performs it automatically.

**Check it actually ran.** The confirm leaves `confirm-result-*.txt` in the job
folder, and the manifest records `strayDegSource` as
`inverse-trace+confirmed`. If those are missing, the angle is a ranking and
nothing more. This matters because the confirm was silently failing here for
several days — the runner held the licence seat and the confirm, being guarded
by the same lock, deadlocked against its own parent. Every number in the table
above predates the fix and has been re-measured since.

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

Each design is banded **at its own forward-confirmed angle**:

| design | angle | low (TIS 12.5%) | mid (shipped) | high (TIS 50%) | spread |
| --- | --- | --- | --- | --- | --- |
| `example-triplet` | 16° | −86.6% | −79.3% | −70.4% | **16.2 pp** |
| `wfov-30` | 30° | −15.7% | −20.0% | −26.1% | 10.5 pp |
| `fast-f2p5` | 15° | −2.1% | −1.6% | −2.4% | 0.8 pp |
| `longbore-f8` | 4° | −0.1% | +0.2% | −0.3% | 0.5 pp |

Reproduce it — both band models ship, so this needs nothing else:

```bash
python run-bsdf-band.py --samples
```

**The hypothesis holds, but less comfortably than a single design suggests.** A
fourfold swing in wall reflectance moves the reduction by 0.5 to 16.2 pp, and
no design's *conclusion* changes across the span:

- `example-triplet` stays decisively beneficial, but over a wide range:
  −70.4% to −86.6%. Quote it as "−79%, between −70% and −87% depending on your
  surface finish", never bare.
- `wfov-30` likewise, −15.7% to −26.1%.
- **`fast-f2p5` and `longbore-f8` are null at every wall model** — neither
  clears significance at any of the three. "This design does not benefit from
  vanes" surviving a fourfold reflectance swing is a far stronger
  recommendation than a single run could support, and it is *half this set*.

The direction is **not** consistent: worse walls give `wfov-30` more benefit
and `example-triplet` less. Four points is far too few to build a rule on, and
this project has already refuted several plausible-looking predictors of
exactly this kind, so it is recorded as an observation and nothing is inferred.

**A caution about measuring the band at the wrong angle.** These figures
replace an earlier set taken at the pre-confirmation angles, and the change is
not small: `example-triplet` measured **2.2 pp** of spread at 15° and **16.2
pp** at its true 16°. Measuring one degree off understated its sensitivity to
the wall model sevenfold. The angle is not a detail you can settle later — it
determines the uncertainty as much as the result.

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
