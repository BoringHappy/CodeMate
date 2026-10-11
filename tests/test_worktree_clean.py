from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from io import StringIO

import pytest
from rich.console import Console
from typer.testing import CliRunner

from cli import host, main, worktree


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = tmp_path / "project with spaces"
    path.mkdir()
    worktree.git(path, "init", "-qb", "main")
    worktree.git(path, "config", "user.name", "Test")
    worktree.git(path, "config", "user.email", "test@example.com")
    (path / "tracked.txt").write_text("initial\n")
    worktree.git(path, "add", ".")
    worktree.git(path, "commit", "-qm", "Initial")
    monkeypatch.setenv("CODEMATE_HOME", str(tmp_path / "codemate home"))
    monkeypatch.chdir(path)
    return path


def add(repo, path, branch="feature/clean", *, detached=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = ["--detach"] if detached else ["-b", branch]
    worktree.git(repo, "worktree", "add", *flags, str(path), "main")
    return next(
        item
        for item in worktree.discover(main.codemate_home() / "host", repo)
        if item.path == path
    )


def test_discovery_from_repo_and_outside_checkout(repo, tmp_path):
    managed = add(repo, main.codemate_home() / "host/worktrees/repo/managed")
    external = add(repo, tmp_path / "external", "feature/external")
    entries = worktree.discover(main.codemate_home() / "host", repo)
    assert {item.path for item in entries} == {repo, managed.path, external.path}
    outside = tmp_path / "outside"
    outside.mkdir()
    entries = worktree.discover(main.codemate_home() / "host", outside)
    assert entries == [managed]


def test_discovery_across_repositories(repo, tmp_path):
    first = add(repo, main.codemate_home() / "host/worktrees/first/managed")
    other = tmp_path / "other"
    other.mkdir()
    worktree.git(other, "clone", "-q", str(repo), ".")
    second = add(other, main.codemate_home() / "host/worktrees/second/managed")
    entries = worktree.discover(main.codemate_home() / "host", tmp_path)
    assert {item.path for item in entries} == {first.path, second.path}


def test_selector_requires_backspace_then_enter_and_retains_branch(repo, tmp_path):
    entry = add(repo, tmp_path / "clean me")
    selector = worktree.Selector([entry], repo)
    selector.handle("enter")
    assert entry.path.is_dir()
    selector.handle("backspace")
    assert selector.pending and entry.path.is_dir()
    selector.handle("escape")
    selector.handle("enter")
    assert entry.path.is_dir()
    selector.handle("backspace")
    selector.handle("down")
    assert selector.pending  # Confirmation stays on the selected worktree.
    selector.handle("enter")
    assert not entry.path.exists()
    assert not selector.entries and selector.selected == 0
    assert worktree.git(repo, "branch", "--list", entry.branch) == entry.branch
    assert len(worktree.records(entry.common_dir)) == 1


@pytest.mark.parametrize(
    "kind",
    [
        "modified",
        "staged",
        "untracked",
        "current",
        "primary",
        "locked",
        "active",
        "repository-busy",
    ],
)
def test_protected_worktrees_cannot_be_removed(repo, tmp_path, kind):
    entry = add(repo, tmp_path / "protected")
    cwd = repo
    if kind in {"modified", "staged"}:
        (entry.path / "tracked.txt").write_text("changed\n")
        if kind == "staged":
            worktree.git(entry.path, "add", ".")
    elif kind == "untracked":
        (entry.path / "untracked.txt").write_text("keep\n")
    elif kind == "current":
        cwd = entry.path / "subdirectory"
        cwd.mkdir()
    elif kind == "primary":
        entry = next(
            item
            for item in worktree.discover(main.codemate_home() / "host", repo)
            if item.primary
        )
    elif kind == "locked":
        # Lock after discovery to test the deletion-time check.
        worktree.git(repo, "worktree", "lock", str(entry.path))
    if kind in {"active", "repository-busy"}:
        lock = (
            entry.session_lock
            if kind == "active"
            else entry.session_lock.parent / "repository.lock"
        )
        with host.locked(lock), pytest.raises(worktree.WorktreeError, match="busy"):
            worktree.remove(entry, cwd)
    else:
        with pytest.raises(worktree.WorktreeError):
            worktree.remove(entry, cwd)
    assert entry.path.is_dir()
    assert len(worktree.records(entry.common_dir)) == 2


def test_deletion_rechecks_changes_after_confirmation(repo, tmp_path):
    entry = add(repo, tmp_path / "changed after selection")
    selector = worktree.Selector([entry], repo)
    selector.handle("backspace")
    assert selector.pending
    (entry.path / "new.txt").write_text("preserve\n")
    selector.handle("enter")
    assert entry.path.is_dir()
    assert "Uncommitted changes" in selector.message
    assert not selector.pending


def test_changed_branch_requires_refresh(repo, tmp_path):
    entry = add(repo, tmp_path / "switched branch")
    worktree.git(entry.path, "switch", "-c", "new-branch")
    with pytest.raises(worktree.WorktreeError, match="changed"):
        worktree.remove(entry, repo)
    assert entry.path.is_dir()


def test_detached_worktree_can_be_removed(repo, tmp_path):
    entry = add(repo, tmp_path / "detached", detached=True)
    assert entry.label.startswith("detached ")
    worktree.remove(entry, repo)
    assert not entry.path.exists()
    assert worktree.git(repo, "branch", "--show-current") == "main"


def test_git_failure_stays_in_selector(repo, tmp_path, monkeypatch):
    entry = add(repo, tmp_path / "submodule-like failure")
    selector = worktree.Selector([entry], repo)
    selector.handle("backspace")

    def fail(*args):
        raise worktree.WorktreeError("Git refused removal")

    monkeypatch.setattr(worktree, "remove", fail)
    selector.handle("enter")
    assert entry.path.is_dir() and selector.entries == [entry]
    assert selector.message == "Git refused removal"


def test_cli_needs_no_container_setup_or_agent(repo, monkeypatch):
    def unexpected(*args):
        pytest.fail("Cleanup must not run container or agent setup")

    monkeypatch.setattr(main, "run_codemate", unexpected)
    monkeypatch.setattr(host, "run_host", unexpected)
    result = CliRunner().invoke(main.app, ["worktree", "clean"])
    assert result.exit_code == 1
    assert "requires an interactive terminal" in result.output
    assert not main.codemate_home().exists()
    help_result = CliRunner().invoke(main.app, ["worktree", "clean", "--help"])
    assert help_result.exit_code == 0


def test_empty_home_outside_repo_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEMATE_HOME", str(tmp_path / "empty"))
    result = CliRunner().invoke(main.app, ["worktree", "clean"])
    assert result.exit_code == 0
    assert "No host worktrees found" in result.output
    assert not main.codemate_home().exists()


@pytest.mark.parametrize("flag", ["--dry-run", "--setup", "--pure"])
def test_cli_rejects_launch_options_instead_of_silently_ignoring_them(
    repo, monkeypatch, flag
):
    def unexpected(*args):
        pytest.fail("Invalid launch options must not enter the cleaner")

    monkeypatch.setattr(worktree, "run_clean", unexpected)
    result = CliRunner().invoke(main.app, [flag, "worktree", "clean"])
    assert result.exit_code == 2
    assert "Launch options cannot be used" in result.output


def test_scrolling_keeps_selection_and_keyboard_footer_visible(repo, tmp_path):
    entry = add(repo, tmp_path / "scroll")
    selector = worktree.Selector([entry] * 30, repo)
    selector.refresh_status()
    for _ in range(20):
        selector.handle("down")
    output = StringIO()
    console = Console(file=output, width=80, height=16, color_system=None)
    console.print(selector.render(80, 16))
    lines = output.getvalue().splitlines()
    assert len(lines) <= 16
    assert "›" in output.getvalue()
    assert "Backspace Delete" in lines[-1]
    selector.handle("backspace")
    output.seek(0)
    output.truncate()
    console.print(selector.render(80, 16))
    assert "Enter Confirm deletion" in output.getvalue().splitlines()[-1]


@pytest.mark.parametrize("quit_key", [b"q", b"\x03"])
def test_real_terminal_navigation_cancel_confirm_and_restore(repo, tmp_path, quit_key):
    entry = add(repo, tmp_path / "terminal cleanup")
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))
    settings = termios.tcgetattr(slave)
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0); "
                "from cli.main import app; app()"
            ),
            "worktree",
            "clean",
        ],
        cwd=repo,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={**os.environ, "TERM": "xterm-256color"},
        start_new_session=True,
    )
    output = bytearray()

    def until(fragment):
        deadline = time.monotonic() + 8
        while fragment not in output:
            assert time.monotonic() < deadline, output.decode(errors="replace")[-2000:]
            if select.select([master], [], [], 0.1)[0]:
                output.extend(os.read(master, 65536))

    try:
        until(b"Backspace")
        os.write(master, b"\x1b[B")  # Primary checkout is the second row.
        os.write(master, b"\x1b[A")
        os.write(master, b"\x7f")
        until(b"Enter Confirm deletion")
        assert entry.path.is_dir()
        os.write(master, b"\x1b")
        until(b"Deletion cancelled")
        assert entry.path.is_dir()
        os.write(master, b"\x08")  # Also support the other Backspace encoding.
        # Wait for a new confirmation frame, not the earlier matching output.
        output.clear()
        until(b"Enter Confirm deletion")
        os.write(master, b"\r")
        until(b"Branch retained")
        assert not entry.path.exists()
        os.write(master, quit_key)
        assert process.wait(timeout=5) == 0
        assert termios.tcgetattr(slave) == settings
        assert worktree.git(repo, "branch", "--list", entry.branch) == entry.branch
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        os.close(slave)
