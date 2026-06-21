# PR Plugin

A Claude Code plugin for managing GitHub Pull Request workflows.

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
/pr:update --summary-only   # Update only the summary
```

**Features:**
- Analyzes PR diff to generate accurate descriptions
- Follows project's PR template format if available
- Uses conventional commit style for titles

## Installation

This plugin is automatically loaded in the CodeMate environment via the `--plugin-dir` flag in the Dockerfile.

For manual installation in other environments:
```bash
claude --plugin-dir /path/to/pr
```

## Requirements

- GitHub CLI (`gh`) installed and authenticated
- Git repository with remote access
- Active pull request (for most skills)

## GitHub CLI Notes

Bare `gh pr view` / `gh pr list` (without `--json`) fail because GitHub is deprecating Projects (classic) and the default selection set still requests `repository.pullRequest.projectCards`:

```
GraphQL: Projects (classic) is being deprecated ... (repository.pullRequest.projectCards)
```

All skills in this plugin pass `--json` with explicit fields to skip the deprecated selection. Keep this pattern when adding new commands:

```bash
gh pr view <number> --json number,title,body,headRefName,baseRefName,state,url,files
gh pr list --json number,title,headRefName,state,url
gh pr diff <number>   # unaffected — no --json needed
```

## Plugin Structure

```
pr/
├── .claude-plugin/
│   └── plugin.json          # Plugin manifest
└── skills/
    ├── get-details/
    │   └── SKILL.md
    ├── fix-comments/
    │   └── SKILL.md
    └── update/
        └── SKILL.md
```

## Version

1.0.2
