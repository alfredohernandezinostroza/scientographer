# Contributing to Scientographer

Thank you for helping. Bug reports, questions and pull requests are all welcome.

## Before you open a pull request

1. Install the development environment: `pip install -e ".[all,dev]"`.
2. Run the tests: `pytest`. For changes to a stage, also run the example project end
   to end: `cd examples/until_1990 && dvc repro`.
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

By contributing, you agree that your contribution is licensed under the license of the
files you change: GNU AGPL-3.0-or-later for the pipeline, MIT for the map frontend
(`scientographer/website_assets/`) and the project templates. Keep the
`SPDX-License-Identifier` header at the top of every file, and add one to new files.
