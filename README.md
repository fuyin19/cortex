# Cortex Record KB 6.0

Cortex is a small, single-writer record knowledge base. A Bundle has `profiles/` and zero or more direct flat record units. Cortex 6.0.0 accepts Record 1, Tag 2, Layout 3, and Registry 1 only; Layout 2 has no runtime fallback.

```text
<bundle>/
  profiles/{record-schema.json,tags.json,layout.json}
  <project-title-date>/
    record.json
    <converter-stem>.md
    <converter-stem>.json
    src/<one-original-source>
    assets/...                         # optional, safe opaque bytes
```

Markdown-only units contain exactly `record.json` and one original-name `.md`. Legacy `original/` and `representations/` wrappers are invalid. Full adds copy conversion children once; `--source` must have the same basename and SHA-256 as the conversion's sole `src/` file. Top-level `record.json` is reserved Cortex metadata and is never accepted from a converter payload.

Layout 3 names are `<exact-selected-tag>-<semantic-title>-<YYYYMMDD>`. Naming requires Python 3.11 and UCD 14.0.0. `unit_name_tag_group: null` is valid only while empty; add returns `validation_error` / `bundle_not_operational` before staging. Duplicates and case-fold collisions are rejected; suffixes do not exist.

Public record operations use one exact safe component:

```text
cortex --json --workspace <bundle> record show --record <unit>
cortex --json --workspace <bundle> record delete --record <unit> --expected-tree-sha256 <lowercase64>
```

Show is an authorization read under the writer lock and returns `tree_sha256`, exact metadata derived from the hashed `record.json`, and the second-pass manifest. Delete recomputes and compares the token, deletes only the authorized manifest leaf-first, stops at first failure, and reports `delete_incomplete` with partial residue data. There is no trash, tombstone, journal, or recovery route.

The noninstalled `tools/migrate_legacy_layout3.py` binds itself to this repository's sibling `src/cortex` 6.0.0 package, without an installation or `PYTHONPATH`. It only plans an observed legacy direct-unit grammar or builds a separate absent output after detached plan-digest approval. It never follows a source link/reparse point, changes source, cuts over, adopts a Registry, installs Cortex, or exposes a product migration route.

Pilot runbook: pin and verify exactly one Cortex 6.0.0 executable/Candidate; run read-only plan against `project-summer`; require exactly 27 units (25 full, 2 Markdown-only) or stop on reproducible drift; approve the detached digest; obtain separate authority for build, cutover, and later Registry adoption.

Verify with `python -m pytest` and `python -m compileall -q src tests tools`.
