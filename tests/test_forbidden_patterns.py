from agent_hooks.forbidden_patterns import main
from conftest import write

CONFIG = (
    "rules:\n"
    "  - pattern: 'requests\\.get\\('\n"
    "    message: 'Use the shared HTTP client in app/http.py.'\n"
)


def _setup(tmp_path, monkeypatch, config=CONFIG):
    monkeypatch.chdir(tmp_path)
    if config is not None:
        write(tmp_path, ".agent-hooks.yaml", config)


def test_match_blocks_and_reports_file_line_and_fix(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    write(tmp_path, "src/api.py", "import requests\n\nrequests.get(url)\n")
    assert main(["src/api.py"]) == 1
    out = capsys.readouterr().out
    assert "src/api.py:3" in out
    assert "COMMIT REJECTED" in out
    assert "Use the shared HTTP client in app/http.py." in out


def test_no_match_passes(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    write(tmp_path, "src/api.py", "from app.http import get\nget(url)\n")
    assert main(["src/api.py"]) == 0


def test_paths_glob_scopes_the_rule(tmp_path, monkeypatch):
    _setup(
        tmp_path,
        monkeypatch,
        "rules:\n"
        "  - pattern: 'requests\\.get\\('\n"
        "    paths: ['src/**']\n"
        "    message: 'wrapper only in src'\n",
    )
    write(tmp_path, "docs/example.py", "requests.get(url)\n")
    write(tmp_path, "src/api.py", "requests.get(url)\n")
    assert main(["docs/example.py"]) == 0
    assert main(["src/api.py"]) == 1


def test_no_config_means_no_rules(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, config=None)
    write(tmp_path, "src/api.py", "requests.get(url)\n")
    assert main(["src/api.py"]) == 0


def test_multiple_files_each_reported(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    write(tmp_path, "a.py", "requests.get(u)\n")
    write(tmp_path, "b.py", "x = 1\nrequests.get(u)\n")
    assert main(["a.py", "b.py"]) == 1
    out = capsys.readouterr().out
    assert "a.py:1" in out
    assert "b.py:2" in out


def test_missing_file_is_ignored(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert main(["does/not/exist.py"]) == 0


def test_bad_regex_is_a_config_error_not_a_crash(tmp_path, monkeypatch, capsys):
    _setup(
        tmp_path,
        monkeypatch,
        "rules:\n  - pattern: '(['\n    message: 'broken'\n",
    )
    write(tmp_path, "a.py", "x = 1\n")
    assert main(["a.py"]) == 2
    assert "bad regex" in capsys.readouterr().out
