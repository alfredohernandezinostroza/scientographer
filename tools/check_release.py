# SPDX-FileCopyrightText: 2026 Alfredo Hernández Inostroza and the Scientographer contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Check that a release is consistent before it is published (see RELEASING.md).

    python tools/check_release.py v0.2.0     # what the release workflow runs
    python tools/check_release.py            # the same checks for the version in the code

A release vX.Y.Z needs:
- scientographer/__init__.py: __version__ = "X.Y.Z" (no .dev suffix);
- the git tag: vX.Y.Z;
- CHANGELOG.md: a "## [X.Y.Z] - YYYY-MM-DD" section;
- CITATION.cff: version X.Y.Z, released on that same date.
Standard library only; exits 1 with every problem listed.
"""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent


def version_in_code() -> str:
    text = (ROOT / "scientographer" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, flags=re.M)
    if not match:
        raise SystemExit("scientographer/__init__.py has no __version__")
    return match.group(1)


def problems(tag: str | None) -> list[str]:
    version = version_in_code()
    found = []
    if not re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc)\d+)?", version):
        found.append(f"__version__ is {version!r}: a release is X.Y.Z (or X.Y.ZrcN), without .dev")
    if tag is not None and tag != f"v{version}":
        found.append(f"the tag {tag!r} does not match __version__ {version!r} (expected 'v{version}')")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    entry = re.search(rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})\s*$", changelog, flags=re.M)
    if not entry:
        found.append(f"CHANGELOG.md has no '## [{version}] - YYYY-MM-DD' section")

    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    cited = re.search(r'^version:\s*"?([^"\n]+)"?\s*$', citation, flags=re.M)
    released = re.search(r'^date-released:\s*"?([^"\n]+)"?\s*$', citation, flags=re.M)
    if not cited or cited.group(1).strip() != version:
        found.append(f"CITATION.cff version is {cited.group(1).strip() if cited else None!r}, not {version!r}")
    if entry and (not released or released.group(1).strip() != entry.group(1)):
        found.append(f"CITATION.cff date-released is {released.group(1).strip() if released else None!r}, "
                     f"but the changelog dates {version} {entry.group(1)}")
    return found


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else None
    found = problems(tag)
    for problem in found:
        print(f"release check: {problem}", file=sys.stderr)
    if not found:
        print(f"release check: {version_in_code()} is consistent (code, tag, changelog, citation)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
