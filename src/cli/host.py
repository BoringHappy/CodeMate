"""Launch native agents with session-scoped CodeMate plugins on macOS/Linux."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

HOST_PLUGINS = ("git", "pr", "dev", "issue", "workspace-host")
LEGACY_PLUGINS = ("git", "pr", "dev", "issue", "workspace", "pm", "workspace-host")


def resources() -> Path:
    packaged = Path(__file__).parent / "resources" / "plugins"
    if packaged.is_dir():
        return packaged
    source = Path(__file__).resolve().parents[2] / "plugins"
    if source.is_dir():
        return source
    raise SystemExit(
        "CodeMate host plugin resources are missing; reinstall codemate-cli."
    )


def bundle_id(source: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(source)).encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(str(path.stat().st_mode & 0o111).encode())
            digest.update(b"\0")
    return digest.hexdigest()[:16]


@contextmanager
def locked(path: Path, *, wait: bool = False) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise SystemExit(
                "Another CodeMate host session is using this worktree. Use a separate git worktree."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def publish_tree(source: Path, target: Path, *, marketplace: str = "") -> None:
    """Publish immutable resources atomically; never edit a running session's files."""
    if target.is_dir():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".codemate-", dir=target.parent) as temp:
        staging = Path(temp) / "bundle"
        shutil.copytree(
            source, staging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        if marketplace:
            catalog = staging / ".agents" / "plugins" / "marketplace.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(
                json.dumps(
                    {
                        "name": marketplace,
                        "plugins": [
                            {
                                "name": name,
                                "source": {"source": "local", "path": f"./{name}"},
                                "policy": {
                                    "installation": "AVAILABLE",
                                    "authentication": "ON_INSTALL",
                                },
                                "category": "Developer Tools",
                            }
                            for name in HOST_PLUGINS
                        ],
                    },
                    indent=2,
                )
                + "\n"
            )
        staging.rename(target)


def prepare_plugins(
    source: Path, bundle: Path, codex_home: Path, agent: str, marketplace: str
) -> None:
    with locked(bundle.parent / ".publish.lock", wait=True):
        publish_tree(source, bundle, marketplace=marketplace)
    if agent == "codex":
        # `codex plugin add` enables the plugin in global config. Populate only
        # its cache instead: discovery and enablement are supplied on launch.
        cache = codex_home / "plugins" / "cache" / marketplace
        with locked(cache.parent / f".{marketplace}.lock", wait=True):
            for name in HOST_PLUGINS:
                manifest = json.loads(
                    (bundle / name / ".codex-plugin" / "plugin.json").read_text()
                )
                version = manifest.get("version", "local")
                publish_tree(bundle / name, cache / name / version)


def native_command(
    agent: str, bundle: Path, marketplace: str, prompt: str, query: str, *, chat: bool
) -> list[str]:
    if agent == "codex":
        command = [
            "codex",
            "--no-daemon",
            "--no-alt-screen",
            "-c",
            "features.plugins=true",
            "-c",
            "features.hooks=true",
        ]
        command += [
            "-c",
            f'marketplaces.{marketplace}.source_type="local"',
            "-c",
            f"marketplaces.{marketplace}.source={json.dumps(str(bundle))}",
        ]
        # Codex -c splits dotted keys literally; TOML-style quotes in a key
        # would enable a different entry named '"git@..."'. Quote the shell
        # argument, not the key inside it.
        for name in LEGACY_PLUGINS:
            command += ["-c", f"plugins.{name}@codemate.enabled=false"]
        for name in HOST_PLUGINS:
            command += ["-c", f"plugins.{name}@{marketplace}.enabled=true"]
        if not chat:
            command += ["-c", f"developer_instructions={json.dumps(prompt)}"]
    else:
        command = [
            "claude",
            "--settings",
            json.dumps(
                {
                    "enabledPlugins": {
                        f"{name}@codemate": False for name in LEGACY_PLUGINS
                    }
                }
            ),
        ]
        for name in HOST_PLUGINS:
            command += ["--plugin-dir", str(bundle / name)]
        if not chat:
            command += ["--append-system-prompt", prompt]
    if query:
        command += ["--", query]
    return command


def host_environment(args: SimpleNamespace) -> dict[str, str]:
    from .main import parse_env_file

    # Native auth, MCP and model config stay in their original locations. The
    # container's project .env and CodeMate setup config are not auto-imported.
    env = dict(os.environ)
    for path in getattr(args, "env_file", []) or []:
        env.update(parse_env_file(Path(path)))
    for item in getattr(args, "env", []) or []:
        key, separator, value = item.partition("=")
        if not separator or not key.isidentifier():
            raise SystemExit(f"--env expects KEY=VALUE, got: {item}")
        env[key] = value
    return env


def git_output(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args], text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise SystemExit(
            "--host requires an existing Git worktree. Run it inside your local checkout."
        )
    return result.stdout.strip()


def validate_options(args: SimpleNamespace) -> None:
    incompatible = {
        "setup": "--setup",
        "pure": "--pure",
        "shell": "--shell",
        "build": "--build",
        "dockerfile": "--dockerfile",
        "tag": "--tag",
        "docker_param": "--docker-param",
        "network": "--network",
        "mount": "--mount",
        "image": "--image",
        "image_registry": "--image-registry",
        "skip_pull": "--skip-pull",
        "tz": "--tz",
        "repo": "--repo",
        "upstream": "--upstream",
    }
    selected = [flag for key, flag in incompatible.items() if getattr(args, key, None)]
    if selected:
        raise SystemExit(
            "--host uses the current checkout without container setup; remove "
            + ", ".join(selected)
            + "."
        )
    if sum(bool(getattr(args, key, None)) for key in ("branch", "pr", "issue")) > 1:
        raise SystemExit("Specify only one target: --branch, --pr, or --issue.")
    for key in ("pr", "issue"):
        target = getattr(args, key, None)
        if target and (not str(target).isdigit() or int(target) < 1):
            raise SystemExit(f"--{key} expects a positive number.")


