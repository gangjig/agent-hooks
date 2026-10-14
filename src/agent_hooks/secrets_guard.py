"""agent-secrets-guard: never let secret-bearing files into a commit.

Agents are fast and literal: if a task seems to require editing ``.env``,
an agent will happily do it and commit the result. This hook makes that
impossible and tells the agent what to do instead — ask the human to make
the change out-of-band, where the secret values never enter the session.
"""

from __future__ import annotations

import sys

from .common import blocked, matches_any
from .config import ConfigError, load_config

DEFAULT_PATTERNS = [
    ".env*",
    "*credentials*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "*secret*",
]

INTRO = (
    "You are a coding agent, and a pre-commit hook has blocked this commit\n"
    "because it contains files that look like they hold secrets.\n"
    "\n"
    "Secrets must never be committed — and you must not edit these files\n"
    "through this session either. If the task needs a secret created or\n"
    "changed, stop and ask the human to do it out-of-band (their own\n"
    "editor, a secrets manager, a side channel), then continue your work\n"
    "without ever seeing the secret values. Refer to secrets by name only."
)


def main(argv: list[str] | None = None) -> int:
    files = list(sys.argv[1:] if argv is None else argv)
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[agent-hooks] agent-secrets-guard: bad config: {exc}")
        return 2
    if not files:
        return 0

    patterns = DEFAULT_PATTERNS + config.secrets.extra_patterns
    allow = config.secrets.allow
    flagged = [
        f
        for f in files
        if matches_any(f, patterns) and not matches_any(f, allow)
    ]
    if not flagged:
        return 0

    findings = [f"{f} matches the secrets guard" for f in flagged]
    findings.append(
        "If one of these files is actually a harmless template with no real\n"
        "       values, a human can allowlist it under `secrets.allow` in\n"
        "       .agent-hooks.yaml. Do not add the allowlist entry yourself."
    )
    return blocked("agent-secrets-guard", INTRO, findings)


if __name__ == "__main__":
    raise SystemExit(main())
