#!/usr/bin/env python3
import os
import subprocess
import sys
import shlex
import tempfile
import json
from datetime import datetime, timezone


# Color codes
YELLOW = '\033[1;33m'
GREEN = '\033[1;32m'
RED = '\033[1;31m'
BLUE = '\033[1;34m'
MAGENTA = '\033[1;35m'
RESET = '\033[0m'


def run(cmd, check=True):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}\n{result.stderr}")
    return result


def get_repo_name_from_url(git_url):
    """Extract repository name from git URL."""
    # Remove .git suffix if present
    if git_url.endswith(".git"):
        git_url = git_url[:-4]

    # Extract the last part of the path
    # Works for both https://github.com/user/repo and git@github.com:user/repo
    repo_name = git_url.rstrip("/").split("/")[-1]
    return repo_name


def get_pr_template(workspace):
    """Read PR template from .github/PULL_REQUEST_TEMPLATE.md if it exists."""
    template_path = f"{workspace}/.github/PULL_REQUEST_TEMPLATE.md"
    if os.path.exists(template_path):
        with open(template_path, "r") as f:
            return f.read()
    return """## Summary
<N/A>

## Test plan
- [ ] Review and test changes
"""


def main():
    git_repo_url = os.environ["CODEMATE_GIT_REPO_URL"]
    upstream_repo_url = os.getenv("CODEMATE_UPSTREAM_REPO_URL", "")
    pr_title = os.getenv("CODEMATE_PR_TITLE", "")

    branch_name = os.getenv("CODEMATE_BRANCH_NAME", "")
    pr_number = os.getenv("CODEMATE_PR_NUMBER", "")
    no_pr = os.getenv("CODEMATE_NO_PR", "")

    if not branch_name and not pr_number:
        print(f"{RED}Skipping git PR setup: CODEMATE_GIT_REPO_URL or CODEMATE_BRANCH_NAME/CODEMATE_PR_NUMBER not set{RESET}")
        sys.exit(0)

    # Validate branch name is not main/master or default branch
    if branch_name:
        forbidden_branches = ["main", "master"]
        if branch_name.lower() in forbidden_branches:
            print(f"{RED}Error: Cannot use '{branch_name}' as branch name.{RESET}")
            print(f"{RED}Branch name cannot be 'main' or 'master'.{RESET}")
            sys.exit(1)

    print(f"{YELLOW}Setting up git repository...{RESET}")

    # Extract repo name from git URL
    repo_name = get_repo_name_from_url(git_repo_url)
    workspace = f"/home/agent/{repo_name}"

    # Ensure agent user has permission to the workspace directory
    run(f"sudo chown -R agent:agent {workspace}", check=False)

    if not os.path.exists(f"{workspace}/.git"):
        print(f"  Cloning repository: {BLUE}{git_repo_url}{RESET}")
        os.chdir("/home/agent")
        run(f"git clone {git_repo_url} {repo_name}")
        os.chdir(workspace)
    else:
        print(f"  Using existing repository")
        os.chdir(workspace)
        run("git fetch origin")

    # Add upstream remote if fork workflow
    if upstream_repo_url:
        print(f"  {MAGENTA}Fork workflow detected{RESET}")
        print(f"  Adding upstream remote: {BLUE}{upstream_repo_url}{RESET}")

        # Use shlex.quote to prevent shell injection
        safe_upstream_url = shlex.quote(upstream_repo_url)
        result = run(f"git remote add upstream {safe_upstream_url}", check=False)

        # Check if remote add failed (but ignore "already exists" error)
        if result.returncode != 0:
            if "already exists" in result.stderr:
                print(f"  {YELLOW}Upstream remote already exists, updating...{RESET}")
                run(f"git remote set-url upstream {safe_upstream_url}", check=False)
            else:
                print(f"  {RED}Warning: Failed to add upstream remote{RESET}")
                print(f"  {RED}{result.stderr.strip()}{RESET}")

        # Fetch from upstream
        result = run("git fetch upstream", check=False)
        if result.returncode != 0:
            print(f"  {YELLOW}Warning: Failed to fetch from upstream{RESET}")
            print(f"  {YELLOW}{result.stderr.strip()}{RESET}")

    # Additional validation: check against repository's default branch
    if branch_name:
        result = run("gh repo view --json defaultBranchRef -q .defaultBranchRef.name", check=False)
        if result.returncode == 0:
            default_branch = result.stdout.strip()
            if default_branch and branch_name.lower() == default_branch.lower():
                print(f"{RED}Error: Cannot use '{branch_name}' as branch name.{RESET}")
                print(f"{RED}Branch name cannot be the repository's default branch '{default_branch}'.{RESET}")
                sys.exit(1)

    if pr_number:
        print(f"  Getting branch name from PR {MAGENTA}#{pr_number}{RESET}")
        run(f"gh pr checkout {pr_number}")
        result = run(f"gh pr view {pr_number} --json url -q .url")
        pr_url = result.stdout.strip()
    else:
        result = run(
            f"git show-ref --verify --quiet refs/heads/{branch_name}", check=False
        )
        if result.returncode == 0:
            print(f"  Branch {BLUE}{branch_name}{RESET} exists locally, switching to it")
            run(f"git checkout {branch_name}")
            run(f"git pull origin {branch_name}", check=False)
            result = run(
                f"gh pr list --head {branch_name} --json url -q '.[0].url'", check=False
            )
            pr_url = result.stdout.strip() if result.returncode == 0 else ""
        else:
            result = run(
                f"git show-ref --verify --quiet refs/remotes/origin/{branch_name}",
                check=False,
            )
            if result.returncode == 0:
                print(f"  Branch {BLUE}{branch_name}{RESET} exists remotely, checking it out")
                run(f"git checkout -b {branch_name} origin/{branch_name}")
                result = run(
                    f"gh pr list --head {branch_name} --json url -q '.[0].url'",
                    check=False,
                )
                pr_url = result.stdout.strip() if result.returncode == 0 else ""
            else:
                print(f"  Creating new branch: {BLUE}{branch_name}{RESET}")
                run(f"git checkout -b {branch_name}")

                # Check if fork workflow (upstream exists) or no-pr mode
                if upstream_repo_url or no_pr:
                    print(f"  {YELLOW}Branch created locally. Create PR when ready.{RESET}")
                    pr_url = ""
                else:
                    # Standard workflow: Create PR immediately
                    safe_branch_name = shlex.quote(branch_name)
                    run(f"git commit --allow-empty -m 'Initial commit for {branch_name}'")
                    run(f"git push -u origin {safe_branch_name}")

                    print(f"  {MAGENTA}Creating pull request{RESET}")
                    pr_body = get_pr_template(workspace)
                    title = pr_title if pr_title else branch_name.replace("-", " ")

                    # Use shlex.quote to prevent shell injection
                    safe_title = shlex.quote(title)
                    safe_body = shlex.quote(pr_body)
                    result = run(f"gh pr create --draft --title {safe_title} --body {safe_body}")
                    pr_url = result.stdout.strip()

    print(f"{GREEN}✓ Git setup completed successfully{RESET}")
    if pr_url:
        print(f"  PR URL: {BLUE}{pr_url}{RESET}")

    # Write branch-scoped PR status under this worktree's Git metadata. This is
    # isolated across repositories, worktrees, and branches while remaining
    # discoverable by both Claude and Codex plugin workflows.
    try:
        current_branch = run("git branch --show-current").stdout.strip()
        if not current_branch:
            current_branch = f"detached-{run('git rev-parse --short=12 HEAD').stdout.strip()}"
        git_dir = run("git rev-parse --absolute-git-dir").stdout.strip()
        pr_status_file = os.path.join(
            git_dir, "codemate", "pr-status", f"{current_branch}.json"
        )
        os.makedirs(os.path.dirname(pr_status_file), exist_ok=True)

        resolved_pr_number = None
        if pr_number.isdigit():
            resolved_pr_number = int(pr_number)
        elif pr_url:
            url_number = pr_url.rstrip("/").rsplit("/", 1)[-1]
            if url_number.isdigit():
                resolved_pr_number = int(url_number)

        status = {
            "state": "open" if pr_url else "none",
            "branch": current_branch,
            "number": resolved_pr_number,
            "url": pr_url if pr_url else "",
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        # Use an atomic same-directory rename so readers never see partial JSON.
        with tempfile.NamedTemporaryFile(
            mode="w",
            delete=False,
            dir=os.path.dirname(pr_status_file),
            prefix=".pr-status.",
        ) as f:
            json.dump(status, f)
            f.write("\n")
            temp_path = f.name

        os.rename(temp_path, pr_status_file)

        if pr_url:
            print(f"  {GREEN}✓ Branch PR status saved to {pr_status_file}{RESET}")
        else:
            print(f"  {YELLOW}No PR exists yet. Create one when ready.{RESET}")
    except Exception as e:
        print(f"  {YELLOW}Warning: Failed to write PR status file: {e}{RESET}")
        # Clean up temp file if it exists
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.unlink(temp_path)


if __name__ == "__main__":
    main()