def validate_pr(
    args: SimpleNamespace, cwd: Path, branch: str, env: dict[str, str]
) -> None:
    if not getattr(args, "pr", None):
        return
    result = subprocess.run(
        ["gh", "pr", "view", str(args.pr), "--json", "number,url,state,headRefName"],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit(
            "Cannot read the requested PR with the host's gh authentication."
        )
    data = json.loads(result.stdout)
    if data["state"] != "OPEN" or data["headRefName"] != branch:
        raise SystemExit(
            "--host --pr requires the current branch to match the open PR. Check out its branch in a separate worktree first."
        )


def run_host(args: SimpleNamespace) -> None:
    from .main import codemate_home

    validate_options(args)
    if getattr(args, "update", False):
        print("Installed with uv tool. Update with: uv tool upgrade codemate-cli")
        return
    env = host_environment(args)
    agent = getattr(args, "agent", None) or env.get("CODEMATE_AGENT") or "claude"
    if agent not in {"codex", "claude"}:
        raise SystemExit(f"Invalid host agent: {agent}. Expected: claude or codex")
    dry_run = getattr(args, "dry_run", False)
    show_config = getattr(args, "config", False)
    chat = bool(getattr(args, "chat", False)) or env.get(
        "CODEMATE_CHAT", ""
    ).lower() in {"1", "true", "yes", "on"}
    no_pr = (
        bool(getattr(args, "no_pr", False))
        or chat
        or env.get("CODEMATE_NO_PR", "").lower() in {"1", "true", "yes", "on"}
    )
    if not dry_run and not show_config:
        required = (agent, "git", "bash", "jq") + (() if chat else ("gh",))
        missing = [
            name for name in required if not shutil.which(name, path=env.get("PATH"))
        ]
        if missing:
            raise SystemExit("Host prerequisites missing: " + ", ".join(missing))
    cwd = Path.cwd().resolve()
    git_dir = git_output(cwd, "rev-parse", "--absolute-git-dir")
    branch = git_output(cwd, "branch", "--show-current")
    if not branch:
        raise SystemExit("--host requires a named branch; check out a branch first.")
    if getattr(args, "branch", None) and args.branch != branch:
        raise SystemExit(
            f"Current branch is {branch}; --host does not switch to {args.branch}."
        )
    source = resources()
    identity = bundle_id(source)
    home = codemate_home().resolve() / "host"
    bundle = home / "plugins" / identity
    marketplace = f"codemate-host-{identity}"
    codex_home = (
        Path(env.get("CODEX_HOME") or str(Path.home() / ".codex"))
        .expanduser()
        .resolve()
    )
    runtime = (
        Path(env.get("CODEMATE_RUNTIME_DIR") or str(home / "runtime"))
        .expanduser()
        .resolve()
    )
    env.update(
        {
            "CODEMATE_MODE": "host",
            "CODEMATE_AGENT": agent,
            "CODEMATE_INSTANCE_ID": f"host-{uuid.uuid4().hex}",
            "CODEMATE_RUNTIME_DIR": str(runtime),
            "CODEMATE_PYTHON": sys.executable,
            "CODEMATE_PLUGIN_ROOT": str(bundle / "workspace-host"),
            "CODEMATE_WORKSPACE_HOOKS_ROOT": str(bundle / "workspace" / "hooks"),
            "CODEMATE_PR_PLUGIN_ROOT": str(bundle / "pr"),
            "CODEMATE_NO_PR": "true" if no_pr else "",
            "CODEMATE_CHAT": "true" if chat else "",
        }
    )
    if getattr(args, "co_author_by", None):
        env["CODEMATE_CO_AUTHOR_BY"] = args.co_author_by
    prompt = (Path(__file__).parent / "host_prompt.txt").read_text()
    if getattr(args, "pr", None):
        prompt += f"\nThe selected PR is #{args.pr}; read it with pr:get-details.\n"
    if getattr(args, "issue", None):
        prompt += f"\nStart by reading issue #{args.issue} with issue:read-issue. Work on the current branch.\n"
    command = native_command(
        agent,
        bundle,
        marketplace,
        prompt,
        getattr(args, "query", None) or "",
        chat=chat,
    )
    print(f"CodeMate Host: {agent} · {cwd} · {branch}")
    if show_config:
        print(
            json.dumps(
                {
                    "mode": "host",
                    "agent": agent,
                    "workspace": str(cwd),
                    "branch": branch,
                    "plugins": list(HOST_PLUGINS),
                    "bundle": str(bundle),
                    "runtime": str(runtime),
                    "chat": chat,
                    "no_pr": bool(env["CODEMATE_NO_PR"]),
                },
                indent=2,
            )
        )
        return
    if dry_run:
        print(shlex.join(command))
        return
    key = hashlib.sha256(git_dir.encode()).hexdigest()
    with locked(home / "locks" / f"{key}.lock"):
        if not chat and git_output(cwd, "status", "--porcelain"):
            raise SystemExit(
                "PR automation requires a clean worktree at launch. Commit/stash existing changes, use a separate worktree, or use --chat."
            )
        if not chat:
            validate_pr(args, cwd, branch, env)
        prepare_plugins(source, bundle, codex_home, agent, marketplace)
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.run(command, cwd=cwd, env=env, check=True)
