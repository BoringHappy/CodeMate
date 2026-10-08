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
        **{
            "agent": "codex",
            "env": [],
            "env_file": [],
            "host": True,
            "branch": "feature/host",
            **values,
        }
    )


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    repo = tmp_path / "project with spaces"
    repo.mkdir()
    subprocess.run(["git", "init", "-qb", "main", str(repo)], check=True)
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
    monkeypatch.delenv("CODEMATE_AGENT", raising=False)
    monkeypatch.delenv("CODEMATE_NO_PR", raising=False)
    monkeypatch.delenv("CODEMATE_CHAT", raising=False)
    subprocess.run(["git", "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
    return repo


def test_cli_dispatch_bypasses_container_setup(checkout, monkeypatch):
    def unexpected(*a, **kw):
        pytest.fail("Container setup/config resolution must not run")

    monkeypatch.setattr(main, "ensure_global_config", unexpected)
    monkeypatch.setattr(main, "resolve_config", unexpected)
    result = CliRunner().invoke(
        main.app, ["--host", "--branch", "feature/host", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert "codex --no-daemon" in result.output
    assert "--yolo" not in result.output
    assert not (Path(os.environ["CODEMATE_HOME"])).exists()
    assert not (checkout / ".env").exists()


def test_host_requires_an_explicit_target(checkout):
    result = CliRunner().invoke(main.app, ["--host", "--dry-run"])
    assert result.exit_code == 1
    assert "Specify --branch" in result.output
    assert not Path(os.environ["CODEMATE_HOME"]).exists()


def test_codex_is_default_for_host_and_container(checkout):
    host.run_host(args(agent=None, dry_run=True))
    assert (
        next(field for field in main.FIELDS if field.name == "CODEMATE_AGENT").default
        == "codex"
    )


@pytest.mark.parametrize("branch", ["main", "master"])
def test_base_branch_detection_and_linked_worktree(checkout, branch):
    subprocess.run(["git", "branch", "-m", branch], cwd=checkout, check=True)
    home = Path(os.environ["CODEMATE_HOME"]) / "host"
    plan = host.plan_worktree(args(), checkout, home, dict(os.environ))
    assert plan.base_branch == branch
    worktree = host.prepare_worktree(plan, home, dict(os.environ))
    assert (worktree / ".git").is_file()
    assert host.git_output(worktree, "branch", "--show-current") == "feature/host"
    assert host.git_output(checkout, "branch", "--show-current") == branch
    assert host.prepare_worktree(plan, home, dict(os.environ)) == worktree


def test_unsupported_or_missing_base_is_rejected(checkout):
    home = Path(os.environ["CODEMATE_HOME"]) / "host"
    with pytest.raises(SystemExit, match="must be main or master"):
        host.plan_worktree(
            args(base_branch="develop"), checkout, home, dict(os.environ)
        )
    subprocess.run(["git", "branch", "-m", "develop"], cwd=checkout, check=True)
    with pytest.raises(SystemExit, match="No main/master"):
        host.plan_worktree(args(), checkout, home, dict(os.environ))


def test_primary_task_branch_is_not_force_checked_out(checkout):
    subprocess.run(["git", "switch", "-qc", "feature/host"], cwd=checkout, check=True)
    home = Path(os.environ["CODEMATE_HOME"]) / "host"
    plan = host.plan_worktree(args(), checkout, home, dict(os.environ))
    with pytest.raises(SystemExit, match="primary checkout"):
        host.prepare_worktree(plan, home, dict(os.environ))


def test_startup_creates_draft_pr_before_agent_and_reuses_it(
    checkout, tmp_path, monkeypatch
):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)], cwd=checkout, check=True
    )
    subprocess.run(["git", "push", "-qu", "origin", "main"], cwd=checkout, check=True)
    real_run = subprocess.run
    calls = []
    created = []
    launched = []

    def run(command, **kwargs):
        if command[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(created))
        if command[:3] == ["gh", "pr", "checkout"]:
            return subprocess.CompletedProcess(command, 0)
        if command[:3] == ["gh", "pr", "create"]:
            assert command[command.index("--base") + 1] == "main"
            assert "--draft" in command
            assert command[command.index("--title") + 1] == "Task title"
            assert Path(command[command.index("--body-file") + 1]).is_file()
            assert host.has_ref(remote, "refs/heads/feature/host")
            created.append(
                {
                    "number": 123,
                    "url": "https://github.com/test/repo/pull/123",
                    "baseRefName": "main",
                }
            )
            calls.append("create")
            return subprocess.CompletedProcess(command, 0, created[0]["url"] + "\n")
        if command[0] == "codex":
            assert created
            assert kwargs["env"]["CODEMATE_PR_NUMBER"] == "123"
            prompt = next(
                item for item in command if item.startswith("developer_instructions=")
            )
            assert "CRITICAL WORKFLOW REQUIREMENTS" in prompt
            assert "prepared PR #123" in prompt
            launched.append(host.git_output(kwargs["cwd"], "rev-parse", "HEAD"))
            calls.append("agent")
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(host.subprocess, "run", run)
    host.run_host(args(pr_title="Task title"))
    host.run_host(args(pr_title="Task title"))
    assert calls == ["create", "agent", "agent"]
    assert launched[0] == launched[1]
    runtime = Path(os.environ["CODEMATE_HOME"]) / "host/runtime/pr-status"
    assert [
        json.loads(path.read_text())["number"] for path in runtime.glob("*.json")
    ] == [123]
    assert host.git_output(checkout, "rev-list", "--count", "main..feature/host") == "1"


def test_pr_creation_failure_prevents_agent_start(checkout, monkeypatch):
    monkeypatch.setattr(
        host,
        "prepare_pr",
        lambda *a, **kw: (_ for _ in ()).throw(SystemExit("PR setup failed")),
    )
    real_run = subprocess.run

    def run(command, **kwargs):
        assert command[0] != "codex", "Agent started before PR setup succeeded"
        return real_run(command, **kwargs)

    monkeypatch.setattr(host.subprocess, "run", run)
    with pytest.raises(SystemExit, match="PR setup failed"):
        host.run_host(args())


def test_requested_pr_is_checked_out_in_linked_worktree(checkout, monkeypatch):
    real_run = subprocess.run
    pr = {
        "number": 42,
        "url": "https://github.com/test/repo/pull/42",
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": "feature/from-pr",
    }

    def run(command, **kwargs):
        if command[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(pr))
        if command[:3] == ["gh", "pr", "checkout"]:
            assert kwargs["cwd"] != checkout
            if (
                host.git_output(kwargs["cwd"], "branch", "--show-current")
                == "feature/from-pr"
            ):
                return subprocess.CompletedProcess(command, 0)
            return real_run(["git", "switch", "-qc", "feature/from-pr"], **kwargs)
        if command[0] == "codex":
            assert (
                host.git_output(kwargs["cwd"], "branch", "--show-current")
                == "feature/from-pr"
            )
            assert kwargs["env"]["CODEMATE_PR_NUMBER"] == "42"
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(host.subprocess, "run", run)
    host.run_host(args(branch=None, pr="42"))
    assert host.git_output(checkout, "branch", "--show-current") == "main"
    with pytest.raises(SystemExit, match="must match"):
        host.run_host(args(branch=None, pr="42", base_branch="master", dry_run=True))


def test_issue_selects_task_branch(checkout):
    home = Path(os.environ["CODEMATE_HOME"]) / "host"
    plan = host.plan_worktree(
        args(branch=None, issue="99"), checkout, home, dict(os.environ)
    )
    assert plan.branch == "issue-99"


def test_fork_pr_target_uses_upstream_and_fork_owner(checkout):
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:fork-owner/project.git"],
        cwd=checkout,
        check=True,
    )
    subprocess.run(
        [
            "git",
            "remote",
            "add",
            "upstream",
            "https://github.com/upstream-owner/project.git",
        ],
        cwd=checkout,
        check=True,
    )
    assert host.github_target(checkout, "feature/host") == (
        ["--repo", "upstream-owner/project"],
        "fork-owner:feature/host",
    )


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
    assert kwargs["cwd"] != checkout
    assert host.git_output(kwargs["cwd"], "branch", "--show-current") == "feature/host"
    assert kwargs["env"]["CODEMATE_REPO_DIR"] == str(kwargs["cwd"])
    assert not (kwargs["cwd"] / ".env").exists()
    assert kwargs["env"]["CODEX_HOME"] == str(codex_home)
    assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == os.environ["CLAUDE_CONFIG_DIR"]
    assert kwargs["env"]["CODEMATE_MODE"] == "host"
    assert kwargs["env"].get("CODEMATE_GITHUB_TOKEN") != "container-only"
    assert config.read_text() == 'model = "native-model"\n'
    assert not (Path(os.environ["HOME"]) / ".agents").exists()
    assert "developer_instructions=" not in " ".join(command)


