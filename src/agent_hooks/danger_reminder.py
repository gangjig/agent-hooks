"""agent-danger-reminder: remind the agent of the specific danger it just
invoked, right after a tool call returns.

Motivation: the ROGUE-AI study (arXiv:2611.26553) reconstructed 108 real
agent accidents and re-ran them against five frontier models. The models
repeated the reported harm in 18%-33% of runs — and in 62 of the 72
accidents that caused real harm, there was no attacker at all: the agent
simply did its job and missed an ordinary danger standing next to it (a
database shared with a live site, a destructive default, ...). The one
intervention that helped across all five models was a reminder inserted
after each tool result that named the *specific* danger and its
consequence. A same-length generic safety tip did not help.

This hook is that intervention, as a command an agent harness runs after
each tool call (for example a Claude Code PostToolUse hook). It reads a
JSON tool-call record — tool name, arguments, and result — and prints one
short reminder per danger it detects, naming the danger and what it
costs. It is silent when nothing dangerous is detected, so a reminder
stays rare enough to be read, and it never blocks: it always exits 0.

Usage: ``agent-danger-reminder [--input FILE] [--format text|json]
[--config FILE]``. With no ``--input`` the JSON record is read from
stdin. Rules come from the ``danger_reminder`` section of
``.agent-hooks.yaml`` plus the built-ins in this module.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import Config, ConfigError, load_config

_TOOL_NAME_KEYS = ("tool_name", "tool", "name")
_INPUT_KEYS = ("tool_input", "input", "arguments", "command")
_RESPONSE_KEYS = ("tool_response", "tool_result", "result", "output", "response")


@dataclass(frozen=True)
class Rule:
    """One danger pattern and the reminder it produces when it matches.

    ``danger`` and ``consequence`` are templates: ``{match}`` expands to
    the matched text and ``{name}`` to a named regex group, so the
    reminder can name the specific object at risk (the table, the path).
    ``tool`` optionally scopes the rule to one tool (a regex on the tool
    name); ``unless`` is a regex that suppresses the rule when it also
    matches — that is how the built-in DELETE rule stays silent when the
    statement has a WHERE clause.
    """

    id: str
    pattern: str
    danger: str
    consequence: str
    tool: str = ""
    unless: str = ""


# Built-in rules. Each one names a concrete danger and its consequence;
# a generic "be careful" reminder is not one of these — the ROGUE-AI
# finding is that only the specific wording helps.
BUILTIN_RULES: list[Rule] = [
    Rule(
        id="drop-table",
        pattern=r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?P<table>[\"'`]?[\w.]+[\"'`]?)",
        danger="this command drops the database table {table}",
        consequence="the table and all its rows are gone unless a backup exists",
    ),
    Rule(
        id="drop-database",
        pattern=r"DROP\s+DATABASE\s+(?:IF\s+EXISTS\s+)?(?P<database>[\"'`]?[\w.]+[\"'`]?)",
        danger="this command drops the entire database {database}",
        consequence="every table in the database is lost unless a backup exists",
    ),
    Rule(
        id="delete-without-where",
        pattern=r"DELETE\s+FROM\s+(?P<table>[\"'`]?[\w.]+[\"'`]?)",
        unless=r"\bWHERE\b",
        danger="this command deletes rows from {table} with no WHERE clause",
        consequence="every row in the table is deleted",
    ),
    Rule(
        id="truncate-table",
        pattern=r"TRUNCATE\s+(?:TABLE\s+)?(?P<table>[\"'`]?[\w.]+[\"'`]?)",
        danger="this command empties the table {table}",
        consequence="all rows are removed at once and cannot be rolled back",
    ),
    Rule(
        id="update-without-where",
        pattern=r"UPDATE\s+(?P<table>[\"'`]?[\w.]+[\"'`]?)\s+SET\b",
        unless=r"\bWHERE\b",
        danger="this command updates {table} with no WHERE clause",
        consequence="every row in the table gets the new values",
    ),
    Rule(
        id="rm-recursive",
        pattern=r"\brm\s+(?:-[a-z]*r[a-z]*\s+|--recursive\s+)(?P<target>\S+)",
        danger="this command recursively deletes {target}",
        consequence="deleted files cannot be recovered unless a backup exists",
    ),
    Rule(
        id="force-push",
        pattern=r"\bgit\s+push\b[^\n]*?--force(?:\s|$)",
        danger="this command force-pushes to a shared branch",
        consequence="commits based on the old history can be discarded",
    ),
    Rule(
        id="reset-hard",
        pattern=r"\bgit\s+reset\s+--hard\b",
        danger="this command resets the working tree with --hard",
        consequence="uncommitted changes are discarded and cannot be recovered",
    ),
    Rule(
        id="destroy-infrastructure",
        pattern=(
            r"(?P<command>terraform\s+destroy|kubectl\s+delete\b"
            r"|helm\s+uninstall\b|aws\s+\S+\s+delete-\S+"
            r"|aws\s+ec2\s+terminate-instances|gcloud\s+\S+\s+delete\b)"
        ),
        danger="this command destroys infrastructure ({command})",
        consequence="the resource and its data are gone; recreating it does not restore the data",
    ),
]


@dataclass
class ToolCall:
    name: str
    text: str  # searchable text: tool name + arguments + result


@dataclass
class Reminder:
    rule_id: str
    text: str


class _SafeFormat(dict):  # type: ignore[type-arg]
    """A format mapping that leaves unknown {placeholders} untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def parse_record(data: object) -> ToolCall:
    """Pull the tool name, arguments, and result out of a JSON record.

    Accepts the Claude Code PostToolUse shape (``tool_name`` /
    ``tool_input`` / ``tool_response``) and plain variants (``tool`` /
    ``input`` / ``result``, ``command`` / ``output``). Anything else in
    the record is ignored.
    """
    if not isinstance(data, dict):
        raise ValueError("tool-call record must be a JSON object")
    name = ""
    for key in _TOOL_NAME_KEYS:
        if isinstance(data.get(key), str):
            name = data[key]
            break
    parts = [name]
    for keys in (_INPUT_KEYS, _RESPONSE_KEYS):
        for key in keys:
            if key in data and data[key] is not None:
                parts.append(_as_text(data[key]))
                break
    return ToolCall(name=name, text="\n".join(p for p in parts if p))


