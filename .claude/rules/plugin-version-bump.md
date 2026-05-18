# Plugin Version Bump on Update

When modifying any file under `plugins/`, bump the `version` field in the corresponding plugin's `.claude-plugin/plugin.json`.

**Scope:**
- Each plugin directory (e.g. `plugins/git/`, `plugins/pr/`, `plugins/issue/`, `plugins/dev/`, `plugins/pm/`, `plugins/workspace/`) is versioned independently.
- Only bump the version of the plugin(s) whose files were actually changed.

**Versioning:**
- Use semantic versioning (`MAJOR.MINOR.PATCH`).
- Patch bump (`1.0.0` → `1.0.1`) for bug fixes and small tweaks.
- Minor bump (`1.0.0` → `1.1.0`) for new skills, commands, or backwards-compatible features.
- Major bump (`1.0.0` → `2.0.0`) for breaking changes to skill interfaces or removed commands.

**One bump per plugin per PR:**
- Within a single PR, each plugin's `version` is bumped at most **once**. Subsequent changes to the same plugin in follow-up commits on the same PR must **not** bump the version again.
- The single bump should reflect the highest semver tier required by *any* change in the PR. If the PR adds a new skill (minor) and later commits also include patch-level tweaks, the final version is a single minor bump from the base — not minor-then-patch.
- Before bumping, check `git log origin/<base-branch>..HEAD -- plugins/<name>/.claude-plugin/plugin.json` (or the PR diff) to see whether this plugin has already been bumped in the current PR. If yes, do not bump again.

**How to apply:**
- Before completing an edit to any `plugins/<name>/**` file, open `plugins/<name>/.claude-plugin/plugin.json` and bump `version` accordingly — unless the plugin has already been bumped earlier in this PR.
- Bundle the (single) version bump in the same commit as the first change in the PR that touches that plugin.
