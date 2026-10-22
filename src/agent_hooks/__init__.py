"""agent-hooks: pre-commit hooks that enforce rules for AI coding agents.

The hooks share one idea: a rule written in AGENTS.md is a request, but a
pre-commit hook is a fact. When a hook blocks a commit, its output is a
corrective instruction addressed to the agent that made the change, so the
agent can fix its own work and retry.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
