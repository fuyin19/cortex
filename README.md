# Cortex Record KB 8.1.1

Cortex 8.1.1 is a small, single-writer record KB using Record 1, Tag 2, Layout 5, and exact Registry v1 or identity-bearing `cortex-kb-registry/v2`. New Registry roots use v2; ordinary operations do not migrate v1. A Bundle contains `profiles/` and nonempty tag-named partitions; each partition directly contains canonical record units. Layout 3 and Layout 4 are rejected by normal runtime operation.

Layout 5 uses `partition_tag_group`, exact `partition_name_strategy: tag`, `unit_name_strategy: tag-title-date`, component limit 16..200, and duplicate rejection. Each record has Cortex-private `record.json` plus the base envelope owned by anti-entropy Core. Cortex retains record/profile/naming rules but does not vendor or fall back to a local envelope implementation. An empty Bundle may temporarily have a null group; add requires exactly one selected tag and derives both `<partition>/<tag-title-date-unit>` without new naming arguments.

Record edit/show/delete require separate exact operands:

```text
export CORTEX_PYTHON=/absolute/path/to/python3.11
# Optional override for a Core skill installed under another root:
# export ANTI_ENTROPY_CORE_RUNNER=/absolute/path/to/knowledge_unit_runner.py
"$CORTEX_PYTHON" -I <CORTEX-SKILL>/scripts/kb/run_cortex.py --json --workspace <bundle> record show --partition <tag> --record <unit>
"$CORTEX_PYTHON" -I <CORTEX-SKILL>/scripts/kb/run_cortex.py --json --workspace <bundle> record delete --partition <tag> --record <unit> --expected-tree-sha256 <lowercase64>
```

`CORTEX_PYTHON` is mandatory and must name the same ordinary, non-reparse Python 3.11/UCD 14 executable that runs the skill-local launcher. The installed launcher binds the sibling `anti-entropy-core` skill at `<cortex-skill-parent>/anti-entropy-core/scripts/knowledge_unit_runner.py`. An explicit `ANTI_ENTROPY_CORE_RUNNER` absolute path overrides that default; a present empty, relative, missing, linked/reparse, or nonregular value fails without fallback. Core-dependent operations preflight ABI `anti-entropy-core.runner/v1` and exact Core version `1.2.1` within 30 seconds before business writes, then retain that runner for the operation. Update Core and the consumer to their matching releases if this check fails. Direct source/library use requires the explicit runner setting; it does not infer installation roots. Windows may invoke the byte-identical `scripts\kb\run_cortex.cmd` convenience launcher after setting the same variables. There is no PATH, install, network, or alternate-runtime fallback. Human stdout/stderr is UTF-8; `--json` retains the existing compact ASCII-escaped Result encoding.

The explicit-only skill taxonomy separates responsibilities without changing the KB CLI. The `cortex` skill selects exactly one internal role and carries three private runtime adapters:

- Internal role `kb.ingest` owns `record.add` and the sequential batch wrapper, preserving exact v1 syntax and adding exact v2 source/conversion/both syntax.
- Internal role `kb.build` owns `manage.init`, `manage.config.set`, and `registry.set` only. It requires one explicit active build session and keyed-monotonic profile/Registry expansion.
- Internal role `kb.manage` owns `align.plan`/`align.apply`, reads and validation,
  plus exact `record.show`, `record.edit`, and `record.delete` only. Alignment
  applies only Core-supported Envelope repair and creates no recovery system.

All KB roles use the same private Cortex 8.1.1 wheel and one selected Core runner for non-init work; ownership is enforced by the skill contract, not by removing CLI routes.

### Collaborative Workspace 1.1.2

The internal `collaborative-workspace` role uses its own deterministic offline adapter. It prepares the fixed outer Collaborative Workspace and nested Agent Workbench contracts without importing Record KB or Notes:

```text
<Collaborative Workspace>/
├── AGENTS.md
├── CLAUDE.md
├── collaborative-workspace.json
├── ref/
│   └── _outdated/
└── agent-workbench/
    ├── AGENTS.md
    ├── CLAUDE.md
    ├── ref/
    │   ├── .agent-workbench.json
    │   └── _outdated/
    ├── temp/
    └── output/
```

