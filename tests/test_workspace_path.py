from types import SimpleNamespace

import pytest

from cli import main


def make_config(**overrides) -> dict:
    values = {
        "CODEMATE_GIT_REPO_URL": "https://github.com/BoringHappy/CodeMate.git",
        "CODEMATE_BRANCH_NAME": "main",
        "CODEMATE_DOCKER_PARAMS": "",
        "CODEMATE_MOUNTS": "",
        "TZ": "UTC",
        "CODEMATE_AGENT": "claude",
        "CODEMATE_IMAGE": "codemate:latest",
    }
    values.update(overrides)
    return {
        key: main.ResolvedValue(val, "test", main.FIELD_BY_NAME[key])
        for key, val in values.items()
    }


def make_args(**overrides) -> SimpleNamespace:
    values = {
        "mount": [],
        "docker_param": [],
        "network": None,
        "dry_run": True,
        "shell": False,
        "pure": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def host_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def test_paths_under_the_home_keep_their_layout(host_home) -> None:
    project = host_home / "code" / "projecta"
    project.mkdir(parents=True)

    assert main.container_workspace_path(project) == "/home/agent/code/projecta"


def test_the_home_itself_and_other_paths_fall_back_to_a_single_name(host_home, tmp_path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    assert main.container_workspace_path(host_home) == "/home/agent/home"
    assert main.container_workspace_path(outside) == "/home/agent/elsewhere"
    assert main.container_workspace_path(outside, "my-repo") == "/home/agent/my-repo"


def test_pure_mode_mirrors_the_host_home_layout(host_home, monkeypatch) -> None:
    project = host_home / "code" / "projecta"
    project.mkdir(parents=True)
    monkeypatch.chdir(project)
    monkeypatch.setenv("CODEMATE_HOME", str(host_home / ".codemate"))

    cmd = main.pure_docker_command(make_config(), make_args(pure=True), "/tmp/codemate.env")

    assert f"{project}:/home/agent/code/projecta" in cmd
    assert cmd[cmd.index("-w") + 1] == "/home/agent/code/projecta"
    assert cmd[cmd.index("--name") + 1] == "codemate-pure-code-projecta"


def test_pure_mode_falls_back_to_the_directory_name_under_the_home(host_home, monkeypatch) -> None:
    project = host_home / "projecta"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("CODEMATE_HOME", str(host_home / ".codemate"))

    cmd = main.pure_docker_command(make_config(), make_args(pure=True), "/tmp/codemate.env")

    assert f"{project}:/home/agent/projecta" in cmd
    assert cmd[cmd.index("--name") + 1] == "codemate-pure-projecta"


def test_standard_mode_mirrors_the_checkout_path(host_home, monkeypatch) -> None:
    project = host_home / "code" / "projecta"
    project.mkdir(parents=True)
    monkeypatch.chdir(project)
    monkeypatch.setenv("CODEMATE_HOME", str(host_home / ".codemate"))

    config = make_config()
    main.workspace_defaults(config)

    assert config["CODEMATE_REPO_DIR"].value == "/home/agent/code/projecta"
    assert config["CODEMATE_REPO_DIR"].source == "derived"

    cmd = main.docker_command(config, make_args(shell=True), "/tmp/codemate.env")
    assert cmd[cmd.index("-w") + 1] == "/home/agent/code/projecta"


def test_standard_mode_mirrors_the_repository_root_from_a_subdirectory(host_home, monkeypatch) -> None:
    repo = host_home / "code" / "projecta"
    subdirectory = repo / "src"
    subdirectory.mkdir(parents=True)
    monkeypatch.chdir(subdirectory)
    monkeypatch.setattr(
        main,
        "run_capture",
        lambda args: str(repo) if list(args)[:2] == ["git", "rev-parse"] else "",
    )

    assert main.standard_workspace_path(make_config()) == "/home/agent/code/projecta"


def test_standard_mode_keeps_an_explicit_workspace_dir(host_home, monkeypatch) -> None:
    project = host_home / "code" / "projecta"
    project.mkdir(parents=True)
    monkeypatch.chdir(project)

    config = make_config(CODEMATE_REPO_DIR="/home/agent/legacy")
    main.workspace_defaults(config)

    assert config["CODEMATE_REPO_DIR"].value == "/home/agent/legacy"

