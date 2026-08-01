from __future__ import annotations

import json
import hashlib
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "plugins" / "workspace" / "hooks"
PR_STATUS = ROOT / "plugins" / "pr" / "scripts" / "pr-status.sh"


def run_hook(script: str, hook_input: dict, *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(HOOKS / script)],
        cwd=cwd,
        env=env,
        input=json.dumps(hook_input),
        text=True,
        capture_output=True,
        check=True,
    )


def init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "config", "user.name", "CodeMate Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "tracked.txt").write_text("initial\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)
    subprocess.run(["git", "switch", "-qc", "feature/hooks"], cwd=path, check=True)


def hook_input(session_id: str, cwd: Path, event: str, **extra: object) -> dict:
    return {
        "session_id": session_id,
        "cwd": str(cwd),
        "hook_event_name": event,
        "stop_hook_active": False,
    } | extra


def gh_pr_list_json(number: int, branch: str = "feature/hooks") -> str:
    return json.dumps(
        [
            {
                "number": number,
                "url": f"https://github.com/example/repo/pull/{number}",
                "state": "OPEN",
                "headRefName": branch,
            }
        ]
    )


def gh_pr_view_json(number: int, *, draft: bool = False, labels: list[str] | None = None) -> str:
    return json.dumps(
        {
            "number": number,
            "state": "OPEN",
            "url": f"https://github.com/example/repo/pull/{number}",
            "isDraft": draft,
            "labels": labels or [],
            "statusCheckRollup": [],
            "headRefOid": "abc123",
        }
    )


def write_gh(
    fake_bin: Path,
    number: int,
    *,
    draft: bool = False,
    labels: list[str] | None = None,
    api_issues: str = "",
    api_pulls: str = "",
) -> None:
    """Writes a fake `gh` that answers the query-first `pr list` resolution,
    `pr view`, and (optionally) the issue/pull comment APIs."""
    lines = [
        "#!/usr/bin/env bash\n",
        "printf '%s\\n' \"$*\" >> \"$CODEMATE_TEST_GH_LOG\"\n",
        "if [ \"$1 $2\" = \"pr list\" ]; then\n",
        f"  printf '%s\\n' '{gh_pr_list_json(number)}'\n",
        "  exit 0\n",
        "fi\n",
        "if [ \"$1 $2\" = \"pr view\" ]; then\n",
        f"  printf '%s\\n' '{gh_pr_view_json(number, draft=draft, labels=labels)}'\n",
        "  exit 0\n",
        "fi\n",
    ]
    if api_issues:
        lines += [
            "if [ \"$1\" = \"api\" ] && [[ \"$*\" == *'/issues/'* ]]; then\n",
            f"{api_issues}",
            "  exit 0\n",
            "fi\n",
        ]
    else:
        lines += ["if [ \"$1\" = \"api\" ] && [[ \"$*\" == *'/issues/'* ]]; then exit 0; fi\n"]
    if api_pulls:
        lines += [
            "if [ \"$1\" = \"api\" ] && [[ \"$*\" == *'/pulls/'* ]]; then\n",
            f"{api_pulls}",
            "  exit 0\n",
            "fi\n",
        ]
    else:
        lines += ["if [ \"$1\" = \"api\" ] && [[ \"$*\" == *'/pulls/'* ]]; then exit 0; fi\n"]
    lines += ["exit 1\n"]
    (fake_bin / "gh").write_text("".join(lines))
    (fake_bin / "gh").chmod(0o755)


def monitor_state_path(runtime: Path, repo: Path, branch: str = "feature/hooks") -> Path:
    git_dir = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=repo, text=True, capture_output=True, check=True
    ).stdout.strip()
    key = hashlib.sha256(f"{git_dir}\n{branch}".encode()).hexdigest()
    return runtime / "monitor" / f"{key}.monitor-state.json"


