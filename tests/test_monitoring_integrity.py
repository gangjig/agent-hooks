from agent_hooks.monitoring_integrity import is_oversight_file, main
from conftest import commit_all, stage, write


def _baseline(repo):
    write(repo, "src/app.py", "def handler():\n    return 'ok'\n")
    write(repo, "config/prometheus.yml", "global:\n  scrape_interval: 15s\n")
    write(repo, "evals/run_eval.py", "def grade():\n    return 1.0\n")
    write(
        repo,
        "tests/test_app.py",
        "def test_handler():\n    assert handler() == 'ok'\n",
    )
    commit_all(repo)


def test_nothing_staged_passes(git_repo, monkeypatch):
    _baseline(git_repo)
    monkeypatch.chdir(git_repo)
    assert main([]) == 0


def test_production_change_passes(git_repo, monkeypatch):
    _baseline(git_repo)
    write(git_repo, "src/app.py", "def handler():\n    return 'ok!'\n")
    stage(git_repo, "src/app.py")
    monkeypatch.chdir(git_repo)
    assert main([]) == 0


def test_monitoring_config_change_blocked(git_repo, monkeypatch, capsys):
    _baseline(git_repo)
    write(git_repo, "config/prometheus.yml", "global:\n  scrape_interval: 60s\n")
    stage(git_repo, "config/prometheus.yml")
    monkeypatch.chdir(git_repo)
    assert main([]) == 1
    out = capsys.readouterr().out
    assert "oversight file touched" in out
    assert "prometheus.yml" in out


def test_new_alerting_file_blocked(git_repo, monkeypatch):
    _baseline(git_repo)
    write(git_repo, "ops/alerts.yaml", "groups: []\n")
    stage(git_repo, "ops/alerts.yaml")
    monkeypatch.chdir(git_repo)
    assert main([]) == 1


def test_deleted_eval_file_blocked(git_repo, monkeypatch, capsys):
    _baseline(git_repo)
    (git_repo / "evals/run_eval.py").unlink()
    stage(git_repo, "evals/run_eval.py")
    monkeypatch.chdir(git_repo)
    assert main([]) == 1
    assert "run_eval.py" in capsys.readouterr().out


def test_allowlisted_oversight_file_passes(git_repo, monkeypatch):
    _baseline(git_repo)
    write(
        git_repo,
        ".agent-hooks.yaml",
        "monitoring:\n  allow: ['config/prometheus.yml']\n",
    )
    commit_all(git_repo, "add config")
    write(git_repo, "config/prometheus.yml", "global:\n  scrape_interval: 30s\n")
    stage(git_repo, "config/prometheus.yml")
    monkeypatch.chdir(git_repo)
    assert main([]) == 0


def test_extra_pattern_blocks_custom_path(git_repo, monkeypatch, capsys):
    _baseline(git_repo)
    write(
        git_repo,
        ".agent-hooks.yaml",
        "monitoring:\n  extra_patterns: ['infra/dashboards/**']\n",
    )
    write(git_repo, "infra/dashboards/main.json", "{}\n")
    commit_all(git_repo, "add config and dashboard")
    write(git_repo, "infra/dashboards/main.json", "{\"panels\": []}\n")
    stage(git_repo, "infra/dashboards/main.json")
    monkeypatch.chdir(git_repo)
    assert main([]) == 1
    assert "oversight file touched" in capsys.readouterr().out


def test_monitoring_claim_contradicted_by_diff_blocked(
    git_repo, monkeypatch, capsys
):
    _baseline(git_repo)
    (git_repo / "config/prometheus.yml").unlink()
    stage(git_repo, "config/prometheus.yml")
    msg = write(
        git_repo,
        "COMMIT_EDITMSG",
        "fix eval pipeline\n\nMonitoring is still in place.\n",
    )
    monkeypatch.chdir(git_repo)
    assert main(["--commit-msg-file", str(msg)]) == 1
    out = capsys.readouterr().out
    assert "claims monitoring/oversight is intact" in out
    assert "self-report is not evidence" in out.lower()


def test_monitoring_claim_consistent_with_diff_passes(git_repo, monkeypatch):
    _baseline(git_repo)
    write(git_repo, "src/app.py", "def handler():\n    return 'ok v2'\n")
    stage(git_repo, "src/app.py")
    msg = write(
        git_repo,
        "COMMIT_EDITMSG",
        "tweak handler\n\nMonitoring is still in place.\n",
    )
    monkeypatch.chdir(git_repo)
    assert main(["--commit-msg-file", str(msg)]) == 0


def test_tests_pass_claim_contradicted_by_deleted_test_blocked(
    git_repo, monkeypatch, capsys
):
    _baseline(git_repo)
    (git_repo / "tests/test_app.py").unlink()
    stage(git_repo, "tests/test_app.py")
    msg = write(git_repo, "COMMIT_EDITMSG", "fix bug — all tests pass\n")
    monkeypatch.chdir(git_repo)
    assert main(["--commit-msg-file", str(msg)]) == 1
    out = capsys.readouterr().out
    assert "claims the tests pass" in out
    assert "test file deleted" in out


def test_commit_msg_positional_arg_supported(git_repo, monkeypatch, capsys):
    # The pre-commit framework passes the commit message file positionally
    # when this hook runs at the commit-msg stage.
    _baseline(git_repo)
    (git_repo / "evals/run_eval.py").unlink()
    stage(git_repo, "evals/run_eval.py")
    msg = write(git_repo, "COMMIT_EDITMSG", "cleanup; no monitoring removed\n")
    monkeypatch.chdir(git_repo)
    assert main([str(msg)]) == 1
    assert "claims monitoring/oversight is intact" in capsys.readouterr().out


def test_is_oversight_file_matching():
    assert is_oversight_file("config/prometheus.yml", [], [])
    assert is_oversight_file("src/monitoring/health.py", [], [])
    assert is_oversight_file("evals/run_eval.py", [], [])
    assert is_oversight_file("app/logging_config.py", [], [])
    assert not is_oversight_file("src/app.py", [], [])
    # Word-shaped patterns: "retrieval" contains "eval" but is not an eval.
    assert not is_oversight_file("src/retrieval.py", [], [])
    # Allowlist wins over the built-ins.
    assert not is_oversight_file(
        "config/prometheus.yml", [], ["config/prometheus.yml"]
    )
