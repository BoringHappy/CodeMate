from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import typer
from rich.console import Console
from rich.table import Table


DEFAULT_IMAGE_REGISTRY = "ghcr.io"
DEFAULT_IMAGE_REPOSITORY = "boringhappy/codemate:latest"
DEFAULT_PURE_IMAGE_REPOSITORY = "boringhappy/codemate-pure:latest"
DEFAULT_IMAGE = f"{DEFAULT_IMAGE_REGISTRY}/{DEFAULT_IMAGE_REPOSITORY}"
DEFAULT_PURE_IMAGE = f"{DEFAULT_IMAGE_REGISTRY}/{DEFAULT_PURE_IMAGE_REPOSITORY}"
DEFAULT_DOCKERFILE = "docker/Dockerfile"
DEFAULT_PURE_DOCKERFILE = "docker/Dockerfile.pure"
DEFAULT_TAG = "codemate:local"
DEFAULT_PURE_TAG = "codemate-pure:local"
# Home directory inside the container; pure mode mirrors the host working
# directory below it so both sides agree on the workspace layout.
CONTAINER_HOME = "/home/agent"
# Docker flags that take a value and therefore must not be passed alone.
DOCKER_VALUE_FLAGS = ("--network",)
DEFAULT_MARKETPLACES = "BoringHappy/CodeMate"
DEFAULT_PLUGINS = "git@codemate,pr@codemate,dev@codemate,issue@codemate,workspace@codemate"

app = typer.Typer(add_completion=False, context_settings={"help_option_names": ["-h", "--help"]})
console = Console()


class Agent(str, Enum):
    claude = "claude"
    codex = "codex"


@dataclass(frozen=True)
class Field:
    name: str
    cli_attr: Optional[str] = None
    derived: Optional[Callable[[], str]] = None
    default: str = ""
    allow_empty_override: bool = False
    docker_export: bool = True
    secret: bool = False


@dataclass
class ResolvedValue:
    value: str
    source: str
    field: Optional[Field] = None


def run_capture(args: Sequence[str]) -> str:
    try:
        return subprocess.run(args, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()
    except FileNotFoundError:
        return ""


def run_checked(args: Sequence[str]) -> None:
    subprocess.run(args, check=True)


def codemate_home() -> Path:
    """Resolve the CodeMate home directory, overridable via CODEMATE_HOME.

    Defaults to ~/.codemate; CODEMATE_HOME may point anywhere (absolute path,
    ~ expansion, or $VAR references are all supported).
    """
    raw = os.environ.get("CODEMATE_HOME") or str(Path.home() / ".codemate")
    return Path(os.path.expanduser(os.path.expandvars(raw)))


def default_image(registry: str, repository: str = DEFAULT_IMAGE_REPOSITORY) -> str:
    """Build a built-in image reference served from the configured registry.

    Only the default images go through here: a mirror registry substitutes for
    ghcr.io, while an explicit ``--image``/``CODEMATE_IMAGE`` is never rewritten.
    """
    return f"{registry.strip().rstrip('/') or DEFAULT_IMAGE_REGISTRY}/{repository}"


def pure_home() -> Path:
    """Resolve the CodeMate home used by pure mode.

    Pure sessions get their own home so they never read or write the standard
    CodeMate home's agent credentials and plugin state. Defaults to the
    standard home with a ``-pure`` suffix (``~/.codemate-pure``), so a custom
    CODEMATE_HOME keeps its sibling; CODEMATE_PURE_HOME overrides it entirely.
    """
    raw = os.environ.get("CODEMATE_PURE_HOME")
    if raw:
        return Path(os.path.expanduser(os.path.expandvars(raw)))
    base = codemate_home()
    if base.name:
        return Path(f"{base}-pure")
    return base / "codemate-pure"


def ensure_pure_home() -> Path:
    """Create the pure home plus the agent state entries mounted into $HOME.

    Seeding ~/.claude, ~/.claude.json, and ~/.codex means the first login or
    config write inside a pure container lands in the pure home instead of the
    container's throwaway filesystem.
    """
    home = pure_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".codex").mkdir(exist_ok=True)
    claude_json = home / ".claude.json"
    if not claude_json.exists():
        claude_json.write_text("{}\n")
    return home


def is_pure(args: SimpleNamespace) -> bool:
    """Pure mode: no repository setup, no plugins, just a zsh shell.

    ``getattr`` keeps the helper usable with the lightweight argument objects
    the tests build.
    """
    return bool(getattr(args, "pure", False))


def git_remote() -> str:
    return run_capture(["git", "config", "--get", "remote.origin.url"])


def git_user_name() -> str:
    return run_capture(["git", "config", "user.name"])


def git_user_email() -> str:
    return run_capture(["git", "config", "user.email"])


def gh_token() -> str:
    return run_capture(["gh", "auth", "token"])