def run_pr_status(*args: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PR_STATUS), *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_agent_hook_configs_use_supported_stop_delivery() -> None:
    codex_hooks = json.loads((HOOKS / "hooks.json").read_text())
    claude_hooks = json.loads((HOOKS / "claude-hooks.json").read_text())

    codex_stop = codex_hooks["hooks"]["Stop"][0]["hooks"]
    claude_stop = claude_hooks["hooks"]["Stop"][0]["hooks"]

    assert len(codex_stop) == 1
    assert codex_stop[0]["command"].endswith("/hooks/stop.sh")
    assert "asyncRewake" not in codex_stop[0]
    assert len(claude_stop) == 2
    assert claude_stop[1]["command"].endswith("/hooks/claude_stop.sh")
    assert claude_stop[1]["asyncRewake"] is True


def test_session_state_is_isolated_by_agent_and_session(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    repo.mkdir()
    init_repo(repo)

    base_env = os.environ.copy()
    base_env.pop("CODEMATE_INSTANCE_ID", None)
    base_env["CODEMATE_RUNTIME_DIR"] = str(runtime)

    claude_env = base_env | {"CODEMATE_AGENT": "claude"}
    codex_env = base_env | {"CODEMATE_AGENT": "codex"}
    other_instance_env = codex_env | {"CODEMATE_INSTANCE_ID": "second-runtime"}
    run_hook("record_session_status.sh", hook_input("same-id", repo, "SessionStart"), cwd=repo, env=claude_env)
    run_hook("record_session_status.sh", hook_input("same-id", repo, "SessionStart"), cwd=repo, env=codex_env)
    run_hook("record_session_status.sh", hook_input("same-id", repo, "SessionStart"), cwd=repo, env=other_instance_env)
    run_hook("record_session_status.sh", hook_input("same-id", repo, "UserPromptSubmit"), cwd=repo, env=claude_env)

    statuses = sorted((runtime / "sessions").glob("*/status.json"))
    assert len(statuses) == 3
    events = {
        (payload["instance_id"], payload["agent"]): payload["event"]
        for path in statuses
        if (payload := json.loads(path.read_text()))
    }
    assert events == {
        ("", "claude"): "UserPromptSubmit",
        ("", "codex"): "SessionStart",
        ("second-runtime", "codex"): "SessionStart",
    }


def test_session_state_uses_codemate_tmpdir_when_runtime_dir_unset(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    codemate_tmp = tmp_path / "codemate-tmp"
    repo.mkdir()
    init_repo(repo)

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "claude",
        "CODEMATE_TMPDIR": str(codemate_tmp),
        "CODEMATE_NO_PR": "true",
    }
    env.pop("CODEMATE_RUNTIME_DIR", None)
    env.pop("XDG_RUNTIME_DIR", None)
    env.pop("TMPDIR", None)
    run_hook("record_session_status.sh", hook_input("tmpdir-session", repo, "SessionStart"), cwd=repo, env=env)

    statuses = list((codemate_tmp / "codemate" / "sessions").glob("*/status.json"))
    assert len(statuses) == 1
    payload = json.loads(statuses[0].read_text())
    assert payload["session_id"] == "tmpdir-session"


def test_stop_returns_native_continuation_and_scopes_retry_counter(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    repo.mkdir()
    init_repo(repo)
    (repo / "tracked.txt").write_text("dirty\n")

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_NO_PR": "true",
    }
    result = run_hook("stop.sh", hook_input("codex-session", repo, "Stop"), cwd=repo, env=env)

    output = json.loads(result.stdout)
    assert output["decision"] == "block"
    assert "git:commit" in output["reason"]
    counters = list((runtime / "sessions").glob("*/workspaces/*/git-changes-block-count"))
    assert len(counters) == 1
    assert counters[0].read_text().strip() == "1"


def test_one_session_partitions_hook_state_by_repository_and_branch(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    init_repo(repo_a)
    init_repo(repo_b)
    subprocess.run(["git", "branch", "-m", "feature/other"], cwd=repo_b, check=True)
    (repo_a / "tracked.txt").write_text("dirty a\n")
    (repo_b / "tracked.txt").write_text("dirty b\n")

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_INSTANCE_ID": "shared-runtime",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_NO_PR": "true",
    }
    run_hook("stop.sh", hook_input("shared-session", repo_a, "Stop"), cwd=repo_a, env=env)
    run_hook("stop.sh", hook_input("shared-session", repo_b, "Stop"), cwd=repo_b, env=env)

    session_dirs = list((runtime / "sessions").iterdir())
    counters = list((runtime / "sessions").glob("*/workspaces/*/git-changes-block-count"))
    metadata = [json.loads(path.read_text()) for path in (runtime / "sessions").glob("*/workspaces/*/workspace.json")]
    assert len(session_dirs) == 1
    assert len(counters) == 2
    assert {item["branch"] for item in metadata} == {"feature/hooks", "feature/other"}


def test_claude_async_stop_uses_rewake_exit_code(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    repo.mkdir()
    init_repo(repo)
    (repo / "tracked.txt").write_text("dirty\n")

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "claude",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_NO_PR": "true",
    }
    stop = hook_input("claude-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)
    result = subprocess.run(
        [str(HOOKS / "claude_stop.sh")],
        cwd=repo,
        env=env,
        input=json.dumps(stop),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "/git:commit" in result.stderr
    assert result.stdout == ""


def test_monitor_checks_immediately_and_uses_stop_continuation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    write_gh(fake_bin, 41)

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_TEST_GH_LOG": str(call_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_NO_PR", None)
    stop = hook_input("monitor-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)

    result = run_hook("monitor_pr.sh", stop, cwd=repo, env=env)
    output = json.loads(result.stdout)

    assert output["decision"] == "block"
    assert "pr:update" in output["reason"]
    assert call_log.read_text().splitlines() == [
        "pr list --head feature/hooks --state open --json number,url,state,headRefName",
        "pr view 41 --json number,state,url,isDraft,labels,statusCheckRollup,headRefOid"
    ]

    active = hook_input("monitor-session", repo, "UserPromptSubmit")
    run_hook("record_session_status.sh", active, cwd=repo, env=env)
    result = run_hook("monitor_pr.sh", stop, cwd=repo, env=env)
    assert result.stdout == ""
    assert len(call_log.read_text().splitlines()) == 2


def test_monitor_requires_pr_before_gh(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CODEMATE_TEST_GH_LOG\"\n"
        "exit 1\n"
    )
    fake_gh.chmod(0o755)

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_PR_NUMBER": "99",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_TEST_GH_LOG": str(call_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_NO_PR", None)
    stop = hook_input("no-local-pr-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)

    result = run_hook("monitor_pr.sh", stop, cwd=repo, env=env)
    assert result.stdout == ""
    # Query-first resolution makes exactly one `gh pr list` call; with no PR it
    # stops before any polling calls.
    assert call_log.read_text().splitlines() == [
        "pr list --head feature/hooks --state open --json number,url,state,headRefName"
    ]


def test_old_monitor_exits_when_a_newer_stop_generation_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CODEMATE_TEST_GH_LOG\"\n"
        "exit 1\n"
    )
    fake_gh.chmod(0o755)

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "claude",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_TEST_GH_LOG": str(call_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_NO_PR", None)
    old_stop = hook_input("generation-session", repo, "Stop", last_assistant_message="old turn")
    new_stop = hook_input("generation-session", repo, "Stop", last_assistant_message="new turn")
    run_hook("record_session_status.sh", old_stop, cwd=repo, env=env)
    run_hook("record_session_status.sh", new_stop, cwd=repo, env=env)

    result = run_hook("monitor_pr.sh", old_stop, cwd=repo, env=env)
    assert result.stdout == ""
    assert not call_log.exists()


def test_monitor_interrupts_backoff_when_its_session_resumes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    write_gh(fake_bin, 42, draft=True)

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_TEST_GH_LOG": str(call_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_NO_PR", None)
    stop = hook_input("interrupt-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)

    process = subprocess.Popen(
        [str(HOOKS / "monitor_pr.sh")],
        cwd=repo,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(stop))
    process.stdin.close()
    process.stdin = None

    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if call_log.exists() and len(call_log.read_text().splitlines()) == 4:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("monitor did not complete its immediate poll")

        active = hook_input("interrupt-session", repo, "UserPromptSubmit")
        run_hook("record_session_status.sh", active, cwd=repo, env=env)
        stdout, stderr = process.communicate(timeout=3)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)

    assert process.returncode == 0
    assert stdout == ""
    assert stderr == ""
    assert len(call_log.read_text().splitlines()) == 4


def test_monitor_exits_when_codex_user_prompt_is_pending(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    codex_home = tmp_path / "codex-home"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    codex_home.mkdir()
    init_repo(repo)

    write_gh(fake_bin, 47, draft=True)

    history = codex_home / "history.jsonl"
    history.write_text(json.dumps({"session_id": "pending-prompt-session", "ts": 100}) + "\n")

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEX_HOME": str(codex_home),
        "CODEMATE_TEST_GH_LOG": str(call_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_NO_PR", None)
    stop = hook_input("pending-prompt-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)

    process = subprocess.Popen(
        [str(HOOKS / "monitor_pr.sh")],
        cwd=repo,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(stop))
    process.stdin.close()
    process.stdin = None

    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if call_log.exists() and len(call_log.read_text().splitlines()) == 4:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("monitor did not complete its immediate poll")

        # The user submits a new prompt: history.jsonl is appended while the
        # Stop hook is still running and the session status has NOT changed.
        history.write_text(history.read_text() + json.dumps({"session_id": "pending-prompt-session", "ts": 200}) + "\n")
        stdout, stderr = process.communicate(timeout=3)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)

    assert process.returncode == 0
    assert stdout == ""
    assert stderr == ""
    assert len(call_log.read_text().splitlines()) == 4


def test_monitor_exits_when_claude_user_prompt_is_pending(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    claude_home = tmp_path / "claude-home"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    claude_home.mkdir()
    init_repo(repo)

    write_gh(fake_bin, 48, draft=True)

    history = claude_home / "history.jsonl"
    history.write_text(json.dumps({"sessionId": "pending-claude-session", "timestamp": 100000, "display": "first"}) + "\n")

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "claude",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CLAUDE_CONFIG_DIR": str(claude_home),
        "CODEMATE_TEST_GH_LOG": str(call_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_NO_PR", None)
    stop = hook_input("pending-claude-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)

    process = subprocess.Popen(
        [str(HOOKS / "monitor_pr.sh")],
        cwd=repo,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(stop))
    process.stdin.close()
    process.stdin = None

    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if call_log.exists() and len(call_log.read_text().splitlines()) == 4:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("monitor did not complete its immediate poll")

        history.write_text(history.read_text() + json.dumps({"sessionId": "pending-claude-session", "timestamp": 200000, "display": "second"}) + "\n")
        stdout, stderr = process.communicate(timeout=3)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)

    assert process.returncode == 0
    assert stdout == ""
    assert stderr == ""
    assert len(call_log.read_text().splitlines()) == 4


def test_monitor_checks_both_histories_when_runtime_is_unidentified(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    codex_home.mkdir()
    claude_home.mkdir()
    init_repo(repo)

    write_gh(fake_bin, 49, draft=True)

    (codex_home / "history.jsonl").write_text(
        json.dumps({"session_id": "dual-history-session", "ts": 100}) + "\n"
    )
    claude_history = claude_home / "history.jsonl"
    claude_history.write_text(
        json.dumps({"sessionId": "dual-history-session", "timestamp": 100000}) + "\n"
    )

    env = os.environ.copy() | {
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEX_HOME": str(codex_home),
        "CLAUDE_CONFIG_DIR": str(claude_home),
        "CODEMATE_TEST_GH_LOG": str(call_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_AGENT", None)
    env.pop("CODEMATE_NO_PR", None)
    env.pop("PLUGIN_ROOT", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)

    stop = hook_input("dual-history-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)

    process = subprocess.Popen(
        [str(HOOKS / "monitor_pr.sh")],
        cwd=repo,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(stop))
    process.stdin.close()
    process.stdin = None

    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if call_log.exists() and len(call_log.read_text().splitlines()) == 4:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("monitor did not complete its immediate poll")

        # Only Claude's history gains a newer entry; the unidentified runtime
        # must consult both files rather than defaulting to Codex's.
        claude_history.write_text(
            claude_history.read_text()
            + json.dumps({"sessionId": "dual-history-session", "timestamp": 200000})
            + "\n"
        )
        stdout, stderr = process.communicate(timeout=3)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)

    assert process.returncode == 0
    assert stdout == ""
    assert stderr == ""
    assert len(call_log.read_text().splitlines()) == 4


def test_monitor_exits_after_max_polls(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    write_gh(fake_bin, 46, draft=True)

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_MONITOR_DELAYS": "0",
        "CODEMATE_TEST_GH_LOG": str(call_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_NO_PR", None)
    stop = hook_input("limit-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)

    result = run_hook("monitor_pr.sh", stop, cwd=repo, env=env)

    assert result.stdout == ""
    # One query-first `pr list` resolution, then per poll:
    # pr view + issue comments + review comments.
    assert len(call_log.read_text().splitlines()) == 1 + 30 * 3


def test_review_comment_cursor_only_advances_past_delivered_batch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    comments = [
        {
            "id": comment_id,
            "in_reply_to_id": None,
            "path": "tracked.txt",
            "line": comment_id,
            "body": f"review comment {comment_id}",
            "user": {"login": f"reviewer-{comment_id}"},
        }
        for comment_id in range(1, 6)
    ]
    comments.append(
        {
            "id": 100,
            "in_reply_to_id": 1,
            "path": "tracked.txt",
            "line": 1,
            "body": "late reply to review comment 1",
            "user": {"login": "reviewer-100"},
        }
    )
    review_output = "".join(f"printf '%s\\n' '{json.dumps(comment)}'\n" for comment in comments)
    write_gh(fake_bin, 44, draft=True, api_pulls=review_output)

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_TEST_GH_LOG": str(tmp_path / "gh-calls.log"),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_NO_PR", None)
    stop = hook_input("review-batch-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)

    first = run_hook("monitor_pr.sh", stop, cwd=repo, env=env)
    first_reason = json.loads(first.stdout)["reason"]
    monitor_state = monitor_state_path(runtime, repo)
    assert "comment_id: 2" in first_reason
    assert "comment_id: 3" in first_reason
    assert "comment_id: 4" in first_reason
    assert "comment_id: 5" not in first_reason
    assert "comment_id: 100" not in first_reason
    assert json.loads(monitor_state.read_text())["last_review_comment_id"] == 4

    second = run_hook("monitor_pr.sh", stop, cwd=repo, env=env)
    second_reason = json.loads(second.stdout)["reason"]
    assert "comment_id: 5" in second_reason
    assert "comment_id: 100" in second_reason
    assert json.loads(monitor_state.read_text())["last_review_comment_id"] == 100


def test_branch_lease_hands_polling_to_another_stopped_session(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s:%s\\n' \"$CODEMATE_TEST_CALLER\" \"$*\" >> \"$CODEMATE_TEST_GH_LOG\"\n"
        "if [ \"$1 $2\" = \"pr list\" ]; then\n"
        f"  printf '%s\\n' '{gh_pr_list_json(45)}'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1 $2\" = \"pr view\" ]; then\n"
        "  printf '%s\\n' '{\"number\":45,\"state\":\"OPEN\",\"url\":\"https://github.com/example/repo/pull/45\",\"isDraft\":true,\"labels\":[],\"statusCheckRollup\":[],\"headRefOid\":\"abc123\"}'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"api\" ]; then exit 0; fi\n"
        "exit 1\n"
    )
    fake_gh.chmod(0o755)

    base_env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_TEST_GH_LOG": str(call_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    base_env.pop("CODEMATE_NO_PR", None)
    stop_a = hook_input("lease-session-a", repo, "Stop")
    stop_b = hook_input("lease-session-b", repo, "Stop")
    env_a = base_env | {"CODEMATE_TEST_CALLER": "a"}
    env_b = base_env | {"CODEMATE_TEST_CALLER": "b"}
    run_hook("record_session_status.sh", stop_a, cwd=repo, env=env_a)
    run_hook("record_session_status.sh", stop_b, cwd=repo, env=env_b)

    def start_monitor(payload: dict, env: dict[str, str]) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            [str(HOOKS / "monitor_pr.sh")],
            cwd=repo,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps(payload))
        process.stdin.close()
        process.stdin = None
        return process

    processes: list[subprocess.Popen[str]] = []
    try:
        process_a = start_monitor(stop_a, env_a)
        processes.append(process_a)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if call_log.exists() and len(call_log.read_text().splitlines()) == 4:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("first lease owner did not poll immediately")

        process_b = start_monitor(stop_b, env_b)
        processes.append(process_b)
        time.sleep(0.3)
        assert all(line.startswith("a:") for line in call_log.read_text().splitlines())

        run_hook("record_session_status.sh", hook_input("lease-session-a", repo, "UserPromptSubmit"), cwd=repo, env=env_a)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if len(call_log.read_text().splitlines()) == 8:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("branch lease was not handed to the stopped session")

        run_hook("record_session_status.sh", hook_input("lease-session-b", repo, "UserPromptSubmit"), cwd=repo, env=env_b)
        for process in processes:
            stdout, stderr = process.communicate(timeout=3)
            assert process.returncode == 0
            assert stdout == ""
            assert stderr == ""
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)

    callers = [line.split(":", 1)[0] for line in call_log.read_text().splitlines()]
    assert callers == ["a", "a", "a", "a", "b", "b", "b", "b"]


def _pr_env(fake_bin: Path, runtime: Path, call_log: Path | None = None) -> dict[str, str]:
    env = os.environ.copy() | {
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    if call_log is not None:
        env["CODEMATE_TEST_GH_LOG"] = str(call_log)
    env.pop("CODEMATE_AGENT", None)
    env.pop("CODEMATE_NO_PR", None)
    env.pop("PLUGIN_ROOT", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    return env


def test_pr_status_get_resolves_from_github_and_caches(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    runtime = tmp_path / "runtime"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)
    write_gh(fake_bin, 51)

    env = _pr_env(fake_bin, runtime, call_log)
    first = run_pr_status("get", cwd=repo, env=env)
    assert first.returncode == 0, first.stderr
    payload = json.loads(first.stdout)
    assert payload == {
        "number": 51,
        "url": "https://github.com/example/repo/pull/51",
        "state": "OPEN",
        "branch": "feature/hooks",
        "source": "github",
    }
    assert call_log.read_text().splitlines() == [
        "pr list --head feature/hooks --state open --json number,url,state,headRefName"
    ]
    caches = list((runtime / "pr-status").glob("*.json"))
    assert len(caches) == 1
    assert json.loads(caches[0].read_text())["number"] == 51

    # GitHub becomes unavailable: the plugin-owned cache is the fallback.
    (fake_bin / "gh").write_text("#!/usr/bin/env bash\nexit 1\n")
    (fake_bin / "gh").chmod(0o755)
    second = run_pr_status("get", cwd=repo, env=env)
    assert second.returncode == 0, second.stderr
    payload = json.loads(second.stdout)
    assert payload["number"] == 51
    assert payload["source"] == "cache"


def test_pr_status_get_migrates_legacy_status_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    runtime = tmp_path / "runtime"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    legacy = repo / ".git" / "codemate" / "pr-status" / "feature" / "hooks.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(
            {
                "state": "open",
                "branch": "feature/hooks",
                "number": 52,
                "url": "https://github.com/example/repo/pull/52",
            }
        )
    )
    (fake_bin / "gh").write_text("#!/usr/bin/env bash\nexit 1\n")
    (fake_bin / "gh").chmod(0o755)

    env = _pr_env(fake_bin, runtime)
    result = run_pr_status("get", cwd=repo, env=env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["number"] == 52
    assert payload["source"] == "cache"
    caches = list((runtime / "pr-status").glob("*.json"))
    assert len(caches) == 1
    assert json.loads(caches[0].read_text())["number"] == 52


def test_pr_status_get_uses_fork_workflow_query(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    runtime = tmp_path / "runtime"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/forkowner/repo.git"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "remote", "add", "upstream", "https://github.com/upstream/repo.git"],
        cwd=repo,
        check=True,
    )
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CODEMATE_TEST_GH_LOG\"\n"
        "if [ \"$1 $2\" = \"pr list\" ]; then\n"
        f"  printf '%s\\n' '{gh_pr_list_json(53)}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    fake_gh.chmod(0o755)

    env = _pr_env(fake_bin, runtime, call_log)
    result = run_pr_status("get", cwd=repo, env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["number"] == 53
    query = call_log.read_text().splitlines()[0]
    assert "--repo upstream/repo" in query
    assert "--head forkowner:feature/hooks" in query


def test_pr_status_set_and_clear(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    runtime = tmp_path / "runtime"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)
    (fake_bin / "gh").write_text("#!/usr/bin/env bash\nexit 1\n")
    (fake_bin / "gh").chmod(0o755)

    env = _pr_env(fake_bin, runtime)
    result = run_pr_status(
        "set",
        "--number",
        "54",
        "--url",
        "https://github.com/example/repo/pull/54",
        "--branch",
        "feature/hooks",
        cwd=repo,
        env=env,
    )
    assert result.returncode == 0, result.stderr

    result = run_pr_status("get", cwd=repo, env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["number"] == 54

    result = run_pr_status("clear", cwd=repo, env=env)
    assert result.returncode == 0, result.stderr
    result = run_pr_status("get", cwd=repo, env=env)
    assert result.returncode == 1


def test_monitor_uses_configured_reply_prefix_and_ack_reaction(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    comments = [
        {
            "id": 1,
            "user": {"login": "human"},
            "body": "Custom: already handled",
            "reactions": {"eyes": 0, "rocket": 1},
        },
        {
            "id": 2,
            "user": {"login": "human"},
            "body": "already acked with rocket",
            "reactions": {"eyes": 0, "rocket": 1},
        },
        {
            "id": 3,
            "user": {"login": "human"},
            "body": "please fix this",
            "reactions": {},
        },
    ]
    issue_output = "".join(f"printf '%s\\n' '{json.dumps(comment)}'\n" for comment in comments)
    write_gh(fake_bin, 60, draft=True, api_issues=issue_output)

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "CODEMATE_REPLY_PREFIX": "Custom:",
        "CODEMATE_ACK_REACTION": "rocket",
        "CODEMATE_TEST_GH_LOG": str(call_log),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_NO_PR", None)
    stop = hook_input("marker-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)

    result = run_hook("monitor_pr.sh", stop, cwd=repo, env=env)
    reason = json.loads(result.stdout)["reason"]
    assert "please fix this" in reason
    assert "Custom: already handled" not in reason
    assert "already acked with rocket" not in reason
