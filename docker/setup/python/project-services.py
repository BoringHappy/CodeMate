#!/usr/bin/python3
"""Initialize and manage background development services for a repository."""

from __future__ import annotations

import argparse
import configparser
import fcntl
import hashlib
import http.client
import json
import math
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xmlrpc.client
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml


class ConfigError(ValueError):
    pass


class Shutdown(BaseException):
    def __init__(self, signum):
        self.signum = signum


class UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys rather than silently losing a service or command."""

    def construct_mapping(self, node, deep=False):
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise ConfigError("Configuration keys must be strings")
            if key in mapping:
                raise ConfigError(f"Duplicate configuration key: {key}")
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


def message(text):
    print(f"[services] {text}", flush=True)


def mapping(value, allowed, label):
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")
    unknown = value.keys() - allowed
    if unknown:
        raise ConfigError(f"Unknown {label} fields: {', '.join(sorted(unknown))}")
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ConfigError(f"{label} must be a non-empty string")
    return value


def timeout(value, label):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ConfigError(f"{label} must be a positive number of seconds")
    return value


def working_directory(workspace, value):
    relative = Path(text(value, "cwd"))
    directory = (workspace / relative).resolve()
    if relative.is_absolute() or not directory.is_relative_to(workspace):
        raise ConfigError("cwd must be a path inside the repository")
    if any(char in str(directory) for char in "\r\n"):
        raise ConfigError("cwd cannot contain newlines")
    return directory


def load_config(workspace):
    with (workspace / ".codemate/config.yaml").open() as stream:
        config = yaml.load(stream, Loader=UniqueLoader)
    config = mapping(config, {"version", "setup", "services"}, "config")
    version = config.get("version", 1)
    if type(version) is not int or version != 1:
        raise ConfigError("Only config version 1 is supported")
    steps = config.get("setup", [])
    if not isinstance(steps, list):
        raise ConfigError("setup must be a list")
    for index, step in enumerate(steps):
        mapping(step, {"cwd", "run", "timeout"}, f"setup[{index}]")
        text(step.get("run"), f"setup[{index}].run")
        working_directory(workspace, step.get("cwd", "."))
        timeout(step.get("timeout", 300), f"setup[{index}].timeout")
    services = config.get("services", {})
    if not isinstance(services, dict):
        raise ConfigError("services must be a mapping")
    for name, service in services.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) or name == "all":
            raise ConfigError(f"Invalid service name: {name}")
        mapping(service, {"cwd", "command", "ready"}, f"services.{name}")
        text(service.get("command"), f"services.{name}.command")
        working_directory(workspace, service.get("cwd", "."))
        if "ready" in service:
            ready = mapping(
                service["ready"], {"http", "timeout"}, f"services.{name}.ready"
            )
            url = urllib.parse.urlsplit(
                text(ready.get("http"), f"services.{name}.ready.http")
            )
            if url.scheme not in {"http", "https"} or not url.hostname:
                raise ConfigError(f"services.{name}.ready.http must be an HTTP(S) URL")
            _ = url.port  # Validate malformed ports before running any setup commands.
            timeout(ready.get("timeout", 30), f"services.{name}.ready.timeout")
    return steps, services


def workspace_path():
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        Path(result.stdout.strip()).resolve()
        if result.returncode == 0
        else Path.cwd().resolve()
    )


def runtime_path(workspace):
    # Container-local state: never put sockets/PIDs in the shared CodeMate home
    # or the tracked .codemate directory. Keep Unix socket paths short.
    key = hashlib.sha256(os.fsencode(workspace)).hexdigest()[:16]
    return Path(f"/tmp/codemate-services-{os.getuid()}-{key}")


def service_log(runtime, name):
    return runtime / "services" / f"{name}.log"


def terminate_group(process):
    """Also clean up children of a setup shell which has already exited."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_setup(workspace, runtime, steps):
    with (runtime / "setup.log").open("w") as log:
        for index, step in enumerate(steps, 1):
            message(f"Setup {index}/{len(steps)} (log: {runtime / 'setup.log'})")
            log.write(f"\n--- Setup {index} ---\n")
            log.flush()
            process = subprocess.Popen(
                ["/bin/bash", "-ec", step["run"]],
                cwd=working_directory(workspace, step.get("cwd", ".")),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                code = process.wait(timeout=step.get("timeout", 300))
                if code:
                    raise RuntimeError(
                        f"Setup {index} exited with status {code}; see {runtime / 'setup.log'}"
                    )
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    f"Setup {index} timed out; see {runtime / 'setup.log'}"
                ) from error
            finally:
                terminate_group(process)


