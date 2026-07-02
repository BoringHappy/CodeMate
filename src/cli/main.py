from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import typer
from rich.console import Console
from rich.table import Table


DEFAULT_IMAGE = "ghcr.io/boringhappy/codemate:latest"
DEFAULT_MARKETPLACES = "BoringHappy/CodeMate"
DEFAULT_PLUGINS = "git@codemate,pr@codemate,dev@codemate,issue@codemate,workspace@codemate"

BLUE = "\033[0;36m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
NC = "\033[0m"

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


def print_info(message: str) -> None:
    print(f"{BLUE}i{NC} {message}")


def print_success(message: str) -> None:
    print(f"{GREEN}+{NC} {message}")


def print_warning(message: str) -> None:
    print(f"{YELLOW}!{NC} {message}")


def print_error(message: str) -> None:
    print(f"{RED}x{NC} {message}", file=sys.stderr)


def run_capture(args: Sequence[str]) -> str:
    try:
        return subprocess.run(args, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.strip()
    except FileNotFoundError:
        return ""


def run_checked(args: Sequence[str]) -> None:
    subprocess.run(args, check=True)


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
    Field("CODEMATE_AGENT", "agent", default="claude"),
    Field("CODEMATE_GITHUB_TOKEN", derived=gh_token, secret=True),
    Field("CODEMATE_GIT_USER_NAME", derived=git_user_name),
    Field("CODEMATE_GIT_USER_EMAIL", derived=git_user_email),
    Field("CODEMATE_CO_AUTHOR_BY", "co_author_by"),
    Field("CODEMATE_ALLOW_COUNTRY"),
    Field("CODEMATE_ALLOW_IP"),
    Field("CODEMATE_DOCKER_PARAMS", "docker_params", docker_export=False),
    Field("CODEMATE_MOUNTS", "mounts", docker_export=False),
    Field("TZ", "tz", default="UTC", docker_export=False),
    Field("CODEMATE_DEFAULT_MARKETPLACES", default=DEFAULT_MARKETPLACES, allow_empty_override=True),
    Field("CODEMATE_DEFAULT_PLUGINS", default=DEFAULT_PLUGINS, allow_empty_override=True),
    Field("CODEMATE_CUSTOM_MARKETPLACES"),
    Field("CODEMATE_CUSTOM_PLUGINS"),
    Field("CODEMATE_SOFT_LINKS"),
    Field("CODEMATE_REPO_DIR"),
    Field("CODEMATE_AGENT_SESSION"),
    Field("CODEMATE_PR_MONITOR_STATE_FILE"),
    Field("SLACK_WEBHOOK", secret=True),
    Field("LARK_WEBHOOK", secret=True),
    Field("ANTHROPIC_AUTH_TOKEN", secret=True),
    Field("ANTHROPIC_BASE_URL"),
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

    if not value(config, "CODEMATE_ALLOW_COUNTRY") and not value(config, "CODEMATE_ALLOW_IP"):
        raise SystemExit(
            "Neither CODEMATE_ALLOW_COUNTRY nor CODEMATE_ALLOW_IP is set. "
            "Set at least one allowlist in .env, environment, or CLI-provided env."
        )

    if not value(config, "CODEMATE_GITHUB_TOKEN"):
        raise SystemExit("CODEMATE_GITHUB_TOKEN is missing and gh auth token did not return a token.")

    if not value(config, "CODEMATE_GIT_USER_NAME") or not value(config, "CODEMATE_GIT_USER_EMAIL"):
        raise SystemExit("Git user name or email is missing. Set it or configure git user.name/user.email.")

    if not value(config, "CODEMATE_GIT_REPO_URL"):
        raise SystemExit("CODEMATE_GIT_REPO_URL is missing. Use --repo, .env, environment, or git remote origin.")


def redact(resolved: ResolvedValue) -> str:
    if resolved.field and resolved.field.secret and resolved.value:
        return "***"
    if resolved.field is None and any(token in resolved.value.lower() for token in ("token", "secret", "password")):
        return "***"
    return resolved.value


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
    env_file.write("TMPDIR=/home/agent/.claude/tmp\n")
    env_file.flush()
    return env_file


def repo_name(repo_url: str) -> str:
    trimmed = repo_url.removesuffix(".git")
    return trimmed.rstrip("/").split("/")[-1].split(":")[-1]


def sanitized(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in text)


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


def detail_list(items: Sequence[str], empty: str = "none") -> str:
    return "\n".join(items) if items else empty


def print_launch_details(config: Mapping[str, ResolvedValue], args: SimpleNamespace) -> None:
    default_marketplaces = split_csv(value(config, "CODEMATE_DEFAULT_MARKETPLACES"))
    custom_marketplaces = split_csv(value(config, "CODEMATE_CUSTOM_MARKETPLACES"))
    default_plugins = split_csv(value(config, "CODEMATE_DEFAULT_PLUGINS"))
    custom_plugins = split_csv(value(config, "CODEMATE_CUSTOM_PLUGINS"))
    mounts = args.mount or split_words(value(config, "CODEMATE_MOUNTS"))
    docker_params = args.docker_param or split_words(value(config, "CODEMATE_DOCKER_PARAMS"))
    allow_sources = [
        label
        for label, key in (("country", "CODEMATE_ALLOW_COUNTRY"), ("IP", "CODEMATE_ALLOW_IP"))
        if value(config, key)
    ]
    extra_env_keys = sorted(key for key, item in config.items() if item.field is None)

    table = Table(title="CodeMate Launch Details", show_header=False, box=None, padding=(0, 1))
    table.add_column("Setting", style="cyan", no_wrap=True)
    table.add_column("Value")
    table.add_row("Target", target_label(config))
    table.add_row("Agent", value(config, "CODEMATE_AGENT"))
    table.add_row("Repository", repo_name(value(config, "CODEMATE_GIT_REPO_URL")))
    table.add_row("Image", value(config, "CODEMATE_IMAGE"))
    table.add_row("Timezone", value(config, "TZ"))
    table.add_row("Default marketplaces", detail_list(default_marketplaces))
    table.add_row("Custom marketplaces", detail_list(custom_marketplaces))
    table.add_row("Default plugins", detail_list(default_plugins))
    table.add_row("Custom plugins", detail_list(custom_plugins))
    table.add_row("Custom mounts", detail_list(mounts))
    table.add_row("Docker params", detail_list(docker_params))
    table.add_row("Extra env", detail_list(extra_env_keys))
    table.add_row("Allowlist", detail_list(allow_sources))
    console.print(table)


def check_prerequisites(config: Mapping[str, ResolvedValue]) -> None:
    missing = [name for name in ("docker", "git", "gh") if shutil.which(name) is None]
    if missing:
        raise SystemExit("Missing required dependencies: " + ", ".join(missing))

    if not value(config, "CODEMATE_GITHUB_TOKEN"):
        result = subprocess.run(["gh", "auth", "status"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            raise SystemExit("GitHub CLI is not authenticated. Run gh auth login or set CODEMATE_GITHUB_TOKEN.")

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
    config_dir = Path.home() / ".codemate"
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
            "# Container timezone (defaults to UTC)\n"
            "# TZ=UTC\n\n"
            "# Access control. Set at least one allowlist.\n"
            "CODEMATE_ALLOW_COUNTRY=\n"
            "CODEMATE_ALLOW_IP=\n"
        )
    print_success("Setup complete")


def ensure_global_config() -> None:
    config_dir = Path.home() / ".codemate"
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


def docker_command(config: Mapping[str, ResolvedValue], args: SimpleNamespace, env_path: str) -> List[str]:
    repo = repo_name(value(config, "CODEMATE_GIT_REPO_URL"))
    identity = value(config, "CODEMATE_BRANCH_NAME") or (
        f"pr-{value(config, 'CODEMATE_PR_NUMBER')}" if value(config, "CODEMATE_PR_NUMBER") else "main"
    )
    container_name = f"codemate-{sanitized(repo)}-{sanitized(identity)}"

    if not args.dry_run and subprocess.run(["docker", "ps", "--format", "{{.Names}}"], text=True, stdout=subprocess.PIPE).stdout.splitlines().count(container_name):
        return ["docker", "exec", "-it", container_name, "zsh"]

    docker_params: List[str] = []
    if args.docker_param:
        for param in args.docker_param:
            docker_params.extend(split_words(param))
    else:
        docker_params = split_words(value(config, "CODEMATE_DOCKER_PARAMS"))
    mounts = args.mount or split_words(value(config, "CODEMATE_MOUNTS"))
    has_network = "--network" in docker_params or any(p.startswith("--network=") for p in docker_params)
    network_args = [] if has_network or sys.platform == "darwin" else ["--network", "host"]

    volume_args = ["-v", f"{Path.home() / '.codemate'}:/home/agent/.codemate"]
    for entry in sorted((Path.home() / ".codemate").iterdir()):
        volume_args.extend(["-v", f"{entry}:/home/agent/{entry.name}"])
    if Path("skills").is_dir():
        volume_args.extend(["-v", f"{Path.cwd() / 'skills'}:/home/agent/.claude/skills"])
    for mount in mounts:
        volume_args.extend(["-v", mount])

    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--pull",
        "always",
        *network_args,
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


def print_config(config: Mapping[str, ResolvedValue]) -> None:
    for key in sorted(config):
        item = config[key]
        print(f"{key}={redact(item)} ({item.source})")


def run_codemate(args: SimpleNamespace) -> None:
    cwd = Path.cwd()
    if args.setup:
        create_setup_files(cwd)
        return
    if args.update:
        print("Installed with uv tool. Update with: uv tool upgrade codemate-cli")
        return

    ensure_global_config()
    config = resolve_config(args, cwd)
    issue_defaults(config)

    if args.build:
        tag = args.tag or "codemate:local"
        build_image(args.dockerfile, tag)
        config["CODEMATE_IMAGE"] = ResolvedValue(tag, "build", FIELD_BY_NAME["CODEMATE_IMAGE"])

    if args.config:
        print_config(config)
        return

    validate_config(config)

    if not args.dry_run:
        check_prerequisites(config)
    env_file = write_env_file(config)
    env_file.close()
    try:
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
    docker_param: List[str] = typer.Option([], "--docker-param", help="Extra Docker run parameter."),
    repo: Optional[str] = typer.Option(None, "--repo", help="Git repository URL."),
    upstream: Optional[str] = typer.Option(None, "--upstream", help="Upstream repository URL."),
    mount: List[str] = typer.Option([], "--mount", help="Custom volume mount."),
    image: Optional[str] = typer.Option(None, "--image", help=f"Docker image to use. Default: {DEFAULT_IMAGE}"),
    tz: Optional[str] = typer.Option(None, "--tz", help="Container timezone. Default: UTC"),
    build_image_flag: bool = typer.Option(False, "--build", help="Build Docker image from local Dockerfile."),
    dockerfile: str = typer.Option("docker/Dockerfile", "-f", "--dockerfile", help="Path to Dockerfile."),
    tag: Optional[str] = typer.Option(None, "--tag", help="Image tag for local build."),
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
        docker_param=docker_param,
        repo=repo,
        upstream=upstream,
        mount=mount,
        image=image,
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
