# agent-hooks

**Pre-commit hooks that enforce your project's rules on AI coding agents — mechanically, at commit time — and explain the fix to the agent when they block.**

```yaml
# .pre-commit-config.yaml
repos:
  - repo: <url-of-this-repository>
    rev: v0.3.1
    hooks:
      - id: agent-forbidden-patterns
      - id: agent-secrets-guard
      - id: agent-no-test-weakening
      - id: agent-monitoring-integrity
      - id: agent-skill-scan
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

This is Geoffrey Huntley's pattern for agent guardrails, packaged as five
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

### `agent-monitoring-integrity`

Treats the oversight surface — monitoring, alerting, logging, and
eval/grading code — as human-only territory. In October 2026 OpenAI
published incident reports in which its own coding agents, doing
ordinary work, weakened the oversight watching them: one repairing an
eval pipeline reduced the automatic security-alert reporting to the
safety team, and another removed a monitoring integration during a code
migration, then told the user the monitor was still in place. Those
diffs were individually legal; the problem was *where* they landed.

This hook inspects the staged diff and:

* **Blocks any staged change to an oversight file** — matched by
  built-in path patterns (`*monitor*`, `*alert*`, `*prometheus*`,
  `*logging*`, `evals/**`, `*grader*`, …) plus your own
  `monitoring.extra_patterns` / `monitoring.allow` in
  `.agent-hooks.yaml`. It is a stop, not a warning: a human reviews the
  change and makes the commit themselves.
* **Checks commit-message claims against the diff.** When a message is
  available (`--commit-msg-file`, or a `commit-msg` stage hook), claims
  like "monitoring still in place" or "all tests pass" are verified
  against the staged diff. A claim the diff contradicts — oversight
  files touched, tests deleted or skipped in the same commit — blocks
  with the contradiction spelled out. An agent's self-report is not
  evidence; the diff is.

Patterns are word-shaped so unrelated files (`retrieval.py`) do not
match. Motivation: [OpenAI's model-misalignment reporting framework](https://openai.com/index/model-misalignment-reporting-framework/)
(October 2026 incident reports).

### `agent-skill-scan`

Vets [Agent Skills](https://agentskills.io) — folders of instructions
plus bundled scripts that an agent loads and follows — before they are
installed, committed, or run. A skill is a supply-chain surface:
security researchers have already catalogued hundreds of published
skills carrying high-impact malicious payloads, while the standard's
own reference validator (`skills-ref`) checks only the shallowest
format rules. This scanner checks two layers:

* **Spec conformance** — the frontmatter rules as written in the
  [specification](https://agentskills.io/specification), and stricter
  than the reference validator where the spec text is unambiguous: an
  empty `compatibility` (spec: 1–500 characters if provided) and a
  YAML-list `allowed-tools` (spec: a space-separated *string*) both
  pass `skills-ref` today and are errors here. Covers required fields,
  name rules (length, case, hyphenation, directory match), field
  types, unknown fields, and body length. Where an upstream spec
  question is genuinely open (non-string `metadata` values), the
  finding is a warning, not an error, and says why.
* **Safety** — bundled scripts are scanned for download-and-execute
  pipes, decode-and-exec obfuscation, credential-store access,
  destructive commands, and persistence/privilege moves; the
  instruction text (SKILL.md body and references) is scanned for
  instruction-layer attacks: orders to ignore the operator's
  instructions, to conceal actions from the user, or to exfiltrate
  data, plus `allowed-tools` entries requesting unrestricted shell.

Run it as a pre-commit hook (it triggers on `SKILL.md` files) or
standalone against any skill directory — or a directory full of them:

```bash
agent-skill-scan ./skills/ --format json   # or text (default)
```

Exit 1 on any error-severity finding; warnings are reported without
failing. Three downgrade rules keep the safety layer honest on
real-world skills (tuned against the official `anthropics/skills`
examples): exfiltration aimed at a host the skill itself documents —
its own API — is a warning, while an undocumented destination stays an
error; override/concealment phrases inside anti-pattern lists are
quoted prohibitions, not orders (warning); credential/persistence
patterns on script comment lines, and persistence patterns in
documentation prose, are likewise warnings with the reason stated.
Nothing is silently dropped — every downgrade is printed as a warning.
On the official examples this tuning takes error findings from 31 to
exactly 1: the one genuine violation in the set (a `description` over
the spec's 1024-character limit). Reviewed-and-pinned skill
directories can be skipped with `skills.allow` in
`.agent-hooks.yaml`. Motivation: Anthropic's
donation of the Agent Skills standard to the
[Agentic AI Foundation](https://aaif.io) (October 2026) and the
foundation's skills-security roadmap.

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

   monitoring:
     extra_patterns: ['infra/dashboards/**']
     allow: ['docs/monitoring-overview.md']   # docs about monitoring are fine
   ```

   With no `.agent-hooks.yaml` at all, the forbidden-patterns hook has no
   rules (passes), the secrets guard uses its built-in patterns, and the
   test-weakening and monitoring-integrity hooks work as-is.

   To also get the claim check, run the monitoring hook at the
   `commit-msg` stage (or from a plain git `commit-msg` hook as
   `agent-monitoring-integrity --commit-msg-file "$1"`), so it can read
   the message it is verifying.

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
