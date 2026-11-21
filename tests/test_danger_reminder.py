"""Tests for agent-danger-reminder: the tool-return danger reminder hook.

The design principle under test comes from ROGUE-AI (arXiv:2611.26553):
a reminder helps only when it names the SPECIFIC danger and its
consequence. So these tests assert the reminder names the object at
risk (the table, the path) and states what is lost — and that generic
safety wording never appears.
"""

import json

import pytest

from agent_hooks.config import Config, load_config
from agent_hooks.danger_reminder import (
    BUILTIN_RULES,
    ToolCall,
    find_reminders,
    main,
    parse_record,
)
from conftest import write

CONFIG = (
    "danger_reminder:\n"
    "  rules:\n"
    "    - pattern: 'deploy\\.sh\\s+prod'\n"
    "      danger: 'this deploys to production'\n"
    "      consequence: 'a bad deploy takes the live site down until it is rolled back'\n"
)


def _setup(tmp_path, monkeypatch, config=CONFIG):
    monkeypatch.chdir(tmp_path)
    if config is not None:
        write(tmp_path, ".agent-hooks.yaml", config)


def _remind(text, tool="Bash", config=None):
    return find_reminders(
        ToolCall(name=tool, text=text), config or Config()
    )


def _texts(reminders):
    return [r.text for r in reminders]


# --- built-in rules: the reminder names the specific danger -------------


def test_drop_table_names_the_table_and_the_loss():
    out = _texts(_remind("psql -c 'DROP TABLE users;'"))
    assert len(out) == 1
    assert "users" in out[0]
    assert "drops the database table" in out[0]
    assert "all its rows are gone" in out[0]


def test_drop_table_is_case_insensitive():
    assert _texts(_remind("drop table if exists sessions"))


def test_drop_database_names_the_database():
    out = _texts(_remind("DROP DATABASE analytics;"))
    assert "analytics" in out[0]
    assert "entire database" in out[0]


def test_delete_without_where_fires_and_names_the_table():
    out = _texts(_remind("DELETE FROM sessions;"))
    assert "sessions" in out[0]
    assert "no WHERE clause" in out[0]
    assert "every row" in out[0]


def test_delete_with_where_stays_silent():
    assert _remind("DELETE FROM sessions WHERE user_id = 7;") == []


def test_truncate_fires():
    out = _texts(_remind("TRUNCATE TABLE events;"))
    assert "events" in out[0]
    assert "cannot be rolled back" in out[0]


def test_update_without_where_fires():
    out = _texts(_remind("UPDATE users SET admin = true;"))
    assert "users" in out[0]
    assert "no WHERE clause" in out[0]


def test_update_with_where_stays_silent():
    assert _remind("UPDATE users SET admin = true WHERE id = 3;") == []


def test_rm_recursive_names_the_target():
    out = _texts(_remind("rm -rf /data/old-backups"))
    assert "/data/old-backups" in out[0]
    assert "recursively deletes" in out[0]
    assert "cannot be recovered" in out[0]


def test_plain_rm_is_not_dangerous():
    assert _remind("rm /tmp/scratch.txt") == []


def test_force_push_fires():
    out = _texts(_remind("git push origin main --force"))
    assert "force-pushes" in out[0]
    assert "discarded" in out[0]


def test_force_with_lease_stays_silent():
    # --force-with-lease is the safe variant; it must not match.
    assert _remind("git push --force-with-lease origin main") == []


def test_reset_hard_fires():
    out = _texts(_remind("git reset --hard HEAD~1"))
    assert "uncommitted changes are discarded" in out[0]


def test_destroy_infrastructure_fires():
    out = _texts(_remind("terraform destroy -auto-approve"))
    assert "destroys infrastructure" in out[0]
    assert "does not restore the data" in out[0]
    out = _texts(_remind("kubectl delete deployment api"))
    assert "kubectl delete" in out[0]


def test_harmless_tool_call_is_silent():
    assert _remind("ls -la src/") == []
    assert _remind("SELECT id FROM users WHERE active = true;") == []
    assert _remind("pytest tests/ -q") == []


def test_multiple_dangers_produce_multiple_reminders():
    out = _texts(_remind("DROP TABLE a; DROP DATABASE b;"))
    assert len(out) == 2
    assert any("table a" in t for t in out)
    assert any("database b" in t for t in out)


def test_no_reminder_is_generic():
    # The ROGUE-AI finding: generic tips do not help. No reminder may
    # fall back to vague safety wording.
    for rule in BUILTIN_RULES:
        assert "be careful" not in rule.danger.lower()
        assert "be careful" not in rule.consequence.lower()
    out = _texts(_remind("DROP TABLE t; rm -rf /x; git push --force"))
    for text in out:
        assert "be careful" not in text.lower()


# --- record parsing ------------------------------------------------------


def test_parse_claude_code_post_tool_use_shape():
    call = parse_record(
        {
            "tool_name": "Bash",
            "tool_input": {"command": "DROP TABLE users;"},
            "tool_response": "DROP TABLE",
        }
    )
    assert call.name == "Bash"
    assert "DROP TABLE users" in call.text
    assert "DROP TABLE" in call.text


def test_parse_dict_input_leaves_no_json_punctuation_in_target():
    # Regression: tool_input as a dict used to be json.dumps'd, so the
    # rm-recursive rule captured '/tmp/build"}' as its target.
    call = parse_record(
        {"tool_name": "Bash", "tool_input": {"command": "rm -rf /tmp/build"}}
    )
    assert "{" not in call.text and "}" not in call.text
    texts = _texts(find_reminders(call, Config()))
    assert texts == [
        "Reminder: this command recursively deletes /tmp/build"
        " — deleted files cannot be recovered unless a backup exists."
    ]


