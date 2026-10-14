# agent-hooks

**Pre-commit hooks that enforce your project's rules on AI coding agents — mechanically, at commit time — and explain the fix to the agent when they block.**

```yaml
# .pre-commit-config.yaml
repos:
  - repo: <url-of-this-repository>
    rev: v0.1.0
    hooks:
      - id: agent-forbidden-patterns
      - id: agent-secrets-guard
      - id: agent-no-test-weakening
```

## The problem

Every team working with coding agents (Claude Code, Codex, Cursor, …)
writes rules down: *use the shared HTTP client*, *never touch the `.env`
file*, *don't weaken the tests to make them pass*. Those rules live in
`AGENTS.md` / `CLAUDE.md` — and compliance is **probabilistic**. The agent
follows the rule most of the time, forgets it under context pressure, or
rationalizes an exception at exactly the wrong moment. A written rule is a
request; the failure mode is silent.

## The pattern

Move the rules that actually matter out of prose and into the commit path:

1. **Mechanical enforcement.** A [pre-commit](https://pre-commit.com) hook
   is deterministic. It runs on every commit, whoever — or whatever — made
   it, and a failing hook means the commit does not happen.
2. **Corrective output written for the agent.** When a hook blocks, its
   output is not a lint-style error for a human. It is an instruction
   addressed to the agent: which file, which line, which rule, and exactly
   what to change. The agent reads the failure, fixes its own work, and
   retries — the same loop it already uses for failing tests.

This is Geoffrey Huntley's pattern for agent guardrails, packaged as three
ready-to-use hooks.

## The hooks

### `agent-forbidden-patterns`

Blocks staged files that match rules from **your** repo's
`.agent-hooks.yaml`. Each rule is a regex, an optional `paths` glob list,
and a `message` — the corrective instruction the agent will read.

### `agent-secrets-guard`

Blocks commits that add secret-bearing files: `.env*`, `*credentials*`,
`*.pem`, `*.key`, `id_rsa*`, `*secret*` (plus your own extra patterns).
Secrets are edited by the human, out-of-band, so secret values never enter
an agent session at all. Templates can be allowlisted.

### `agent-no-test-weakening`

Inspects the staged diff (`git diff --cached`) and blocks commits that
make the suite "pass" by weakening it: deleting a test file, removing a
test definition (`def test_…`, `it('…')`, `test('…')`, `func Test…(`), or
adding a skip marker (`@pytest.mark.skip`, `xit(`, `it.skip`, `@Disabled`,
`#[ignore]`, …). Adding tests and editing production code pass freely.

## Quickstart

1. Install pre-commit and the hooks:

   ```bash
   pip install pre-commit
   ```

2. Add the snippet at the top of this README to your
   `.pre-commit-config.yaml` (point `repo:` at wherever you host this
   project), then:

   ```bash
   pre-commit install
   ```

3. Write your rules in `.agent-hooks.yaml` at your repo root:

   ```yaml
   rules:
     - pattern: 'requests\.get\('
       message: >-
         Do not call requests directly. Use the shared HTTP client in
         app/http.py (from app.http import get), which adds retries,
         timeouts, and request logging.
     - pattern: 'sqlite3\.connect\('
       paths: ['src/**']
       message: >-
         Do not open database connections here. Go through the
         repository layer in src/repositories/.

   secrets:
     extra_patterns: ['*.token']
     allow: ['.env.example']   # template with placeholder values only
   ```

   With no `.agent-hooks.yaml` at all, the forbidden-patterns hook has no
   rules (passes), the secrets guard uses its built-in patterns, and the
   test-weakening hook works as-is.

## How an agent self-corrects

An agent adds a quick HTTP call and tries to commit:

```text
$ git commit -m "fetch user profile"
========================================================================
[agent-hooks] agent-forbidden-patterns — COMMIT REJECTED
========================================================================
You are a coding agent, and a pre-commit hook has blocked this commit
because staged code matches a forbidden pattern from .agent-hooks.yaml.
This is a mechanical project rule, not a suggestion.

Findings:
  1. src/profile.py:4 matches forbidden pattern `requests\.get\(`
       line: data = requests.get(url)
       fix:  Do not call requests directly. Use the shared HTTP client in
             app/http.py (from app.http import get), which adds retries,
             timeouts, and request logging.

Fix every finding above, re-stage your changes, and commit again.
Do not bypass this hook (no --no-verify) and do not edit the hook
configuration to make the finding go away.
```

The agent swaps in the shared client, re-stages, and the commit goes
through — no human involved, and the rule held 10 times out of 10.

## Design notes

- **stdout is the API.** A hook has exactly two channels to the agent: the
  exit code and its output. agent-hooks treats both as first-class: exit 1
  stops the commit, and the text is written as instructions, not logs.
- **Rules live in the consuming repo**, not in this package. A hook pack
  can't know your architecture; `.agent-hooks.yaml` is where your "use the
  wrapper, not the library" knowledge becomes enforceable.
- **Narrow on purpose.** These hooks do not try to judge code quality.
  Each one checks a small, decidable property (a regex, a filename, a diff
  shape), which is what makes them trustworthy as hard gates.
- **Fast and offline.** Pure Python + PyYAML, no network, no model calls.
  A guardrail that is slow or flaky gets disabled; these run in
  milliseconds.
- **Humans benefit too.** The same hooks catch the same mistakes in
  human-written commits — the agent framing just makes the messages
  explicit enough for anyone to act on.

## Development

```bash
pip install -e ".[test]"
python -m pytest
```

## License

MIT — see [LICENSE](LICENSE).
