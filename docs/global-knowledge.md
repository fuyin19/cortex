# Cortex 5 global knowledge

This document defines the minimum knowledge unit, Bundle, KB root, and authority roles.

## Minimum knowledge unit

```text
<unit>/
  record.json
  original/<one-source-file>
  representations/markdown-conversion/...   # optional
```

`record.json` has exactly `title`, `timestamp`, and `tags`. Cortex preserves accepted source and conversion bytes and paths; content is opaque. The unit folder is derived once from title and does not move when metadata changes.

## Bundle

```text
<bundle>/
  profiles/
    record-schema.json
    tags.json
    layout.json
  <partition-tag>/<unit>/
```

There is no mandatory `records/` or `unstructured/` layer. Record Schema 1 is this Bundle's declaration of its metadata grammar. Cortex validates that declaration against its supported dialect, whose current only shape is the three fields above. Future fields require an explicitly selected versioned dialect. Tag Profile 2 owns ordered groups, tag names, and descriptions. Layout Profile 2 owns `partition_by`, component length, and duplicate handling within Cortex's fixed tag-partition and title-slug algorithms. Tag and Layout policy is enforced on every write.

Initialization creates only the profiles and is valid but nonoperational. Configure tags before linking `layout.json.partition_by` to a tag group. Each record then has exactly one tag from that group, and its direct parent equals that tag.

## KB root and Registry v1

```text
<kb-root>/
  registry.json
  .cortex.lock
  <registered-direct-child-bundle>/
```

Canonical `registry.json` is `{version:1,bundles:[{id,path,description}]}`. IDs are stable lowercase kebab-case names. Paths are safe direct children only. Existing ID/path pairs cannot be removed or reassigned; descriptions may change and new pairs may be added. Targets must be valid Bundles. A direct child with complete profiles but no entry is an orphan. Ordinary root files, `.git`, and non-Bundle directories are not discovered or registered.

## Authority roles

- Cortex owns the supported profile grammar, deterministic validation, path derivation, and every public mutation boundary.
- Each Bundle owns its Record, Tag, and Layout profile instances as authoritative local policy within that grammar.
- The KB root Registry owns only stable Bundle-ID-to-direct-child mappings and descriptions; it does not select a Bundle.
- The caller or installed skill supplies an explicit Bundle ID and proposed data. It may orchestrate Cortex calls but never writes durable KB files directly, invents a Bundle path, or silently creates a tag.

A registered root has one stable zero-byte `.cortex.lock` for every mutation across its direct-child Bundles and registry. A direct workspace call whose parent has that lock uses it even when the Bundle is not registered. A standalone Bundle locks the first byte of its Record Profile. Reads remain lock-free.

Cortex has no default Bundle, semantic selection, nested registry, general JSON Schema engine, index, journal, receipt, crash recovery, content-addressed artifact system, rename, move, batch, search, or delete API.
