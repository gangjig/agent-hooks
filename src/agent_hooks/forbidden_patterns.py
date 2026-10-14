"""agent-forbidden-patterns: block staged files that match project rules.

Rules come from ``.agent-hooks.yaml`` in the user's repo. Each rule is a
regex, an optional glob list scoping which paths it applies to, and a
corrective message that is shown to the agent when the rule fires.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from .common import blocked, matches_any
from .config import ConfigError, Rule, load_config

INTRO = (
    "You are a coding agent, and a pre-commit hook has blocked this commit\n"
    "because staged code matches a forbidden pattern from .agent-hooks.yaml.\n"
    "This is a mechanical project rule, not a suggestion."
)


def _applicable(rule: Rule, filename: str) -> bool:
    return not rule.paths or matches_any(filename, rule.paths)


def main(argv: list[str] | None = None) -> int:
    files = list(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[agent-hooks] agent-forbidden-patterns: bad config: {exc}")
        return 2
    if not config.rules or not files:
        return 0

    compiled: list[tuple[Rule, re.Pattern[str]]] = []
    for rule in config.rules:
        try:
            compiled.append((rule, re.compile(rule.pattern)))
        except re.error as exc:
            print(
                f"[agent-hooks] agent-forbidden-patterns: bad regex "
                f"{rule.pattern!r} in .agent-hooks.yaml: {exc}"
            )
            return 2

    findings: list[str] = []
    for filename in files:
        path = Path(filename)
        if not path.is_file():
            continue
        rules = [(rule, rx) for rule, rx in compiled if _applicable(rule, filename)]
        if not rules:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for rule, rx in rules:
                if rx.search(line):
                    findings.append(
                        f"{filename}:{lineno} matches forbidden pattern "
                        f"`{rule.pattern}`\n"
                        f"       line: {line.strip()[:120]}\n"
                        f"       fix:  {rule.message}"
                    )

    if findings:
        return blocked("agent-forbidden-patterns", INTRO, findings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
