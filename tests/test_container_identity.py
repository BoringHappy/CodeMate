import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli import main


spec = importlib.util.spec_from_file_location("entrypoint", Path(__file__).parents[1] / "docker" / "entrypoint.py")
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


@pytest.mark.parametrize("mode", ["agent", "shell", "pure"])
def test_launch_passes_host_identity(mode, monkeypatch, tmp_path):
    monkeypatch.setenv("CODEMATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CODEMATE_PURE_HOME", str(tmp_path / "pure-state"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main.os, "getuid", lambda: 12345)
    monkeypatch.setattr(main.os, "getgid", lambda: 20)
    config = {
        key: main.ResolvedValue(value, "test")
        for key, value in {
            "CODEMATE_GIT_REPO_URL": "https://github.com/example/project.git",
            "CODEMATE_BRANCH_NAME": "feature",
            "CODEMATE_AGENT": "codex",
            "CODEMATE_IMAGE": "codemate:test",
        }.items()
    }
    args = SimpleNamespace(mount=[], docker_param=[], dry_run=True, shell=mode == "shell")
    launch = main.pure_docker_command if mode == "pure" else main.docker_command

    command = launch(config, args, "/tmp/codemate.env")

    assert command[command.index("CODEMATE_UID=12345") - 1] == "--env"
    assert command[command.index("CODEMATE_GID=20") - 1] == "--env"


def test_host_identity_without_unix_ids(monkeypatch):
    monkeypatch.delattr(main.os, "getuid")
    monkeypatch.delattr(main.os, "getgid")
    assert main.host_identity_args() == []


@pytest.mark.parametrize("raw", ["", "-1", "alice", "1:2", "4294967295"])
def test_invalid_identity_fails_before_account_changes(raw, monkeypatch):
    monkeypatch.setenv("CODEMATE_UID", raw)
    with pytest.raises(ValueError, match="CODEMATE_UID must be an integer"):
        entrypoint.requested_id("CODEMATE_UID", 1000)


@pytest.mark.parametrize("uid,gid", [(0, 0), (1000, 1000)])
def test_root_and_matching_identity_leave_account_unchanged(uid, gid, monkeypatch):
    agent = SimpleNamespace(pw_name="agent", pw_uid=1000, pw_gid=1000, pw_dir="/home/agent")
    monkeypatch.setattr(entrypoint.pwd, "getpwnam", lambda name: agent)
    monkeypatch.setenv("CODEMATE_UID", str(uid))
    monkeypatch.setenv("CODEMATE_GID", str(gid))
    monkeypatch.setattr(entrypoint.subprocess, "run", lambda *args, **kwargs: pytest.fail("account changed"))
    assert entrypoint.configure_agent() is agent


def test_conflicting_uid_fails_before_account_changes(monkeypatch):
    agent = SimpleNamespace(pw_name="agent", pw_uid=1000, pw_gid=1000)
    monkeypatch.setattr(entrypoint.pwd, "getpwnam", lambda name: agent)
    monkeypatch.setattr(entrypoint.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="daemon"))
    monkeypatch.setenv("CODEMATE_UID", "1")
    monkeypatch.setenv("CODEMATE_GID", "20")
    monkeypatch.setattr(entrypoint.subprocess, "run", lambda *args, **kwargs: pytest.fail("account changed"))
    with pytest.raises(ValueError, match="already used by daemon"):
        entrypoint.configure_agent()


def test_mount_paths_decode_escaped_characters(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda self: (
        "1 0 0:1 / / rw - overlay overlay rw\n"
        "2 1 0:1 /host /home/agent/with\\040space rw - ext4 /dev/sda rw\n"
        "3 1 0:1 /file /home/agent/back\\134slash rw - ext4 /dev/sda rw\n"
    ))
    assert entrypoint.mount_paths() == {Path("/"), Path("/home/agent/with space"), Path("/home/agent/back\\slash")}


def test_ownership_update_skips_mounts_and_symlink_targets(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    tool = home / "tool"
    tool.write_text("tool")
    mounted = home / "mounted"
    mounted.mkdir()
    (mounted / "host-owned").touch()
    mounted_file = home / "mounted-file"
    mounted_file.touch()
    (home / "link").symlink_to(mounted, target_is_directory=True)
    updated = []
    monkeypatch.setattr(entrypoint.os, "chown", lambda path, uid, gid, **kw: updated.append((path, uid, gid, kw)))

    entrypoint.update_ownership(home, os.getuid(), os.getgid(), 12345, 23456, {Path("/"), mounted, mounted_file})

    assert {item[0] for item in updated} == {home, tool, home / "link"}
    assert all(item[1:] == (12345, 23456, {"follow_symlinks": False}) for item in updated)


def test_ownership_update_skips_root_inside_a_mount(tmp_path, monkeypatch):
    nested = tmp_path / "mounted" / "nested"
    nested.mkdir(parents=True)
    monkeypatch.setattr(entrypoint.os, "chown", lambda *args, **kwargs: pytest.fail("mount ownership changed"))
    entrypoint.update_ownership(nested, os.getuid(), os.getgid(), 12345, 20, {tmp_path / "mounted"})


def test_ownership_update_preserves_unrelated_owners(tmp_path, monkeypatch):
    updated = []
    monkeypatch.setattr(entrypoint.os, "chown", lambda path, uid, gid, **kw: updated.append((uid, gid)))
    entrypoint.update_ownership(tmp_path, os.getuid() + 1, os.getgid(), 12345, os.getgid() + 1, set())
    assert updated == [(-1, os.getgid() + 1)]