FIELDS: Tuple[Field, ...] = (
    Field("CODEMATE_GIT_REPO_URL", "repo", git_remote),
    Field("CODEMATE_UPSTREAM_REPO_URL", "upstream"),
    Field("CODEMATE_BRANCH_NAME", "branch"),
    Field("CODEMATE_PR_NUMBER", "pr"),
    Field("CODEMATE_PR_TITLE", "pr_title"),
    Field("CODEMATE_ISSUE_NUMBER", "issue"),
    Field("CODEMATE_QUERY", "query"),
    Field("CODEMATE_NO_PR", "no_pr"),
    Field("CODEMATE_CHAT", "chat"),
    Field("CODEMATE_AGENT", "agent", default="claude"),
    Field("CODEMATE_GITHUB_TOKEN", derived=gh_token, secret=True),
    Field("CODEMATE_GIT_USER_NAME", derived=git_user_name),
    Field("CODEMATE_GIT_USER_EMAIL", derived=git_user_email),
    Field("CODEMATE_CO_AUTHOR_BY", "co_author_by"),
    Field("CODEMATE_DOCKER_PARAMS", "docker_params", docker_export=False),
    Field("CODEMATE_MOUNTS", "mounts", docker_export=False),
    Field("CODEMATE_SKIP_PULL", "skip_pull", docker_export=False),
    Field("TZ", "tz", default="UTC", docker_export=False),
    Field("CODEMATE_DEFAULT_MARKETPLACES", default=DEFAULT_MARKETPLACES, allow_empty_override=True),
    Field("CODEMATE_DEFAULT_PLUGINS", default=DEFAULT_PLUGINS, allow_empty_override=True),
    Field("CODEMATE_CUSTOM_MARKETPLACES"),
    Field("CODEMATE_CUSTOM_PLUGINS"),
    Field("CODEMATE_SOFT_LINKS"),
    Field("CODEMATE_REPO_DIR"),
    Field("CODEMATE_INSTANCE_ID"),
    Field("CODEMATE_RUNTIME_DIR"),
    Field("SLACK_WEBHOOK", secret=True),
    Field("LARK_WEBHOOK", secret=True),
    Field("ANTHROPIC_AUTH_TOKEN", secret=True),
    Field("ANTHROPIC_BASE_URL"),
    Field("CODEMATE_IMAGE_REGISTRY", "image_registry", default=DEFAULT_IMAGE_REGISTRY, docker_export=False),
    Field("CODEMATE_IMAGE", "image", default=DEFAULT_IMAGE, docker_export=False),
)

FIELD_BY_NAME = {field.name: field for field in FIELDS}


def parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for line_number, raw_line in enumerate(path.read_text().splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            raise SystemExit(f"{path}:{line_number}: expected KEY=VALUE")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            raise SystemExit(f"{path}:{line_number}: invalid environment key: {key!r}")
        try:
            parts = shlex.split(raw_value, posix=True)
        except ValueError as exc:
            raise SystemExit(f"{path}:{line_number}: invalid quoted value: {exc}") from exc
        value = os.path.expandvars(" ".join(parts) if parts else "")
        values[key] = value
    return values


def first_value(
    field: Field,
    cli_values: Mapping[str, str],
    project_env: Mapping[str, str],
    ambient_env: Mapping[str, str],
) -> ResolvedValue:
    if field.cli_attr and field.cli_attr in cli_values:
        return ResolvedValue(cli_values[field.cli_attr], "cli", field)

    if field.name in project_env and (project_env[field.name] or field.allow_empty_override):
        return ResolvedValue(project_env[field.name], ".env", field)

    if field.name in ambient_env and (ambient_env[field.name] or field.allow_empty_override):
        return ResolvedValue(ambient_env[field.name], "environment", field)

    if field.derived:
        value = field.derived()
        if value:
            return ResolvedValue(value, "command", field)

    return ResolvedValue(field.default, "default", field)


def collect_cli_values(args: SimpleNamespace) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for field in FIELDS:
        if not field.cli_attr:
            continue
        value = getattr(args, field.cli_attr, None)
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                values[field.cli_attr] = "true"
            continue
        if isinstance(value, list):
            if value:
                values[field.cli_attr] = " ".join(value)
            continue
        values[field.cli_attr] = str(value)
    return values


def resolve_config(args: SimpleNamespace, cwd: Path) -> Dict[str, ResolvedValue]:
    project_env = parse_env_file(cwd / ".env")
    cli_values = collect_cli_values(args)
    target_keys = ("CODEMATE_BRANCH_NAME", "CODEMATE_PR_NUMBER", "CODEMATE_ISSUE_NUMBER")
    cli_target = next((key for key in target_keys if FIELD_BY_NAME[key].cli_attr in cli_values), None)
    if cli_target:
        for key in target_keys:
            if key != cli_target:
                project_env.pop(key, None)

    resolved = {
        field.name: first_value(field, cli_values, project_env, os.environ)
        for field in FIELDS
    }

    if cli_target:
        for key in target_keys:
            if key != cli_target:
                resolved[key] = ResolvedValue("", "target-reset", FIELD_BY_NAME[key])

    # Preserve additional project .env keys so existing provider/tool credentials still reach the container.
    for key, value in project_env.items():
        if key not in resolved:
            resolved[key] = ResolvedValue(value, ".env", None)

    for env_file in args.env_file or []:
        for key, value in parse_env_file(Path(env_file)).items():
            resolved[key] = ResolvedValue(value, f"env-file:{env_file}", FIELD_BY_NAME.get(key))

    # Explicit --env values have the highest priority for extra container variables.
    for item in args.env or []:
        if "=" not in item:
            raise SystemExit(f"--env expects KEY=VALUE, got: {item}")
        key, value = item.split("=", 1)
        resolved[key] = ResolvedValue(value, "cli", None)

    # The built-in image follows CODEMATE_IMAGE_REGISTRY, so a mirror registry
    # serves the default images without users having to pass --image.
    image = resolved["CODEMATE_IMAGE"]
    if image.source == "default":
        registry = resolved["CODEMATE_IMAGE_REGISTRY"].value
        resolved["CODEMATE_IMAGE"] = ResolvedValue(
            default_image(registry), "default", FIELD_BY_NAME["CODEMATE_IMAGE"]
        )

    return resolved


def value(config: Mapping[str, ResolvedValue], key: str) -> str:
    return config.get(key, ResolvedValue("", "missing")).value


def validate_config(config: Mapping[str, ResolvedValue]) -> None:
    agent = value(config, "CODEMATE_AGENT")
    if agent not in {"claude", "codex"}:
        raise SystemExit(f"Invalid CODEMATE_AGENT: {agent}. Expected: claude or codex")

    targets = {
        "--branch / CODEMATE_BRANCH_NAME": value(config, "CODEMATE_BRANCH_NAME"),
        "--pr / CODEMATE_PR_NUMBER": value(config, "CODEMATE_PR_NUMBER"),
        "--issue / CODEMATE_ISSUE_NUMBER": value(config, "CODEMATE_ISSUE_NUMBER"),
    }
    selected_targets = [label for label, target_value in targets.items() if target_value]
    if not selected_targets:
        raise SystemExit("Specify one of --branch, --pr, or --issue.")
    if len(selected_targets) > 1:
        raise SystemExit("Specify only one target: " + ", ".join(selected_targets))

    if not value(config, "CODEMATE_GITHUB_TOKEN"):
        raise SystemExit("CODEMATE_GITHUB_TOKEN is missing and gh auth token did not return a token.")

    if not value(config, "CODEMATE_GIT_USER_NAME") or not value(config, "CODEMATE_GIT_USER_EMAIL"):
        raise SystemExit("Git user name or email is missing. Set it or configure git user.name/user.email.")

    if not value(config, "CODEMATE_GIT_REPO_URL"):
        raise SystemExit("CODEMATE_GIT_REPO_URL is missing. Use --repo, .env, environment, or git remote origin.")


def redact(key: str, resolved: ResolvedValue) -> str:
    keywords = (
        "password", "passwd", "passphrase", "token", "secret", "api_key", "apikey",
        "private_key", "access_key", "credential",
    )
    secret = resolved.field.secret if resolved.field else any(keyword in key.lower() for keyword in keywords)
    if not resolved.value or not secret:
        return resolved.value
    if len(resolved.value) < 10:
        return "*" * len(resolved.value)
    visible = len(resolved.value) // 10
    hidden = len(resolved.value) - 2 * visible
    return f"{resolved.value[:visible]}{'*' * hidden}{resolved.value[-visible:]}"


def write_env_file(config: Mapping[str, ResolvedValue]) -> tempfile.NamedTemporaryFile:
    env_file = tempfile.NamedTemporaryFile("w", prefix="codemate-", suffix=".env", delete=False)
    path = Path(env_file.name)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    for key in sorted(config):
        field = config[key].field
        if field is not None and not field.docker_export:
            continue
        value_text = config[key].value
        if "\n" in value_text:
            raise SystemExit(f"{key} contains a newline and cannot be written to a Docker env file")
        env_file.write(f"{key}={value_text}\n")
    # Keep each runtime's temp and hook state in its own config directory so
    # Codex and Claude can run concurrently on the same host without sharing
    # writable state (both config dirs are bind-mounted into every container).
    # Use a CodeMate-scoped variable rather than overriding the global TMPDIR,
    # which all processes in the container inherit and could be blocked by.
    agent = value(config, "CODEMATE_AGENT")
    tmp_dir = "/home/agent/.codex/tmp" if agent == "codex" else "/home/agent/.claude/tmp"
    env_file.write(f"CODEMATE_TMPDIR={tmp_dir}\n")
    env_file.flush()
    return env_file


def repo_name(repo_url: str) -> str:
    trimmed = repo_url.removesuffix(".git")
    return trimmed.rstrip("/").split("/")[-1].split(":")[-1]


def sanitized(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in text)


def container_workspace_path(host_dir: Path) -> str:
    """Path inside the container that mirrors a host directory.

    A directory inside the host home keeps its layout relative to that home
    (``$HOME/code/projecta`` -> ``/home/agent/code/projecta``), so the agent
    sees the same relative paths it does on the host. The home itself and
    anything outside it fall back to a single directory named after the host
    directory instead of mirroring a path that would swallow the agent's own
    home state.
    """
    host_home = Path(os.path.expanduser("~")).resolve()
    try:
        relative = host_dir.resolve().relative_to(host_home)
    except ValueError:
        relative = Path()
    if relative.parts:
        return f"{CONTAINER_HOME}/{relative.as_posix()}"
    name = sanitized(host_dir.name) or "workspace"
    return f"{CONTAINER_HOME}/{name}"


def split_words(text: str) -> List[str]:
    return shlex.split(text) if text else []


def split_csv(text: str) -> List[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


def target_label(config: Mapping[str, ResolvedValue]) -> str:
    branch = value(config, "CODEMATE_BRANCH_NAME")
    pr = value(config, "CODEMATE_PR_NUMBER")
    issue = value(config, "CODEMATE_ISSUE_NUMBER")
    if branch:
        return f"branch {branch}"
    if pr:
        return f"PR #{pr}"
    if issue:
        return f"issue #{issue}"
    return "none"


def detail_list(items: Sequence[str]) -> str:
    return "\n".join(items) if items else "none"


def inline_detail_list(items: Sequence[str]) -> str:
    return (
        textwrap.fill(" ".join(items), width=120, break_long_words=False, break_on_hyphens=False)
        if items
        else "none"
    )


def print_launch_details(config: Mapping[str, ResolvedValue], args: SimpleNamespace) -> None:
    default_marketplaces = split_csv(value(config, "CODEMATE_DEFAULT_MARKETPLACES"))
    custom_marketplaces = split_csv(value(config, "CODEMATE_CUSTOM_MARKETPLACES"))
    default_plugins = split_csv(value(config, "CODEMATE_DEFAULT_PLUGINS"))
    custom_plugins = split_csv(value(config, "CODEMATE_CUSTOM_PLUGINS"))
    mounts = args.mount or split_words(value(config, "CODEMATE_MOUNTS"))
    docker_params = resolved_docker_params(config, args)
    extra_env_keys = sorted(key for key, item in config.items() if item.field is None)
    docker_params_text = inline_detail_list(docker_params)
    extra_env_text = inline_detail_list(extra_env_keys)
    inline_width = max(len(line) for text in (docker_params_text, extra_env_text) for line in text.splitlines())

    table = Table(title="CodeMate Launch Details", show_header=False, box=None, padding=(0, 1))
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value", min_width=inline_width)
    table.add_row("Target", target_label(config))
    table.add_row("Agent", value(config, "CODEMATE_AGENT"))
    table.add_row("Home dir", str(codemate_home()))
    if value(config, "CODEMATE_CHAT"):
        table.add_row("Chat mode", "enabled")
    if args.shell:
        table.add_row("Shell mode", "enabled")
    if value(config, "CODEMATE_SKIP_PULL"):
        table.add_row("Image pull", "skipped")
    table.add_row("Repository", repo_name(value(config, "CODEMATE_GIT_REPO_URL")))
    table.add_row("Image", value(config, "CODEMATE_IMAGE"))
    table.add_row("Timezone", value(config, "TZ"))
    if config["CODEMATE_DEFAULT_MARKETPLACES"].source != "default":
        table.add_row("Default marketplaces", detail_list(default_marketplaces))
    table.add_row("Custom marketplaces", detail_list(custom_marketplaces))
    if config["CODEMATE_DEFAULT_PLUGINS"].source != "default":
        table.add_row("Default plugins", detail_list(default_plugins))
    table.add_row("Custom plugins", detail_list(custom_plugins))
    table.add_row("Custom mounts", detail_list(mounts))
    table.add_row("Docker params", docker_params_text)
    table.add_row("Extra envs", extra_env_text)
    console.print(table, crop=False)


def check_prerequisites(config: Mapping[str, ResolvedValue], pure: bool = False) -> None:
    # Pure mode never touches GitHub or clones a repository, so git and gh are
    # not required on the host.
    required = ("docker",) if pure else ("docker", "git", "gh")
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        raise SystemExit("Missing required dependencies: " + ", ".join(missing))

    docker_info = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if docker_info.returncode != 0:
        raise SystemExit("Docker is not running or not accessible.")


def build_image(dockerfile: str, tag: str) -> None:
    dockerfile_path = Path(dockerfile)
    if not dockerfile_path.exists():
        raise SystemExit(f"Dockerfile not found: {dockerfile}")
    context = str(dockerfile_path.parent)
    run_checked(["docker", "build", "-f", dockerfile, "-t", tag, context])


def create_setup_files(cwd: Path) -> None:
    config_dir = codemate_home()
    claude_dir = config_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / ".claude.json").write_text("{}\n") if not (config_dir / ".claude.json").exists() else None
    settings = claude_dir / "settings.json"
    if not settings.exists():
        settings.write_text(
            '{\n'
            '  "env": {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},\n'
            '  "permissions": {"allow": [], "deny": []},\n'
            '  "theme": "dark",\n'
            '  "dangerouslyDisablePermissions": true,\n'
            '  "defaultMode": "bypassPermissions"\n'
            '}\n'
        )
    env_path = cwd / ".env"
    if not env_path.exists():
        env_path.write_text(
            "# CodeMate Environment Configuration\n\n"
            "# Optional: Default repository URL\n"
            "# CODEMATE_GIT_REPO_URL=\n\n"
            "# Runtime agent: claude (default) or codex\n"
            "# CODEMATE_AGENT=claude\n\n"
            "# Optional commit co-author used by the git commit skill\n"
            "# CODEMATE_CO_AUTHOR_BY=Name <email@example.com>\n\n"
            "# Optional chat mode: skips PR creation and CodeMate system prompt injection\n"
            "# CODEMATE_CHAT=\n\n"
            "# Optional: skip pulling the Docker image at startup (pull only if missing locally)\n"
            "# CODEMATE_SKIP_PULL=true\n\n"
            "# Container timezone (defaults to UTC)\n"
            "# TZ=UTC\n\n"
        )
    typer.secho("+ Setup complete", fg=typer.colors.GREEN)


def ensure_global_config() -> None:
    config_dir = codemate_home()
    if not (config_dir / ".claude").is_dir() or not (config_dir / ".claude.json").exists():
        raise SystemExit("CodeMate configuration not found. Run: codemate --setup")


def issue_defaults(config: Dict[str, ResolvedValue]) -> None:
    issue = value(config, "CODEMATE_ISSUE_NUMBER")
    if not issue:
        return
    config["CODEMATE_BRANCH_NAME"] = ResolvedValue(f"issue-{issue}", "derived", FIELD_BY_NAME["CODEMATE_BRANCH_NAME"])
    issue_repo = value(config, "CODEMATE_UPSTREAM_REPO_URL") or value(config, "CODEMATE_GIT_REPO_URL")
    if issue_repo and not value(config, "CODEMATE_QUERY"):
        repo_path = issue_repo.removesuffix(".git").split("github.com")[-1].lstrip(":/")
        issue_url = f"https://github.com/{repo_path}/issues/{issue}"
        query = f"Please use `/issue:read-issue {issue}` skill to read and address issue #{issue} ({issue_url})"
        config["CODEMATE_QUERY"] = ResolvedValue(query, "derived", FIELD_BY_NAME["CODEMATE_QUERY"])


def chat_defaults(config: Dict[str, ResolvedValue]) -> None:
    if value(config, "CODEMATE_CHAT"):
        config["CODEMATE_NO_PR"] = ResolvedValue("true", "chat", FIELD_BY_NAME["CODEMATE_NO_PR"])


def home_entry_volume_args(home: Path) -> List[str]:
    """Mount each top-level entry of a CodeMate home at the matching path in $HOME.

    This keeps ~/.claude, ~/.codex, and similar agent state directories
    available inside the container with their natural paths. The home
    directory itself is mounted by the standard image only.
    """
    home.mkdir(parents=True, exist_ok=True)
    volume_args: List[str] = []
    for entry in sorted(home.iterdir()):
        volume_args.extend(["-v", f"{entry}:/home/agent/{entry.name}"])
    return volume_args


def custom_mount_args(config: Mapping[str, ResolvedValue], args: SimpleNamespace) -> List[str]:
    mount_args: List[str] = []
    for mount in args.mount or split_words(value(config, "CODEMATE_MOUNTS")):
        mount_args.extend(["-v", mount])
    return mount_args


def validate_docker_params(params: Sequence[str]) -> None:
    """Reject Docker flags that take a value but were given none.

    ``--network`` needs a value, so a trailing ``--network`` would fail inside
    Docker with a confusing message. ``--docker-param --network host`` is also
    wrong, because the CLI consumes ``--network`` as the option value and
    treats ``host`` as an extra argument; pass the pair as one quoted string
    (``--docker-param "--network host"``) or use ``--network host``.
    """
    for index, param in enumerate(params):
        if param in DOCKER_VALUE_FLAGS and index == len(params) - 1:
            raise SystemExit(
                f"Docker parameter {param} needs a value. Use --network <mode> "
                f'or --docker-param "{param} <value>".'
            )


def strip_network_params(params: Sequence[str]) -> List[str]:
    """Drop ``--network <mode>`` / ``--network=<mode>`` pairs from a parameter list."""
    kept: List[str] = []
    skip_next = False
    for param in params:
        if skip_next:
            skip_next = False
            continue
        if param == "--network":
            skip_next = True
            continue
        if param.startswith("--network="):
            continue
        kept.append(param)
    return kept


def resolved_docker_params(config: Mapping[str, ResolvedValue], args: SimpleNamespace) -> List[str]:
    if args.docker_param:
        params: List[str] = []
        for param in args.docker_param:
            params.extend(split_words(param))
    else:
        params = split_words(value(config, "CODEMATE_DOCKER_PARAMS"))

    # An explicit --network wins over one embedded in the Docker parameters.
    network = getattr(args, "network", None)
    if network:
        params = strip_network_params(params)
        params.extend(["--network", network])

    validate_docker_params(params)
    return params


def network_args(docker_params: Sequence[str]) -> List[str]:
    """Share the host network on Linux so agents can reach local services."""
    has_network = "--network" in docker_params or any(param.startswith("--network=") for param in docker_params)
    if has_network or sys.platform == "darwin":
        return []
    return ["--network", "host"]


def pull_args(config: Mapping[str, ResolvedValue]) -> List[str]:
    return ["--pull", "missing"] if value(config, "CODEMATE_SKIP_PULL") else ["--pull", "always"]


def attach_if_running(container_name: str, args: SimpleNamespace) -> Optional[List[str]]:
    """Re-attach to a live session with the same container name.

    The agent runs directly on the container TTY (no tmux), so re-running
    codemate re-attaches to the live session instead of opening a shell.
    """
    if getattr(args, "dry_run", False):
        return None
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], text=True, stdout=subprocess.PIPE).stdout.splitlines()
    if container_name in names:
        return ["docker", "attach", container_name]
    return None


