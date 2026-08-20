# Cortex 6 contributor guide

Cortex is a deliberately small, single-writer record knowledge base. A Bundle directory is complete durable record state; a containing KB root may add only Registry v1 and its stable lock.

## Architecture contract

- `--workspace` is exactly one Bundle: three files under `profiles/` and zero or more direct record-unit directories. A registered KB root contains only canonical `registry.json`, zero-byte `.cortex.lock`, direct-child Bundles, and unrelated ordinary root entries ignored by registry discovery. See `docs/global-knowledge.md`.
- Direct record units use Layout Profile 3. A full unit contains canonical `record.json`, exactly one converter-produced Markdown/JSON same-stem pair, `src/` containing exactly one matching source, and optional safe opaque `assets/`. A Markdown-only unit contains only `record.json` and one original-name Markdown file. Legacy partitions, `original/`, and `representations/` wrappers are invalid.
- Do not add nested registries or Bundle paths, manifests, indexes, schema registries, artifact stores, product migration routes, journals, receipts, external recovery state, hidden identity directories, or `.cortex/` directories.
- Distribution `cortex-record-kb`, import package `cortex`, version `6.0.0`; profile versions Record 1, Tag 2, Layout 3, Registry 1. Agent execution uses the complete skill-local runtime with absolute Python 3.11 and `-I`; the distribution installs no global `cortex` command. Normal v6 operation rejects Layout 2 without fallback.
- Public routes include the four registry routes, existing Bundle/config routes, exact one-component `record show`, exact preconditioned `record delete`, record add/edit, and profile show. No product migration, rename, move, batch, search, auto-selection, auto-tag, trash, tombstone, or repair route.
- Results contain exactly `status`, `exit_code`, `command`, `data`, and `issues` with pairs `ok/0`, `usage_error/2`, `validation_error/3`, `busy/5`, `io_error/6`.
- Profiles, registry, and records use explicit deterministic validators, not a general JSON Schema engine.
- Layout 3 naming operations require Python 3.11 and Unicode database 14.0.0 and fail closed otherwise. `unit_name_tag_group: null` is valid only for an empty Bundle; add and migration reject before staging/output with `validation_error`/`bundle_not_operational` until a nonempty naming group resolves to exactly one Tag 2 value.
- An initialized registered root uses only the nonblocking exclusive OS lock on `.cortex.lock` for registry and all child-Bundle mutations and authorization reads. Direct calls to any child use that lock. Standalone Bundle operations use only the Record Profile byte lock. Other reads never lock or write.
- Initial registry adoption validates before writing, creates the stable lock without replacement, then revalidates and creates the registry without replacement. No bootstrap slot, second lock, journal, receipt, or crash-recovery protocol is permitted.
- Record adds publish one bounded short staged sibling with same-parent no-replace rename. Record edits and registry/profile replacements use same-directory temporaries and `os.replace`.
- `record show` and `record delete` authorize one exact safe unit component from a two-pass, no-follow inventory and the normative unit-tree digest. Delete removes only the authorized manifest, stops on the first failure, and reports partial deletion honestly; it creates no recovery artifact.
- The repository may contain one noninstalled, nonpublic, source-read-only legacy migration planning/build script. It writes only a separate absent destination after plan-digest approval and never performs source mutation, cutover, Registry adoption, or general migration.
- Preserve accepted source/conversion bytes and relative payload paths. Reject links, reparse points, nonregular entries, unsafe components, reserved `.cortex-*` names, and case-fold collisions.
- Do not claim protection for ACLs, ownership, hard links, sparse allocation, alternate data streams, extended attributes, resource forks, handle identity, or noncooperating external-filesystem TOCTOU races.

## Development workflow

1. Preserve unrelated worktree changes.
2. Update implementation, both repository skills, documentation, capability fixtures, and positive/negative tests together.
3. Use disposable temporary workspaces in tests; never mutate a real knowledge base.
4. Run `python -m pytest` and `python -m compileall -q src tests` before handoff.

Do not commit, push, merge, release, deploy, use a browser/model/converter, mutate global skills, or admit sensitive corpus material without explicit authorization.
