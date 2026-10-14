"""Shared helpers: glob matching and the agent-addressed rejection report."""

from __future__ import annotations

import fnmatch


def matches_any(path: str, patterns: list[str]) -> bool:
    """True when ``path`` matches any glob, by full path or by basename.

    fnmatch's ``*`` also matches ``/``, so ``*.pem`` matches
    ``certs/server.pem`` and ``src/**`` matches everything under ``src/``.
    """
    posix = path.replace("\\", "/")
    basename = posix.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatch(posix, pat) or fnmatch.fnmatch(basename, pat)
        for pat in patterns
    )


def blocked(hook: str, intro: str, findings: list[str]) -> int:
    """Print a rejection report addressed to the agent and return exit 1.

    The output *is* the interface: the agent that made the change reads
    stdout/stderr after its commit fails, so every finding states what is
    wrong and exactly what to do about it.
    """
    bar = "=" * 72
    lines = [
        bar,
        f"[agent-hooks] {hook} — COMMIT REJECTED",
        bar,
        intro,
        "",
        "Findings:",
    ]
    for i, finding in enumerate(findings, 1):
        lines.append(f"  {i}. {finding}")
    lines += [
        "",
        "Fix every finding above, re-stage your changes, and commit again.",
        "Do not bypass this hook (no --no-verify) and do not edit the hook",
        "configuration to make the finding go away.",
    ]
    print("\n".join(lines))
    return 1
