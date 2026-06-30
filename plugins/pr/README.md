# PR Plugin

A Claude Code and Codex plugin for managing GitHub Pull Request workflows.

## Overview

This plugin provides skills for creating, updating, and managing pull requests in GitHub repositories. It's designed to work seamlessly with the CodeMate environment.

## Skills

### `/pr:get-details`
Fetches and displays comprehensive PR information including:
- PR title and description
- Source and target branches
- Changed files
- Review comments
- PR-level comments

**Usage:**
```
/pr:get-details
```

### `/pr:fix-comments`
Automatically addresses feedback from PR review comments.

**Usage:**
```
/pr:fix-comments
```

**Workflow:**
1. Fetches all PR comments
2. Analyzes feedback to understand required changes
3. Reads and modifies affected files
4. Commits and pushes changes (uses `/git:commit` from git plugin)
5. Replies to comment threads confirming fixes

### `/pr:update`
Updates the PR title and/or description based on the actual changes.

**Usage:**
```
/pr:update                  # Update both title and summary
/pr:update --skip-title     # Update only the summary (skip the title)
```

**Features:**
- Analyzes PR diff to generate accurate descriptions
- Follows project's PR template format if available
- Uses conventional commit style for titles

## Installation

This plugin is automatically loaded in the CodeMate environment via the `--plugin-dir` flag in the Dockerfile.

For manual installation in Claude Code:
```bash
claude plugin marketplace add /path/to/marketplace
claude plugin install pr@codemate --scope user
```

For manual installation in Codex:
```bash
codex plugin marketplace add /path/to/marketplace
codex plugin add pr@codemate
```

## Requirements

- GitHub CLI (`gh`) installed and authenticated
- Git repository with remote access
- Active pull request (for most skills)

> Always pass `--json` with explicit fields to `gh pr view`/`gh pr list`; bare calls fail on the Projects (classic) deprecation.

## Plugin Structure

```
pr/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
├── .codex-plugin/
│   └── plugin.json          # Plugin manifest
└── skills/
    ├── get-details/
    │   └── SKILL.md
    ├── fix-comments/
    │   └── SKILL.md
    └── update/
        └── SKILL.md
```
