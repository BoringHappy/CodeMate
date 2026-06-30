# Git Plugin

A Claude Code and Codex plugin for Git workflow management.

## Overview

This plugin provides skills for staging, committing, and pushing changes to Git repositories. It's designed to work seamlessly with the CodeMate environment and integrates with the PR plugin for complete workflow automation.

## Skills

### `/git:commit`
Stages all changes, creates a commit with a meaningful message, and pushes to the remote repository.

**Usage:**
```
/git:commit
```

**Workflow:**
1. Reviews current git status and recent commits
2. Stages all changes using `git add -A`
3. Creates a commit with a descriptive message following best practices:
   - Uses imperative mood (e.g., "Add feature" not "Added feature")
   - Follows the style of recent commits if a pattern exists
   - Is concise but descriptive
4. Pushes to the remote repository
5. Sets upstream branch if needed

## Installation

This plugin is automatically installed in the CodeMate environment via the marketplace in the Dockerfile.

For manual installation in Claude Code:
```bash
claude plugin marketplace add /path/to/marketplace
claude plugin install git@codemate --scope user
```

For manual installation in Codex:
```bash
codex plugin marketplace add /path/to/marketplace
codex plugin add git@codemate
```

## Requirements

- Git repository with remote access
- Push access to the remote repository
- Changes to commit (the skill will fail gracefully if there are no changes)

## Plugin Structure

```
git/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── .codex-plugin/
│   └── plugin.json          # Plugin manifest
└── skills/
    └── commit/
        └── SKILL.md
```

## Version

1.0.3
