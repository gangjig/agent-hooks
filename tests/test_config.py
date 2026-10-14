import pytest

from agent_hooks.config import ConfigError, load_config
from conftest import write


def test_missing_config_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.rules == []
    assert config.secrets.extra_patterns == []
    assert config.secrets.allow == []


def test_load_rules_and_secrets(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(
        tmp_path,
        ".agent-hooks.yaml",
        "rules:\n"
        "  - pattern: 'print\\('\n"
        "    paths: ['src/**']\n"
        "    message: 'Use the logger.'\n"
        "  - pattern: 'TODO'\n"
        "    message: 'No TODOs in commits.'\n"
        "secrets:\n"
        "  extra_patterns: ['*.token']\n"
        "  allow: ['.env.example']\n",
    )
    config = load_config()
    assert len(config.rules) == 2
    assert config.rules[0].pattern == "print\\("
    assert config.rules[0].paths == ["src/**"]
    assert config.rules[0].message == "Use the logger."
    assert config.rules[1].paths == []
    assert config.secrets.extra_patterns == ["*.token"]
    assert config.secrets.allow == [".env.example"]


def test_rule_missing_message_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path, ".agent-hooks.yaml", "rules:\n  - pattern: 'x'\n")
    with pytest.raises(ConfigError):
        load_config()