Invoke `scripts/collaborative-workspace/run_collaborative_workspace.py --json prepare|status|validate --root <absolute-root>` through the exact `CORTEX_PYTHON` binding. Prepare alone also accepts repeatable `--outdate <relative-source-path>`. Core uses the same sibling-skill default, explicit override, and exact version preflight described above. Every converter receives this operation's fixed runner through `ANTI_ENTROPY_CORE_RUNNER`. Prepare additionally requires explicit absolute `FILE_CONVERSION_RUNNER`/`FILE_CONVERSION_CONFIG` and/or `MARKDOWN_CONVERSION_RUNNER`/`MARKDOWN_CONVERSION_CONFIG` when those source routes are present. Those conversion providers require their matching installed `file-processing` support skill. There is no provider lookup or fallback.

Outer `ref/` is human-owned; its required `_outdated/` subtree is excluded from active projection. Prepare otherwise changes outer reference data only for an explicit `--outdate`. Missing or changed sources automatically archive their former prepared KUs in the inner generation history. Nonempty `temp/` returns busy, while `output/` and safe extras are preserved. The runtime does not expose sync, duplicate inference, delete, clean, rebuild, watch, queue, recovery, automatic KB/Notes ingest, or output promotion. See `docs/collaborative-workspace-architecture.md`.

Only the private KB adapter carries `scripts/kb/batch_record_add.py`. The helper is a sequential wrapper around the same verified `record add` route. It preserves exact v1 syntax and adds exact v2 source-only, conversion-only, and both forms through `--job <path|->` (stdin is preferred). It validates every item's syntax before the first runner call, continues after valid non-ok Results, creates no persistent job state, and is not a core/public route.

Cortex Notes 2.1 is a separate, dependency-free runtime in this repository. Its internal roles are `notes.ingest`, `notes.build`, and `notes.manage`, sharing one private offline adapter. Every Bundle contains fixed Note Profile 1, independently validated Tag Profile 2, and closed Layout Profile 1 under `profiles/`; legacy `bundle.json` is rejected. Layout, never Bundle id, selects date or tag-group behavior. Tags grow through whole-profile keyed-monotonic candidates, with skeleton publication before the final atomic profile replacement and deterministic first-failure residue. Notes keeps Markdown and strict `note.json` metadata as its complete source of truth and has no database, index, search service, vector store, UI, network, synchronization, backup, move, restore, or trash layer. See `docs/notes-architecture.md`.

The Notes roles are disjoint: build owns Registry/Bundle initialization and whole Tag 2 set, ingest owns note add, and manage owns reads/validation plus existing-note edit/archive/confirmed-delete. Tools-root admission is conditional; it is never required for reads or existing-note management.

Internal role `kb.build` classifies one explicit target as new, resumed empty configured, resumed empty null-sentinel, or populated. It rejects contraction before writing: existing Tag groups/tags retain order and membership, new keys append, Registry id-to-path mappings remain fixed, and configured layout strategies/group are retained. Populated Bundles accept complete keyed-monotonic Tag 2 candidates while Layout 5 remains byte-identical. A configured maximum increase writes Layout before Tags; a null-sentinel transition always writes Tags before Layout. Execution stops at the first non-ok result without rollback and reports completed steps, the failed step, residue/orphan state, and the unchanged core Result.

Show/delete authorization uses `CORTEX_UNIT_TREE_V2`, which binds partition then unit before the no-follow manifest. Deleting the last unit removes its partition. Registered mutations and authorization share the stable root lock; standalone operations use the Record Profile byte lock.

`tools/migrate_layout.py` is the sole noninstalled, nonpublic migration dispatcher. It preserves source-read-only Layout 3 → Layout 4 plan/build and adds source-read-only Layout 4 → Layout 5 plan/build outside the KB and repository roots on the same volume. Build requires the exact initialized Registry 1 KB root, its derived repository boundary, and the explicit Core runner; omitted or false boundary operands fail closed. It has no cutover command. Core completes the Layout 4 → 5 candidate envelope.

Core distribution and binding are closed for Core 1.2.1. Conversion runtime dependencies remain in the matching installed `file-processing` support skill; Cortex does not vendor them. Converter runner/config bindings remain explicit. A nonzero provider exit retains `provider_conversion_failed`, route, and exit code and adds `provider_stderr_excerpt` plus `provider_stderr_truncated` when stderr is nonempty. Tests use synthetic stages and controlled providers; real provider conversion is outside this gate.

For the regression gate, set `CORTEX_REAL_CORE_RUNNER` to the actual Core Candidate runner before `python -m pytest`; the real-Core integration tests fail if it is absent rather than skipping.
