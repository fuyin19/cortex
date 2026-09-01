# Cortex 8 contributor guide

Cortex is a deliberately small, single-writer record knowledge base. A Bundle directory is complete durable record state; a containing KB root may add only Registry v1 and its stable lock.

`AGENTS.md` is the canonical repository guidance; `CLAUDE.md` remains only the one-line `@AGENTS.md` adapter and must not duplicate policy.

## Architecture contract

- `--workspace` is exactly one Bundle: three files under `profiles/` plus zero or more nonempty direct partition directories. Each partition name is one exact Tag 2 value selected by Layout 5 `partition_tag_group`; each partition directly contains canonical record units.
- Every record unit contains private canonical `record.json` plus the exact base envelope: root `AGENTS.md` and `CLAUDE.md`, one or more same-stem representation files with distinct case-folded extensions, mandatory `assets/`, and mandatory `src/`. Empty support directories contain only zero-byte `.keep`; `src/` otherwise has at most one direct ordinary source file. No legacy wrappers are valid.
- The navigation pair and base Envelope are owned by the explicitly configured anti-entropy Core runner. Cortex does not vendor those resources or fall back to a local implementation; it keeps only record/profile/registry/naming responsibilities.
- Distribution `cortex-record-kb`, import `cortex`, version `8.0.0`; profiles Record 1, Tag 2, Layout 5, Registry 1. Python is exactly 3.11/UCD 14.0.0 for naming. Layout 3 and Layout 4 have no runtime fallback.
- Layout 5 fields are exactly `version`, `partition_tag_group`, `partition_name_strategy: tag`, `unit_name_strategy: tag-title-date`, `max_component_length` (16..200), and `duplicate_name_strategy: reject`. Null partition group is empty-initialization-only.
- Record add derives its partition from exactly one selected group tag. Edit/show/delete require separate exact one-component `--partition` and `--record`; deleting the last unit removes its empty partition.
- Authorization uses a two-pass no-follow inventory and `CORTEX_UNIT_TREE_V2`, binding partition then record unit. Registered authorization and mutations use the same nonblocking exclusive root `.cortex.lock`; standalone operations use the Record Profile byte lock.
- Do not add nested registries/Bundle paths, manifests, indexes, schema registries, artifact stores, product migration/cutover routes, journals, receipts, external recovery state, hidden identity directories, or `.cortex/` directories.
- Results contain exactly `status`, `exit_code`, `command`, `data`, `issues`, with `ok/0`, `usage_error/2`, `validation_error/3`, `busy/5`, `io_error/6`.
- Repository skill taxonomy has one instruction-only non-role router, `cortex`, exactly six canonical KB/Notes roles, and one independent `cortex-collaborative-workspace` domain skill. All eight are explicit-only. The router packages no runtime and selects exactly one skill; KB roles embed Cortex 8, Notes roles embed Cortex Notes 2.0, and the workspace skill embeds the separate `cortex-collaborative-workspace` 1.1 runtime.
- Build sessions are single-target, explicit, and keyed-monotonic. Existing Tag groups/tags and membership plus Registry id-to-path mappings never contract or reassign; populated profiles remain byte-identical. Empty configured and null-sentinel Bundles follow the ordering and first-failure residue rules in `skills/cortex-kb-build/SKILL.md`.
- Adds publish one bounded staged sibling with no-replace rename. Edits and profile/registry replacements use same-directory temporaries and `os.replace`. Delete removes only the authorized manifest and reports partial failure honestly.
- The one repository-only `migrate_layout.py` dispatcher preserves the Layout 3→4 edge and adds Layout 4→5 plan/build. Both are source-read-only, publish only an absent separate candidate, and expose no adoption/cutover. The 4→5 edge preserves every existing record path/byte and adds only the navigation pair and required empty support state. Candidate and staging are forbidden under the initialized KB root, source repo, or KB repo and must be on the source volume.
- `record add --metadata META [--source FILE] [--conversion DIR]` requires at least one payload operand. Source-only retains the source once as the sole root representation and marks both support directories empty. Conversion-only preserves representations and fills missing envelope state. Both fills empty `src/` or requires its retained basename and SHA-256 to equal `--source`. A converter-level root `record.json` collision is rejected at the Cortex boundary.
- `align plan` and `align apply` belong to KB manage. They use the explicit Core runner to inspect or apply only Core-supported Envelope repair and add no backup, receipt, journal, rollback, or recovery state.
- Reject links/reparse points, nonregular entries, unsafe/reserved components, and case-fold collisions. Do not claim protection for ACLs, ownership, hard links, sparse allocation, alternate streams, extended attributes, resource forks, handle identity, or noncooperating external-filesystem TOCTOU races.