def supervisor_config(workspace, runtime, services):
    service_dir = runtime / "services"
    service_dir.mkdir(mode=0o700, exist_ok=True)
    config = configparser.ConfigParser(interpolation=None)
    config["unix_http_server"] = {
        "file": str(runtime / "supervisor.sock"),
        "chmod": "0700",
    }
    config["supervisord"] = {
        "nodaemon": "false",
        "silent": "true",
        "pidfile": str(runtime / "supervisor.pid"),
        "logfile": str(runtime / "supervisor.log"),
        "logfile_maxbytes": "5MB",
        "logfile_backups": "2",
    }
    config["rpcinterface:supervisor"] = {
        "supervisor.rpcinterface_factory": "supervisor.rpcinterface:make_main_rpcinterface",
    }
    config["supervisorctl"] = {"serverurl": f"unix://{runtime}/supervisor.sock"}
    for name, service in services.items():
        script = service_dir / f"{name}.sh"
        # Keep arbitrary shell syntax (quotes, semicolons, %, multiline commands)
        # out of Supervisor's INI command parser.
        script.write_text("#!/bin/bash\nset -e\n" + service["command"] + "\n")
        directory = working_directory(workspace, service.get("cwd", "."))
        if not directory.is_dir():
            raise ConfigError(f"Service {name} cwd does not exist: {directory}")
        config[f"program:{name}"] = {
            "command": f"/bin/bash {shlex.quote(str(script))}",
            "directory": str(directory),
            "autostart": "true",
            "autorestart": "unexpected",
            "startsecs": "1",
            "startretries": "3",
            "stopasgroup": "true",
            "killasgroup": "true",
            "stopwaitsecs": "5",
            "redirect_stderr": "true",
            "stdout_logfile": str(service_log(runtime, name)),
            "stdout_logfile_maxbytes": "5MB",
            "stdout_logfile_backups": "2",
        }
    # Supervisor performs %-interpolation even for paths.
    for section in config.values():
        for key, value in section.items():
            section[key] = value.replace("%", "%%")
    path = runtime / "supervisord.conf"
    with path.open("w") as stream:
        config.write(stream)
    return path


def rpc_client(runtime):
    class Connection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.settimeout(1)
            self.sock.connect(str(runtime / "supervisor.sock"))

    class Transport(xmlrpc.client.Transport):
        def make_connection(self, host):
            self._connection = (host, Connection(host))
            return self._connection[1]

    return xmlrpc.client.ServerProxy("http://localhost", transport=Transport())


def process_states(runtime):
    try:
        with rpc_client(runtime) as client:
            return {
                item["name"]: item for item in client.supervisor.getAllProcessInfo()
            }
    except (OSError, http.client.HTTPException, xmlrpc.client.Error):
        return {}


def http_ready(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=1) as response:
            return 200 <= response.status < 300
    except (OSError, http.client.HTTPException, urllib.error.URLError, ValueError):
        return False


def save_startup(runtime, state):
    temporary = runtime / "startup.json.tmp"
    temporary.write_text(json.dumps(state))
    temporary.replace(runtime / "startup.json")


def status_snapshot(workspace):
    runtime = runtime_path(workspace)
    try:
        startup = json.loads((runtime / "startup.json").read_text())
    except (OSError, ValueError):
        startup = {"setup": "unknown", "services": {}}
    states = process_states(runtime)

    def service_status(item):
        name, service = item
        process = states.get(name, {}).get("statename", "UNAVAILABLE")
        readiness = "not_running"
        if process == "RUNNING":
            url = service.get("ready", {}).get("http")
            readiness = (
                ("ready" if http_ready(url) else "not_ready")
                if url
                else "not_configured"
            )
        return {
            "name": name,
            "process": process,
            "readiness": readiness,
            "log": str(service_log(runtime, name)),
        }

    with ThreadPoolExecutor(max_workers=8) as executor:
        services = list(executor.map(service_status, startup["services"].items()))
    return {
        "configured": (workspace / ".codemate/config.yaml").exists(),
        "setup": startup["setup"],
        "startup_error": startup.get("error"),
        "services": services,
        "logs_dir": str(runtime),
    }


