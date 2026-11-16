# Developing MM-Companion with Codex

## Project setup

`AGENTS.md` holds the project's architecture, Git conventions, and verification
requirements. The detailed subsystem notes remain in `docs/notes/`. Read the
relevant notes before changing that subsystem.

The six project skills live in `.agents/skills/`: `affected-tests`,
`build-installer`, `combat-maneuvers`, `make-release`, `rulebook`, and
`run-mm-companion`. Each includes its original workflow and supporting scripts,
adapted to the new paths. The rules specialist is defined in
`.codex/agents/mm-rules.toml`; use it when the notes do not settle a rules question.

These locations follow the official [skills documentation](https://developers.openai.com/codex/skills)
and [custom agent documentation](https://developers.openai.com/codex/subagents).
No project model, permission, or global account settings were changed.

## Everyday use

Describe the result you want in normal language, for example:

- “Fix this bug: …”
- “Run the app and check the simple sheet.”
- “Use $affected-tests to check my changes.”
- “Review the changes on this branch.”

You can explicitly name a skill with `$skill-name`, or describe its task. If a
changed skill does not appear, restart Codex. Continue a related task in the same
chat; for a new task, give the desired behavior and any reproduction steps.
Project conventions and notes provide the durable context across chats.

Work branches start from `develop`. Keep one feature on its branch, open no pull
requests, and merge into `develop` only when you say the feature is done.
Publishing releases follows the existing release workflow and requires a request
to publish.

## Python on this machine

The existing `.venv` has Python 3.13 and the development dependencies. Bare
`python` currently resolves to Inkscape's bundled interpreter. Use the project
interpreter explicitly, or activate the environment first:

```powershell
.venv/Scripts/python.exe -m mm_companion
.venv/Scripts/python.exe .agents/skills/affected-tests/affected.py
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m black --check .
.venv/Scripts/python.exe -m pytest
```

Use affected tests during development and the full suite before declaring an app
feature done or merging it. UI checks use the existing screenshot driver, which
isolates its character workspace unless explicitly told otherwise.

## Preserved Claude setup

The original `CLAUDE.md` and complete `.claude/` directory were moved into
`.legacy-claude/`. That directory is gitignored and contains a SHA-256 manifest
for verifying the preserved files. It includes private local settings, so keep
it local. Git will show the former tracked paths as removed; the original files
still exist in the archive, and earlier commits also retain their tracked copies.

Claude-specific permissions and local scheduling state were archived rather than
translated into Codex permissions or automations. No account settings or scheduled
jobs were created. Earlier Claude chats are not imported by this file migration;
the repository's notes and plans remain the project history available to Codex.
