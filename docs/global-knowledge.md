# Cortex 5 global knowledge

This document is the authoritative definition of Cortex's minimum knowledge unit and basic bundle.

## Minimum knowledge unit

A minimum knowledge unit is one directory containing exactly:

```text
<unit>/
  record.json
  original/<one-source-file>
  representations/markdown-conversion/...   # optional
```

`record.json` has exactly `title`, `timestamp`, and `tags`. `title` is the display name. The unit directory name is generated once from the title when the unit is created and remains stable when metadata changes. Cortex preserves original and conversion file bytes and accepted conversion paths; it does not interpret their content.

## Basic bundle

A basic operational bundle is the workspace root itself:

```text
<bundle>/
  profiles/
    record-schema.json
    tags.json
    layout.json
  <partition-tag>/
    <unit>/
```

There is no mandatory `records/` or `unstructured/` layer. A partition is created only with its first unit and must never remain empty. Every unit has exactly one tag from the group named by `layout.json.partition_by`, and its parent directory is exactly that tag.

The Record Profile fixes the three metadata fields. Tag Profile 2 defines ordered named groups and the globally unique tags within them. Layout Profile 2 links `partition_by` to exactly one existing tag group and fixes tag-based partition names, title-slug unit names, the UTF-8 component limit, and duplicate handling.

Initialization is deliberately transitional: it creates only the three profiles, with `groups: []` and `partition_by: null`. This empty bundle is valid but nonoperational, so records cannot be added. Configure the Tag Profile first, then link the Layout Profile to one group.

## Naming and authority

Cortex derives `<partition-tag>/<unit-folder>` from record metadata. A caller or model supplies metadata, source, and optional conversion operands; it never invents or selects a physical destination. The partition name is the exact tag. Unit naming uses the deterministic title-slug algorithm, a partition-local case-fold collision check, and either numeric suffixes starting at `-2` or rejection. Editing a title or ordinary tag does not move a unit, while changing its partition tag is rejected.

Cortex remains a small single-writer tool. It has one nonblocking workspace lock and operation-owned temporary staging for ordinary atomic publication. It intentionally has no bundle registry or discovery layer, multilevel partitions, automatic relocation, general JSON Schema engine, index, journal, receipt, crash-recovery state, artifact system, or hidden `.cortex/` directory.
