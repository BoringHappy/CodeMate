# Proposal: Host Configuration and Multi-Account State Isolation

**Status:** Draft / Request for Comments
**Date:** 2026-07-13
**Branch:** `thinking-multi-account-method`

## 1. Summary

Add a host-only `~/.codemate/config.yaml` that provides global defaults and named account
configuration without requiring a `--profile` flag. A repository can select an account with
`CODEMATE_ACCOUNT` in its `.env`; one-off commands can select one through an explicit environment
value or env file.

Each account points to a separate persistent state directory. CodeMate mounts only the selected
account's state into the container, allowing containers for different accounts to run concurrently
without sharing Claude, Codex, GitHub, Kubernetes, history, or session state.

`config.yaml` is never mounted into a container. Secret values may be stored directly in the file,
but references to protected environment variables or files are preferred. Secret references are
resolved in memory; CodeMate does not render a second YAML file containing their values.

## 2. Motivation

CodeMate currently treats `~/.codemate` as one shared, mutable container home:

- The CLI always reads the fixed `~/.codemate` path.
- Docker mounts the complete directory at `/home/agent/.codemate`.
- Docker also mounts every top-level entry at the matching path under `/home/agent`.
- The selected host `gh` token and host Git identity become container environment values.
- Container names contain the repository and branch or PR, but no account identity.

In practice, `~/.codemate` can contain:

- Claude credentials, settings, histories, projects, and sessions.
- Codex credentials, configuration, histories, sessions, and SQLite databases.
- GitHub, Kubernetes, plugin, and tool configuration.
- Mutable caches that may not tolerate concurrent writers.

Consequently, two containers using different accounts can observe or overwrite each other's state.
Two launches for the same repository and branch also resolve to the same Docker container name.

## 3. Goals

- Provide one host configuration file for defaults shared across repositories.
- Give repository `.env` values higher priority than host configuration.
- Give host configuration higher priority than ambient environment variables.
- Support different Claude, Codex, GitHub, Git, and tool identities.
- Allow containers using different accounts to run concurrently.
- Keep every account's credentials and mutable state isolated from other accounts.
- Keep `config.yaml` and host-side secret files out of every container.
- Preserve the existing CLI and `.env` workflow when `config.yaml` is absent.
- Make resolved configuration sources inspectable without printing secret values.

## 4. Non-goals

- Encrypting secrets stored directly in `config.yaml`.
- Implementing a hosted secret manager.
- Synchronizing credentials between machines.
- Changing Claude or Codex authentication file formats.
- Guaranteeing safe concurrent writes when multiple containers deliberately select the same state
  directory.

## 5. Host layout

The proposed layout separates host control data from mountable account state:

```text
~/.codemate/
├── config.yaml                         # host-only, mode 0600
├── secrets/                            # optional host-only files, mode 0700
│   ├── anthropic-work                  # mode 0600
│   └── openai-personal                 # mode 0600
└── accounts/
    ├── work/
    │   └── home/                       # only this directory is mountable
    │       ├── .claude/
    │       ├── .claude.json
    │       ├── .codex/
    │       ├── .cache/
    │       └── .kube/
    └── personal/
        └── home/
            ├── .claude/
            ├── .claude.json
            └── .codex/
```

Account state may live outside `~/.codemate` by using an absolute `state_dir`. Relative paths are
resolved against `~/.codemate`.

An account's state directory must not be `~/.codemate` itself or an ancestor of `config.yaml`.
CodeMate resolves symlinks before checking this rule.

## 6. Configuration schema

The initial schema uses existing environment variable names so it remains easy to map into the
current resolver and container environment:

