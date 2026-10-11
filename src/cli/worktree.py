"""Host worktree cleanup with a keyboard-driven terminal selector."""

from __future__ import annotations

import fcntl
import hashlib
import os
import select
import shutil
import subprocess
import sys
import termios
import tty
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text

from .host import locked


class WorktreeError(RuntimeError):
    pass


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise WorktreeError(result.stderr.strip() or "Git command failed.")
    return result.stdout.strip()


def records(common_dir: Path) -> list[dict[str, str]]:
    result = subprocess.run(
        ["git", "--git-dir", str(common_dir), "worktree", "list", "--porcelain", "-z"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise WorktreeError(result.stderr.strip() or "Cannot list Git worktrees.")
    items = []
    record = {}
    for item in result.stdout.split("\0"):
        if not item:
            if "worktree" in record:
                items.append(record)
            record = {}
        else:
            key, _, value = item.partition(" ")
            record[key] = value
    return items


@dataclass(frozen=True)
class Worktree:
    path: Path
    common_dir: Path
    branch: str
    head: str
    primary: bool = False
    git_locked: bool = False

    @property
    def label(self) -> str:
        return self.branch or f"detached {self.head[:8]}"

    @property
    def session_lock(self) -> Path:
        key = hashlib.sha256((self.branch or str(self.path)).encode()).hexdigest()[:16]
        return self.common_dir / "codemate-host-locks" / f"{key}.lock"


def discover(host_home: Path, cwd: Path) -> list[Worktree]:
    """Include this repo's worktrees and managed host worktrees across repos."""
    root = host_home / "worktrees"
    repositories: dict[Path, bool] = {}
    candidates = [(cwd, True)]
    if root.is_dir():
        candidates.extend((path, False) for path in sorted(root.glob("*/*")))
    for path, current in candidates:
        if not path.is_dir():
            continue
        try:
            common = Path(
                git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
            )
        except WorktreeError:
            continue
        repositories[common] = repositories.get(common, False) or current
    entries = []
    for common, include_all in repositories.items():
        for index, record in enumerate(records(common)):
            path = Path(record["worktree"]).resolve()
            if not include_all and root.resolve() not in path.parents:
                continue
            entries.append(
                Worktree(
                    path,
                    common,
                    record.get("branch", "").removeprefix("refs/heads/"),
                    record.get("HEAD", ""),
                    index == 0,
                    "locked" in record,
                )
            )
    return sorted(
        entries, key=lambda item: (str(item.common_dir), item.primary, item.label)
    )


def blocked_reason(entry: Worktree, cwd: Path, *, check_session: bool = True) -> str:
    if entry.primary:
        return "Primary checkout"
    if entry.path == cwd or entry.path in cwd.parents:
        return "Current worktree"
    if entry.git_locked:
        return "Locked by Git"
    if not entry.path.is_dir():
        return "Missing directory; repair/prune with Git"
    if check_session and entry.session_lock.exists():
        with entry.session_lock.open("a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return "Active CodeMate session"
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    try:
        if git(entry.path, "status", "--porcelain", "--untracked-files=all"):
            return "Uncommitted changes"
    except WorktreeError:
        return "Cannot inspect worktree"
    return ""


def remove(entry: Worktree, cwd: Path) -> None:
    """Revalidate under the same locks as host startup; never force removal."""
    try:
        with (
            locked(entry.session_lock),
            locked(entry.common_dir / "codemate-host-locks" / "repository.lock"),
        ):
            registered = records(entry.common_dir)
            record = next(
                (
                    record
                    for record in registered
                    if Path(record["worktree"]).resolve() == entry.path
                ),
                None,
            )
            if record is None:
                raise WorktreeError(
                    "Worktree is no longer registered. Press R to refresh."
                )
            branch = record.get("branch", "").removeprefix("refs/heads/")
            if branch != entry.branch or record.get("HEAD", "") != entry.head:
                raise WorktreeError(
                    "Worktree changed. Press R to refresh before deleting."
                )
            primary = Path(registered[0]["worktree"]).resolve() == entry.path
            fresh = Worktree(
                entry.path,
                entry.common_dir,
                branch,
                entry.head,
                primary,
                "locked" in record,
            )
            reason = blocked_reason(fresh, cwd, check_session=False)
            if reason:
                raise WorktreeError(f"Cannot remove {entry.label}: {reason}.")
            result = subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(entry.common_dir),
                    "worktree",
                    "remove",
                    "--",
                    str(entry.path),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise WorktreeError(
                    result.stderr.strip() or "Git could not remove the worktree."
                )
    except SystemExit as exc:
        raise WorktreeError(
            "Worktree or repository is busy. Try again after the host session exits."
        ) from exc


@dataclass
class Selector:
    entries: list[Worktree]
    cwd: Path
    selected: int = 0
    pending: bool = False
    message: str = "Select a worktree to clean. Branches are retained."
    reasons: dict[Path, str] = field(default_factory=dict)

    def handle(self, key: str) -> str:
        if key in {"quit", "ctrl-c"}:
            return "quit"
        if self.pending:
            if key == "enter":
                self.pending = False
                try:
                    entry = self.entries[self.selected]
                    remove(entry, self.cwd)
                except (WorktreeError, OSError) as exc:
                    self.message = str(exc)
                else:
                    self.entries.pop(self.selected)
                    self.selected = max(0, min(self.selected, len(self.entries) - 1))
                    self.message = f"Removed {entry.label}."
                    if entry.branch:
                        self.message += " Branch retained."
            elif key in {"escape", "backspace"}:
                self.pending = False
                self.message = "Deletion cancelled."
            return ""
        if key == "escape":
            return "quit"
        if key == "refresh":
            return "refresh"
        if not self.entries:
            return ""
        if key in {"up", "down"}:
            step = -1 if key == "up" else 1
            self.selected = (self.selected + step) % len(self.entries)
        elif key == "backspace":
            reason = blocked_reason(self.entries[self.selected], self.cwd)
            if reason:
                self.message = f"Cannot delete: {reason}."
            else:
                self.pending = True
                self.message = "Remove this worktree directory? Press Enter to confirm."
        return ""

    def render(self, width: int, height: int) -> Group:
        viewport = max(1, height - 10)
        start = min(
            max(0, self.selected - viewport + 1), max(0, len(self.entries) - viewport)
        )
        title = Text("Clean Worktrees", style="bold magenta", no_wrap=True)
        title.append(f"  {len(self.entries)} worktrees", style="dim")
        table = Table(expand=True, box=None, pad_edge=False)
        table.add_column("", width=2)
        table.add_column("Branch / HEAD", ratio=2, overflow="ellipsis", no_wrap=True)
        table.add_column("Path", ratio=3, overflow="ellipsis", no_wrap=True)
        table.add_column("Status", ratio=2, overflow="ellipsis", no_wrap=True)
        for index in range(start, min(len(self.entries), start + viewport)):
            entry = self.entries[index]
            # Status is cached for the view; removal always rechecks it.
            reason = self.reasons.get(entry.path, "")
            table.add_row(
                "›" if index == self.selected else "",
                Text(entry.label),
                Text(str(entry.path)),
                Text(reason or "Ready", style="yellow" if reason else "green"),
                style="bold reverse" if index == self.selected else None,
            )
        for _ in range(max(0, viewport - len(self.entries))):
            table.add_row("", "", "", "")
        path = (
            str(self.entries[self.selected].path)
            if self.entries
            else "No host worktrees found."
        )
        footer = (
            "Enter Confirm deletion  ·  Esc / Backspace Cancel  ·  Q Quit"
            if self.pending
            else "↑/↓ Select  ·  Backspace Delete  ·  R Refresh  ·  Esc / Q / Ctrl+C Quit"
        )
        if width < 65:
            footer = (
                "Enter Confirm · Esc Cancel · Q Quit"
                if self.pending
                else "↑↓ Select · ⌫ Delete · R Refresh · Q Quit"
            )
        return Group(
            title,
            Text(
                "Host worktrees · Git removes directories and retains branches",
                style="dim",
                no_wrap=True,
            ),
            Text(""),
            table,
            Text(""),
            Text(path, style="cyan", no_wrap=True, overflow="ellipsis"),
            Text(
                self.message,
                style="bold red" if self.pending else "yellow",
                no_wrap=True,
                overflow="ellipsis",
            ),
            Text(""),
            Text(footer, style="dim", no_wrap=True, overflow="ellipsis"),
        )

    def refresh_status(self) -> None:
        self.reasons = {
            entry.path: blocked_reason(entry, self.cwd) for entry in self.entries
        }


@contextmanager
def keyboard() -> Iterator[int]:
    fd = sys.stdin.fileno()
    settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield fd
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, settings)


def read_key(fd: int) -> str:
    if not select.select([fd], [], [], 0.1)[0]:
        return ""
    key = os.read(fd, 1)
    if key == b"\x1b":
        sequence = b""
        while len(sequence) < 8 and select.select([fd], [], [], 0.03)[0]:
            byte = os.read(fd, 1)
            if not byte:
                return "quit"
            sequence += byte
            if sequence[-1:] in b"ABCDEFGHIJKLMNOPQRSTUVWXYZ~":
                break
        return {
            b"[A": "up",
            b"OA": "up",
            b"[B": "down",
            b"OB": "down",
            b"": "escape",
        }.get(sequence, "")
    return {
        b"k": "up",
        b"j": "down",
        b"\x7f": "backspace",
        b"\x08": "backspace",
        b"\r": "enter",
        b"\n": "enter",
        b"q": "quit",
        b"Q": "quit",
        b"r": "refresh",
        b"R": "refresh",
        b"\x03": "ctrl-c",
        b"\x04": "quit",
        b"": "quit",
    }.get(key, "")


def run_clean(host_home: Path, cwd: Path) -> None:
    if not shutil.which("git"):
        raise WorktreeError("Git is required to clean host worktrees.")
    console = Console()
    entries = discover(host_home, cwd)
    if not entries:
        console.print("No host worktrees found.")
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise WorktreeError(
            "Worktree cleanup requires an interactive terminal. Run: codemate worktree clean"
        )
    selector = Selector(entries, cwd)
    selector.refresh_status()
    try:
        with (
            keyboard() as fd,
            Live(console=console, screen=True, auto_refresh=False) as live,
        ):
            while True:
                live.update(
                    selector.render(console.width, console.height), refresh=True
                )
                key = read_key(fd)
                action = selector.handle(key)
                if action == "quit":
                    break
                if action == "refresh":
                    selector.entries = discover(host_home, cwd)
                    selector.selected = min(
                        selector.selected, max(0, len(selector.entries) - 1)
                    )
                    selector.message = "Worktree list refreshed."
                if action == "refresh" or key == "enter":
                    selector.refresh_status()
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        raise WorktreeError(f"Cannot access worktree or terminal: {exc}") from exc
