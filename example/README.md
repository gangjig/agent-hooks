# Examples

- `.agent-hooks.yaml` — a sample rule set: a shared-HTTP-client rule, a
  repository-layer rule scoped to `src/**`, a debug-output rule, plus
  secrets-guard extras and an allowlist for templates.
- `.pre-commit-config.yaml` — how a consuming repo enables the three
  hooks (point `repo:` at your own clone/fork of agent-hooks).

Copy both files into the root of your project, adjust the rules to your
codebase, and run `pre-commit install` once. From then on the hooks run
on every commit — human or agent.
