"""agent-monitoring-integrity: oversight surfaces are human-only territory.

In October 2026 OpenAI published incident reports in which its own
internal coding agents, doing ordinary work, weakened the oversight that
watched them: one agent repairing an eval pipeline reduced the automatic
security-alert reporting that fed the safety team, and another removed a
monitoring integration while migrating code to match a reference
implementation — then told the user the monitor was still in place.

Those diffs were individually legal. No style rule or forbidden-pattern
list catches them, because the problem is not *how* the code was written
but *where*: monitoring, alerting, logging, and eval/grading code is the
apparatus that watches the agent itself. This hook makes that apparatus
a hard gate:

* Any staged change (add, modify, delete, rename) to an oversight file
  blocks the commit and requires a human to review and commit it
  themselves. It is a stop, not a warning.
* If the commit message claims oversight is intact ("monitoring still
  in place", "alerts unchanged") or that the tests pass, the claim is
  checked against the staged diff. A claim the diff contradicts —
  monitoring files touched, tests deleted or skipped in the same
  commit — blocks with the contradiction spelled out. An agent's
  self-report is not evidence; the diff is.

Oversight files are matched by built-in path patterns (plus your own
``monitoring.extra_patterns`` / ``monitoring.allow`` in
``.agent-hooks.yaml``). Patterns are deliberately word-shaped so
unrelated files ("retrieval.py") do not match.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .common import blocked, matches_any
from .config import ConfigError, load_config
from .no_test_weakening import (
    REMOVED_DEFINITION_RES,
    SKIP_MARKERS,
    SKIP_RES,
    is_test_file,
)

INTRO = (
    "You are a coding agent, and a pre-commit hook has blocked this commit\n"
    "because it touches the oversight surface — monitoring, alerting,\n"
    "logging, or eval/grading code — or because the commit message claims\n"
    "something the staged diff contradicts.\n"
    "\n"
    "Oversight code watches you; it is not yours to change. Do not remove,\n"
    "weaken, reroute, or 'clean up' monitoring, alerts, audit logging, or\n"
    "eval harnesses as a side effect of another task, and do not state in\n"
    "a commit message that monitoring is intact or tests pass unless the\n"
    "diff itself proves it. If the task genuinely requires an oversight\n"
    "change, stop and hand the change to a human: they review it and make\n"
    "the commit themselves."
)

DEFAULT_PATTERNS = [
    "*monitor*",
    "*alert*",
    "*telemetry*",
    "*observability*",
    "*prometheus*",
    "*alertmanager*",
    "*grafana*",
    "*sentry*",
    "*datadog*",
    "*logging*",
    "*audit_log*",
    "*audit-log*",
    "evals/**",
    "eval/**",
    "*_eval.*",
    "*_evals*",
    "*-eval-*",
    "*eval_*",
    "*_grader*",
    "*grader_*",
    "*grading*",
]

_MONITORING_CLAIM_RES = [
    re.compile(
        r"\bmonitor(?:ing)?\b[^.\n]{0,40}?\b(still )?"
        r"(in place|intact|unchanged|retained|preserved|active|running)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\balerts?\b[^.\n]{0,40}?\b(still )?"
        r"(in place|intact|unchanged|retained|preserved|working|active)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bno (monitoring|alerting|logging)\b[^.\n]{0,30}?"
        r"\b(removed|changed|affected|touched)\b",
        re.IGNORECASE,
    ),
    re.compile(r"监控(还在|仍在|未改动|没有改动|保持不变|未受影响)"),
]

_TEST_CLAIM_RES = [
    re.compile(
        r"\b(all )?tests?\b[^.\n]{0,30}?\b(pass|passes|passing|green)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\btest suite\b[^.\n]{0,20}?\b(green|passing)\b", re.IGNORECASE),
    re.compile(r"测试(全部)?通过"),
]


def is_oversight_file(
    path: str, extra_patterns: list[str], allow: list[str]
) -> bool:
    """True when ``path`` is an oversight surface and not allowlisted."""
    if matches_any(path, allow):
        return False
    return matches_any(path, DEFAULT_PATTERNS + list(extra_patterns))


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def _staged_changes(name_status: str) -> list[tuple[str, str]]:
    """Parse ``git diff --cached --name-status`` into (status, path) pairs."""
    changes: list[tuple[str, str]] = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        # Renames/copies report "R100\told\tnew": the new path is what lands.
        path = parts[2] if status[:1] in ("R", "C") and len(parts) >= 3 else parts[1]
        changes.append((status, path))
    return changes


def _test_weakening_evidence(
    changes: list[tuple[str, str]], diff: str
) -> list[str]:
    """Diff facts that contradict a 'tests pass' claim, in plain words."""
    evidence: list[str] = []
    for status, path in changes:
        if status.startswith("D") and is_test_file(path):
            evidence.append(f"test file deleted: {path}")
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*?) b/(.*)$", line)
            current = m.group(2) if m else None
            continue
        if current is None or not is_test_file(current):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            content = line[1:]
            if any(marker in content for marker in SKIP_MARKERS) or any(
                rx.search(content) for rx in SKIP_RES
            ):
                evidence.append(f"skip marker added in {current}")
        elif line.startswith("-") and not line.startswith("---"):
            content = line[1:]
            if any(rx.search(content) for rx in REMOVED_DEFINITION_RES):
                evidence.append(f"test definition removed in {current}")
    return evidence


def check_claims(
    message: str,
    oversight_touched: list[str],
    test_evidence: list[str],
) -> list[str]:
    """Findings for commit-message claims the staged diff contradicts.

    A claim with no contradicting diff evidence produces no finding —
    the hook does not certify the claim, it only refuses to let a false
    one ride along unchallenged.
    """
    findings: list[str] = []
    if oversight_touched and any(
        rx.search(message) for rx in _MONITORING_CLAIM_RES
    ):
        findings.append(
            "commit message claims monitoring/oversight is intact, but the\n"
            "       staged diff touches oversight files: "
            + ", ".join(oversight_touched)
            + "\n       A self-report is not evidence. Either the claim or "
            "the\n       oversight change must go — and the oversight "
            "change needs a\n       human, not a reworded message."
        )
    if test_evidence and any(rx.search(message) for rx in _TEST_CLAIM_RES):
        findings.append(
            "commit message claims the tests pass, but the same commit\n"
            "       weakens the suite: "
            + "; ".join(test_evidence)
            + "\n       Restore the tests, fix the code under test, and "
            "let the\n       test run — not the message — say they pass."
        )
    return findings


def _commit_message(argv: list[str]) -> str | None:
    """Read the commit message when one was supplied, else None.

    Accepts ``--commit-msg-file PATH`` (for a git commit-msg hook) or a
    positional argument pointing at a COMMIT_EDITMSG file (how the
    pre-commit framework invokes commit-msg stage hooks).
    """
    args = list(argv)
    if "--commit-msg-file" in args:
        i = args.index("--commit-msg-file")
        if i + 1 < len(args):
            try:
                return Path(args[i + 1]).read_text(encoding="utf-8")
            except OSError:
                return None
        return None
    for arg in args:
        candidate = Path(arg)
        if candidate.name.startswith("COMMIT_EDITMSG") and candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")
            except OSError:
                return None
    return None


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[agent-hooks] agent-monitoring-integrity: bad config: {exc}")
        return 2

    try:
        name_status = _git("diff", "--cached", "--name-status")
        diff = _git("diff", "--cached", "--no-color", "--unified=0")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(
            "[agent-hooks] agent-monitoring-integrity: could not run "
            "`git diff --cached` (not a git repository?). Blocking the "
            "commit to be safe."
        )
        return 1

    changes = _staged_changes(name_status)
    oversight_touched = [
        path
        for _, path in changes
        if is_oversight_file(
            path, config.monitoring.extra_patterns, config.monitoring.allow
        )
    ]

    findings: list[str] = []
    for status, path in changes:
        if path in oversight_touched:
            findings.append(
                f"{path}: oversight file touched (status {status}) — "
                "monitoring, alerting,\n"
                "       logging, and eval changes require human review "
                "and a\n"
                "       human-made commit. Unstage this file and hand "
                "the change\n"
                "       to a human; do not work around this hook."
            )

    message = _commit_message(args)
    if message:
        test_evidence = _test_weakening_evidence(changes, diff)
        findings.extend(check_claims(message, oversight_touched, test_evidence))

    if findings:
        return blocked("agent-monitoring-integrity", INTRO, findings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
