from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "plugins" / "workspace" / "hooks"


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

    status_file = repo / ".git" / "codemate" / "pr-status" / "feature" / "hooks.json"
    status_file.parent.mkdir(parents=True)
    status_file.write_text(
        json.dumps(
            {
                "state": "open",
                "branch": "feature/hooks",
                "number": 41,
                "url": "https://github.com/example/repo/pull/41",
            }
        )
    )

    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CODEMATE_TEST_GH_LOG\"\n"
        "if [ \"$1 $2\" = \"pr view\" ]; then\n"
        "  printf '%s\\n' '{\"number\":41,\"state\":\"OPEN\",\"url\":\"https://github.com/example/repo/pull/41\",\"isDraft\":false,\"labels\":[],\"statusCheckRollup\":[],\"headRefOid\":\"abc123\"}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    fake_gh.chmod(0o755)

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
        "pr view 41 --json number,state,url,isDraft,labels,statusCheckRollup,headRefOid"
    ]

    active = hook_input("monitor-session", repo, "UserPromptSubmit")
    run_hook("record_session_status.sh", active, cwd=repo, env=env)
    result = run_hook("monitor_pr.sh", stop, cwd=repo, env=env)
    assert result.stdout == ""
    assert len(call_log.read_text().splitlines()) == 1


def test_monitor_requires_branch_local_pr_state_before_gh(tmp_path: Path) -> None:
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
    assert not call_log.exists()


def test_old_monitor_exits_when_a_newer_stop_generation_exists(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    status_file = repo / ".git" / "codemate" / "pr-status" / "feature" / "hooks.json"
    status_file.parent.mkdir(parents=True)
    status_file.write_text(
        json.dumps(
            {
                "state": "open",
                "branch": "feature/hooks",
                "number": 43,
                "url": "https://github.com/example/repo/pull/43",
            }
        )
    )

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

    status_file = repo / ".git" / "codemate" / "pr-status" / "feature" / "hooks.json"
    status_file.parent.mkdir(parents=True)
    status_file.write_text(
        json.dumps(
            {
                "state": "open",
                "branch": "feature/hooks",
                "number": 42,
                "url": "https://github.com/example/repo/pull/42",
            }
        )
    )

    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CODEMATE_TEST_GH_LOG\"\n"
        "if [ \"$1 $2\" = \"pr view\" ]; then\n"
        "  printf '%s\\n' '{\"number\":42,\"state\":\"OPEN\",\"url\":\"https://github.com/example/repo/pull/42\",\"isDraft\":true,\"labels\":[],\"statusCheckRollup\":[],\"headRefOid\":\"abc123\"}'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"api\" ]; then exit 0; fi\n"
        "exit 1\n"
    )
    fake_gh.chmod(0o755)

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
            if call_log.exists() and len(call_log.read_text().splitlines()) == 3:
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
    assert len(call_log.read_text().splitlines()) == 3


def test_monitor_exits_after_max_polls(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    call_log = tmp_path / "gh-calls.log"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    status_file = repo / ".git" / "codemate" / "pr-status" / "feature" / "hooks.json"
    status_file.parent.mkdir(parents=True)
    status_file.write_text(
        json.dumps(
            {
                "state": "open",
                "branch": "feature/hooks",
                "number": 46,
                "url": "https://github.com/example/repo/pull/46",
            }
        )
    )

    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CODEMATE_TEST_GH_LOG\"\n"
        "if [ \"$1 $2\" = \"pr view\" ]; then\n"
        "  printf '%s\\n' '{\"number\":46,\"state\":\"OPEN\",\"url\":\"https://github.com/example/repo/pull/46\",\"isDraft\":true,\"labels\":[],\"statusCheckRollup\":[],\"headRefOid\":\"abc123\"}'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    fake_gh.chmod(0o755)

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
    # Each poll makes 3 gh calls (pr view, issue comments, review comments).
    assert len(call_log.read_text().splitlines()) == 30 * 3


def test_review_comment_cursor_only_advances_past_delivered_batch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    runtime = tmp_path / "runtime"
    fake_bin = tmp_path / "bin"
    repo.mkdir()
    fake_bin.mkdir()
    init_repo(repo)

    status_file = repo / ".git" / "codemate" / "pr-status" / "feature" / "hooks.json"
    status_file.parent.mkdir(parents=True)
    status_file.write_text(
        json.dumps(
            {
                "state": "open",
                "branch": "feature/hooks",
                "number": 44,
                "url": "https://github.com/example/repo/pull/44",
            }
        )
    )
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
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1 $2\" = \"pr view\" ]; then\n"
        "  printf '%s\\n' '{\"number\":44,\"state\":\"OPEN\",\"url\":\"https://github.com/example/repo/pull/44\",\"isDraft\":true,\"labels\":[],\"statusCheckRollup\":[],\"headRefOid\":\"abc123\"}'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = \"api\" ] && [[ \"$*\" == *'/issues/'* ]]; then exit 0; fi\n"
        "if [ \"$1\" = \"api\" ] && [[ \"$*\" == *'/pulls/'* ]]; then\n"
        f"{review_output}"
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    fake_gh.chmod(0o755)

    env = os.environ.copy() | {
        "CODEMATE_AGENT": "codex",
        "CODEMATE_RUNTIME_DIR": str(runtime),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    env.pop("CODEMATE_NO_PR", None)
    stop = hook_input("review-batch-session", repo, "Stop")
    run_hook("record_session_status.sh", stop, cwd=repo, env=env)

    first = run_hook("monitor_pr.sh", stop, cwd=repo, env=env)
    first_reason = json.loads(first.stdout)["reason"]
    monitor_state = Path(f"{status_file}.monitor-state.json")
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

    status_file = repo / ".git" / "codemate" / "pr-status" / "feature" / "hooks.json"
    status_file.parent.mkdir(parents=True)
    status_file.write_text(
        json.dumps(
            {
                "state": "open",
                "branch": "feature/hooks",
                "number": 45,
                "url": "https://github.com/example/repo/pull/45",
            }
        )
    )
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s:%s\\n' \"$CODEMATE_TEST_CALLER\" \"$*\" >> \"$CODEMATE_TEST_GH_LOG\"\n"
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
            if call_log.exists() and len(call_log.read_text().splitlines()) == 3:
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
            if len(call_log.read_text().splitlines()) == 6:
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
    assert callers == ["a", "a", "a", "b", "b", "b"]
