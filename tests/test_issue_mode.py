from types import SimpleNamespace

from cli import main


def test_run_codemate_validates_issue_before_deriving_branch(monkeypatch) -> None:
    config = {
        key: main.ResolvedValue(value, "test", main.FIELD_BY_NAME[key])
        for key, value in {
            "CODEMATE_AGENT": "claude",
            "CODEMATE_BRANCH_NAME": "",
            "CODEMATE_PR_NUMBER": "",
            "CODEMATE_ISSUE_NUMBER": "21",
            "CODEMATE_ALLOW_COUNTRY": "US",
            "CODEMATE_GITHUB_TOKEN": "token",
            "CODEMATE_GIT_USER_NAME": "CodeMate",
            "CODEMATE_GIT_USER_EMAIL": "codemate@example.com",
            "CODEMATE_GIT_REPO_URL": "https://github.com/BoringHappy/CodeMate.git",
        }.items()
    }
    args = SimpleNamespace(
        setup=False,
        update=False,
        build=False,
        config=False,
        dry_run=True,
    )

    monkeypatch.setattr(main, "ensure_global_config", lambda: None)
    monkeypatch.setattr(main, "resolve_config", lambda args, cwd: config)
    monkeypatch.setattr(main, "docker_command", lambda config, args, env_path: ["docker"])
    monkeypatch.setattr(main, "print_launch_details", lambda config, args: None)

    main.run_codemate(args)

    assert config["CODEMATE_BRANCH_NAME"].value == "issue-21"
    assert config["CODEMATE_BRANCH_NAME"].source == "derived"