def docker_command(config: Mapping[str, ResolvedValue], args: SimpleNamespace, env_path: str) -> List[str]:
    repo = repo_name(value(config, "CODEMATE_GIT_REPO_URL"))
    identity = value(config, "CODEMATE_BRANCH_NAME") or (
        f"pr-{value(config, 'CODEMATE_PR_NUMBER')}" if value(config, "CODEMATE_PR_NUMBER") else "main"
    )
    # The agent is part of the container name so Claude and Codex sessions for
    # the same repository/branch can run side by side on one machine; without
    # it, the second `docker run` would find the first agent's container and
    # attach to the wrong runtime instead of starting its own.
    agent = value(config, "CODEMATE_AGENT")
    if args.shell:
        # Shell sessions get their own runtime name so they never collide with,
        # or get attached to by, an agent working the same repository/branch.
        agent = "shell"
    container_name = f"codemate-{sanitized(agent)}-{sanitized(repo)}-{sanitized(identity)}"

    # Re-running the same target re-attaches to the live session; shell
    # sessions have their own name (set above), so they never attach to an
    # agent working the same repository/branch.
    attach = attach_if_running(container_name, args)
    if attach:
        return attach

    docker_params = resolved_docker_params(config, args)
    codemate_dir = codemate_home()
    volume_args = ["-v", f"{codemate_dir}:/home/agent/.codemate"]
    volume_args.extend(home_entry_volume_args(codemate_dir))
    if Path("skills").is_dir():
        volume_args.extend(["-v", f"{Path.cwd() / 'skills'}:/home/agent/.claude/skills"])
    volume_args.extend(custom_mount_args(config, args))

    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        *pull_args(config),
        *network_args(docker_params),
        *docker_params,
        "-it",
        *volume_args,
        "--env",
        f"TZ={value(config, 'TZ')}",
        "--env-file",
        env_path,
        "-w",
        f"/home/agent/{repo}",
        value(config, "CODEMATE_IMAGE"),
    ]
    if args.shell:
        # setup.sh ends with `exec "$@"`, so a trailing command replaces the
        # image's default CMD (run.sh) and drops into zsh once the setup
        # scripts finished.
        command.append("zsh")
    return command