def supervisor_running(runtime):
    try:
        with rpc_client(runtime) as client:
            return client.supervisor.getState()["statename"] == "RUNNING"
    except (OSError, http.client.HTTPException, xmlrpc.client.Error):
        return False


def wait_ready(runtime, services):
    pending = dict(services)
    started = time.monotonic()
    succeeded = True
    while pending:
        if not supervisor_running(runtime):
            raise RuntimeError(f"Supervisor exited; see {runtime / 'supervisor.log'}")
        states = process_states(runtime)
        for name, service in list(pending.items()):
            state = states.get(name, {}).get("statename")
            ready = service.get("ready", {})
            if state in {"FATAL", "STOPPED"} or (
                state == "EXITED" and states[name].get("exitstatus") == 0
            ):
                message(f"{name}: {state}; see {service_log(runtime, name)}")
                succeeded = False
                del pending[name]
                continue
            if time.monotonic() - started >= ready.get("timeout", 30):
                message(
                    f"{name}: readiness timed out; see {service_log(runtime, name)}"
                )
                succeeded = False
                del pending[name]
                continue
            if state != "RUNNING":
                continue
            if ready and not http_ready(ready["http"]):
                continue
            message(f"{name}: {'ready' if ready else 'running'}")
            del pending[name]
        if pending:
            time.sleep(0.2)
    return succeeded


def up(workspace):
    if not (workspace / ".codemate/config.yaml").exists():
        return 0
    runtime = runtime_path(workspace)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (runtime / "startup.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX)

    def shutdown(signum, _frame):
        raise Shutdown(signum)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGHUP, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    startup = {"setup": "pending", "services": {}}
    try:
        if supervisor_running(runtime):
            message("Services are already managed for this workspace; reusing them")
            return 0
        try:
            save_startup(runtime, startup)
            steps, services = load_config(workspace)
            startup.update(
                setup="running",
                services={
                    name: {"ready": service.get("ready", {})}
                    for name, service in services.items()
                },
            )
            save_startup(runtime, startup)
            run_setup(workspace, runtime, steps)
            startup["setup"] = "complete"
            save_startup(runtime, startup)
            if services:
                path = supervisor_config(workspace, runtime, services)
                with (runtime / "supervisor-start.log").open("w") as log:
                    subprocess.run(
                        ["supervisord", "-c", str(path)],
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=True,
                        timeout=10,
                    )
                ready = wait_ready(runtime, services)
                message(
                    f"Logs: {runtime}; manage with codemate-services status/logs/restart/stop"
                )
                return 0 if ready else 1
        except (
            ValueError,
            OSError,
            RuntimeError,
            yaml.YAMLError,
            subprocess.SubprocessError,
        ) as error:
            startup["error"] = f"{type(error).__name__}; see startup.log"
            if startup["setup"] != "complete":
                startup["setup"] = "failed"
            (runtime / "startup.log").write_text(str(error) + "\n")
            save_startup(runtime, startup)
            message(f"Project startup failed: {error}")
            return 1
        return 0
    except Shutdown as error:
        return 128 + error.signum
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["up", "status", "logs", "start", "stop", "restart"]
    )
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    workspace = workspace_path()
    if args.action == "up":
        if args.args:
            parser.error("up does not accept a foreground command")
        return up(workspace)
    runtime = runtime_path(workspace)
    if args.action == "status" and args.args == ["--json"]:
        print(json.dumps(status_snapshot(workspace)))
        return 0
    config = runtime / "supervisord.conf"
    if not config.exists():
        parser.error("No services configured for this workspace")
    arguments = args.args
    if args.action == "logs":
        follow = "--follow" in arguments
        names = [item for item in arguments if item != "--follow"]
        if len(names) != 1 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", names[0]):
            parser.error("logs requires one service name and optional --follow")
        arguments = ["tail", *(["-f"] if follow else []), names[0]]
    else:
        arguments = [
            args.action,
            *(arguments or ([] if args.action == "status" else ["all"])),
        ]
    os.execvp("supervisorctl", ["supervisorctl", "-c", str(config), *arguments])


if __name__ == "__main__":
    sys.exit(main())
