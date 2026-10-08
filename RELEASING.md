# Releasing Scientographer

## How versions work

Versions follow [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`.

| Change | Example | When |
|---|---|---|
| PATCH | 0.1.0 → 0.1.1 | bug fixes only |
| MINOR | 0.1.1 → 0.2.0 | new features; before 1.0, also changes that break existing projects (the changelog says so) |
| MAJOR | 0.x → 1.0.0 | after 1.0, any change that breaks existing projects (a renamed setting, a changed output) |

- The version is written in one place: `__version__` in `scientographer/__init__.py`
  (`pyproject.toml` reads it from there).
- Between releases, `main` carries the *next* version with a `.dev0` suffix (for
  example `0.2.0.dev0` after 0.1.0 is out), so an install from GitHub never claims
  to be a release.
- A release is a git tag on `main`, `vX.Y.Z`; publishing the GitHub release for that
  tag uploads it to PyPI (`.github/workflows/release.yml`). There are no release
  branches; one can be made from a tag later if an old version ever needs a fix
  while `main` has moved on.
- `CHANGELOG.md` collects changes under `## [Unreleased]` as they are merged.

## Day to day

Every change goes through a branch and a pull request into `main`; CI must pass
before merging. A change users would notice adds a line under `## [Unreleased]`
in `CHANGELOG.md`.

## Making a release

Say the release is 0.2.0, on 2026-11-20.

1. **Prepare it on a branch** (`release-0.2.0`):
   - `scientographer/__init__.py`: `__version__ = "0.2.0"` (drop `.dev0`; choose
     the number by the table above).
   - `CHANGELOG.md`: rename `## [Unreleased]` to `## [0.2.0] - 2026-11-20`, add an
     empty `## [Unreleased]` above it, and update the links at the bottom.
   - `CITATION.cff`: `version: "0.2.0"`, `date-released: "2026-11-20"`.
   - Check: `python tools/check_release.py v0.2.0` must say the release is consistent.
2. **Merge** the pull request once CI passes.
3. **Publish** on GitHub: Releases → Draft a new release → tag `v0.2.0` (created on
   `main`) → title `0.2.0` → paste that version's changelog section → Publish. The
   release workflow runs the tests and the same check, builds, and uploads to PyPI;
   it refuses if anything disagrees.
4. **Open the next version** on a branch: `__version__ = "0.3.0.dev0"`, merge.

To rehearse without publishing: Actions → release → Run workflow uploads `main` to
[TestPyPI](https://test.pypi.org/project/scientographer/) as a `.devN` version;
`pip install -i https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ scientographer`
installs it from there.

## One-time setup (done once per index)

PyPI publishes through Trusted Publishing: GitHub proves to PyPI which workflow is
uploading, so no API token is stored anywhere. On pypi.org (and test.pypi.org for
rehearsals): Your account → Publishing → Add a new pending publisher, with project
`scientographer`, owner `alfredohernandezinostroza`, repository `scientographer`,
workflow `release.yml`, environment `pypi` (`testpypi` on TestPyPI).
