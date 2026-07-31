from pathlib import Path
from types import SimpleNamespace

from cli import main


def make_config(**overrides) -> dict:
    values = {
        "CODEMATE_GIT_REPO_URL": "https://github.com/BoringHappy/CodeMate.git",
        "CODEMATE_BRANCH_NAME": "",
        "CODEMATE_PR_NUMBER": "",
        "CODEMATE_DOCKER_PARAMS": "",
        "CODEMATE_MOUNTS": "",
        "TZ": "UTC",
        "CODEMATE_IMAGE": "codemate:latest",
    }
    values.update(overrides)
    return {key: main.ResolvedValue(val, "test", main.FIELD_BY_NAME[key]) for key, val in values.items()}


def test_codemate_home_defaults_to_dot_codemate(monkeypatch) -> None:
    monkeypatch.delenv("CODEMATE_HOME", raising=False)
    assert main.codemate_home() == Path.home() / ".codemate"


def test_codemate_home_uses_custom_location(monkeypatch, tmp_path) -> None:
    custom = tmp_path / "custom-location"
    monkeypatch.setenv("CODEMATE_HOME", str(custom))
    assert main.codemate_home() == custom


def test_codemate_home_expands_tilde(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEMATE_HOME", "~/data")
    assert main.codemate_home() == tmp_path / "data"


def test_codemate_home_expands_env_vars(monkeypatch, tmp_path) -> None:
    base = tmp_path / "base"
    monkeypatch.setenv("CODEMATE_BASE", str(base))
    monkeypatch.setenv("CODEMATE_HOME", "$CODEMATE_BASE/codemate")
    assert main.codemate_home() == base / "codemate"


def test_setup_creates_files_in_custom_home(monkeypatch, tmp_path) -> None:
    custom = tmp_path / "codemate-home"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("CODEMATE_HOME", str(custom))
    main.create_setup_files(project)
    assert (custom / ".claude" / "settings.json").exists()
    assert (custom / ".claude.json").exists()
    assert (project / ".env").exists()


def test_docker_command_mounts_custom_home(monkeypatch, tmp_path) -> None:
    custom = tmp_path / "codemate-home"
    (custom / ".claude").mkdir(parents=True)
    monkeypatch.setenv("CODEMATE_HOME", str(custom))

    config = make_config()
    args = SimpleNamespace(mount=[], docker_param=[], dry_run=True)
    cmd = main.docker_command(config, args, "/tmp/codemate.env")

    assert f"{custom}:/home/agent/.codemate" in cmd
    assert f"{custom / '.claude'}:/home/agent/.claude" in cmd
    assert str(Path.home() / ".codemate") not in cmd
