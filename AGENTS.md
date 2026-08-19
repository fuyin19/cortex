# Cortex 5 contributor guide

Cortex is a deliberately small, single-writer record knowledge base. A Bundle directory is complete durable record state; a containing KB root may add only Registry v1 and its stable lock.

## Architecture contract

- `--workspace` is exactly one Bundle: three files under `profiles/` and direct nonempty partitions selected through Layout-linked tags. A registered KB root contains only canonical `registry.json`, zero-byte `.cortex.lock`, direct-child Bundles, and unrelated ordinary root entries ignored by registry discovery. See `docs/global-knowledge.md`.
- Do not add nested registries or Bundle paths, manifests, indexes, schema registries, artifact stores, plans, journals, receipts, external recovery state, hidden identity directories, or `.cortex/` directories.
- Distribution `cortex-record-kb`, command/package `cortex`, version `5.1.0`; profile versions Record 1, Tag 2, Layout 2, Registry 1.
- Public routes are only the four registry routes, the seven existing Bundle routes, and `record` profile show through config show. No product migration, rename, delete, move, batch, search, auto-selection, or auto-tag route.
- Results contain exactly `status`, `exit_code`, `command`, `data`, and `issues` with pairs `ok/0`, `usage_error/2`, `validation_error/3`, `busy/5`, `io_error/6`.
- Profiles, registry, and records use explicit deterministic validators, not a general JSON Schema engine.
- An initialized registered root uses only the nonblocking exclusive OS lock on `.cortex.lock` for registry and all child-Bundle mutations. Direct calls to any child use that lock. Standalone Bundle mutations use only the Record Profile byte lock. Reads never lock or write.
- Initial registry adoption validates before writing, creates the stable lock without replacement, then revalidates and creates the registry without replacement. No bootstrap slot, second lock, journal, receipt, or crash-recovery protocol is permitted.
- Record adds publish one staged sibling or complete partition with same-parent no-replace rename. Record edits and registry/profile replacements use same-directory temporaries and `os.replace`.
- Preserve source/conversion bytes and accepted paths. Reject links, reparse points, nonregular entries, unsafe components, reserved `.cortex-*` names, and conversion case-fold collisions. Content is opaque.
- Do not claim protection for ACLs, ownership, hard links, sparse allocation, alternate data streams, extended attributes, resource forks, handle identity, or TOCTOU races.

## Development workflow

1. Preserve unrelated worktree changes.
2. Update implementation, both skills, documentation, capability fixtures, and positive/negative tests together.
3. Use disposable temporary workspaces in tests; never mutate a real knowledge base.
4. Run `python -m pytest` and `python -m compileall -q src tests` before handoff.

Do not commit, push, merge, release, deploy, use a browser/model/converter, mutate global skills, or admit sensitive corpus material without explicit authorization.
