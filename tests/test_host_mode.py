from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
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
    assert "codex --yolo --no-daemon --no-alt-screen" in result.output
    assert not (Path(os.environ["CODEMATE_HOME"])).exists()
    assert not (checkout / ".env").exists()


def test_host_requires_an_explicit_target(checkout):
    result = CliRunner().invoke(main.app, ["--host", "--dry-run"])
    assert result.exit_code == 1
    assert "Specify --branch" in result.output
    assert not Path(os.environ["CODEMATE_HOME"]).exists()


@pytest.mark.parametrize("explicit_host", [False, True])
@pytest.mark.parametrize("preview", ["--dry-run", "--config"])
def test_xcode_implies_host_without_side_effects(
    checkout, monkeypatch, explicit_host, preview
):
    monkeypatch.setattr(host.sys, "platform", "darwin")
    def unexpected(*a, **kw):
        pytest.fail("Preview must not initialize Docker, create a worktree, or open Xcode")

    monkeypatch.setattr(main, "ensure_global_config", unexpected)
    monkeypatch.setattr(main, "resolve_config", unexpected)
    monkeypatch.setattr(host, "prepare_worktree", unexpected)
    flags = ["--xcode", "--branch", "feature/ios", preview]
    if explicit_host:
        flags.append("--host")
    result = CliRunner().invoke(main.app, flags)
    assert result.exit_code == 0, result.output
    expected = '"xcode": true' if preview == "--config" else "open -a Xcode"
    assert expected in result.output
    assert not Path(os.environ["CODEMATE_HOME"]).exists()


def test_xcode_is_ignored_by_container_dispatch_on_non_macos(checkout, monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "linux")
    runner = CliRunner()
    normal = runner.invoke(main.app, ["--pure", "--dry-run"])
    result = runner.invoke(main.app, ["--pure", "--dry-run", "--xcode"])
    assert normal.exit_code == result.exit_code == 0, result.output
    assert result.output == normal.output
    assert not Path(os.environ["CODEMATE_HOME"]).exists()


def test_xcode_is_ignored_by_host_launch_on_non_macos(checkout, monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "linux")
    real_run = subprocess.run
    real_which = shutil.which
    launched = []

    def which(name, **kwargs):
        assert name != "open", "Xcode opener must not be required on Linux"
        return real_which(name, **kwargs)

    def run(command, **kwargs):
        assert command[0] != "open", "Xcode must not open on Linux"
        if command[0] == "codex":
            launched.append(kwargs["cwd"])
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(host.subprocess, "run", run)
    monkeypatch.setattr(host.shutil, "which", which)
    result = CliRunner().invoke(
        main.app, ["--host", "--xcode", "--branch", "feature/host", "--chat"]
    )
    assert result.exit_code == 0, result.output
    assert len(launched) == 1
    assert launched[0] != checkout


def test_xcode_is_inactive_in_host_config_on_non_macos(checkout, monkeypatch):
    monkeypatch.setattr(host.sys, "platform", "linux")
    result = CliRunner().invoke(
        main.app, ["--host", "--xcode", "--branch", "feature/host", "--config"]
    )
    assert result.exit_code == 0, result.output
    assert '"xcode": false' in result.output


@pytest.mark.parametrize("extension", ["xcworkspace", "xcodeproj"])
def test_xcode_selects_root_bundle_with_workspace_priority(tmp_path, extension):
    project = tmp_path / f"App with spaces.{extension}"
    project.mkdir()
    if extension == "xcworkspace":
        (tmp_path / "App.xcodeproj").mkdir()
        (tmp_path / "Other.xcodeproj").mkdir()
    assert host.xcode_project(tmp_path) == project


@pytest.mark.parametrize("extension", ["xcworkspace", "xcodeproj"])
def test_xcode_rejects_ambiguous_bundles(tmp_path, extension):
    projects = [tmp_path / f"{name}.{extension}" for name in ("A", "B")]
    for project in projects:
        project.mkdir()
    if extension == "xcworkspace":
        (tmp_path / "Unique.xcodeproj").mkdir()
    with pytest.raises(SystemExit, match="Multiple Xcode") as error:
        host.xcode_project(tmp_path)
    assert all(str(project) in str(error.value) for project in projects)


