"""Integration tests use real Supervisor and local processes (no Docker daemon)."""

from __future__ import annotations

import importlib.util
import json
import os
import pty
import select
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "docker/setup/python/project-services.py"
HOOK = ROOT / "plugins/workspace/hooks/service_status.sh"
spec = importlib.util.spec_from_file_location("project_services", RUNNER)
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


def eventually(check, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    assert check()


def running(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "project with % spaces"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".codemate").mkdir()
    for name in ("backend", "frontend"):
        (repo / name).mkdir()
    yield repo
    runtime = manager.runtime_path(repo)
    groups = [
        item["pid"]
        for item in manager.process_states(runtime).values()
        if item["pid"] > 0
    ]
    pidfile = runtime / "supervisor.pid"
    supervisor_pid = int(pidfile.read_text()) if pidfile.exists() else None
    if manager.supervisor_running(runtime):
        with manager.rpc_client(runtime) as client:
            client.supervisor.shutdown()
    if supervisor_pid:
        deadline = time.monotonic() + 8
        while running(supervisor_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if running(supervisor_pid):
            os.kill(supervisor_pid, signal.SIGKILL)
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    shutil.rmtree(runtime, ignore_errors=True)


def configure(repo, config):
    (repo / ".codemate/config.yaml").write_text(yaml.safe_dump(config))


def python_command(code):
    return shlex.join([sys.executable, "-u", "-c", code])


@pytest.fixture
def launch(project):
    processes = []

    def start():
        log = (project / f"runner-{len(processes)}.log").open("w")
        process = subprocess.Popen(
            [sys.executable, str(RUNNER), "up"],
            cwd=project,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log.close()
        processes.append(process)
        return process

    yield start
    for process in reversed(processes):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=15)


def status(repo):
    result = subprocess.run(
        [sys.executable, str(RUNNER), "status", "--json"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
        timeout=5,
    )
    return json.loads(result.stdout)


def hook(repo, env, event="SessionStart"):
    result = subprocess.run(
        [str(HOOK)],
        input=json.dumps({"cwd": str(repo), "hook_event_name": event}),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=12,
    )
    return json.loads(result.stdout) if result.stdout else None


def test_absent_config_is_a_noop(project, launch):
    process = launch()
    assert process.wait(timeout=5) == 0
    assert (project / "runner-0.log").read_text() == ""
    assert not manager.runtime_path(project).exists()


def test_up_rejects_a_foreground_command(project):
    result = subprocess.run(
        [sys.executable, str(RUNNER), "up", "--", "touch", "should-not-exist"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 2
    assert "up does not accept a foreground command" in result.stderr
    assert not (project / "should-not-exist").exists()


@pytest.mark.parametrize(
    "bad",
    [
        "services: []",
        "setup: {}",
        "version: 2",
        "version: true",
        "services: {web: {command: sleep 100, cwd: ../outside}}",
        "services: {web: {command: sleep 100, ready: {http: file:///tmp/foo}}}",
        "services: {web: {command: sleep 100, ready: {http: 'http://localhost:bad'}}}",
        "services: {web: {command: sleep 100, ready: {http: 'http://localhost', timeout: -1}}}",
        "services: {web: {command: sleep 100, restart: true}}",
        "services: {web: {command: sleep 100}, web: {command: sleep 200}}",
    ],
)
def test_invalid_config_is_rejected_before_commands(project, bad):
    (project / ".codemate/config.yaml").write_text(bad)
    with pytest.raises(ValueError):
        manager.load_config(project)


def test_setup_failure_skips_remaining_steps_and_services(project, launch):
    configure(
        project,
        {
            "setup": [
                {"run": "echo failure; exit 7"},
                {"run": "touch should-not-exist"},
            ],
            "services": {"web": {"command": "touch should-not-start; sleep 100"}},
        },
    )
    process = launch()
    assert process.wait(timeout=5) == 1
    assert not (project / "should-not-exist").exists()
    assert not (project / "should-not-start").exists()
    snapshot = status(project)
    assert snapshot["setup"] == "failed"
    assert snapshot["services"][0]["process"] == "UNAVAILABLE"
    assert "failure" in (manager.runtime_path(project) / "setup.log").read_text()


@pytest.mark.parametrize("service_exit_code", [0, 1])
def test_entrypoint_starts_services_then_prints_banner_and_execs_session(
    project, tmp_path, service_exit_code
):
    setup_dir = tmp_path / "setup"
    (setup_dir / "shell").mkdir(parents=True)
    (setup_dir / "python").mkdir()
    shutil.copy(ROOT / "docker/setup/shell/common.sh", setup_dir / "shell/common.sh")
    for name in ("git", "gh", "precommit", "softlinks"):
        (setup_dir / f"shell/setup-{name}.sh").write_text("exit 0\n")
    (setup_dir / "python/setup-repo.py").write_text("")
    (setup_dir / "python/project-services.py").write_text(
        "import sys\nassert sys.argv[1:] == ['up']\n"
        f"print('service startup finished')\nsys.exit({service_exit_code})\n"
    )
    # Redirect only the container's fixed installation path into this fixture.
    entrypoint = setup_dir / "setup.sh"
    entrypoint.write_text(
        (ROOT / "docker/setup/setup.sh")
        .read_text()
        .replace('SETUP_DIR="/usr/local/bin/setup"', f'SETUP_DIR="{setup_dir}"')
    )
    argument = "a spaced argument; $literal"
    result = subprocess.run(
        [
            "/bin/bash",
            str(entrypoint),
            sys.executable,
            "-c",
            "import sys; print('agent received:', sys.argv[1]); raise SystemExit(23)",
            argument,
        ],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 23
    output = result.stdout
    banner = "Starting CodeMate session"
    assert output.count(banner) == 1
    assert output.index("service startup finished") < output.index(banner)
    assert output.index(banner) < output.index(f"agent received: {argument}")
    assert "All setup scripts completed successfully" not in output
    assert ("continuing to the session for repairs" in output) == bool(
        service_exit_code
    )


def test_setup_timeout_cleans_up_process_group(project, launch):
    configure(
        project,
        {"setup": [{"run": "sleep 100 & echo $! > child.pid; wait", "timeout": 0.2}]},
    )
    process = launch()
    assert process.wait(timeout=8) == 1
    pid = int((project / "child.pid").read_text())
    eventually(lambda: not running(pid))
    assert status(project)["setup"] == "failed"


def test_background_services_setup_health_controls_and_hooks(project, launch, tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    configure(
        project,
        {
            "setup": [
                {
                    "cwd": "backend",
                    "run": "printf '100%%; ready' > message; echo once >> ../setups",
                },
                {
                    "cwd": "frontend",
                    "run": "test -f ../backend/message; cp ../backend/message index.html",
                },
            ],
            "services": {
                "web": {
                    "cwd": "frontend",
                    "command": f"{shlex.quote(sys.executable)} -u -m http.server {port} --bind 127.0.0.1",
                    "ready": {
                        "http": f"http://127.0.0.1:{port}/index.html",
                        "timeout": 8,
                    },
                },
                "worker": {
                    "cwd": "backend",
                    "command": "echo '100% started'; sleep 100 & echo $! > child.pid; wait",
                },
            },
        },
    )
    process = launch()
    assert process.wait(timeout=12) == 0
    assert (project / "frontend/index.html").read_text() == "100%; ready"
    snapshot = status(project)
    assert snapshot["setup"] == "complete"
    services = {item["name"]: item for item in snapshot["services"]}
    assert services["web"]["readiness"] == "ready"
    assert services["worker"]["process"] == "RUNNING"
    assert "100% started" in Path(services["worker"]["log"]).read_text()

    # Re-entering the same workspace must not rerun setup or replace services.
    supervisor_pid = int((manager.runtime_path(project) / "supervisor.pid").read_text())
    assert running(supervisor_pid)
    duplicate = launch()
    assert duplicate.wait(timeout=5) == 0
    assert (project / "setups").read_text() == "once\n"
    assert (
        int((manager.runtime_path(project) / "supervisor.pid").read_text())
        == supervisor_pid
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / "codemate-services"
    executable.write_text(
        f'#!/bin/bash\nexec {shlex.join([sys.executable, str(RUNNER)])} "$@"\n'
    )
    executable.chmod(0o755)
    for agent in ("codex", "claude"):
        output = hook(
            project / "backend",
            {
                **os.environ,
                "CODEMATE_AGENT": agent,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
            },
        )
        context = output["hookSpecificOutput"]
        assert context["hookEventName"] == "SessionStart"
        assert '"readiness":"ready"' in context["additionalContext"]
        assert "100% started" not in context["additionalContext"]

    (project / "frontend/index.html").unlink()
    output = hook(project, {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"})
    assert (
        '"readiness":"not_ready"' in output["hookSpecificOutput"]["additionalContext"]
    )
    (project / "frontend/index.html").write_text("healthy again")

    # A later SessionStart must see current process status, not the startup snapshot.
    stopped = subprocess.run(
        [sys.executable, str(RUNNER), "stop", "web"],
        cwd=project,
        capture_output=True,
        check=False,
    )
    assert stopped.returncode == 0, stopped.stderr
    output = hook(project, {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"})
    assert '"process":"STOPPED"' in output["hookSpecificOutput"]["additionalContext"]
    restarted = subprocess.run(
        [sys.executable, str(RUNNER), "restart", "web"],
        cwd=project,
        capture_output=True,
        check=False,
    )
    assert restarted.returncode == 0, restarted.stderr
    assert (
        next(item for item in status(project)["services"] if item["name"] == "web")[
            "readiness"
        ]
        == "ready"
    )

    child_pid = int((project / "backend/child.pid").read_text())
    # A separate foreground command ending does not stop project services.
    subprocess.run(["/bin/true"], check=True)
    assert manager.http_ready(f"http://127.0.0.1:{port}/")
    subprocess.run([sys.executable, str(RUNNER), "stop"], cwd=project, check=True)
    eventually(lambda: not running(child_pid))
    assert running(supervisor_pid)
    assert not manager.http_ready(f"http://127.0.0.1:{port}/")


def test_readiness_timeout_leaves_background_service_running(project, launch):
    configure(
        project,
        {
            "services": {
                "worker": {
                    "command": "sleep 100",
                    "ready": {"http": "http://127.0.0.1:1/", "timeout": 1.5},
                }
            }
        },
    )
    assert launch().wait(timeout=5) == 1
    assert "readiness timed out" in (project / "runner-0.log").read_text()
    assert status(project)["services"][0]["readiness"] == "not_ready"


def test_stop_kills_an_uncooperative_service(project, launch):
    configure(
        project,
        {
            "services": {
                "worker": {
                    "command": "exec "
                    + python_command(
                        "import os, signal, time; from pathlib import Path; "
                        "Path('child.pid').write_text(str(os.getpid())); signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(100)"
                    )
                }
            }
        },
    )
    process = launch()
    assert process.wait(timeout=5) == 0
    pid = int((project / "child.pid").read_text())
    subprocess.run(
        [sys.executable, str(RUNNER), "stop", "worker"],
        cwd=project,
        check=True,
        timeout=10,
    )
    eventually(lambda: not running(pid))


def test_separate_session_ctrl_c_and_exit_do_not_stop_services(project, launch):
    configure(project, {"services": {"worker": {"command": "sleep 100"}}})
    assert launch().wait(timeout=5) == 0
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(project)
        os.execv(
            sys.executable,
            [
                sys.executable,
                "-u",
                "-c",
                (
                    "import sys; print('TTY', sys.stdin.isatty());\n"
                    "try: input()\n"
                    "except KeyboardInterrupt: print('INTERRUPTED'); input()\n"
                ),
            ],
        )
    output = b""

    def read_until(marker):
        nonlocal output
        deadline = time.monotonic() + 10
        while marker not in output and time.monotonic() < deadline:
            if select.select([fd], [], [], 0.1)[0]:
                output += os.read(fd, 4096)
        assert marker in output, output

    try:
        read_until(b"TTY True")
        os.write(fd, b"\x03")
        read_until(b"INTERRUPTED")
        assert status(project)["services"][0]["process"] == "RUNNING"
        os.write(fd, b"done\n")
    finally:
        os.killpg(pid, signal.SIGTERM)
        os.waitpid(pid, 0)
        os.close(fd)
    assert status(project)["services"][0]["process"] == "RUNNING"


@pytest.mark.parametrize("agent", ["codex", "claude"])
def test_hook_is_registered_for_both_agents(agent):
    filename = "hooks.json" if agent == "codex" else "claude-hooks.json"
    config = json.loads((HOOK.parent / filename).read_text())
    hooks = config["hooks"]["SessionStart"][0]["hooks"]
    assert any(item["command"].endswith("/hooks/service_status.sh") for item in hooks)


def test_hook_ignores_unconfigured_workspaces_and_other_events(project):
    assert hook(project, os.environ) is None
    configure(project, {})
    assert hook(project, os.environ, "UserPromptSubmit") is None


def test_hook_reports_unavailable_manager_without_blocking(project, tmp_path):
    configure(project, {})
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in ("bash", "cat", "git", "jq"):
        (fake_bin / command).symlink_to(shutil.which(command))
    output = hook(project, {**os.environ, "PATH": str(fake_bin)})
    assert "unavailable" in output["hookSpecificOutput"]["additionalContext"]


def test_bad_config_returns_failure_and_records_error(project, launch):
    configure(project, {"setup": [{"run": "touch should-not-exist"}], "services": []})
    process = launch()
    assert process.wait(timeout=5) == 1
    assert not (project / "should-not-exist").exists()
    assert status(project)["setup"] == "failed"
    assert (
        "services must be a mapping"
        in (manager.runtime_path(project) / "startup.log").read_text()
    )


def test_sigterm_during_setup_cleans_children(project, launch):
    configure(project, {"setup": [{"run": "sleep 100 & echo $! > child.pid; wait"}]})
    process = launch()
    eventually(lambda: (project / "child.pid").exists())
    pid = int((project / "child.pid").read_text())
    os.killpg(process.pid, signal.SIGTERM)
    assert process.wait(timeout=8) == 143
    eventually(lambda: not running(pid))
    assert not manager.supervisor_running(manager.runtime_path(project))


def test_service_start_failure_is_reported(project, launch):
    configure(
        project, {"services": {"broken": {"command": "echo cannot-start; exit 9"}}}
    )
    assert launch().wait(timeout=12) == 1
    snapshot = status(project)
    assert snapshot["services"][0]["process"] == "FATAL"
    assert "cannot-start" in Path(snapshot["services"][0]["log"]).read_text()


@pytest.mark.parametrize("output", ["exit 1", "echo invalid-json", "echo '{}'"])
def test_hook_status_errors_are_informational(project, tmp_path, output):
    configure(project, {})
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    executable = fake_bin / "codemate-services"
    executable.write_text(f"#!/bin/bash\n{output}\n")
    executable.chmod(0o755)
    result = hook(project, {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"})
    assert "status check failed" in result["hookSpecificOutput"]["additionalContext"]
