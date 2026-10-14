"""Loading and validating ``.agent-hooks.yaml`` from the user's repo.

Config format::

    rules:
      - pattern: 'requests\\.get\\('
        paths: ['src/**']            # optional glob list
        message: 'Use the shared HTTP client in app/http.py instead.'

    secrets:
      extra_patterns: ['*.token']
      allow: ['.env.example']

A missing config file is not an error: pattern rules default to none,
the secrets guard falls back to its built-in patterns, and the
test-weakening hook needs no config at all.
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
class Config:
    rules: list[Rule] = field(default_factory=list)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)


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
    return Config(rules=rules, secrets=secrets)
