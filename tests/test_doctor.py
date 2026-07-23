from types import SimpleNamespace

from cli import main


def resolved_config(**overrides: str) -> dict[str, main.ResolvedValue]:
    values = {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_BRANCH_NAME": "",
        "CODEMATE_PR_NUMBER": "",
        "CODEMATE_ISSUE_NUMBER": "",
        "CODEMATE_GIT_REPO_URL": "https://github.com/BoringHappy/CodeMate.git",
        "CODEMATE_GITHUB_TOKEN": "token",
        "CODEMATE_GIT_USER_NAME": "CodeMate",
        "CODEMATE_GIT_USER_EMAIL": "codemate@example.com",
        "CODEMATE_ALLOW_COUNTRY": "US",
        "CODEMATE_ALLOW_IP": "",
    }
    values.update(overrides)
    return {
        key: main.ResolvedValue(value, "test", main.FIELD_BY_NAME[key])
        for key, value in values.items()
    }


def test_doctor_allows_general_health_check_without_target(monkeypatch, tmp_path) -> None:
    config_dir = tmp_path / ".codemate"
    (config_dir / ".claude").mkdir(parents=True)
    (config_dir / ".claude.json").write_text("{}")
    monkeypatch.setattr(main.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(main.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    checks = main.doctor_checks(resolved_config(), tmp_path)
    target = next(check for check in checks if check.name == "Target")

    assert target.status == "warn"
    assert not [check for check in checks if check.status == "fail"]


def test_doctor_reports_all_configuration_failures(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(main.shutil, "which", lambda name: None)
    config = resolved_config(
        CODEMATE_AGENT="other",
        CODEMATE_GIT_REPO_URL="",
        CODEMATE_GITHUB_TOKEN="",
        CODEMATE_GIT_USER_EMAIL="",
        CODEMATE_ALLOW_COUNTRY="",
    )

    checks = main.doctor_checks(config, tmp_path)
    failures = {check.name for check in checks if check.status == "fail"}

    assert {
        "Global config",
        "docker",
        "git",
        "gh",
        "Agent",
        "Repository",
        "GitHub auth",
        "Git identity",
        "Access allowlist",
    } <= failures


def test_doctor_does_not_print_repository_credentials(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(main.shutil, "which", lambda name: None)
    config = resolved_config(
        CODEMATE_GIT_REPO_URL="https://user:secret@example.com/org/project.git",
    )

    checks = main.doctor_checks(config, tmp_path)
    repository = next(check for check in checks if check.name == "Repository")

    assert repository.detail == "project (via test)"
    assert "secret" not in repository.detail


def test_doctor_mode_does_not_require_launch_target_or_global_config(monkeypatch) -> None:
    args = SimpleNamespace(
        setup=False,
        update=False,
        doctor=True,
    )
    config = resolved_config()
    called = []
    monkeypatch.setattr(main, "resolve_config", lambda args, cwd: config)
    monkeypatch.setattr(main, "run_doctor", lambda config, cwd: called.append(True))
    monkeypatch.setattr(
        main,
        "ensure_global_config",
        lambda: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    main.run_codemate(args)

    assert called == [True]