```yaml
version: 1
default_account: work

defaults:
  env:
    CODEMATE_AGENT: claude
    CODEMATE_ALLOW_COUNTRY: US
    TZ: UTC
  mounts:
    - source: ~/shared-data
      target: /data
      read_only: true

accounts:
  work:
    state_dir: accounts/work/home
    env:
      CODEMATE_GIT_USER_NAME: Work Name
      CODEMATE_GIT_USER_EMAIL: work@example.com
      CODEMATE_GITHUB_TOKEN:
        from_env: CODEMATE_WORK_GITHUB_TOKEN
      ANTHROPIC_AUTH_TOKEN:
        from_file: ~/.codemate/secrets/anthropic-work

  personal:
    state_dir: accounts/personal/home
    env:
      CODEMATE_AGENT: codex
      CODEMATE_GIT_USER_NAME: Personal Name
      CODEMATE_GIT_USER_EMAIL: personal@example.com
      CODEMATE_GITHUB_TOKEN:
        from_env: CODEMATE_PERSONAL_GITHUB_TOKEN
      OPENAI_API_KEY:
        from_file: ~/.codemate/secrets/openai-personal
```

### 6.1 Schema rules

- `version` is required. Unknown versions fail with an actionable error.
- `default_account` must name an entry under `accounts`.
- Account names use letters, digits, `_`, and `-` only.
- `state_dir` is required for every account and is created during setup if missing.
- `defaults.env` applies to all accounts.
- `accounts.<name>.env` overrides `defaults.env`.
- Plain YAML string values are literal values.
- Structured values must contain exactly one supported secret source.
- Mounts use structured source, target, and read-only fields instead of shell-split strings.
- Unknown top-level or account fields fail validation to catch spelling mistakes.
- YAML is loaded with a safe loader; custom tags and executable objects are rejected.

The first version intentionally does not perform implicit `${VARIABLE}` substitution. Explicit
references distinguish secrets from ordinary strings, give better missing-variable errors, and let
diagnostic output identify a value as secret without resolving or displaying it.

## 7. Account selection

No new profile flag is required. The selected account is resolved before the remaining
configuration:

1. Explicit `--env CODEMATE_ACCOUNT=<name>`.
2. The last explicit `--env-file` containing `CODEMATE_ACCOUNT`.
3. The repository `.env`.
4. Ambient `CODEMATE_ACCOUNT`.
5. `default_account` in `config.yaml`.

This supports stable repository assignment:

```dotenv
# project-a/.env
CODEMATE_ACCOUNT=work
```

It also supports a one-off selection without changing the repository:

```bash
codemate --env CODEMATE_ACCOUNT=personal --branch feature/example
```

`CODEMATE_ACCOUNT` is host-side metadata and is not exported into the container unless a future
container feature requires it.

## 8. Configuration precedence

After account selection, each normal setting uses the following precedence, from highest to lowest:

1. Dedicated CLI options and explicit `--env KEY=VALUE` entries.
2. Explicit `--env-file` files, with later files winning.
3. Repository `.env`.
4. Selected account `env` in `~/.codemate/config.yaml`.
5. `defaults.env` in `~/.codemate/config.yaml`.
6. Ambient environment variables.
7. Command-derived values such as `gh auth token`, Git identity, and repository remote.
8. Built-in defaults.

This preserves repository ownership of repository-specific behavior while allowing host
configuration to replace ambient shell defaults. Explicit invocation inputs remain authoritative.

Empty-value behavior remains field-specific. Fields that currently allow an intentional empty
override continue to do so; other empty values fall through to the next source.

## 9. Secret handling

### 9.1 Supported forms

A literal secret is allowed:

```yaml
CODEMATE_GITHUB_TOKEN: github_pat_example
```

This is plaintext at rest and is recommended only on a trusted, single-user host with correct file
permissions and backup policy.

An environment reference is preferred when a launcher or secret manager already supplies the
value:

```yaml
CODEMATE_GITHUB_TOKEN:
  from_env: CODEMATE_WORK_GITHUB_TOKEN
```

A protected file reference avoids exporting a secret to every child of the user's login shell:

```yaml
OPENAI_API_KEY:
  from_file: ~/.codemate/secrets/openai-personal
```

`from_file` removes one trailing newline, rejects embedded newlines for Docker environment values,
and otherwise preserves the content exactly. Arbitrary command execution such as `from_command` is
not included in version 1.

