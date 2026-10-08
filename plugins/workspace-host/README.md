# Host workspace

Loaded for a native `codemate --host` session. Tracks session events and reuses
the workspace PR feedback, CI, commit continuation and notification hooks.
It does not start services, run setup, or include the container setup skills.
`--chat` records session events but skips Stop automation.

The launcher supplies `CODEMATE_WORKSPACE_HOOKS_ROOT`, `CODEMATE_PR_PLUGIN_ROOT`,
`CODEMATE_RUNTIME_DIR`, `CODEMATE_INSTANCE_ID`, and `CODEMATE_PYTHON`. Without
`CODEMATE_MODE=host` these hooks do nothing. The plugin is loaded by session
configuration; the environment guard alone does not control skill discovery.
