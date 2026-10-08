from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from cli import host, main


def args(**values):
    return SimpleNamespace(
        **{"agent": "codex", "env": [], "env_file": [], "host": True, **values}
    )


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    repo = tmp_path / "project with spaces"
    repo.mkdir()
    subprocess.run(["git", "init", "-qb", "feature/host", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-qm",
            "Initial",
        ],
        check=True,
    )
    native_home = tmp_path / "home"
    native_home.mkdir()
    monkeypatch.setenv("HOME", str(native_home))
    monkeypatch.setenv("CODEX_HOME", str(native_home / ".codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(native_home / ".claude"))
    monkeypatch.setenv("CODEMATE_HOME", str(tmp_path / "codemate home"))
    monkeypatch.delenv("CODEMATE_RUNTIME_DIR", raising=False)
    monkeypatch.chdir(repo)
    return repo


def test_cli_dispatch_bypasses_container_setup(checkout, monkeypatch):
    def unexpected(*a, **kw):
        pytest.fail("Container setup/config resolution must not run")

    monkeypatch.setattr(main, "ensure_global_config", unexpected)
    monkeypatch.setattr(main, "resolve_config", unexpected)
    result = CliRunner().invoke(main.app, ["--host", "--agent", "codex", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "codex --no-daemon" in result.output
    assert "--yolo" not in result.output
    assert not (Path(os.environ["CODEMATE_HOME"])).exists()
    assert not (checkout / ".env").exists()


@pytest.mark.parametrize(
    "flag",
    [
        "--setup",
        "--pure",
        "--shell",
        "--build",
        "--mount=data:/data",
        "--repo=https://github.com/example/repo",
    ],
)
def test_rejects_container_options_before_writing(checkout, flag):
    result = CliRunner().invoke(main.app, ["--host", flag, "--dry-run"])
    assert result.exit_code == 1
    assert "remove" in result.output
    assert not Path(os.environ["CODEMATE_HOME"]).exists()


def test_native_config_and_environment_are_preserved(checkout, monkeypatch):
    codex_home = Path(os.environ["CODEX_HOME"])
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text('model = "native-model"\n')
    (checkout / ".env").write_text(
        "CODEMATE_AGENT=claude\nCODEMATE_GITHUB_TOKEN=container-only\n"
    )
    calls = []
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "codex":
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(host.subprocess, "run", run)
    host.run_host(args(chat=True, query="hello"))
    command, kwargs = calls[0]
    assert command[-1] == "hello"
    assert kwargs["cwd"] == checkout
    assert kwargs["env"]["CODEX_HOME"] == str(codex_home)
    assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == os.environ["CLAUDE_CONFIG_DIR"]
    assert kwargs["env"]["CODEMATE_MODE"] == "host"
    assert kwargs["env"].get("CODEMATE_GITHUB_TOKEN") != "container-only"
    assert config.read_text() == 'model = "native-model"\n'
    assert not (Path(os.environ["HOME"]) / ".agents").exists()
    assert "developer_instructions=" not in " ".join(command)


def test_dirty_checkout_cannot_start_pr_automation(checkout, monkeypatch):
    (checkout / "user-work.txt").write_text("keep me")
    with pytest.raises(SystemExit, match="clean worktree"):
        host.run_host(args())
    assert (checkout / "user-work.txt").read_text() == "keep me"
    assert not (Path(os.environ["CODEX_HOME"]) / "plugins").exists()


def test_branch_validation_does_not_switch_branches(checkout):
    with pytest.raises(SystemExit, match="does not switch"):
        host.run_host(args(branch="other", dry_run=True))
    assert host.git_output(checkout, "branch", "--show-current") == "feature/host"


@pytest.mark.parametrize(
    "state,branch", [("CLOSED", "feature/host"), ("OPEN", "different")]
)
def test_pr_target_must_match_the_open_branch(checkout, monkeypatch, state, branch):
    monkeypatch.setattr(
        host.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps({"state": state, "headRefName": branch})
        ),
    )
    with pytest.raises(SystemExit, match="current branch to match"):
        host.validate_pr(args(pr="123"), checkout, "feature/host", dict(os.environ))


def test_explicit_no_pr_environment_reaches_the_native_agent(checkout, monkeypatch):
    captured = []
    real_run = subprocess.run

    def run(command, **kwargs):
        if command[0] == "codex":
            captured.append(kwargs["env"])
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(host.subprocess, "run", run)
    host.run_host(args(env=["CODEMATE_NO_PR=true"]))
    host.run_host(args(env=["CODEMATE_NO_PR=true"]))
    assert all(env["CODEMATE_NO_PR"] == "true" for env in captured)
    assert captured[0]["CODEMATE_INSTANCE_ID"] != captured[1]["CODEMATE_INSTANCE_ID"]


def test_host_lock_excludes_same_worktree_but_not_other_worktrees(tmp_path):
    with host.locked(tmp_path / "one.lock"):
        with (
            pytest.raises(SystemExit, match="Another CodeMate"),
            host.locked(tmp_path / "one.lock"),
        ):
            pytest.fail("Acquired held lock")
        with host.locked(tmp_path / "two.lock"):
            pass
    with host.locked(tmp_path / "one.lock"):
        pass


def test_claude_loads_session_plugins_without_permission_bypass(tmp_path):
    command = host.native_command(
        "claude", tmp_path, "unused", "PR automation", "query", chat=False
    )
    assert command.count("--plugin-dir") == len(host.HOST_PLUGINS)
    assert str(tmp_path / "workspace-host") in command
    assert str(tmp_path / "workspace") not in command
    assert "--dangerously-skip-permissions" not in command
    assert command[-1] == "query"
    assert "--append-system-prompt" in command
    settings = json.loads(command[command.index("--settings") + 1])
    assert settings["enabledPlugins"]["workspace@codemate"] is False


@pytest.mark.parametrize("agent", ["codex", "claude"])
def test_initial_query_is_not_interpreted_as_agent_flags(tmp_path, agent):
    command = host.native_command(
        agent, tmp_path, "host-test", "", "--dangerously-skip-permissions", chat=True
    )
    assert command[-2:] == ["--", "--dangerously-skip-permissions"]


@pytest.mark.skipif(not shutil.which("codex"), reason="Native Codex not installed")
def test_real_codex_loads_plugins_only_with_launch_overrides(checkout, tmp_path):
    source = host.resources()
    identity = host.bundle_id(source)
    bundle = tmp_path / "private plugins" / identity
    codex_home = Path(os.environ["CODEX_HOME"])
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text('sandbox_mode = "read-only"\n')
    custom = Path(os.environ["HOME"]) / ".agents" / "skills" / "personal-skill"
    custom.mkdir(parents=True)
    (custom / "SKILL.md").write_text(
        "---\nname: personal-skill\ndescription: Personal skill\n---\nPersonal instructions.\n"
    )
    marketplace = f"codemate-host-{identity}"
    host.prepare_plugins(source, bundle, codex_home, "codex", marketplace)
    command = host.native_command("codex", bundle, marketplace, "", "", chat=True)
    for params, enabled in ((command[3:], True), ([], False)):
        result = subprocess.run(
            ["codex", *params, "debug", "prompt-input", "hello"],
            cwd=checkout,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert ("pr:get-details" in result.stdout) is enabled
        assert ("git:commit" in result.stdout) is enabled
        assert "personal-skill" in result.stdout
        assert "workspace:setup-services" not in result.stdout
    assert config.read_text() == 'sandbox_mode = "read-only"\n'
    assert not (Path(os.environ["HOME"]) / ".agents" / "plugins").exists()


def test_python_file_lock_persists_in_parent_and_releases_on_exit(tmp_path):
    helper = host.resources() / "workspace" / "hooks" / "file_lock.py"
    lock = tmp_path / "hook.lock"
    ready = tmp_path / "ready"
    process = subprocess.Popen(
        [
            "bash",
            "-c",
            'exec 9>"$1"; "$2" "$3" -w 1 9; touch "$4"; read -r release',
            "bash",
            str(lock),
            os.sys.executable,
            str(helper),
            str(ready),
        ],
        stdin=subprocess.PIPE,
    )
    try:
        import time

        for _ in range(100):
            if ready.exists():
                break
            time.sleep(0.02)
        assert ready.exists()
        with lock.open("a") as other, pytest.raises(BlockingIOError):
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        process.communicate(b"release\n", timeout=5)
        with lock.open("a") as other:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
