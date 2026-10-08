"""Launch native agents with session-scoped CodeMate plugins on macOS/Linux."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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
def locked(path: Path, *, wait: bool = False) -> Iterator[int]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise SystemExit(
                "Another CodeMate host session is using this worktree. Use a separate git worktree."
            ) from exc
        try:
            yield handle.fileno()
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
            "--yolo",
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
    targets = sum(bool(getattr(args, key, None)) for key in ("branch", "pr", "issue"))
    if targets > 1:
        raise SystemExit("Specify only one target: --branch, --pr, or --issue.")
    if not targets and not getattr(args, "update", False):
        raise SystemExit(
            "Specify --branch for a new task, or --pr / --issue for an existing target."
        )
    for key in ("pr", "issue"):
        target = getattr(args, key, None)
        if target and (not str(target).isdigit() or int(target) < 1):
            raise SystemExit(f"--{key} expects a positive number.")


def github_target(cwd: Path, branch: str = "") -> tuple[list[str], str]:
    """Use upstream as the PR repository when this checkout is a fork."""
    result = subprocess.run(
        ["git", "-C", str(cwd), "remote", "get-url", "upstream"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        return [], branch

    def github_repo(url: str) -> str:
        match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
        if not match:
            raise SystemExit(
                "Host fork workflow requires GitHub origin/upstream remotes."
            )
        return match[1]

    repository = github_repo(result.stdout.strip())
    owner = github_repo(git_output(cwd, "remote", "get-url", "origin")).split("/")[0]
    return ["--repo", repository], f"{owner}:{branch}" if branch else ""


def github_output(command: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, check=False
    )
    if result.returncode:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise SystemExit(f"GitHub CLI failed during {' '.join(command[:3])}: {detail}")
    return result.stdout.strip()


def read_pr(args: SimpleNamespace, cwd: Path, env: dict[str, str]) -> dict:
    if not getattr(args, "pr", None):
        return {}
    output = github_output(
        [
            "gh",
            "pr",
            "view",
            str(args.pr),
            *github_target(cwd)[0],
            "--json",
            "number,url,state,headRefName,headRefOid,baseRefName",
        ],
        cwd,
        env,
    )
    data = json.loads(output)
    if data["state"] != "OPEN":
        raise SystemExit("--host --pr requires an open PR.")
    if data["baseRefName"] not in {"main", "master"}:
        raise SystemExit("The PR base branch must be main or master.")
    return data


def has_ref(cwd: Path, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--verify", f"{ref}^{{commit}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
        == 0
    )


def base_reference(cwd: Path, requested: str | None) -> tuple[str, str]:
    if requested and requested not in {"main", "master"}:
        raise SystemExit("--base-branch must be main or master.")
    for name in [requested] if requested else ["main", "master"]:
        for ref in (
            f"refs/remotes/upstream/{name}",
            f"refs/remotes/origin/{name}",
            f"refs/heads/{name}",
        ):
            if has_ref(cwd, ref):
                if ref.startswith("refs/heads/"):
                    remotes = git_output(cwd, "remote").splitlines()
                    for remote in ("upstream", "origin"):
                        if remote in remotes:
                            return name, f"refs/remotes/{remote}/{name}"
                return name, ref
    raise SystemExit(
        "No main/master base branch is available. Fetch or create main/master before using --host."
    )


def worktree_records(cwd: Path) -> list[dict[str, str]]:
    records = []
    record = {}
    for field in git_output(cwd, "worktree", "list", "--porcelain", "-z").split("\0"):
        if not field:
            if "worktree" in record:
                records.append(record)
            record = {}
        else:
            key, _, value = field.partition(" ")
            record[key] = value
    return records


def registered_worktrees(cwd: Path) -> dict[str, Path]:
    return {
        record["branch"].removeprefix("refs/heads/"): Path(record["worktree"]).resolve()
        for record in worktree_records(cwd)
        if "branch" in record
    }


@dataclass(frozen=True)
class WorktreePlan:
    repository: Path
    common_dir: Path
    branch: str
    base_branch: str
    base_ref: str
    path: Path
    pr_number: str = ""

    @property
    def repository_key(self) -> str:
        return hashlib.sha256(str(self.common_dir).encode()).hexdigest()[:16]

    @property
    def branch_key(self) -> str:
        return hashlib.sha256(self.branch.encode()).hexdigest()[:16]

    @property
    def lock_directory(self) -> Path:
        # Shared by every linked worktree and independent of CODEMATE_HOME.
        return self.common_dir / "codemate-host-locks"

    @property
    def session_lock(self) -> Path:
        return self.lock_directory / f"{self.branch_key}.lock"


def plan_worktree(
    args: SimpleNamespace, cwd: Path, home: Path, env: dict[str, str]
) -> WorktreePlan:
    repository = Path(git_output(cwd, "rev-parse", "--show-toplevel"))
    common_dir = (
        repository / git_output(repository, "rev-parse", "--git-common-dir")
    ).resolve()
    pr = read_pr(args, repository, env)
    requested = getattr(args, "base_branch", None)
    if pr and requested and requested != pr["baseRefName"]:
        raise SystemExit("--base-branch must match the PR's base branch.")
    base_branch, base_ref = base_reference(
        repository, pr.get("baseRefName") or requested
    )
    branch = (
        pr.get("headRefName") or getattr(args, "branch", None) or f"issue-{args.issue}"
    )
    if branch in {"main", "master"}:
        raise SystemExit(
            "The task branch cannot be main or master; choose a feature branch."
        )
    valid = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        capture_output=True,
        check=False,
    )
    if valid.returncode or branch.startswith("-") or "@{" in branch:
        raise SystemExit(f"Invalid task branch: {branch}")
    repository_key = hashlib.sha256(str(common_dir).encode()).hexdigest()[:16]
    branch_key = hashlib.sha256(branch.encode()).hexdigest()[:16]
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", branch)[:40]
    path = home / "worktrees" / repository_key / f"{slug}-{branch_key}"
    path = registered_worktrees(repository).get(branch, path)
    return WorktreePlan(
        repository,
        common_dir,
        branch,
        base_branch,
        base_ref,
        path,
        str(getattr(args, "pr", None) or ""),
    )


def prepare_worktree(plan: WorktreePlan, home: Path, env: dict[str, str]) -> Path:
    # Serialize repository metadata changes, while task sessions on distinct
    # branches retain independent locks and can run concurrently.
    with locked(plan.lock_directory / "repository.lock", wait=True):
        if plan.base_ref.startswith("refs/remotes/"):
            remote = plan.base_ref.split("/")[2]
            print(f"Updating base: {remote}/{plan.base_branch}")
            subprocess.run(
                [
                    "git",
                    "fetch",
                    remote,
                    f"{plan.base_branch}:refs/remotes/{remote}/{plan.base_branch}",
                ],
                cwd=plan.repository,
                env=env,
                check=True,
            )
        existing = registered_worktrees(plan.repository).get(plan.branch)
        if existing:
            if not existing.is_dir():
                raise SystemExit(
                    f"The worktree registered for {plan.branch} is missing: {existing}. Repair/prune it first."
                )
            if (
                Path(git_output(existing, "rev-parse", "--absolute-git-dir"))
                == plan.common_dir
            ):
                raise SystemExit(
                    f"Branch {plan.branch} is checked out in the primary checkout. Switch that checkout to main/master before starting its host worktree."
                )
            return existing
        detached = any(
            "detached" in record and Path(record["worktree"]).resolve() == plan.path
            for record in worktree_records(plan.repository)
        )
        if plan.path.exists() and not (plan.pr_number and detached):
            raise SystemExit(
                f"Worktree destination already exists but is not registered: {plan.path}"
            )
        if detached:
            if (
                git_output(plan.path, "status", "--porcelain")
                or git_output(
                    plan.path, "rev-list", "--count", f"{plan.base_ref}..HEAD"
                )
                != "0"
            ):
                raise SystemExit(
                    f"Cannot resume PR checkout in {plan.path}: detached worktree has changes or commits beyond the base. Preserve that work before retrying."
                )
            subprocess.run(
                [
                    "gh",
                    "pr",
                    "checkout",
                    plan.pr_number,
                    *github_target(plan.path)[0],
                    "--branch",
                    plan.branch,
                ],
                cwd=plan.path,
                env=env,
                check=True,
            )
            return plan.path
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        command = ["git", "-C", str(plan.repository), "worktree", "add"]
        if has_ref(plan.repository, f"refs/heads/{plan.branch}"):
            command += [str(plan.path), plan.branch]
        elif plan.pr_number:
            # gh handles GitHub fork/head remotes without cloning a repository
            # or switching the user's primary checkout.
            command += ["--detach", str(plan.path), plan.base_ref]
        elif has_ref(plan.repository, f"refs/remotes/origin/{plan.branch}"):
            command += [
                "--track",
                "-b",
                plan.branch,
                str(plan.path),
                f"refs/remotes/origin/{plan.branch}",
            ]
        else:
            command += ["--no-track", "-b", plan.branch, str(plan.path), plan.base_ref]
        subprocess.run(command, env=env, check=True)
        if (
            plan.pr_number
            and git_output(plan.path, "branch", "--show-current") != plan.branch
        ):
            subprocess.run(
                [
                    "gh",
                    "pr",
                    "checkout",
                    plan.pr_number,
                    *github_target(plan.path)[0],
                    "--branch",
                    plan.branch,
                ],
                cwd=plan.path,
                env=env,
                check=True,
            )
        return plan.path


def prepare_pr(
    plan: WorktreePlan, cwd: Path, args: SimpleNamespace, env: dict[str, str]
) -> dict:
    """Complete setup-pr before handing the worktree to the native agent."""
    repo_flags, head = github_target(cwd, plan.branch)
    if plan.pr_number:
        pr = read_pr(args, cwd, env)
    else:
        output = github_output(
            [
                "gh",
                "pr",
                "list",
                *repo_flags,
                "--head",
                head,
                "--state",
                "open",
                "--json",
                "number,url,baseRefName",
            ],
            cwd,
            env,
        )
        candidates = json.loads(output)
        matches = [pr for pr in candidates if pr["baseRefName"] == plan.base_branch]
        if candidates and not matches:
            raise SystemExit(f"The existing PR base must match {plan.base_branch}.")
        if len(matches) > 1:
            raise SystemExit(
                "Multiple open PRs match this branch; select one with --pr."
            )
        pr = matches[0] if matches else {}
    if pr:
        # Resolve the real PR head even in a single-branch/stale local clone.
        # gh updates with fast-forward semantics; local divergent work is
        # preserved and prevents startup rather than being reset.
        subprocess.run(
            [
                "gh",
                "pr",
                "checkout",
                str(pr["number"]),
                *repo_flags,
                "--branch",
                plan.branch,
            ],
            cwd=cwd,
            env=env,
            check=True,
        )
    if not pr:
        # GitHub needs a commit beyond the base even when the task has not
        # started. Retrying after a failed push/create reuses this commit.
        if git_output(cwd, "rev-list", "--count", f"{plan.base_ref}..HEAD") == "0":
            subprocess.run(
                [
                    "git",
                    "commit",
                    "--allow-empty",
                    "-m",
                    f"Initial commit for {plan.branch}",
                ],
                cwd=cwd,
                env=env,
                check=True,
            )
        subprocess.run(
            ["git", "push", "-u", "origin", plan.branch], cwd=cwd, env=env, check=True
        )
        template = next(
            (
                path
                for path in (
                    cwd / ".github/PULL_REQUEST_TEMPLATE.md",
                    cwd / ".github/pull_request_template.md",
                    cwd / "pull_request_template.md",
                )
                if path.is_file()
            ),
            None,
        )
        body = (
            template.read_text()
            if template
            else "## Summary\n\nWork in progress.\n\n## Testing\n\nPending.\n"
        )
        title = getattr(args, "pr_title", None) or plan.branch.replace("-", " ")
        with tempfile.TemporaryDirectory(prefix="codemate-pr-") as temporary:
            body_file = Path(temporary) / "body.md"
            body_file.write_text(body)
            created = github_output(
                [
                    "gh",
                    "pr",
                    "create",
                    *repo_flags,
                    "--draft",
                    "--head",
                    head,
                    "--base",
                    plan.base_branch,
                    "--title",
                    title,
                    "--body-file",
                    str(body_file),
                ],
                cwd,
                env,
            )
        url = created.splitlines()[-1] if created else ""
        number = url.rstrip("/").rsplit("/", 1)[-1]
        if not number.isdigit():
            raise SystemExit(f"Cannot identify the newly created PR: {url}")
        pr = {"number": int(number), "url": url, "baseRefName": plan.base_branch}
    subprocess.run(
        [
            "bash",
            str(Path(env["CODEMATE_PR_PLUGIN_ROOT"]) / "scripts/pr-status.sh"),
            "set",
            "--number",
            str(pr["number"]),
            "--url",
            pr["url"],
            "--branch",
            plan.branch,
        ],
        cwd=cwd,
        env=env,
        check=True,
    )
    env["CODEMATE_PR_NUMBER"] = str(pr["number"])
    print(f"PR: {pr['url']}")
    return pr


def workflow_prompt(
    plan: WorktreePlan, args: SimpleNamespace, pr: dict | None = None
) -> str:
    standard = Path(__file__).parent / "resources/system_prompt.txt"
    if not standard.is_file():
        standard = (
            Path(__file__).resolve().parents[2]
            / "docker/setup/prompt/system_prompt.txt"
        )
    prompt = (
        standard.read_text()
        + "\n\n"
        + (Path(__file__).parent / "host_prompt.txt").read_text()
    )
    prompt += f"\nTask branch: {plan.branch}. Base branch: {plan.base_branch}.\n"
    if pr:
        prompt += f"\nCodeMate prepared PR #{pr['number']}: {pr['url']}. Read it with pr:get-details before starting.\n"
    elif plan.pr_number:
        prompt += (
            f"\nThe selected PR is #{plan.pr_number}; read it with pr:get-details.\n"
        )
    if getattr(args, "issue", None):
        prompt += f"\nRead issue #{args.issue} with issue:read-issue, then work on the prepared task branch.\n"
    return prompt


def xcode_project(worktree: Path) -> Path:
    """Select a single root workspace, falling back to a root project."""
    if not worktree.is_dir():
        raise SystemExit(f"Worktree directory does not exist: {worktree}")
    for extension in ("xcworkspace", "xcodeproj"):
        projects = sorted(
            path for path in worktree.glob(f"*.{extension}") if path.is_dir()
        )
        if len(projects) > 1:
            raise SystemExit(
                "Multiple Xcode projects found; choose one to open manually:\n"
                + "\n".join(str(path) for path in projects)
            )
        if projects:
            return projects[0]
    raise SystemExit(f"No Xcode workspace/project found in worktree root: {worktree}")


def run_host(args: SimpleNamespace) -> None:
    from .main import codemate_home

    validate_options(args)
    if getattr(args, "update", False):
        print("Installed with uv tool. Update with: uv tool upgrade codemate-cli")
        return
    env = host_environment(args)
    agent = getattr(args, "agent", None) or env.get("CODEMATE_AGENT") or "codex"
    if agent not in {"codex", "claude"}:
        raise SystemExit(f"Invalid host agent: {agent}. Expected: claude or codex")
    dry_run = getattr(args, "dry_run", False)
    show_config = getattr(args, "config", False)
    xcode = bool(getattr(args, "xcode", False)) and sys.platform == "darwin"
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
        if xcode:
            required += ("open",)
        missing = [
            name for name in required if not shutil.which(name, path=env.get("PATH"))
        ]
        if missing:
            raise SystemExit("Host prerequisites missing: " + ", ".join(missing))
    home = codemate_home().resolve() / "host"
    plan = plan_worktree(args, Path.cwd().resolve(), home, env)
    source = resources()
    identity = bundle_id(source)
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
            "CODEMATE_BASE_BRANCH": plan.base_branch,
            "CODEMATE_BRANCH_NAME": plan.branch,
            "CODEMATE_PR_NUMBER": plan.pr_number,
            "CODEMATE_ISSUE_NUMBER": str(getattr(args, "issue", None) or ""),
        }
    )
    if getattr(args, "co_author_by", None):
        env["CODEMATE_CO_AUTHOR_BY"] = args.co_author_by
    prompt = workflow_prompt(plan, args)
    command = native_command(
        agent,
        bundle,
        marketplace,
        prompt,
        getattr(args, "query", None) or "",
        chat=chat,
    )
    print(f"CodeMate Host: {agent} · {plan.branch} · base {plan.base_branch}")
    if show_config:
        print(
            json.dumps(
                {
                    "mode": "host",
                    "agent": agent,
                    "repository": str(plan.repository),
                    "workspace": str(plan.path),
                    "branch": plan.branch,
                    "base_branch": plan.base_branch,
                    "base_ref": plan.base_ref,
                    "plugins": list(HOST_PLUGINS),
                    "bundle": str(bundle),
                    "runtime": str(runtime),
                    "chat": chat,
                    "no_pr": bool(env["CODEMATE_NO_PR"]),
                    "xcode": xcode,
                },
                indent=2,
            )
        )
        return
    if dry_run:
        if plan.base_ref.startswith("refs/remotes/"):
            print(f"Update base: fetch {plan.base_ref} before worktree checkout")
        print(f"Worktree: {plan.path} (create/reuse; base {plan.base_ref})")
        if xcode:
            print("Xcode: open -a Xcode <root .xcworkspace or .xcodeproj> after worktree setup")
        print(shlex.join(command))
        return
    with locked(plan.session_lock) as session_fd:
        cwd = prepare_worktree(plan, home, env)
        print(f"Worktree: {cwd}")
        env["CODEMATE_REPO_DIR"] = str(cwd)
        if not chat and git_output(cwd, "status", "--porcelain"):
            raise SystemExit(
                "PR automation requires a clean target worktree. Commit/stash its existing changes or use --chat."
            )
        project = xcode_project(cwd) if xcode else None
        prepare_plugins(source, bundle, codex_home, agent, marketplace)
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not no_pr:
            pr = prepare_pr(plan, cwd, args, env)
            command = native_command(
                agent,
                bundle,
                marketplace,
                workflow_prompt(plan, args, pr),
                getattr(args, "query", None) or "",
                chat=chat,
            )
        if project is not None:
            print(f"Opening Xcode: {project}")
            subprocess.run(
                ["open", "-a", "Xcode", str(project)],
                cwd=cwd,
                env=env,
                check=True,
            )
        # Keep the lock in the agent too. If the launcher is killed, another
        # session cannot enter this branch while its agent is still running.
        subprocess.run(command, cwd=cwd, env=env, check=True, pass_fds=(session_fd,))
        print(f"Worktree retained: {cwd}")