def pure_image(config: Mapping[str, ResolvedValue]) -> str:
    """Image used by --pure mode.

    ``--image`` and ``--build`` win; ``CODEMATE_IMAGE`` coming from .env or the
    ambient environment is ignored so an existing standard-image setting does
    not silently leak into pure mode. The pure default follows
    ``CODEMATE_IMAGE_REGISTRY`` like the standard default does.
    """
    resolved = config.get("CODEMATE_IMAGE")
    if resolved is not None and resolved.source in {"cli", "build"}:
        return resolved.value
    return default_image(value(config, "CODEMATE_IMAGE_REGISTRY"), DEFAULT_PURE_IMAGE_REPOSITORY)


def pure_docker_command(config: Mapping[str, ResolvedValue], args: SimpleNamespace, env_path: str) -> List[str]:
    """Run the pure image with the local pure CodeMate home mounted.

    There is no repository clone, no agent launcher, and no GitHub setup: the
    container starts the image's default command (zsh) in the current working
    directory, which is mounted read-write into the container at the path it
    has relative to the host home. Only the entries of the pure home
    (~/.codemate-pure by default) are mounted into $HOME, so pure sessions keep
    their own credentials and config without sharing ~/.codemate or exposing
    the pure home itself.
    """
    workspace = container_workspace_path(Path.cwd())
    workspace_key = sanitized(workspace.removeprefix(f"{CONTAINER_HOME}/")) or "workspace"
    container_name = f"codemate-pure-{workspace_key}"
    attach = attach_if_running(container_name, args)
    if attach:
        return attach

    docker_params = resolved_docker_params(config, args)
    volume_args = home_entry_volume_args(ensure_pure_home())
    volume_args.extend(["-v", f"{Path.cwd()}:{workspace}"])
    volume_args.extend(custom_mount_args(config, args))

    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        *pull_args(config),
        *network_args(docker_params),
        *docker_params,
        "-it",
        *volume_args,
        "--env",
        f"TZ={value(config, 'TZ')}",
        "--env-file",
        env_path,
        "-w",
        workspace,
        value(config, "CODEMATE_IMAGE"),
    ]


