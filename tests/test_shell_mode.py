from types import SimpleNamespace

import pytest

from cli import main


def make_config(**overrides) -> dict:
    values = {
        "CODEMATE_GIT_REPO_URL": "https://github.com/BoringHappy/CodeMate.git",
        "CODEMATE_BRANCH_NAME": "feature/x",
        "CODEMATE_PR_NUMBER": "",
        "CODEMATE_DOCKER_PARAMS": "",
        "CODEMATE_MOUNTS": "",
        "TZ": "UTC",
        "CODEMATE_AGENT": "claude",
        "CODEMATE_IMAGE": "codemate:latest",
    }
    values.update(overrides)
    return {
        key: main.ResolvedValue(value, "test", main.FIELD_BY_NAME[key])
        for key, value in values.items()
    }


def make_args(**overrides) -> SimpleNamespace:
    values = {"mount": [], "docker_param": [], "dry_run": True, "shell": True}
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def codemate_home(monkeypatch, tmp_path):
    custom = tmp_path / "codemate-home"
    (custom / ".claude").mkdir(parents=True)
    monkeypatch.setenv("CODEMATE_HOME", str(custom))
    return custom


def test_shell_mode_runs_zsh_instead_of_the_agent(codemate_home) -> None:
    cmd = main.docker_command(make_config(), make_args(), "/tmp/codemate.env")

    assert cmd[-1] == "zsh"
    assert cmd[cmd.index("--name") + 1] == "codemate-shell-CodeMate-feature-x"
    assert "-it" in cmd


def test_agent_mode_keeps_the_default_entrypoint(codemate_home) -> None:
    cmd = main.docker_command(make_config(), make_args(shell=False), "/tmp/codemate.env")

    assert "zsh" not in cmd
    assert cmd[cmd.index("--name") + 1] == "codemate-claude-CodeMate-feature-x"


def test_shell_and_agent_sessions_use_distinct_container_names(codemate_home) -> None:
    shell_cmd = main.docker_command(make_config(), make_args(), "/tmp/codemate.env")
    agent_cmd = main.docker_command(make_config(), make_args(shell=False), "/tmp/codemate.env")

    assert shell_cmd[shell_cmd.index("--name") + 1] != agent_cmd[agent_cmd.index("--name") + 1]


def test_shell_mode_rejects_an_initial_query() -> None:
    args = SimpleNamespace(setup=False, update=False, shell=True, query="fix the bug")

    with pytest.raises(SystemExit, match="--query"):
        main.run_codemate(args)
