---
name: ack-comments
description: Acknowledges new PR issue comments by adding an eye emoji (👀) reaction. Use when you need to mark issue comments as seen after processing them.
---

# Acknowledge PR Issue Comments

Adds a 👀 reaction to PR issue comments to indicate they have been seen and addressed.

## Shared Contract

The reaction used for acknowledgment is shared with the workspace monitor (see
`docs/plugin-contracts.md`). The default is the `eyes` reaction (👀); deployments
may override it with the `CODEMATE_ACK_REACTION` environment variable. The
monitor skips comments that already carry the configured reaction.

## What it does

1. **Fetches issue comments**: Gets all issue comments (pure PR comments) from the current pull request
2. **Filters unacknowledged comments**: Skips comments that already have the 👀 reaction
3. **Adds eye reaction**: Adds 👀 reaction to each new comment to mark it as acknowledged

## Current PR Issue Comments

PR Number:
!`gh pr view --json number -q .number | cat`

Issue comments (showing comment ID and body):
!`gh api repos/:owner/:repo/issues/$(gh pr view --json number -q .number)/comments --jq '.[] | "ID: \(.id) | User: \(.user.login) | Body: \(.body)"' | cat`

## Instructions

For each issue comment shown above that does NOT already have a 👀 reaction:

1. **Check if already acknowledged**: Look at the `reactions.eyes` count in the comment data
2. **Add eye reaction**: Use the following command to add the reaction:
   ```bash
   gh api repos/:owner/:repo/issues/comments/{comment_id}/reactions -X POST -f content=eyes
   ```

   Note: This adds an "eyes" reaction (👀) to the comment, which is cleaner than a text reply.

3. **Report status**: Tell the user which comments were acknowledged

## Prerequisites

- Must be run in a git repository
- GitHub CLI (`gh`) must be installed and authenticated
- Pull request must exist for the current branch

## Notes

- Uses GitHub reactions API to add 👀 emoji reaction to comments
- The reaction serves as an acknowledgment that the comment has been seen
- The monitor script filters out comments that have the 👀 reaction
