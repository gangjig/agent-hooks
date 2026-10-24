"""agent-skill-scan: vet an Agent Skill before it is installed or run.

Agent Skills (the open format stewarded by the Agentic AI Foundation) are
folders of instructions plus optional scripts that an agent loads and
follows — effectively code and standing orders distributed as Markdown.
That makes a skill a supply-chain surface: security researchers have
already catalogued hundreds of published skills carrying high-impact
malicious payloads, and the standard's own reference validator
(``skills-ref``) checks only the shallowest format rules.

This scanner checks a skill directory on two layers, *before* an agent
ever loads it:

* **Spec conformance** — the frontmatter rules as written in the
  specification (docs/specification.mdx of agentskills/agentskills,
  which is authoritative). It is deliberately stricter than the
  reference validator where the spec text is unambiguous: an empty
  ``compatibility`` (spec: 1-500 characters if provided) and a YAML-list
  ``allowed-tools`` (spec: a space-separated *string*) both pass
  ``skills-ref`` today and are errors here. Where the spec question is
  genuinely open upstream — whether non-string ``metadata`` values are
  legal (a shipping runtime stores nested maps there) — the finding is
  a warning, not an error, and says so.
* **Safety** — the bundled scripts and the instruction text itself.
  Scripts are scanned for download-and-execute pipes, decode-and-exec
  obfuscation, credential-store access, destructive commands, and
  persistence/privilege moves. The Markdown (SKILL.md body and
  references) is scanned for instruction-layer attacks: orders to
  ignore the operator's instructions, to conceal actions from the
  user, or to exfiltrate data — plus ``allowed-tools`` entries that
  request unrestricted shell access.

Usage: ``agent-skill-scan [PATH ...] [--format text|json]``. A path may
be a skill directory, a ``SKILL.md`` file, or a directory whose
immediate subdirectories are skills. Exit 1 when any error-severity
finding exists; warnings are reported but do not fail the scan.
Skill directories listed in ``skills.allow`` in ``.agent-hooks.yaml``
are skipped.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .common import matches_any
from .config import ConfigError, load_config

MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_COMPATIBILITY_LENGTH = 500
MAX_BODY_LINES = 500

ALLOWED_FIELDS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}

SCRIPT_SUFFIXES = {
    ".py", ".sh", ".bash", ".zsh", ".js", ".mjs", ".cjs", ".ts",
    ".rb", ".pl", ".ps1", ".bat", ".cmd",
}

ERROR = "error"
WARNING = "warning"


@dataclass
class Finding:
    severity: str
    check: str
    path: str
    message: str
    line: int | None = None

    def render(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"[{self.severity}] ({self.check}) {loc} — {self.message}"


# ---------------------------------------------------------------------------
# Safety patterns
#
# Each entry: (check id, compiled regex, message). Patterns run against
# scripts and, where marked, against Markdown instruction text. They are
# intentionally specific: a skill that discusses these techniques in
# prose (e.g. a security-education skill) can be allow-listed in config.

def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


SCRIPT_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "download-pipe-shell",
        _rx(r"\b(curl|wget)\b[^|\n]*\|\s*(sudo\s+)?(sh|bash|zsh|dash|python\d*|perl|ruby|node)\b"),
        "downloads remote content and pipes it straight into an interpreter; "
        "the fetched bytes are unreviewed code — vendor the script or pin and verify it instead",
    ),
    (
        "download-exec",
        _rx(r"(\bsh\s+-c\s+[\"']?\$\(\s*(curl|wget)|\b(iex|invoke-expression)\b[^\\n]*\b(downloadstring|download|invoke-webrequest|invoke-restmethod)|\bexec\s*\([^)]*(urlopen|requests\.get|fetch\())"),
        "executes content fetched from the network at runtime; "
        "the executed code is whatever the server returns today, not what was reviewed",
    ),
    (
        "decode-and-execute",
        _rx(r"(base64\s+(-d|--decode|-D)\b[^|\n]*\|\s*(sh|bash|zsh))|(\b(b64decode|atob)\s*\([^)]*\)[^\\n]{0,80}\b(exec|eval)\b)|(\b(exec|eval)\s*\([^)]*\b(b64decode|atob)\b)"),
        "decodes obfuscated content and executes it; "
        "base64 is not encryption — decode it, ship the plain code, and let reviewers read it",
    ),
    (
        "credential-access",
        _rx(r"(~|\$HOME|%USERPROFILE%)[/\\]\.(ssh|aws|gnupg|azure|kube)\b|\bid_(rsa|ed25519|ecdsa|dsa)\b|[/\\]\.netrc\b|\bprintenv\b(?!\s+\w)|\b(os\.environ\b[^\\n]{0,60}\b(items|keys|values)\s*\()"),
        "touches credential stores or dumps the whole environment; "
        "a skill should name the single secret it needs and read only that",
    ),
    (
        "destructive-command",
        _rx(r"\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+(/|~/|~|\$HOME|/\*)(\s|$)|(\bmkfs\b|\bdd\b[^\\n]*\bof=/dev/|:()\s*{\s*:\|:&\s*};:)"),
        "contains a destructive command aimed at the filesystem root, the "
        "home directory, or a raw device; skills must scope deletions to their own work area",
    ),
    (
        "persistence-or-privilege",
        _rx(r"(\bcrontab\s+-|\bsudo\s+\w|>>?\s*~?/\.?(bashrc|zshrc|profile|bash_profile)\b|\blaunchctl\s+(load|bootstrap)|\bsystemctl\s+(enable|start)|\bchmod\s+(-R\s+)?(777|a\+rwx))"),
        "installs persistence (cron, shell profile, service) or escalates "
        "privilege; a skill runs inside a task, it does not move into the machine",
    ),
]

INSTRUCTION_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "instruction-override",
        _rx(r"\b(ignore|disregard|forget|override)\b[^.\n]{0,50}\b(previous|prior|above|system|operator|developer|original)\b[^.\n]{0,30}\b(instruction|prompt|rule|message)s?\b"),
        "instructs the agent to ignore or override the operator's own "
        "instructions; a skill extends the agent's orders, it never outranks them",
    ),
    (
        "concealment",
        _rx(r"\b(do not|don't|never|must not)\b[^.\n]{0,40}\b(tell|inform|notify|mention to|reveal to|disclose to|report to)\b[^.\n]{0,30}\b(the )?(user|operator|human|owner)\b|\bwithout\s+(telling|informing|notifying|asking)\b[^.\n]{0,30}\b(the )?(user|operator|human|owner)\b|\bhide\b[^.\n]{0,30}\bfrom\b[^.\n]{0,20}\b(the )?(user|operator|human|owner)\b"),
        "instructs the agent to conceal its actions from the user; "
        "legitimate steps survive daylight — remove the secrecy, keep the step",
    ),
    (
        "exfiltration",
        _rx(r"\b(send|upload|post|forward|transmit|email|exfiltrate)\b[^.\n]{0,80}(https?://|[\w.+-]+@[\w-]+\.[\w.]+)|\b(send|upload|forward|transmit|exfiltrate)\b[^.\n]{0,60}\b(the user's|user's|their|all)\b[^.\n]{0,30}\b(files?|documents?|conversation|history|data|contents?|credentials?|tokens?|keys?)\b[^.\n]{0,30}\bto\b"),
        "instructs the agent to send data to an outside destination; "
        "a skill's outputs belong to the user — name the destination and get consent in the open",
    ),
]


# ---------------------------------------------------------------------------
# Frontmatter parsing


def _split_frontmatter(text: str) -> tuple[dict | None, str, str | None]:
    """Return (metadata, body, error). Metadata is None on parse failure."""
    if not text.startswith("---"):
        return None, text, "SKILL.md does not start with a YAML frontmatter block (---)"
    end = text.find("\n---", 3)
    if end == -1:
        return None, text, "SKILL.md frontmatter block is never closed (---)"
    raw = text[3:end]
    body = text[end + 4 :]
    try:
        meta = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        return None, body, f"frontmatter is not valid YAML: {exc}"
    if not isinstance(meta, dict):
        return None, body, "frontmatter must be a YAML mapping of fields"
    return meta, body, None


# ---------------------------------------------------------------------------
# Conformance checks


def _check_name(meta: dict, skill_dir: Path, rel: str, out: list[Finding]) -> None:
    def err(msg: str) -> None:
        out.append(Finding(ERROR, "spec-name", rel, msg))

    if "name" not in meta:
        err("missing required field 'name'")
        return
    name = meta["name"]
    if not isinstance(name, str) or not name.strip():
        err("field 'name' must be a non-empty string")
        return
    name = unicodedata.normalize("NFKC", name.strip())
    if len(name) > MAX_NAME_LENGTH:
        err(f"name is {len(name)} characters; the spec allows at most {MAX_NAME_LENGTH}")
    if name != name.lower():
        err("name must be lowercase")
    if name.startswith("-") or name.endswith("-"):
        err("name must not start or end with a hyphen")
    if "--" in name:
        err("name must not contain consecutive hyphens")
    if not all(c.isalnum() or c == "-" for c in name):
        err("name may only contain letters, digits, and hyphens")
    dir_name = unicodedata.normalize("NFKC", skill_dir.name)
    if dir_name != name:
        err(f"name '{name}' does not match the parent directory name '{skill_dir.name}'")


def _check_description(meta: dict, rel: str, out: list[Finding]) -> None:
    if "description" not in meta:
        out.append(Finding(ERROR, "spec-description", rel, "missing required field 'description'"))
        return
    desc = meta["description"]
    if not isinstance(desc, str) or not desc.strip():
        out.append(Finding(ERROR, "spec-description", rel, "field 'description' must be a non-empty string"))
    elif len(desc) > MAX_DESCRIPTION_LENGTH:
        out.append(Finding(ERROR, "spec-description", rel,
                           f"description is {len(desc)} characters; the spec allows at most {MAX_DESCRIPTION_LENGTH}"))


def _check_optional_fields(meta: dict, rel: str, out: list[Finding]) -> None:
    compat = meta.get("compatibility")
    if compat is not None:
        if not isinstance(compat, str):
            out.append(Finding(ERROR, "spec-compatibility", rel, "field 'compatibility' must be a string"))
        elif not (1 <= len(compat) <= MAX_COMPATIBILITY_LENGTH):
            out.append(Finding(ERROR, "spec-compatibility", rel,
                               f"compatibility is {len(compat)} characters; the spec requires 1-{MAX_COMPATIBILITY_LENGTH} when the field is provided"))
    for fname in ("allowed-tools", "license"):
        value = meta.get(fname)
        if value is not None and not isinstance(value, str):
            out.append(Finding(ERROR, f"spec-{fname}", rel,
                               f"field '{fname}' must be a string (the spec defines it as a string); "
                               f"got {type(value).__name__} — most clients will mis-read it"))
    metadata = meta.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            out.append(Finding(ERROR, "spec-metadata", rel, "field 'metadata' must be a mapping"))
        else:
            non_string = [k for k, v in metadata.items() if not isinstance(k, str) or not isinstance(v, str)]
            if non_string:
                out.append(Finding(WARNING, "spec-metadata", rel,
                                   f"metadata entries {sorted(map(str, non_string))} are not string-to-string; "
                                   "the spec defines metadata as a map from string keys to string values "
                                   "(some runtimes accept nested values — the question is open upstream, "
                                   "so this is a warning, not an error)"))
    extra = sorted(set(meta) - ALLOWED_FIELDS)
    if extra:
        out.append(Finding(ERROR, "spec-fields", rel,
                           f"unexpected frontmatter fields: {', '.join(extra)}; "
                           f"the spec allows only {', '.join(sorted(ALLOWED_FIELDS))}"))


def _check_allowed_tools(meta: dict, rel: str, out: list[Finding]) -> None:
    tools = meta.get("allowed-tools")
    if not isinstance(tools, str):
        return
    for token in tools.split():
        if token == "*" or token in ("Bash(*)", "Bash(*:*)"):
            out.append(Finding(ERROR, "unrestricted-tools", rel,
                               f"allowed-tools requests '{token}', which pre-approves unrestricted "
                               "execution; scope it to the commands the skill actually needs (e.g. Bash(git:*))"))


# ---------------------------------------------------------------------------
# Content checks


def _scan_lines(
    path: Path,
    rel: str,
    patterns: list[tuple[str, re.Pattern[str], str]],
    out: list[Finding],
) -> None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for lineno, line in enumerate(lines, 1):
        for check, rx, message in patterns:
            if rx.search(line):
                out.append(Finding(ERROR, check, rel, message, line=lineno))
                break


def scan_skill(skill_dir: Path) -> list[Finding]:
    """Scan one skill directory; returns all findings."""
    skill_dir = Path(skill_dir)
    rel_root = skill_dir.name
    out: list[Finding] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [Finding(ERROR, "missing-skill-md", rel_root,
                        "directory has no SKILL.md; it is not a loadable skill")]
    text = skill_md.read_text(encoding="utf-8", errors="replace")
    meta, body, error = _split_frontmatter(text)
    rel_md = f"{rel_root}/SKILL.md"
    if error is not None:
        out.append(Finding(ERROR, "frontmatter-parse", rel_md, error))
        meta = None
    if meta is not None:
        _check_name(meta, skill_dir, rel_md, out)
        _check_description(meta, rel_md, out)
        _check_optional_fields(meta, rel_md, out)
        _check_allowed_tools(meta, rel_md, out)
        body_lines = body.count("\n") + 1
        if body_lines > MAX_BODY_LINES:
            out.append(Finding(WARNING, "body-length", rel_md,
                               f"SKILL.md body is ~{body_lines} lines; the spec recommends under "
                               f"{MAX_BODY_LINES} — move detail into references/ so it loads on demand"))

    # Instruction-layer patterns apply to every Markdown file (the body
    # and anything under references/); script patterns to script files.
    # Credential/destructive/persistence script patterns also apply to
    # Markdown: instructions that tell the agent to run a destructive
    # command are the same hazard with one more step.
    script_like = SCRIPT_PATTERNS
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path == skill_md:
            continue
        rel = f"{rel_root}/{path.relative_to(skill_dir).as_posix()}"
        if path.suffix.lower() in SCRIPT_SUFFIXES:
            _scan_lines(path, rel, script_like, out)
        elif path.suffix.lower() == ".md":
            _scan_lines(path, rel, INSTRUCTION_PATTERNS + script_like[3:], out)
    if meta is not None:
        # Line numbers are reported against the whole SKILL.md, so add
        # back the lines the frontmatter occupied.
        offset = text[: len(text) - len(body)].count("\n")
        for lineno, line in enumerate(body.splitlines(), 1):
            for check, rx, message in INSTRUCTION_PATTERNS + script_like[3:]:
                if rx.search(line):
                    out.append(Finding(ERROR, check, rel_md, message,
                                       line=lineno + offset))
                    break
    return out


def discover_skills(paths: list[Path]) -> list[Path]:
    """Resolve CLI paths to skill directories."""
    skills: list[Path] = []
    for path in paths:
        if path.is_file() and path.name == "SKILL.md":
            skills.append(path.parent)
        elif path.is_dir():
            if (path / "SKILL.md").is_file():
                skills.append(path)
            else:
                skills.extend(
                    child for child in sorted(path.iterdir())
                    if child.is_dir() and (child / "SKILL.md").is_file()
                )
    return skills


INTRO = (
    "A skill is standing orders plus bundled code that an agent will follow\n"
    "without re-reviewing it line by line. These findings are the reasons not\n"
    "to install or load the skill as-is. Fix them in the skill itself — do not\n"
    "silence the scanner, and do not narrow its patterns to make a finding\n"
    "go away."
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    fmt = "text"
    if "--format" in args:
        i = args.index("--format")
        try:
            fmt = args[i + 1]
        except IndexError:
            print("agent-skill-scan: --format needs a value (text|json)", file=sys.stderr)
            return 2
        del args[i : i + 2]
    paths = [Path(a) for a in args] or [Path.cwd()]

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"agent-skill-scan: {exc}", file=sys.stderr)
        return 2

    skills = discover_skills(paths)
    if not skills:
        print("agent-skill-scan: no skills found (no SKILL.md under the given paths)",
              file=sys.stderr)
        return 2

    results: dict[str, list[Finding]] = {}
    for skill in skills:
        if config.skills.allow and matches_any(
            skill.as_posix(), config.skills.allow
        ):
            continue
        results[skill.name] = scan_skill(skill)

    errors = sum(1 for fs in results.values() for f in fs if f.severity == ERROR)
    warnings = sum(1 for fs in results.values() for f in fs if f.severity == WARNING)

    if fmt == "json":
        payload = {
            "skills": {
                name: [
                    {"severity": f.severity, "check": f.check, "path": f.path,
                     "line": f.line, "message": f.message}
                    for f in fs
                ]
                for name, fs in results.items()
            },
            "errors": errors,
            "warnings": warnings,
        }
        print(json.dumps(payload, indent=2))
        return 1 if errors else 0

    if errors or warnings:
        bar = "=" * 72
        lines = [bar, "[agent-hooks] agent-skill-scan — FINDINGS", bar, INTRO, ""]
        for name, fs in results.items():
            if not fs:
                lines.append(f"{name}: OK")
                continue
            lines.append(f"{name}:")
            for i, f in enumerate(fs, 1):
                lines.append(f"  {i}. {f.render()}")
        lines += ["", f"Summary: {errors} error(s), {warnings} warning(s) "
                        f"across {len(results)} skill(s)."]
        if errors:
            lines.append("Do not install or load a skill with error findings until they are fixed.")
        print("\n".join(lines))
        return 1 if errors else 0

    for name in results:
        print(f"{name}: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
