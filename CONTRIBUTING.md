# Contributing to Scientographer

Thank you for helping. Bug reports, questions and pull requests are all welcome.

## How changes reach `main`

Work on a branch (`feature/...`, `fix/...`) and open a pull request into `main`; it is
merged once CI passes. `main` is always in a working state, and releases are tags on
it (see [RELEASING.md](RELEASING.md)). A change users would notice adds a line under
`## [Unreleased]` in [CHANGELOG.md](CHANGELOG.md).

## Before you open a pull request

1. Install the development environment: `pip install -e ".[all,dev]"`.
2. Run the tests: `pytest`. For changes to a stage, also run the example project end
   to end: `scientographer example /tmp/example && cd /tmp/example && git init && dvc init && dvc repro`.
3. Add or update tests next to the code you change (`scientographer/tests/`). Stages
   keep their logic in private helper functions (`_name`) so it can be tested on tiny
   hand-built graphs without running Hamilton.

## How a stage is built

Each stage is one Hamilton DAG module: functions are nodes, parameter names are edges,
`@dataloader` / `@datasaver` read and write files, and `_main()` builds the driver. Every
setting comes from `params.yaml` through `scientographer.config.params()`; never hard-code
a path or a threshold. A new stage also needs an entry in `scientographer/templates/dvc.yaml`
and documented defaults in `scientographer/params.yaml`.

## Licensing of contributions

Before your first pull request can be merged, you sign the project's
[Contributor License Agreement](CLA.md). A bot asks you on the pull request: reply with
the sentence it gives you, once, and it covers all your future contributions. You keep
the copyright in your work; the agreement lets the maintainer license and relicense the
project (for example, alongside the open-source license, a commercial one), and commits
the maintainer to keeping every accepted contribution available under an open-source
license.

Your contribution is licensed under the license of the files you change: GNU
AGPL-3.0-or-later for the pipeline, MIT for the map frontend
(`scientographer/website_assets/`) and the project templates. Keep the
`SPDX-License-Identifier` header at the top of every file, and add one to new files.
Only contribute data you are allowed to redistribute.
