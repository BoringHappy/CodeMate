#!/bin/bash

# Shared setup for every CodeMate agent image.
run_common_setup() {
    local setup_dir="$1"
    local agent_setup="${2:-}"

    run_setup_script "$setup_dir/shell/setup-git.sh" "Running setup-git.sh..."
    run_setup_script "$setup_dir/shell/setup-gh.sh" "Running setup-gh.sh..."

    if [ -n "$agent_setup" ]; then
        "$agent_setup"
    fi

    run_setup_script "$setup_dir/python/setup-repo.py" "Running setup-repo.py..."
    run_setup_script "$setup_dir/shell/setup-precommit.sh" "Running setup-precommit.sh..."
    run_setup_script "$setup_dir/shell/setup-softlinks.sh" "Running setup-softlinks.sh..."
}
