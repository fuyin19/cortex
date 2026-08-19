# Cortex 5 contributor guide

Cortex is a deliberately small, single-writer record knowledge base. The workspace directory itself is the complete durable state.

## Architecture contract

- `--workspace` is exactly one record-KB root. It contains only `profiles/record-schema.json`, `profiles/tags.json`, `profiles/layout.json`, and the records root selected by the layout profile.
- Do not add manifests, indexes, schema registries, artifacts, plans, journals, receipts, external state, hidden identity, recovery state, or `.cortex/` directories.
- The distribution is `cortex-record-kb`; the command and Python package are `cortex`; the version is `5.0.0`.
- Public business routes are only `manage init`, `manage status`, `manage validate`, `manage config show`, `manage config set`, `record add`, and `record edit`.
- Results have exactly `status`, `exit_code`, `command`, `data`, and `issues`. Status/exit pairs are `ok/0`, `usage_error/2`, `validation_error/3`, `busy/5`, and `io_error/6`.
- Profiles and records are validated by explicit deterministic code. Do not add a general JSON Schema engine.
- Every initialized mutation uses only a nonblocking exclusive OS lock on the first byte of `profiles/record-schema.json`. Reads do not lock and do not write.
- `record add` stages one reserved directory in the records root and publishes with a same-parent no-replace rename. Record edits and profile replacements use a same-directory temporary file and `os.replace`.
- Preserve source and conversion bytes and accepted path structure. Reject symlinks, reparse points, nonregular entries, unsafe components, reserved `.cortex-*` names, and conversion case-fold collisions. Content is otherwise opaque.
- Do not claim protection for ACLs, ownership, hard links, sparse allocation, alternate data streams, extended attributes, resource forks, handle identity, or TOCTOU races.

## Development workflow

1. Preserve unrelated worktree changes.
2. Update implementation, both skills, documentation, capability fixtures, and positive/negative tests together.
3. Use disposable temporary workspaces in tests; never mutate a real knowledge base.
4. Run `python -m pytest` and `python -m compileall -q src tests` before handoff.

Do not commit, push, merge, release, deploy, use a browser/model/converter, or admit sensitive corpus material without explicit authorization.