def print_pure_launch_details(config: Mapping[str, ResolvedValue], args: SimpleNamespace) -> None:
    workspace = container_workspace_path(Path.cwd())
    mounts = args.mount or split_words(value(config, "CODEMATE_MOUNTS"))
    docker_params_text = inline_detail_list(resolved_docker_params(config, args))
    extra_env_keys = sorted(key for key, item in config.items() if item.field is None)
    extra_env_text = inline_detail_list(extra_env_keys)
    inline_width = max(len(line) for text in (docker_params_text, extra_env_text) for line in text.splitlines())

    table = Table(title="CodeMate Pure Launch Details", show_header=False, box=None, padding=(0, 1))
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value", min_width=inline_width)
    table.add_row("Mode", "pure (zsh, no repository setup)")
    table.add_row("Image", value(config, "CODEMATE_IMAGE"))
    table.add_row("Home dir", str(pure_home()))
    table.add_row("Workspace", f"{Path.cwd()} → {workspace}")
    if value(config, "CODEMATE_SKIP_PULL"):
        table.add_row("Image pull", "skipped")
    table.add_row("Timezone", value(config, "TZ"))
    table.add_row("Custom mounts", detail_list(mounts))
    table.add_row("Docker params", docker_params_text)
    table.add_row("Extra envs", extra_env_text)
    console.print(table, crop=False)