def test_xcode_requires_existing_worktree_and_root_bundle(tmp_path):
    with pytest.raises(SystemExit, match="directory does not exist"):
        host.xcode_project(tmp_path / "missing")
    (tmp_path / "Not a bundle.xcworkspace").touch()
    (tmp_path / "nested" / "App.xcodeproj").mkdir(parents=True)
    with pytest.raises(SystemExit, match="No Xcode workspace/project"):
        host.xcode_project(tmp_path)


@pytest.mark.parametrize("failure", [None, "pr", "open", "missing"])
def test_xcode_opens_prepared_worktree_before_agent(checkout, monkeypatch, failure):
    if failure != "missing":
        project = checkout / "App with spaces.xcworkspace"
        project.mkdir()
        (project / "contents.xcworkspacedata").write_text("<Workspace/>")
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-qm", "Add workspace"], check=True)
    monkeypatch.setattr(host.sys, "platform", "darwin")
    real_which = shutil.which
    monkeypatch.setattr(
        host.shutil,
        "which",
        lambda name, **kw: "/usr/bin/open" if name == "open" else real_which(name, **kw),
    )
    real_run = subprocess.run
    events = []
    opened = []

    def prepare_pr(plan, cwd, args, env):
        assert cwd != checkout
        events.append("pr")
        if failure == "pr":
            raise SystemExit("PR setup failed")
        return {"number": 123, "url": "https://github.com/test/repo/pull/123"}

    def run(command, **kwargs):
        if command[0] == "open":
            events.append("open")
            assert command[:3] == ["open", "-a", "Xcode"]
            assert Path(command[3]) == kwargs["cwd"] / "App with spaces.xcworkspace"
            assert kwargs["cwd"] != checkout
            assert (
                host.git_output(kwargs["cwd"], "branch", "--show-current")
                == "feature/host"
            )
            opened.append(command[3])
            if failure == "open":
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)
        if command[0] == "codex":
            events.append("agent")
            return subprocess.CompletedProcess(command, 0)
        return real_run(command, **kwargs)

    monkeypatch.setattr(host, "prepare_pr", prepare_pr)
    monkeypatch.setattr(host.subprocess, "run", run)
    if failure:
        error = subprocess.CalledProcessError if failure == "open" else SystemExit
        with pytest.raises(error):
            host.run_host(args(xcode=True))
        expected = {"pr": ["pr"], "open": ["pr", "open"], "missing": []}
        assert events == expected[failure]
    else:
        host.run_host(args(xcode=True))
        host.run_host(args(xcode=True))
        assert events == ["pr", "open", "agent"] * 2
        assert opened[0] == opened[1]
    assert host.git_output(checkout, "branch", "--show-current") == "main"


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


