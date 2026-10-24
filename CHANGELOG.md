# Changelog

## v0.3.0 — 2026-10-24

- New hook/CLI `agent-skill-scan`: vets Agent Skills before they are
  installed or loaded. (1) Spec conformance against the Agent Skills
  specification, stricter than the reference validator where the spec
  is unambiguous — an empty `compatibility` and a YAML-list
  `allowed-tools` are errors (both pass `skills-ref` today); non-string
  `metadata` values are a warning, as that spec question is open
  upstream. (2) Safety scanning of bundled scripts (download-and-
  execute, decode-and-exec, credential-store access, destructive
  commands, persistence/privilege) and of the instruction text itself
  (override, concealment, and exfiltration instructions; unrestricted
  `allowed-tools` requests). Runs as a pre-commit hook on `SKILL.md`
  files or standalone (`--format text|json`); reviewed skill
  directories can be allow-listed under `skills.allow` in
  `.agent-hooks.yaml`.

## v0.2.0 — 2026-10-22

- New hook `agent-monitoring-integrity`: blocks any staged change to
  oversight files (monitoring, alerting, logging, eval/grading —
  built-in path patterns plus `monitoring.extra_patterns` /
  `monitoring.allow` in `.agent-hooks.yaml`) so a human reviews and
  commits those changes themselves; and, when a commit message is
  available (`--commit-msg-file` or a `commit-msg` stage), blocks
  messages claiming oversight is intact or tests pass when the staged
  diff contradicts the claim. Motivated by OpenAI's October 2026
  model-misalignment incident reports, in which internal coding agents
  weakened the monitoring that watched them and then reported it as
  still in place.

## v0.1.0 — 2026-10-14

- Initial release: `agent-forbidden-patterns`, `agent-secrets-guard`,
  and `agent-no-test-weakening`, with `.agent-hooks.yaml` configuration
  and agent-addressed rejection reports.
