"""Loading and validating ``.agent-hooks.yaml`` from the user's repo.

Config format::

    rules:
      - pattern: 'requests\\.get\\('
        paths: ['src/**']            # optional glob list
        message: 'Use the shared HTTP client in app/http.py instead.'

    secrets:
      extra_patterns: ['*.token']
      allow: ['.env.example']

    monitoring:
      extra_patterns: ['infra/dashboards/**']
      allow: ['docs/monitoring-overview.md']

    skills:
      allow: ['vendor/skills/**']

    danger_reminder:
      rules:
        - pattern: 'deploy\\.sh\\s+prod'
          danger: 'this deploys to production'
          consequence: 'a bad deploy takes the live site down until it is rolled back'
        - pattern: 'psql.*prod'
          tool: 'Bash'
          danger: 'this opens a shell on the production database'
          consequence: 'any statement you run here acts on live user data'
      disable: ['reset-hard']

A missing config file is not an error: pattern rules default to none,
the secrets guard falls back to its built-in patterns, and the
test-weakening and monitoring-integrity hooks use their built-ins.
The danger reminder likewise falls back to its built-in rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_NAMES = (".agent-hooks.yaml", ".agent-hooks.yml")


class ConfigError(Exception):
    """The config file exists but is not valid."""


@dataclass
class Rule:
    pattern: str
    message: str
    paths: list[str] = field(default_factory=list)


@dataclass
class SecretsConfig:
    extra_patterns: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)


@dataclass
class MonitoringConfig:
    extra_patterns: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)


@dataclass
class SkillsConfig:
    allow: list[str] = field(default_factory=list)


@dataclass
class DangerRule:
    pattern: str
    danger: str
    consequence: str
    tool: str = ""
    unless: str = ""


@dataclass
class DangerReminderConfig:
    rules: list[DangerRule] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)


@dataclass
class Config:
    rules: list[Rule] = field(default_factory=list)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    danger_reminder: DangerReminderConfig = field(default_factory=DangerReminderConfig)


def find_config(start: Path | None = None) -> Path | None:
    """Look for a config file in ``start`` (default: current directory)."""
    start = start or Path.cwd()
    for name in CONFIG_NAMES:
        candidate = start / name
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path | None = None) -> Config:
    """Load the config, returning defaults when there is no config file."""
    if path is None:
        path = find_config()
    if path is None:
        return Config()

    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if raw is None:
        return Config()
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top level must be a mapping, got {type(raw).__name__}")

    rules: list[Rule] = []
    for i, item in enumerate(raw.get("rules") or []):
        if not isinstance(item, dict) or "pattern" not in item or "message" not in item:
            raise ConfigError(
                f"{path}: rules[{i}] must have both 'pattern' and 'message' keys"
            )
        rules.append(
            Rule(
                pattern=str(item["pattern"]),
                message=str(item["message"]),
                paths=[str(p) for p in item.get("paths") or []],
            )
        )

    secrets_raw = raw.get("secrets") or {}
    if not isinstance(secrets_raw, dict):
        raise ConfigError(f"{path}: 'secrets' must be a mapping")
    secrets = SecretsConfig(
        extra_patterns=[str(p) for p in secrets_raw.get("extra_patterns") or []],
        allow=[str(p) for p in secrets_raw.get("allow") or []],
    )

    monitoring_raw = raw.get("monitoring") or {}
    if not isinstance(monitoring_raw, dict):
        raise ConfigError(f"{path}: 'monitoring' must be a mapping")
    monitoring = MonitoringConfig(
        extra_patterns=[str(p) for p in monitoring_raw.get("extra_patterns") or []],
        allow=[str(p) for p in monitoring_raw.get("allow") or []],
    )

    skills_raw = raw.get("skills") or {}
    if not isinstance(skills_raw, dict):
        raise ConfigError(f"{path}: 'skills' must be a mapping")
    skills = SkillsConfig(
        allow=[str(p) for p in skills_raw.get("allow") or []],
    )

    danger_raw = raw.get("danger_reminder") or {}
    if not isinstance(danger_raw, dict):
        raise ConfigError(f"{path}: 'danger_reminder' must be a mapping")
    danger_rules: list[DangerRule] = []
    for i, item in enumerate(danger_raw.get("rules") or []):
        if not isinstance(item, dict) or not all(
            k in item for k in ("pattern", "danger", "consequence")
        ):
            raise ConfigError(
                f"{path}: danger_reminder.rules[{i}] must have 'pattern', "
                f"'danger', and 'consequence' keys"
            )
        danger_rules.append(
            DangerRule(
                pattern=str(item["pattern"]),
                danger=str(item["danger"]),
                consequence=str(item["consequence"]),
                tool=str(item.get("tool") or ""),
                unless=str(item.get("unless") or ""),
            )
        )
    danger_reminder = DangerReminderConfig(
        rules=danger_rules,
        disable=[str(d) for d in danger_raw.get("disable") or []],
    )
    return Config(
        rules=rules,
        secrets=secrets,
        monitoring=monitoring,
        skills=skills,
        danger_reminder=danger_reminder,
    )