def print_config(config: Mapping[str, ResolvedValue]) -> None:
    for key in sorted(config):
        item = config[key]
        print(f"{key}={redact(key, item)} ({item.source})")


def run_codemate(args: SimpleNamespace) -> None:
    cwd = Path.cwd()
    if args.setup:
        create_setup_files(cwd)
        return
    if args.update:
        print("Installed with uv tool. Update with: uv tool upgrade codemate-cli")
        return
    if args.shell and args.query:
        raise SystemExit("--shell opens an interactive zsh session; remove --query.")
    pure = is_pure(args)
    if pure and args.shell:
        raise SystemExit("--pure already opens an interactive zsh session; remove --shell.")
    if pure and args.query:
        raise SystemExit("--pure opens an interactive zsh session; remove --query.")

    if not pure:
        # Pure mode needs no CodeMate configuration: it mounts ~/.codemate when
        # it exists and works with an empty directory otherwise.
        ensure_global_config()
    config = resolve_config(args, cwd)
    chat_defaults(config)

    if args.build:
        dockerfile = args.dockerfile or (DEFAULT_PURE_DOCKERFILE if pure else DEFAULT_DOCKERFILE)
        tag = args.tag or (DEFAULT_PURE_TAG if pure else DEFAULT_TAG)
        build_image(dockerfile, tag)
        config["CODEMATE_IMAGE"] = ResolvedValue(tag, "build", FIELD_BY_NAME["CODEMATE_IMAGE"])

    if pure:
        config["CODEMATE_IMAGE"] = ResolvedValue(pure_image(config), "pure", FIELD_BY_NAME["CODEMATE_IMAGE"])
    elif not args.config:
        validate_config(config)

    if not pure:
        issue_defaults(config)

    if args.config:
        print_config(config)
        return

    if not args.dry_run:
        check_prerequisites(config, pure=pure)
    env_file = write_env_file(config)
    env_file.close()
    try:
        if pure:
            cmd = pure_docker_command(config, args, env_file.name)
            print_pure_launch_details(config, args)
        else:
            cmd = docker_command(config, args, env_file.name)
            print_launch_details(config, args)
        if args.dry_run:
            print(" ".join(shlex.quote(part) for part in cmd).replace(env_file.name, "<generated-env-file>"))
            return
        run_checked(cmd)
    finally:
        try:
            Path(env_file.name).unlink()
        except FileNotFoundError:
            pass


