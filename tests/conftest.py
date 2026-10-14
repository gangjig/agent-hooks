"""Shared fixtures and helpers for the agent-hooks test suite."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def run_git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def write(repo: Path, rel: str, content: str) -> Path:
    path = Path(repo) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def stage(repo: Path, *paths: str) -> None:
    run_git(repo, "add", *paths)


def commit_all(repo: Path, message: str = "baseline") -> None:
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real git repo with a baseline commit-ready identity configured."""
    run_git(tmp_path, "init", "-q")
    run_git(tmp_path, "config", "user.email", "test@example.com")
    run_git(tmp_path, "config", "user.name", "Test User")
    run_git(tmp_path, "config", "commit.gpgsign", "false")
    return tmp_path
