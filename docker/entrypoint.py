#!/usr/bin/python3
"""Match the agent account to the host before starting setup or a pure shell."""

import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile


def mount_paths():
    # st_dev / os.path.ismount cannot identify bind mounts on the same device.
    # mountinfo escapes whitespace and backslashes with octal sequences.
    return {
        Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), line.split()[4]))
        for line in Path("/proc/self/mountinfo").read_text().splitlines()
    }


def update_ownership(root, old_uid, old_gid, uid, gid, mounts):
    """Update image-owned files without following links or entering mounts."""
    root = Path(root)
    if not root.exists() or any(root == mount or mount in root.parents for mount in mounts if mount != Path("/")):
        return

    def chown(path):
        info = path.lstat()
        owner = uid if info.st_uid == old_uid and uid != old_uid else -1
        group = gid if info.st_gid == old_gid and gid != old_gid else -1
        if owner != -1 or group != -1:
            os.chown(path, owner, group, follow_symlinks=False)

    chown(root)
    if root.is_symlink():
        return
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if Path(directory, name) not in mounts]
        for name in dirs + files:
            path = Path(directory, name)
            if path not in mounts:
                chown(path)


def requested_id(name, default):
    raw = os.environ.get(name, str(default))
    if not re.fullmatch(r"[0-9]+", raw) or int(raw) >= 2**32 - 1:
        raise ValueError(f"{name} must be an integer between 0 and {2**32 - 2}")
    return int(raw)


def configure_agent():
    agent = pwd.getpwnam("agent")
    uid = requested_id("CODEMATE_UID", agent.pw_uid)
    gid = requested_id("CODEMATE_GID", agent.pw_gid)
    # Claude's permission-bypass mode requires a non-root user. A root host
    # invocation keeps the image's existing agent identity.
    if uid == 0:
        return agent
    if (uid, gid) == (agent.pw_uid, agent.pw_gid):
        return agent
    try:
        existing = pwd.getpwuid(uid)
    except KeyError:
        existing = None
    if existing is not None and existing.pw_name != "agent":
        raise ValueError(f"Cannot map agent to UID {uid}: already used by {existing.pw_name}")

    # usermod normally changes ownership recursively under the home, including
    # host mounts. Temporarily point the account at an empty home instead.
    with tempfile.TemporaryDirectory(prefix="codemate-user-", dir="/run") as empty_home:
        subprocess.run(["/usr/sbin/usermod", "--home", empty_home, "agent"], check=True)
        try:
            if gid != agent.pw_gid:
                # Keep the agent group name even when a system group (e.g.
                # macOS's GID 20) already uses the desired numeric ID.
                subprocess.run(["/usr/sbin/groupmod", "--non-unique", "--gid", str(gid), "agent"], check=True)
            subprocess.run(["/usr/sbin/usermod", "--uid", str(uid), "--gid", str(gid), "agent"], check=True)
        finally:
            subprocess.run(["/usr/sbin/usermod", "--home", agent.pw_dir, "agent"], check=True)

    mounts = mount_paths()
    for directory in (agent.pw_dir, "/usr/local/share/npm-global"):
        update_ownership(directory, agent.pw_uid, agent.pw_gid, uid, gid, mounts)
    return pwd.getpwnam("agent")


def main():
    command = sys.argv[1:] or ["zsh"]
    if os.getuid() == 0:
        agent = configure_agent()
        os.initgroups(agent.pw_name, agent.pw_gid)
        os.setgid(agent.pw_gid)
        os.setuid(agent.pw_uid)
        os.environ.update(HOME=agent.pw_dir, USER=agent.pw_name, LOGNAME=agent.pw_name)
    # Explicit docker --user overrides already running as non-root are honored.
    # exec preserves the working directory, environment, TTY, signals and status.
    os.execvp(command[0], command)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"CodeMate user setup failed: {exc}")
