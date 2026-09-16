import sys
from types import SimpleNamespace

import pytest

from cli import main


def make_config(**overrides) -> dict:
    values = {
        "CODEMATE_DOCKER_PARAMS": "",
        "CODEMATE_MOUNTS": "",
        "TZ": "UTC",
        "CODEMATE_AGENT": "claude",
        "CODEMATE_IMAGE": main.DEFAULT_PURE_IMAGE,
    }
    values.update(overrides)
    return {
        key: main.ResolvedValue(value, "test", main.FIELD_BY_NAME[key])
        for key, value in values.items()
    }


def make_args(**overrides) -> SimpleNamespace:
    values = {
        "mount": [],
        "docker_param": [],
        "network": None,
        "dry_run": True,
        "shell": False,
        "pure": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_run_args(**overrides) -> SimpleNamespace:
    values = {
        "setup": False,
        "update": False,
        "shell": False,
        "pure": True,
        "query": None,
        "build": False,
        "dockerfile": None,
        "tag": None,
        "config": False,
        "dry_run": True,
        "mount": [],
        "docker_param": [],
        "network": None,
        "env": [],
        "env_file": [],
        "image": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def codemate_home(monkeypatch, tmp_path):
    custom = tmp_path / "codemate-home"
    (custom / ".claude").mkdir(parents=True)
    monkeypatch.setenv("CODEMATE_HOME", str(custom))
    return custom


def test_pure_mode_mounts_the_local_codemate_home(codemate_home, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    cmd = main.pure_docker_command(make_config(), make_args(), "/tmp/codemate.env")

    assert f"{codemate_home}:/home/agent/.codemate" in cmd
    assert f"{codemate_home / '.claude'}:/home/agent/.claude" in cmd


def test_pure_mode_mounts_the_working_directory_and_starts_zsh(codemate_home, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    cmd = main.pure_docker_command(make_config(), make_args(), "/tmp/codemate.env")

    assert f"{tmp_path}:/home/agent/{tmp_path.name}" in cmd
    assert cmd[cmd.index("-w") + 1] == f"/home/agent/{tmp_path.name}"
    assert "-it" in cmd
    # The image's own CMD (zsh) is used; no repository setup command is appended.
    assert cmd[-1] == main.DEFAULT_PURE_IMAGE


def test_pure_mode_uses_a_dedicated_container_name(codemate_home, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    pure_cmd = main.pure_docker_command(make_config(), make_args(), "/tmp/codemate.env")
    shell_cmd = main.docker_command(
        make_config(CODEMATE_GIT_REPO_URL="https://github.com/BoringHappy/CodeMate.git", CODEMATE_BRANCH_NAME="main"),
        make_args(pure=False, shell=True),
        "/tmp/codemate.env",
    )

    assert pure_cmd[pure_cmd.index("--name") + 1] == f"codemate-pure-{main.sanitized(tmp_path.name)}"
    assert pure_cmd[pure_cmd.index("--name") + 1] != shell_cmd[shell_cmd.index("--name") + 1]


def test_pure_mode_adds_custom_mounts_and_docker_params(codemate_home, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    cmd = main.pure_docker_command(
        make_config(),
        make_args(mount=["/host/data:/data"], docker_param=["--network", "bridge"]),
        "/tmp/codemate.env",
    )

    assert "/host/data:/data" in cmd
    # The explicit network parameter wins; no host-network default is added.
    assert cmd.count("--network") == 1
    assert "host" not in cmd


def test_pure_mode_defaults_to_host_network_on_linux(codemate_home, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    cmd = main.pure_docker_command(make_config(), make_args(), "/tmp/codemate.env")

    if sys.platform == "darwin":
        assert "--network" not in cmd
    else:
        assert cmd[cmd.index("--network") + 1] == "host"


def test_network_option_needs_a_value_and_wins_over_docker_params() -> None:
    params = main.resolved_docker_params(
        make_config(),
        make_args(network="none", docker_param=["--network", "bridge"]),
    )

    assert params == ["--network", "none"]


def test_network_docker_param_without_a_value_fails_fast(codemate_home, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit, match="needs a value"):
        main.pure_docker_command(make_config(), make_args(docker_param=["--network"]), "/tmp/codemate.env")


def test_pure_mode_requires_only_docker(monkeypatch) -> None:
    monkeypatch.setattr(main.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(main.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))

    main.check_prerequisites({}, pure=True)

    with pytest.raises(SystemExit, match="Missing required dependencies"):
        main.check_prerequisites({})


def test_pure_mode_skips_repository_and_github_validation(codemate_home, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    # No branch/PR/issue, no GitHub token, no git identity, no repository URL.
    main.run_codemate(make_run_args())

    printed = capsys.readouterr().out
    assert main.DEFAULT_PURE_IMAGE in printed


def test_pure_mode_build_defaults_to_the_pure_dockerfile(codemate_home, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    builds = []
    monkeypatch.setattr(main, "build_image", lambda dockerfile, tag: builds.append((dockerfile, tag)))

    main.run_codemate(make_run_args(build=True))

    assert builds == [(main.DEFAULT_PURE_DOCKERFILE, main.DEFAULT_PURE_TAG)]
    assert main.DEFAULT_PURE_TAG in capsys.readouterr().out


def test_pure_mode_keeps_explicit_image_selection() -> None:
    config = make_config()
    config["CODEMATE_IMAGE"] = main.ResolvedValue(
        "codemate-pure:dev", "cli", main.FIELD_BY_NAME["CODEMATE_IMAGE"]
    )
    assert main.pure_image(config) == "codemate-pure:dev"


def test_pure_mode_ignores_the_configured_standard_image() -> None:
    config = make_config()
    config["CODEMATE_IMAGE"] = main.ResolvedValue(
        main.DEFAULT_IMAGE, ".env", main.FIELD_BY_NAME["CODEMATE_IMAGE"]
    )
    assert main.pure_image(config) == main.DEFAULT_PURE_IMAGE


def test_pure_mode_rejects_shell_and_query() -> None:
    with pytest.raises(SystemExit, match="--shell"):
        main.run_codemate(make_run_args(shell=True))

    with pytest.raises(SystemExit, match="--query"):
        main.run_codemate(make_run_args(query="fix the bug"))
