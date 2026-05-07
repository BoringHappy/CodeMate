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

**How to apply:**
- Before completing an edit to any `plugins/<name>/**` file, open `plugins/<name>/.claude-plugin/plugin.json` and bump `version` accordingly.
- Bundle the version bump in the same commit as the change it accompanies.