## Development workflow

1. Preserve unrelated worktree changes.
2. Update implementation, all repository skills, documentation, capability fixtures, and positive/negative tests together.
3. Use disposable temporary workspaces; never mutate a real KB.
4. Run `python -m pytest`, external-cache `python -m compileall -q src notes_runtime/src collaborative_workspace_runtime/src tests`, and every packaged runtime `--check` before handoff.

## Notes contract

- Distribution `cortex-notes`, import `cortex_notes`, version `2.0.0` is separate from `cortex-record-kb`; do not import one from the other.
- A Notes Bundle contains exactly `profiles/note-schema.json`, `profiles/tags.json`, and `profiles/layout.json`, plus canonical partitions. Note 1 is fixed, Tag 2 is independently validated, and closed Layout 1 drives date or tag-group partition behavior. Legacy `bundle.json` is invalid with no fallback or migration route.
- Notes state is one explicit absolute root containing exact Registry 1, three Bundles, `.notes.lock`, and independent two-file note units. Markdown plus `note.json` is the complete source of truth; do not add a database, index, search, vector, server, UI, network, sync, backup, migration, move, restore, or trash surface.
- Notes mutations are single-writer, no-follow, fail closed, and bind existing-unit changes to a fresh exact tree digest. Tag expansion is keyed-monotonic, publishes canonical skeletons before atomically replacing Tag 2, and reports ordered residue without rollback. Git admission is required only for tools initialization, tools tag addition, and adding notes to Git-admission partitions; stale configured partitions remain read/manage-only.

Do not commit, push, merge, release, deploy, use a browser/model/converter, mutate global skills, or admit sensitive corpus material without explicit authorization.

## Collaborative Workspace contract

- Distribution `cortex-collaborative-workspace`, import `cortex_collaborative_workspace`, version `1.1.0` is isolated from Record KB and Notes. Its closed structured routes are `collaborative_workspace.prepare`, `.status`, and `.validate`, using one explicit absolute `--root`; prepare alone also accepts repeatable `--outdate <relative-source-path>`.
- The outer `collaborative-workspace-envelope/v1` owns fixed navigation, stable UUIDv4 identity, raw human-owned `ref/`, and `agent-workbench/`. The inner `agent-workbench-envelope/v1` owns prepared `ref/`, literal-empty gating `temp/`, and preserved `output/`. Cortex delegates both envelope contracts and every prepared KU validation to the explicit Core runner.
- Prepare is explicit, nonwaiting, and idempotent: missing-root create, safe ordinary-directory adoption, exact no-op, or stale prepared-ref replacement. It snapshots every ordinary source before fixed routing to file-conversion or markdown-conversion, preserves complete basenames, copies valid KUs byte-for-byte, and writes outer `ref/` only to create its fixed `_outdated/` role or fulfill explicit `--outdate`; it never clears temp/output.
- Do not add delete, clean, force/rebuild, watcher, queue, scheduler, provider discovery/registry, journal, receipt, crash recovery, DACL normalization, automatic KB/Notes ingest, output promotion, or task lifecycle state.
