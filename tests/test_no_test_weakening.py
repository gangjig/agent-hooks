from agent_hooks.no_test_weakening import main
from conftest import commit_all, stage, write

PY_TESTS = (
    "def test_add():\n"
    "    assert add(1, 2) == 3\n"
    "\n"
    "def test_sub():\n"
    "    assert sub(3, 1) == 2\n"
)


def _baseline(repo):
    write(repo, "src/calc.py", "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")
    write(repo, "tests/test_calc.py", PY_TESTS)
    commit_all(repo)


def test_nothing_staged_passes(git_repo, monkeypatch):
    _baseline(git_repo)
    monkeypatch.chdir(git_repo)
    assert main() == 0


def test_production_change_passes(git_repo, monkeypatch):
    _baseline(git_repo)
    write(git_repo, "src/calc.py", "def add(a, b):\n    return a + b  # faster\n\ndef sub(a, b):\n    return a - b\n")
    stage(git_repo, "src/calc.py")
    monkeypatch.chdir(git_repo)
    assert main() == 0


def test_deleted_test_file_blocked(git_repo, monkeypatch, capsys):
    _baseline(git_repo)
    (git_repo / "tests/test_calc.py").unlink()
    stage(git_repo, "tests/test_calc.py")
    monkeypatch.chdir(git_repo)
    assert main() == 1
    assert "test file deleted" in capsys.readouterr().out


def test_removed_python_test_blocked(git_repo, monkeypatch, capsys):
    _baseline(git_repo)
    write(
        git_repo,
        "tests/test_calc.py",
        "def test_add():\n    assert add(1, 2) == 3\n",
    )
    stage(git_repo, "tests/test_calc.py")
    monkeypatch.chdir(git_repo)
    assert main() == 1
    out = capsys.readouterr().out
    assert "test definition removed" in out
    assert "test_sub" in out


def test_added_skip_marker_blocked(git_repo, monkeypatch, capsys):
    _baseline(git_repo)
    write(
        git_repo,
        "tests/test_calc.py",
        "import pytest\n\n@pytest.mark.skip(reason='flaky')\n" + PY_TESTS,
    )
    stage(git_repo, "tests/test_calc.py")
    monkeypatch.chdir(git_repo)
    assert main() == 1
    assert "skip marker added" in capsys.readouterr().out


def test_added_new_test_passes(git_repo, monkeypatch):
    _baseline(git_repo)
    write(
        git_repo,
        "tests/test_calc.py",
        PY_TESTS + "\ndef test_mul():\n    assert mul(2, 3) == 6\n",
    )
    stage(git_repo, "tests/test_calc.py")
    monkeypatch.chdir(git_repo)
    assert main() == 0


def test_edited_test_without_removal_passes(git_repo, monkeypatch):
    _baseline(git_repo)
    write(
        git_repo,
        "tests/test_calc.py",
        PY_TESTS.replace("add(1, 2) == 3", "add(1, 2) == 3  # still three"),
    )
    stage(git_repo, "tests/test_calc.py")
    monkeypatch.chdir(git_repo)
    assert main() == 0


def test_removed_js_it_blocked(git_repo, monkeypatch, capsys):
    write(
        git_repo,
        "tests/app.test.js",
        "it('adds', () => { expect(add(1, 2)).toBe(3); });\n"
        "it('subs', () => { expect(sub(3, 1)).toBe(2); });\n",
    )
    commit_all(git_repo)
    write(
        git_repo,
        "tests/app.test.js",
        "it('adds', () => { expect(add(1, 2)).toBe(3); });\n",
    )
    stage(git_repo, "tests/app.test.js")
    monkeypatch.chdir(git_repo)
    assert main() == 1
    assert "test definition removed" in capsys.readouterr().out


def test_added_xit_blocked(git_repo, monkeypatch):
    write(
        git_repo,
        "tests/app.test.js",
        "it('adds', () => { expect(add(1, 2)).toBe(3); });\n",
    )
    commit_all(git_repo)
    write(
        git_repo,
        "tests/app.test.js",
        "it('adds', () => { expect(add(1, 2)).toBe(3); });\n"
        "xit('subs', () => { expect(sub(3, 1)).toBe(2); });\n",
    )
    stage(git_repo, "tests/app.test.js")
    monkeypatch.chdir(git_repo)
    assert main() == 1


def test_test_like_code_in_production_file_ignored(git_repo, monkeypatch):
    # A helper named test_* in a non-test file is not a test definition.
    write(
        git_repo,
        "src/helpers.py",
        "def test_connection():\n    return True\n",
    )
    commit_all(git_repo)
    write(git_repo, "src/helpers.py", "def check_connection():\n    return True\n")
    stage(git_repo, "src/helpers.py")
    monkeypatch.chdir(git_repo)
    assert main() == 0