@dataclass
class _CompiledRule:
    rule: Rule
    rx: re.Pattern[str]
    tool_rx: re.Pattern[str] | None
    unless_rx: re.Pattern[str] | None


def _compile(rule: Rule) -> _CompiledRule:
    try:
        rx = re.compile(rule.pattern, re.IGNORECASE)
        tool_rx = re.compile(rule.tool, re.IGNORECASE) if rule.tool else None
        unless_rx = (
            re.compile(rule.unless, re.IGNORECASE) if rule.unless else None
        )
    except re.error as exc:
        raise ValueError(f"bad regex in rule {rule.id!r}: {exc}") from exc
    return _CompiledRule(rule=rule, rx=rx, tool_rx=tool_rx, unless_rx=unless_rx)


def _active_rules(config: Config) -> list[_CompiledRule]:
    compiled: list[_CompiledRule] = []
    disabled = set(config.danger_reminder.disable)
    for rule in BUILTIN_RULES:
        if rule.id not in disabled:
            compiled.append(_compile(rule))
    for i, custom in enumerate(config.danger_reminder.rules):
        compiled.append(_compile(Rule(id=f"custom-{i}", **vars(custom))))
    return compiled


def _render(template: str, match: re.Match[str]) -> str:
    values = {"match": match.group(0)}
    values.update({k: v for k, v in match.groupdict().items() if v is not None})
    return template.format_map(_SafeFormat(values))


def find_reminders(call: ToolCall, config: Config) -> list[Reminder]:
    """Return one reminder per rule that matches the tool call.

    A rule matches when its pattern matches the tool name, arguments, or
    result, its ``tool`` filter (if any) matches the tool name, and its
    ``unless`` pattern (if any) does not match. The reminder names the
    specific danger and its consequence — never a generic safety tip.
    """
    reminders: list[Reminder] = []
    for compiled in _active_rules(config):
        rule = compiled.rule
        if compiled.tool_rx is not None and not compiled.tool_rx.search(call.name):
            continue
        match = compiled.rx.search(call.text)
        if match is None:
            continue
        if compiled.unless_rx is not None and compiled.unless_rx.search(call.text):
            continue
        danger = _render(rule.danger, match)
        consequence = _render(rule.consequence, match)
        reminders.append(
            Reminder(
                rule_id=rule.id,
                text=f"Reminder: {danger} — {consequence}.",
            )
        )
    return reminders


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    fmt = "text"
    input_path: str | None = None
    config_path: str | None = None
    while args:
        arg = args.pop(0)
        if arg in ("--format", "--input", "--config"):
            if not args:
                print(
                    f"agent-danger-reminder: {arg} needs a value",
                    file=sys.stderr,
                )
                return 2
            value = args.pop(0)
            if arg == "--format":
                fmt = value
                if fmt not in ("text", "json"):
                    print(
                        "agent-danger-reminder: --format must be text or json",
                        file=sys.stderr,
                    )
                    return 2
            elif arg == "--input":
                input_path = value
            else:
                config_path = value
        else:
            print(
                f"agent-danger-reminder: unknown argument {arg!r}",
                file=sys.stderr,
            )
            return 2

    try:
        raw = (
            Path(input_path).read_text(encoding="utf-8")
            if input_path
            else sys.stdin.read()
        )
    except OSError as exc:
        print(f"agent-danger-reminder: cannot read input: {exc}", file=sys.stderr)
        return 2
    if not raw.strip():
        print(
            "agent-danger-reminder: no tool-call record on stdin "
            "(expected a JSON object with tool name, arguments, result)",
            file=sys.stderr,
        )
        return 2
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"agent-danger-reminder: invalid JSON: {exc}", file=sys.stderr)
        return 2

    try:
        if config_path is not None:
            if not Path(config_path).is_file():
                print(
                    f"agent-danger-reminder: config file not found: {config_path}",
                    file=sys.stderr,
                )
                return 2
            config = load_config(Path(config_path))
        else:
            config = load_config()
    except ConfigError as exc:
        print(f"[agent-hooks] agent-danger-reminder: bad config: {exc}")
        return 2

    try:
        call = parse_record(data)
        reminders = find_reminders(call, config)
    except ValueError as exc:
        print(f"agent-danger-reminder: {exc}", file=sys.stderr)
        return 2

    if fmt == "json":
        print(
            json.dumps(
                {"reminders": [r.text for r in reminders]}, ensure_ascii=False
            )
        )
    else:
        for reminder in reminders:
            print(reminder.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