@pytest.mark.parametrize("branch", ["main", "master"])
@pytest.mark.parametrize("remote_name", ["origin", "upstream"])
@pytest.mark.parametrize("no_pr", [False, True])
@pytest.mark.parametrize("cached_base", [False, True])
def test_fetches_latest_base_before_checkout(
    checkout, tmp_path, branch, remote_name, no_pr, cached_base
):
    subprocess.run(["git", "branch", "-m", branch], check=True)
    initial = host.git_output(checkout, "rev-parse", "HEAD")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "remote", "add", remote_name, str(remote)], check=True)
    subprocess.run(["git", "push", "-q", remote_name, branch], check=True)
    if not cached_base:
        subprocess.run(
            ["git", "update-ref", "-d", f"refs/remotes/{remote_name}/{branch}"],
            check=True,
        )
    producer = tmp_path / "producer"
    subprocess.run(["git", "clone", "-qb", branch, str(remote), str(producer)], check=True)
    (producer / "latest.txt").write_text("latest remote base")
    subprocess.run(["git", "add", "."], cwd=producer, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "-qm", "Update base",
        ],
        cwd=producer,
        check=True,
    )
    subprocess.run(["git", "push", "-q", "origin", branch], cwd=producer, check=True)
    latest = host.git_output(producer, "rev-parse", "HEAD")
    (checkout / "unfinished.txt").write_text("preserve local changes")
    home = Path(os.environ["CODEMATE_HOME"]) / "host"
    env = dict(os.environ, CODEMATE_NO_PR="true" if no_pr else "")
    plan = host.plan_worktree(args(), checkout, home, env)
    assert plan.base_ref == f"refs/remotes/{remote_name}/{branch}"
    worktree = host.prepare_worktree(plan, home, env)
    assert host.git_output(worktree, "rev-parse", "HEAD") == latest
    assert (worktree / "latest.txt").read_text() == "latest remote base"
    assert host.git_output(checkout, "rev-parse", "HEAD") == initial
    assert (checkout / "unfinished.txt").read_text() == "preserve local changes"
    # Reuse still refreshes the base without moving an existing task branch.
    subprocess.run(
        [
            "git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "--allow-empty", "-qm", "Update again",
        ],
        cwd=producer,
        check=True,
    )
    subprocess.run(["git", "push", "-q", "origin", branch], cwd=producer, check=True)
    assert host.prepare_worktree(plan, home, env) == worktree
    assert host.git_output(checkout, "rev-parse", plan.base_ref) == host.git_output(
        producer, "rev-parse", "HEAD"
    )
    assert host.git_output(worktree, "rev-parse", "HEAD") == latest


def test_failed_base_fetch_prevents_worktree_creation(checkout, tmp_path):
    subprocess.run(
        ["git", "remote", "add", "origin", str(tmp_path / "missing.git")],
        check=True,
    )
    home = Path(os.environ["CODEMATE_HOME"]) / "host"
    env = dict(os.environ, CODEMATE_NO_PR="true")
    plan = host.plan_worktree(args(), checkout, home, env)
    with pytest.raises(subprocess.CalledProcessError):
        host.prepare_worktree(plan, home, env)
    assert not plan.path.exists()
    assert not host.has_ref(checkout, "refs/heads/feature/host")


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


