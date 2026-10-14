import pytest

from agent_hooks.secrets_guard import main
from conftest import write


@pytest.mark.parametrize(
    "filename",
    [
        ".env",
        ".env.local",
        "config/credentials.json",
        "my-credentials.txt",
        "certs/server.pem",
        "keys/private.key",
        "id_rsa",
        "home/id_rsa.pub",
        "app_secret.txt",
        "secrets/prod.env",
    ],
)
def test_default_patterns_block(tmp_path, monkeypatch, filename):
    monkeypatch.chdir(tmp_path)
    assert main([filename]) == 1


@pytest.mark.parametrize(
    "filename",
    ["README.md", "src/main.py", "docker-compose.yml", "notes/environment.md"],
)
def test_clean_files_pass(tmp_path, monkeypatch, filename):
    monkeypatch.chdir(tmp_path)
    assert main([filename]) == 0


def test_allowlist_permits_template(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(
        tmp_path,
        ".agent-hooks.yaml",
        "secrets:\n  allow: ['.env.example']\n",
    )
    assert main([".env.example"]) == 0
    # ...but the real thing is still blocked.
    assert main([".env.production"]) == 1


def test_extra_patterns_from_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(
        tmp_path,
        ".agent-hooks.yaml",
        "secrets:\n  extra_patterns: ['*.token']\n",
    )
    assert main(["auth.token"]) == 1
    assert main(["auth.txt"]) == 0


def test_message_tells_agent_to_involve_the_human(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([".env"]) == 1
    out = capsys.readouterr().out
    assert "human" in out
    assert "out-of-band" in out


def test_no_files_passes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