def test_dirty_primary_checkout_is_preserved(checkout, monkeypatch):
    (checkout / "user-work.txt").write_text("keep me")
    real_run = subprocess.run
    calls = []

    def run(command, **kwargs):
        if command[0] == "codex":
            calls.append(kwargs["cwd"])
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(host.subprocess, "run", run)
    host.run_host(args(no_pr=True))
    assert (checkout / "user-work.txt").read_text() == "keep me"
    assert not (calls[0] / "user-work.txt").exists()
    assert host.git_output(checkout, "branch", "--show-current") == "main"
    (calls[0] / "dirty.txt").write_text("unfinished")
    with pytest.raises(SystemExit, match="clean target worktree"):
        host.run_host(args(no_pr=True))


def test_branch_validation_does_not_switch_primary_branch(checkout):
    host.run_host(args(branch="other", dry_run=True))
    assert host.git_output(checkout, "branch", "--show-current") == "main"
    assert not host.has_ref(checkout, "refs/heads/other")


@pytest.mark.parametrize(
    "state,base,message",
    [("CLOSED", "main", "open PR"), ("OPEN", "develop", "main or master")],
)
def test_pr_requires_open_state_and_supported_base(
    checkout, monkeypatch, state, base, message
):
    monkeypatch.setattr(host, "github_target", lambda *a: ([], ""))
    monkeypatch.setattr(
        host.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, json.dumps({"state": state, "baseRefName": base})
        ),
    )
    with pytest.raises(SystemExit, match=message):
        host.read_pr(args(branch=None, pr="123"), checkout, dict(os.environ))


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
