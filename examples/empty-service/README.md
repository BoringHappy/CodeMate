# Empty service example

A minimal HTTP service for trying CodeMate's automatic project services. It uses
Python's standard library, serves this directory on port **8000**, and has no
application logic, extra dependencies, or required environment variables.

The repository's [`.codemate/config.yaml`](../../.codemate/config.yaml) starts
the `example` service and checks `http://127.0.0.1:8000/` for readiness. Standard
CodeMate container startup loads it automatically, including `--shell` and
`--chat` modes; `--pure` skips project setup.

To try it in an existing CodeMate container with no services initialized yet,
run these commands from anywhere in the repository:

```bash
codemate-services up
codemate-services status --json
curl --noproxy '*' http://127.0.0.1:8000/
codemate-services logs example
```

The page displays `CodeMate example service is running.` Manage it with
`codemate-services stop example`, `codemate-services start example`, or
`codemate-services restart example`. Configuration edits take effect on the
next container start; `up` reuses an existing Supervisor, and `restart` reuses
the loaded service definition.

To run directly without CodeMate, from the repository root:

```bash
python3 -u -m http.server 8000 --bind 0.0.0.0 --directory examples/empty-service
```

The server binds to `0.0.0.0`; the URL above is reachable inside the container.
CodeMate does not automatically publish Docker ports. With bridge networking,
add `--network bridge --docker-param "-p 127.0.0.1:8000:8000"` when creating the
container to access the page from the host.
