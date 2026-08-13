# Licences

## This pipeline

MIT — see `LICENSE`. Copyright (c) 2026 Bob Householder.

## Third-party code included in this tree

| component | licence | holder | where |
|---|---|---|---|
| `ansys_optical_automation` | MIT | Copyright (c) 2022 ANSYS Inc. | `tools/ansys_optical_automation/LICENSE` |

Ansys's optical-automation library (upstream `ansys/optical-automation`) is
vendored under `tools/` because it is not distributed on PyPI. It is used only
to author surface-scatter (BSDF) data; nothing in `lib/` or `survey/` imports
it, and the pipeline runs without it. **If you copy `tools/` anywhere, its
`LICENSE` goes with it** — MIT permits redistribution on the condition that the
notice travels with the code.

## Not included, and not ours to distribute

The optical prescriptions used to build the validation corpus are stock Ansys
Zemax OpticStudio sample files. They are **not redistributed** here or in any
package built from this tree. The corpus identifies each one by SHA-256 so a
recipient holding OpticStudio can confirm they have the same designs, without
those designs being copied.
