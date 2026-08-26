# Cortex Record KB 8.0

Cortex 8.0.0 is a small, single-writer record KB using Record 1, Tag 2, Layout 5, and Registry 1. A Bundle contains `profiles/` and nonempty tag-named partitions; each partition directly contains canonical record units. Layout 3 and Layout 4 are rejected by normal runtime operation.

Layout 5 uses `partition_tag_group`, exact `partition_name_strategy: tag`, `unit_name_strategy: tag-title-date`, component limit 16..200, and duplicate rejection. Each record has private `record.json` plus the exact knowledge-unit base envelope and independently vendored navigation pair. An empty Bundle may temporarily have a null group; add requires exactly one selected tag and derives both `<partition>/<tag-title-date-unit>` without new naming arguments.

Record edit/show/delete require separate exact operands:

```text
export CORTEX_PYTHON=/absolute/path/to/python3.11
"$CORTEX_PYTHON" -I <SKILL>/scripts/run_cortex.py --json --workspace <bundle> record show --partition <tag> --record <unit>
"$CORTEX_PYTHON" -I <SKILL>/scripts/run_cortex.py --json --workspace <bundle> record delete --partition <tag> --record <unit> --expected-tree-sha256 <lowercase64>
```

`CORTEX_PYTHON` is mandatory and must name the same ordinary, non-reparse Python 3.11/UCD 14 executable that runs the skill-local launcher. Windows may invoke the byte-identical `scripts\run_cortex.cmd` convenience launcher after setting the same absolute variable. There is no PATH, install, network, or alternate-runtime fallback. Human stdout/stderr is UTF-8; `--json` retains the existing compact ASCII-escaped Result encoding.

The explicit-only skill taxonomy separates responsibilities without changing the KB CLI. The instruction-only `cortex` router selects exactly one of six canonical roles and packages no runtime:

- `cortex-kb-ingest` owns `record.add` and the sequential batch wrapper, preserving exact v1 syntax and adding exact v2 source/conversion/both syntax.
- `cortex-kb-build` owns `manage.init`, `manage.config.set`, and `registry.set` only. It requires one explicit active build session and keyed-monotonic profile/Registry expansion.
- `cortex-kb-manage` owns reads and validation plus exact `record.show`, `record.edit`, and `record.delete` only.

Every canonical KB skill embeds the same complete offline Cortex 8 runtime; ownership is enforced by the skill contract, not by removing CLI routes.

Only `cortex-kb-ingest` carries `scripts/batch_record_add.py`. The helper is a sequential wrapper around the same verified `record add` route. It preserves exact v1 syntax and adds exact v2 source-only, conversion-only, and both forms through `--job <path|->` (stdin is preferred). It validates every item's syntax before the first runner call, continues after valid non-ok Results, creates no persistent job state, and is not a core/public route.

Cortex Notes 2.0 is a separate, dependency-free runtime in this repository. Its canonical roles are `cortex-notes-ingest`, `cortex-notes-build`, and `cortex-notes-manage`; each embeds one identical offline runtime. Every Bundle contains fixed Note Profile 1, independently validated Tag Profile 2, and closed Layout Profile 1 under `profiles/`; legacy `bundle.json` is rejected. Layout, never Bundle id, selects date or tag-group behavior. Tags grow through whole-profile keyed-monotonic candidates, with skeleton publication before the final atomic profile replacement and deterministic first-failure residue. Notes keeps Markdown and strict `note.json` metadata as its complete source of truth and has no database, index, search service, vector store, UI, network, synchronization, backup, move, restore, or trash layer. See `docs/notes-architecture.md`.

The Notes roles are disjoint: build owns Registry/Bundle initialization and whole Tag 2 set, ingest owns note add, and manage owns reads/validation plus existing-note edit/archive/confirmed-delete. Tools-root admission is conditional; it is never required for reads or existing-note management.

`cortex-kb-build` classifies one explicit target as new, resumed empty configured, resumed empty null-sentinel, or populated. It rejects contraction before writing: Tag groups/tags and membership, Registry id-to-path mappings, configured layout strategies/group, and populated profile bytes are retained. A configured maximum increase writes Layout before Tags; a null-sentinel transition always writes Tags before Layout. Execution stops at the first non-ok result without rollback and reports completed steps, the failed step, residue/orphan state, and the unchanged core Result.

Show/delete authorization uses `CORTEX_UNIT_TREE_V2`, which binds partition then unit before the no-follow manifest. Deleting the last unit removes its partition. Registered mutations and authorization share the stable root lock; standalone operations use the Record Profile byte lock.

`tools/migrate_layout.py` is the sole noninstalled, nonpublic migration dispatcher. It preserves source-read-only Layout 3 → Layout 4 plan/build and adds source-read-only Layout 4 → Layout 5 plan/build outside the KB and repository roots on the same volume. Build requires the exact initialized Registry 1 KB root and its derived repository boundary; omitted or false boundary operands fail closed. It has no cutover command. The Layout 4 → 5 edge preserves every existing record path and byte while adding only the exact guides and missing empty support state.