@pytest.mark.parametrize("retry_creation", [False, True])
def test_startup_creates_draft_pr_before_agent_and_reuses_it(
    checkout, tmp_path, monkeypatch, retry_creation
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
    failed = False

    def run(command, **kwargs):
        nonlocal failed
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
            if retry_creation and not failed:
                failed = True
                return subprocess.CompletedProcess(
                    command, 1, "", "Permission denied creating PR"
                )
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
    if retry_creation:
        with pytest.raises(SystemExit, match="Permission denied creating PR"):
            host.run_host(args(pr_title="Task title"))
        assert not launched
        assert (
            host.git_output(checkout, "rev-list", "--count", "main..feature/host")
            == "1"
        )
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


@pytest.mark.parametrize("unfinished", [None, "files", "commits"])
def test_failed_pr_checkout_can_retry_without_discarding_work(
    checkout, monkeypatch, unfinished
):
    real_run = subprocess.run
    failure = True
    pr = {
        "number": 42,
        "state": "OPEN",
        "baseRefName": "main",
        "headRefName": "feature/from-pr",
    }

    def run(command, **kwargs):
        if command[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(command, 0, json.dumps(pr))
        if command[:3] == ["gh", "pr", "checkout"]:
            if failure:
                raise subprocess.CalledProcessError(1, command)
            return real_run(["git", "switch", "-qc", "feature/from-pr"], **kwargs)
        return real_run(command, **kwargs)

    monkeypatch.setattr(host.subprocess, "run", run)
    home = Path(os.environ["CODEMATE_HOME"]) / "host"
    env = dict(os.environ, CODEMATE_NO_PR="true")
    plan = host.plan_worktree(args(branch=None, pr="42"), checkout, home, env)
    with pytest.raises(subprocess.CalledProcessError):
        host.prepare_worktree(plan, home, env)
    assert plan.path.is_dir()
    assert host.git_output(plan.path, "branch", "--show-current") == ""
    failure = False
    if unfinished:
        (plan.path / "user-work.txt").write_text("preserve me")
        if unfinished == "commits":
            real_run(["git", "add", "user-work.txt"], cwd=plan.path, check=True)
            real_run(
                ["git", "commit", "-qm", "Detached task work"],
                cwd=plan.path,
                check=True,
            )
        with pytest.raises(SystemExit, match="Preserve"):
            host.prepare_worktree(plan, home, env)
        assert (plan.path / "user-work.txt").read_text() == "preserve me"
        assert host.git_output(checkout, "branch", "--show-current") == "main"
        return
    assert host.prepare_worktree(plan, home, env) == plan.path
    assert host.git_output(plan.path, "branch", "--show-current") == "feature/from-pr"
    assert host.git_output(checkout, "branch", "--show-current") == "main"


def test_task_locks_span_homes_processes_and_agent_lifetime(
    checkout, tmp_path, monkeypatch
):
    binary = tmp_path / "bin"
    binary.mkdir()
    agent = binary / "codex"
    agent.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['TEST_HOST_READY']).write_text(json.dumps({'pid':os.getpid(),'cwd':os.getcwd()}))\n"
        "sys.stdin.read()\n"
    )
    agent.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    ready = tmp_path / "ready.json"
    command = [
        sys.executable,
        "-m",
        "cli.main",
        "--host",
        "--branch",
        "feature/host",
        "--chat",
    ]
    env = dict(os.environ, TEST_HOST_READY=str(ready))
    # Suppress the TUI with a real executable stub; Git, worktree creation,
    # plugin publication and kernel locks all run in separate processes.
    process = subprocess.Popen(
        command,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    agent_pid = None
    try:
        for _ in range(100):
            if ready.exists():
                break
            if process.poll() is not None:
                pytest.fail(process.stderr.read().decode())
            time.sleep(0.05)
        assert ready.exists(), "Agent did not start"
        data = json.loads(ready.read_text())
        agent_pid = data["pid"]
        assert Path(data["cwd"]) != checkout
        alternate = dict(
            env,
            CODEMATE_HOME=str(tmp_path / "other home"),
            TEST_HOST_READY=str(tmp_path / "other.json"),
        )
        duplicate = subprocess.run(
            command,
            env=alternate,
            input="",
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert duplicate.returncode != 0
        assert "Another CodeMate" in duplicate.stderr
        independent = command.copy()
        independent[independent.index("--branch") + 1] = "feature/other"
        result = subprocess.run(
            independent,
            env=alternate,
            input="",
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert host.git_output(checkout, "branch", "--show-current") == "main"
        process.kill()
        process.wait(timeout=5)
        duplicate = subprocess.run(
            command,
            env=alternate,
            input="",
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert duplicate.returncode != 0, (
            "Live agent lost its branch lock when its launcher exited"
        )
        assert "Another CodeMate" in duplicate.stderr
        process.stdin.close()
        # Wait for the child to finish reading EOF and release its inherited
        # lock before proving the branch can be reused.
        plan = host.plan_worktree(args(), checkout, tmp_path / "other home/host", env)
        for _ in range(100):
            try:
                with host.locked(plan.session_lock):
                    break
            except SystemExit:
                time.sleep(0.05)
        else:
            pytest.fail("Agent exit did not release the lock")
        result = subprocess.run(
            command,
            env=alternate,
            input="",
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    finally:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        if process.poll() is None:
            process.wait(timeout=5)
        if agent_pid:
            try:
                os.kill(agent_pid, 15)
            except ProcessLookupError:
                pass


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


@pytest.mark.parametrize("mode", ["--host", "--xcode"])
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
def test_rejects_container_options_before_writing(checkout, monkeypatch, flag, mode):
    monkeypatch.setattr(host.sys, "platform", "darwin")
    result = CliRunner().invoke(main.app, [mode, flag, "--dry-run"])
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