### 9.2 Resolution and temporary data

- Parse and validate YAML before resolving secret references.
- Resolve only the selected account's references.
- Keep resolved values in memory and never write a rendered YAML document.
- Continue passing runtime values through a generated Docker env file with mode `0600`.
- Never put secret values directly in Docker command-line arguments or launch output.
- Remove the generated env file on every success or failure path.

The current synchronous `docker run` leaves its generated env file on disk until the container
exits. The implementation should shorten this lifetime. One deterministic option is:

1. Generate the mode-`0600` env file.
2. Run `docker create --env-file ...`.
3. Delete the env file immediately after `docker create` returns.
4. Run `docker start --attach --interactive <container>`.
5. Remove the container when the session ends.

Docker stores container environment values in its metadata, so users with Docker daemon access can
still inspect them. Protecting secrets from Docker administrators is outside the local Docker threat
model.

### 9.3 File and output protections

- Create `~/.codemate` and `~/.codemate/secrets` with mode `0700`.
- Create `config.yaml`, referenced secret files, and temporary env files with mode `0600`.
- Refuse literal or file-referenced secrets when their source is group- or world-readable.
- Ensure `config.yaml` and `secrets/` are excluded from any generated Git repository files.
- `codemate --config` prints `<redacted>` for the entire secret value plus its source type.
- Errors identify the account, key, and missing reference name, but never its resolved value.
- Dry-run output uses a placeholder for the generated env-file path and contains no secrets.

An ambient environment variable is not inherently safer than a protected file: it is inherited by
all child processes. Recommended secret sources, in order, are an OS secret store exposed through a
narrow launcher, a protected file, a dedicated environment variable, and finally a literal YAML
value.

## 10. Mount isolation

Ignoring `config.yaml` only in the top-level entry loop is insufficient. The current CLI also mounts
the complete `~/.codemate` directory, which would expose the file at
`/home/agent/.codemate/config.yaml`.

With `config.yaml` enabled, CodeMate must:

1. Resolve the selected account's `state_dir` on the host.
2. Mount that state directory at `/home/agent/.codemate` if the compatibility path is needed.
3. Mount each top-level state entry at `/home/agent/<entry-name>`, preserving current home behavior.
4. Never mount the parent `~/.codemate`, `config.yaml`, `secrets/`, or another account directory.
5. Apply user custom mounts after managed mounts, as today.

For example, selecting `work` produces mounts conceptually equivalent to:

```text
~/.codemate/accounts/work/home          -> /home/agent/.codemate
~/.codemate/accounts/work/home/.claude  -> /home/agent/.claude
~/.codemate/accounts/work/home/.codex   -> /home/agent/.codex
~/.codemate/accounts/work/home/.kube    -> /home/agent/.kube
```

It does not mount:

```text
~/.codemate/config.yaml
~/.codemate/secrets
~/.codemate/accounts/personal
```

Read-only mounting the root is not a substitute: it prevents writes but still reveals every
account's credentials.

## 11. Container identity and concurrency

Include the selected account in the generated container name:

```text
codemate-<account>-<repository>-<branch-or-pr>
```

Add labels with the account name and canonical state-directory hash. Labels make it possible to
diagnose active state users without exposing secret paths in ordinary output.

Different accounts use different state directories and can run concurrently, including against the
same repository and branch. They receive different container names.

Concurrent containers selecting the same state directory retain the risks of the current design:
Claude and Codex may update histories, plugins, sessions, SQLite databases, and token state at the
same time. Version 1 should detect another running container with the same state-directory hash and
warn. A later version may provide per-container writable state initialized from account credentials.

## 12. Launch flow

The resulting host-side flow is:

```text
Read repo .env and explicit env inputs
                |
                v
Select CODEMATE_ACCOUNT
                |
                v
Load and safely validate config.yaml
                |
                v
Merge values using documented precedence
                |
                v
Resolve selected account's secret references in memory
                |
                v
Validate state isolation and permissions
                |
                v
Create account-qualified container with selected state mounts
                |
                v
Delete temporary env data, then attach
```

