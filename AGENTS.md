# Cortex 7 contributor guide

Cortex is a deliberately small, single-writer record knowledge base. A Bundle directory is complete durable record state; a containing KB root may add only Registry v1 and its stable lock.

## Architecture contract

- `--workspace` is exactly one Bundle: three files under `profiles/` plus zero or more nonempty direct partition directories. Each partition name is one exact Tag 2 value selected by Layout 4 `partition_tag_group`; each partition directly contains unchanged canonical record units.
- Full units contain canonical `record.json`, one converter-produced Markdown/JSON same-stem pair, `src/` with one matching source, and optional opaque `assets/`. Markdown-only units contain only `record.json` and one original-name Markdown file. No legacy wrappers are valid.
- Distribution `cortex-record-kb`, import `cortex`, version `7.0.0`; profiles Record 1, Tag 2, Layout 4, Registry 1. Python is exactly 3.11/UCD 14.0.0 for naming. Layout 3 has no runtime fallback.
- Layout 4 fields are exactly `version`, `partition_tag_group`, `partition_name_strategy: tag`, `unit_name_strategy: tag-title-date`, `max_component_length` (16..200), and `duplicate_name_strategy: reject`. Null partition group is empty-initialization-only.
- Record add derives its partition from exactly one selected group tag. Edit/show/delete require separate exact one-component `--partition` and `--record`; deleting the last unit removes its empty partition.
- Authorization uses a two-pass no-follow inventory and `CORTEX_UNIT_TREE_V2`, binding partition then record unit. Registered authorization and mutations use the same nonblocking exclusive root `.cortex.lock`; standalone operations use the Record Profile byte lock.
- Do not add nested registries/Bundle paths, manifests, indexes, schema registries, artifact stores, product migration/cutover routes, journals, receipts, external recovery state, hidden identity directories, or `.cortex/` directories.
- Results contain exactly `status`, `exit_code`, `command`, `data`, `issues`, with `ok/0`, `usage_error/2`, `validation_error/3`, `busy/5`, `io_error/6`.
- Repository skill taxonomy is canonical `cortex-kb-ingest` (record add and exact-v1 batch), `cortex-kb-build` (init, profile set, Registry set), and `cortex-kb-manage` (reads/validation and exact record show/edit/delete). `cortex-build` and `cortex-manage` are explicit-only deprecated compatibility aliases for ingest and manage respectively. Ownership is a skill contract; every skill still embeds the complete closed Cortex 7 runtime.
- Build sessions are single-target, explicit, and keyed-monotonic. Existing Tag groups/tags and membership plus Registry id-to-path mappings never contract or reassign; populated profiles remain byte-identical. Empty configured and null-sentinel Bundles follow the ordering and first-failure residue rules in `skills/cortex-kb-build/SKILL.md`.
- Adds publish one bounded staged sibling with no-replace rename. Edits and profile/registry replacements use same-directory temporaries and `os.replace`. Delete removes only the authorized manifest and reports partial failure honestly.
- The one repository-only migration tool is Layout 3 to Layout 4 plan/build only: source-read-only, absent separate candidate, exact byte/path preservation, no source mutation/adoption/cutover. Candidate and staging are forbidden under the initialized KB root, source repo, or KB repo and must be on the source volume.
- Reject links/reparse points, nonregular entries, unsafe/reserved components, and case-fold collisions. Do not claim protection for ACLs, ownership, hard links, sparse allocation, alternate streams, extended attributes, resource forks, handle identity, or noncooperating external-filesystem TOCTOU races.

## Development workflow

1. Preserve unrelated worktree changes.
2. Update implementation, all repository skills, documentation, capability fixtures, and positive/negative tests together.
3. Use disposable temporary workspaces; never mutate a real KB.
4. Run `python -m pytest`, external-cache `python -m compileall -q src tests`, and packaged runtime `--check` before handoff.

Do not commit, push, merge, release, deploy, use a browser/model/converter, mutate global skills, or admit sensitive corpus material without explicit authorization.