def test_parse_plain_variants():
    call = parse_record(
        {"tool": "Bash", "command": "rm -rf /x", "output": "done"}
    )
    assert call.name == "Bash"
    assert "rm -rf /x" in call.text


def test_parse_non_object_is_an_error():
    with pytest.raises(ValueError, match="JSON object"):
        parse_record(["DROP TABLE users;"])


def test_result_text_is_also_searched():
    # The danger can be visible only in what the tool returned.
    call = parse_record(
        {"tool_name": "Bash", "tool_input": {"command": "run-migration.sh"},
         "tool_response": "DROP TABLE legacy_users; -- done"}
    )
    assert _texts(find_reminders(call, Config()))


# --- custom rules and config --------------------------------------------


def test_custom_rule_from_config(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    config = load_config()
    out = _texts(find_reminders(ToolCall("Bash", "deploy.sh prod"), config))
    assert len(out) == 1
    assert "deploys to production" in out[0]
    assert "takes the live site down" in out[0]


def test_custom_rule_tool_filter(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        "danger_reminder:\n"
        "  rules:\n"
        "    - pattern: 'prod'\n"
        "      tool: 'Bash'\n"
        "      danger: 'production tool call'\n"
        "      consequence: 'it acts on live data'\n",
    )
    config = load_config()
    assert find_reminders(ToolCall("Bash", "prod"), config)
    assert find_reminders(ToolCall("Read", "prod"), config) == []


def test_custom_rule_unless_suppresses(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        "danger_reminder:\n"
        "  rules:\n"
        "    - pattern: 'DELETE'\n"
        "      unless: 'WHERE'\n"
        "      danger: 'a delete'\n"
        "      consequence: 'rows go away'\n",
    )
    config = load_config()
    assert find_reminders(ToolCall("Bash", "DELETE FROM t;"), config)
    assert find_reminders(ToolCall("Bash", "DELETE FROM t WHERE id=1;"), config) == []


def test_disable_builtin_by_id(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        "danger_reminder:\n  disable: ['reset-hard']\n",
    )
    config = load_config()
    assert find_reminders(ToolCall("Bash", "git reset --hard"), config) == []
    assert find_reminders(ToolCall("Bash", "DROP TABLE t;"), config)


def test_custom_rule_interpolates_match_and_groups(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        "danger_reminder:\n"
        "  rules:\n"
        "    - pattern: 'rm\\s+(?P<target>\\S+\\.db)'\n"
        "      danger: 'this deletes the database file {target} ({match})'\n"
        "      consequence: 'the data is gone'\n",
    )
    config = load_config()
    out = _texts(find_reminders(ToolCall("Bash", "rm app.db"), config))
    assert "app.db" in out[0]
    assert "rm app.db" in out[0]


def test_bad_custom_regex_is_a_config_error(tmp_path, monkeypatch, capsys):
    _setup(
        tmp_path,
        monkeypatch,
        "danger_reminder:\n"
        "  rules:\n"
        "    - pattern: '(['\n"
        "      danger: 'x'\n"
        "      consequence: 'y'\n",
    )
    config = load_config()
    with pytest.raises(ValueError, match="bad regex"):
        find_reminders(ToolCall("Bash", "x"), config)


def test_config_missing_keys_is_an_error(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        "danger_reminder:\n  rules:\n    - pattern: 'x'\n      danger: 'y'\n",
    )
    with pytest.raises(Exception, match="danger_reminder.rules\\[0\\]"):
        load_config()


# --- CLI -----------------------------------------------------------------


def test_cli_text_output(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.stdin",
        _FakeStdin('{"tool_name": "Bash", "tool_input": {"command": "DROP TABLE users;"}}'),
    )
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Reminder:" in out
    assert "users" in out
    assert "gone unless a backup exists" in out


def test_cli_silent_when_safe(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.stdin", _FakeStdin('{"tool_name": "Read", "tool_input": {"file_path": "a.py"}}')
    )
    assert main([]) == 0
    assert capsys.readouterr().out == ""


def test_cli_json_format(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.stdin", _FakeStdin('{"tool_name": "Bash", "command": "rm -rf /x"}')
    )
    assert main(["--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["reminders"]) == 1
    assert "/x" in data["reminders"][0]


def test_cli_never_blocks(monkeypatch, capsys):
    # Even the most dangerous call exits 0: this hook reminds, it does
    # not gate.
    monkeypatch.setattr(
        "sys.stdin",
        _FakeStdin('{"tool_name": "Bash", "command": "rm -rf /; DROP DATABASE prod;"}'),
    )
    assert main([]) == 0


def test_cli_empty_input_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _FakeStdin(""))
    assert main([]) == 2
    assert "no tool-call record" in capsys.readouterr().err


def test_cli_invalid_json_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _FakeStdin("{not json"))
    assert main([]) == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_cli_bad_format_is_an_error(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _FakeStdin("{}"))
    assert main(["--format", "yaml"]) == 2


def test_cli_input_file(tmp_path, monkeypatch, capsys):
    record = tmp_path / "record.json"
    record.write_text(
        '{"tool_name": "Bash", "command": "git push --force"}', encoding="utf-8"
    )
    assert main(["--input", str(record)]) == 0
    assert "force-pushes" in capsys.readouterr().out


def test_cli_config_file(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "custom.yaml"
    cfg.write_text(
        "danger_reminder:\n"
        "  rules:\n"
        "    - pattern: 'launch'\n"
        "      danger: 'a launch'\n"
        "      consequence: 'rockets are expensive'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.stdin", _FakeStdin('{"command": "launch"}'))
    assert main(["--config", str(cfg)]) == 0
    assert "rockets are expensive" in capsys.readouterr().out


class _FakeStdin:
    """A stdin stand-in with a .read() that returns fixed text."""

    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text