@app.callback(invoke_without_command=True)
def cli(
    setup: bool = typer.Option(False, "--setup", help="Create configuration files."),
    update: bool = typer.Option(False, "--update", help="Show update instructions."),
    branch: Optional[str] = typer.Option(None, "--branch", help="Branch name to work on."),
    pr: Optional[str] = typer.Option(None, "--pr", help="Existing PR number to work on."),
    pr_title: Optional[str] = typer.Option(None, "--pr-title", help="PR title."),
    issue: Optional[str] = typer.Option(None, "--issue", help="GitHub issue number to work on."),
    query: Optional[str] = typer.Option(None, "--query", help="Initial query to send to the selected agent."),
    agent: Optional[Agent] = typer.Option(None, "--agent", help="Runtime agent."),
    co_author_by: Optional[str] = typer.Option(None, "--co-author-by", help="Commit co-author, e.g. 'Name <email@example.com>'."),
    no_pr: bool = typer.Option(False, "--no-pr", help="Skip PR creation and branch push."),
    chat: bool = typer.Option(False, "--chat", help="Run in chat mode: skip PR creation and CodeMate system prompt injection."),
    shell: bool = typer.Option(False, "--shell", help="Open an interactive zsh shell in the container instead of launching the agent."),
    pure: bool = typer.Option(
        False,
        "--pure",
        help=(
            "Run the pure image: mount the local CodeMate home and the current directory, "
            "then start an interactive zsh session with no repository or GitHub setup."
        ),
    ),
    docker_param: List[str] = typer.Option([], "--docker-param", help="Extra Docker run parameter."),
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help=(
            "Docker network mode, e.g. host, bridge, or none. "
            "Default: host on Linux, the Docker default on macOS."
        ),
    ),
    repo: Optional[str] = typer.Option(None, "--repo", help="Git repository URL."),
    upstream: Optional[str] = typer.Option(None, "--upstream", help="Upstream repository URL."),
    mount: List[str] = typer.Option([], "--mount", help="Custom volume mount."),
    image: Optional[str] = typer.Option(None, "--image", help=f"Docker image to use. Default: {DEFAULT_IMAGE}"),
    image_registry: Optional[str] = typer.Option(
        None,
        "--image-registry",
        help=f"Registry serving the default images. Default: {DEFAULT_IMAGE_REGISTRY}",
    ),
    skip_pull: bool = typer.Option(
        False,
        "--skip-pull",
        help="Skip pulling the image at startup; pull only if it is missing locally.",
    ),
    tz: Optional[str] = typer.Option(None, "--tz", help="Container timezone. Default: UTC"),
    build_image_flag: bool = typer.Option(False, "--build", help="Build Docker image from local Dockerfile."),
    dockerfile: Optional[str] = typer.Option(
        None,
        "-f",
        "--dockerfile",
        help=f"Path to Dockerfile. Default: {DEFAULT_DOCKERFILE} ({DEFAULT_PURE_DOCKERFILE} with --pure).",
    ),
    tag: Optional[str] = typer.Option(
        None,
        "--tag",
        help=f"Image tag for local build. Default: {DEFAULT_TAG} ({DEFAULT_PURE_TAG} with --pure).",
    ),
    env_values: List[str] = typer.Option([], "--env", help="Extra container env KEY=VALUE."),
    env_files: List[str] = typer.Option([], "--env-file", help="Additional env file to merge."),
    show_config: bool = typer.Option(False, "--config", help="Print resolved config with sources."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print Docker command without running it."),
) -> None:
    args = SimpleNamespace(
        setup=setup,
        update=update,
        branch=branch,
        pr=pr,
        pr_title=pr_title,
        issue=issue,
        query=query,
        agent=agent.value if agent else None,
        co_author_by=co_author_by,
        no_pr=no_pr,
        chat=chat,
        shell=shell,
        pure=pure,
        docker_param=docker_param,
        network=network,
        repo=repo,
        upstream=upstream,
        mount=mount,
        image=image,
        image_registry=image_registry,
        skip_pull=skip_pull,
        tz=tz,
        build=build_image_flag,
        dockerfile=dockerfile,
        tag=tag,
        env=env_values,
        env_file=env_files,
        config=show_config,
        dry_run=dry_run,
    )
    try:
        run_codemate(args)
    except subprocess.CalledProcessError as exc:
        typer.secho(
            f"Error: Command failed with exit code {exc.returncode}: {' '.join(exc.cmd)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(exc.returncode) from exc
    except SystemExit as exc:
        if isinstance(exc.code, str):
            typer.secho(f"Error: {exc.code}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
        if exc.code:
            raise typer.Exit(int(exc.code)) from exc


if __name__ == "__main__":
    app()