Only the selected account is resolved. A missing secret in an unused account does not block a
launch.

## 13. Setup and migration

### 13.1 New installations

`codemate --setup` should:

- Create `~/.codemate/config.yaml` with mode `0600`.
- Create a `default` account and `accounts/default/home` with mode `0700`.
- Create the existing Claude settings under the default account state.
- Keep creating the repository `.env` as it does today.
- Print examples for selecting the default account and adding another account.

### 13.2 Existing installations

When `config.yaml` is absent, CodeMate retains the legacy resolver and mounts so upgrades do not
silently disconnect existing credentials.

`codemate --setup` on a legacy installation should offer a migration that:

1. Refuses to run while a CodeMate container is active.
2. Creates `accounts/default/home`.
3. Moves existing persistent home entries into that directory without copying secret files.
4. Writes `config.yaml` only after all moves succeed.
5. Rolls back completed moves if a later move or config write fails.

The migration excludes the new reserved host-only names `config.yaml`, `accounts`, and `secrets`.
It should support `--dry-run` before making filesystem changes.

## 14. Validation and diagnostics

Configuration failures should be explicit and local. Examples include:

- Unknown YAML version, field, or account.
- Missing or invalid `default_account`.
- Selected account does not exist.
- State directory overlaps host-only control or secret paths.
- Missing referenced environment variable or file.
- Unsafe permissions on a file containing or supplying a secret.
- Invalid mount target or duplicate managed mount.

`codemate --config` should add these non-secret diagnostics:

- Selected account and the source that selected it.
- Canonical state directory, optionally abbreviated under the user's home.
- Each resolved key's winning source.
- `<redacted> (from_env: NAME)` or `<redacted> (from_file: PATH)` for secrets.
- Effective mounts, showing which are managed and which are user-provided.

## 15. Implementation outline

1. Add a YAML dependency and typed schema parser.
2. Split account selection from normal value resolution.
3. Add global defaults and account values to the resolver precedence chain.
4. Implement literal, `from_env`, and `from_file` values with permission checks.
5. Replace fixed `~/.codemate` mounts with selected `state_dir` mounts.
6. Add account identity and state hash to container names and labels.
7. Shorten temporary env-file lifetime by separating container creation from attachment.
8. Extend setup for new installations and safe legacy migration.
9. Update English and Chinese documentation after the behavior is implemented.

## 16. Test plan

- Parse a valid version 1 configuration and reject unsafe YAML tags.
- Reject unknown versions, fields, accounts, and malformed secret references.
- Verify every level of the precedence table, including intentional empty values.
- Verify account-selection precedence independently of normal value precedence.
- Resolve only the selected account's secrets.
- Reject missing references and unsafe secret-file permissions without printing values.
- Verify diagnostic and dry-run output never contains complete or partial secret values.
- Verify `config.yaml`, `secrets/`, and unselected accounts never appear in Docker mounts.
- Verify two accounts produce different container names for the same repository and branch.
- Warn when two active containers use the same state-directory hash.
- Verify generated env data is deleted immediately after container creation and on failure.
- Verify legacy behavior remains unchanged when `config.yaml` is absent.
- Verify setup migration preserves credentials, settings, and permissions and can roll back.

## 17. Decisions

| Topic | Decision |
|---|---|
| Host configuration path | `~/.codemate/config.yaml` |
| Required profile flag | None |
| Account selector | `CODEMATE_ACCOUNT` plus `default_account` fallback |
| Repository override | Repository `.env` wins over YAML |
| Ambient override | YAML wins over ambient environment values |
| Direct secrets in YAML | Supported but discouraged |
| Preferred secret syntax | Explicit `from_env` or `from_file` reference |
| Secret interpolation | Resolve in memory; never render temporary YAML |
| Root `.codemate` mount | Do not mount; mount only selected account state |
| Cross-account concurrency | Supported through isolated state and container identity |
| Same-state concurrency | Warning in version 1; stronger isolation deferred |
