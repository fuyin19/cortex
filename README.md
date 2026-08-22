# Cortex Record KB 7.0

Cortex 7.0.0 is a small, single-writer record KB using Record 1, Tag 2, Layout 4, and Registry 1. A Bundle contains `profiles/` and nonempty tag-named partitions; each partition directly contains canonical record units. Layout 3 is rejected by normal runtime operation.

Layout 4 uses `partition_tag_group`, exact `partition_name_strategy: tag`, `unit_name_strategy: tag-title-date`, component limit 16..200, and duplicate rejection. An empty Bundle may temporarily have a null group; add requires exactly one selected tag and derives both `<partition>/<tag-title-date-unit>` without new CLI arguments.

Record edit/show/delete require separate exact operands:

```text
export CORTEX_PYTHON=/absolute/path/to/python3.11
"$CORTEX_PYTHON" -I <SKILL>/scripts/run_cortex.py --json --workspace <bundle> record show --partition <tag> --record <unit>
"$CORTEX_PYTHON" -I <SKILL>/scripts/run_cortex.py --json --workspace <bundle> record delete --partition <tag> --record <unit> --expected-tree-sha256 <lowercase64>
```

`CORTEX_PYTHON` is mandatory and must name the same ordinary, non-reparse Python 3.11/UCD 14 executable that runs the skill-local launcher. Windows may invoke the byte-identical `scripts\run_cortex.cmd` convenience launcher after setting the same absolute variable. There is no PATH, install, network, or alternate-runtime fallback. Human stdout/stderr is UTF-8; `--json` retains the existing compact ASCII-escaped Result encoding.

`cortex-build` alone also carries `scripts/batch_record_add.py`, a sequential wrapper around the same verified `record add` route. It accepts exact v1 jobs from `--job <path|->` (stdin is preferred), validates every item before the first runner call, continues after valid non-ok Results, creates no persistent job state, and is not a core/public route.

Show/delete authorization uses `CORTEX_UNIT_TREE_V2`, which binds partition then unit before the no-follow manifest. Deleting the last unit removes its partition. Registered mutations and authorization share the stable root lock; standalone operations use the Record Profile byte lock.

`tools/migrate_legacy_layout3.py` is noninstalled and nonpublic. It only plans/builds a source-read-only Layout 3 → Layout 4 candidate outside the KB and repository roots on the same volume. Planning requires canonical Record 1, Tag 2, and Layout 3 bytes and exact Layout 3 unit names. Build requires the exact initialized Registry 1 KB root and its derived repository boundary; omitted or false boundary operands fail closed. It has no cutover command. The `ibd-projects` acceptance gate is exactly 30 partitions, 395 units, 25 full, and 370 Markdown-only, preserving unit names, `record.json` bytes, payload bytes, and relative paths.
