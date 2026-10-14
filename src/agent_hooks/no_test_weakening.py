"""agent-no-test-weakening: stop agents from "fixing" tests by weakening them.

The cheapest way for an agent to make a failing suite green is to delete
the failing test or mark it skipped. This hook inspects the *staged diff*
(``git diff --cached``) and blocks a commit when it:

* deletes a test file,
* removes a test definition (``def test_...``, ``it('...')``, ``test('...')``,
  ``func Test...(``), or
* adds a skip marker (``@pytest.mark.skip``, ``xit(``, ``it.skip``,
  ``@Disabled``, ``#[ignore]``, ...).

Adding tests, changing production code, and editing tests without removing
or skipping them all pass.
"""

from __future__ import annotations

import re
import subprocess
import sys

from .common import blocked

INTRO = (
    "You are a coding agent, and a pre-commit hook has blocked this commit\n"
    "because it weakens the test suite. Making the tests pass by deleting,\n"
    "removing, or skipping tests is not a fix — the tests describe the\n"
    "intended behavior. Fix the code under test instead, then commit again.\n"
    "If a test is genuinely obsolete, a human must make that call."
)

_TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs"}
_TEST_FILE_RE = re.compile(
    r"(^test_.*|.*_test|.*\.test|.*\.spec|.*_spec)\."
    r"(py|go|rb|js|jsx|ts|tsx|mjs|cjs)$"
)

REMOVED_DEFINITION_RES = [
    re.compile(r"\bdef\s+test_\w+\s*\("),           # Python: def test_x(
    re.compile(r"\b(?:it|test)\s*\(\s*['\"`]"),     # JS/TS: it('...'), test("...")
    re.compile(r"\bfunc\s+Test\w+\s*\("),           # Go: func TestX(
]

SKIP_MARKERS = ["@pytest.mark.skip", "@Disabled", "#[ignore]"]
SKIP_RES = [
    re.compile(r"\bxit\s*\("),
    re.compile(r"\bit\.skip\b"),
    re.compile(r"\btest\.skip\b"),
    re.compile(r"\bxdescribe\s*\("),
]


def is_test_file(path: str) -> bool:
    posix = path.replace("\\", "/")
    parts = posix.split("/")
    if any(part in _TEST_DIR_NAMES for part in parts[:-1]):
        return True
    return bool(_TEST_FILE_RE.search(parts[-1]))


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def _deleted_test_files(name_status: str) -> list[str]:
    deleted = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].startswith("D") and is_test_file(parts[1]):
            deleted.append(parts[1])
    return deleted


def _diff_findings(diff: str, skip_files: set[str]) -> list[str]:
    findings: list[str] = []
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*?) b/(.*)$", line)
            current = m.group(2) if m else None
            continue
        if current is None or current in skip_files or not is_test_file(current):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            content = line[1:]
            for marker in SKIP_MARKERS:
                if marker in content:
                    findings.append(
                        f"{current}: skip marker added: `{marker}`\n"
                        f"       line: {content.strip()[:120]}"
                    )
            for rx in SKIP_RES:
                if rx.search(content):
                    findings.append(
                        f"{current}: skip marker added: `{rx.pattern}`\n"
                        f"       line: {content.strip()[:120]}"
                    )
        elif line.startswith("-") and not line.startswith("---"):
            content = line[1:]
            for rx in REMOVED_DEFINITION_RES:
                if rx.search(content):
                    findings.append(
                        f"{current}: test definition removed\n"
                        f"       line: {content.strip()[:120]}"
                    )
                    break
    return findings


def main(argv: list[str] | None = None) -> int:
    del argv  # this hook inspects the staged diff, not the file list
    try:
        name_status = _git("diff", "--cached", "--name-status")
        diff = _git("diff", "--cached", "--no-color", "--unified=0")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(
            "[agent-hooks] agent-no-test-weakening: could not run "
            "`git diff --cached` (not a git repository?). Blocking the "
            "commit to be safe."
        )
        return 1

    findings: list[str] = []
    deleted = _deleted_test_files(name_status)
    for path in deleted:
        findings.append(
            f"{path}: test file deleted — restore it; if its tests fail, "
            "fix the code under test"
        )
    findings.extend(_diff_findings(diff, skip_files=set(deleted)))

    if findings:
        return blocked("agent-no-test-weakening", INTRO, findings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
