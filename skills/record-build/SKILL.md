---
name: record-build
description: Add external source files and optional conversion output to a Cortex 5 record KB.
---

# Build Cortex 5 records

Use the `cortex` command as the only workspace mutation boundary. Confirm `manage status` reports version `5.0.0` and `valid: true`.

The bundle must first be operational: Tag Profile 2 has named groups and Layout Profile 2 names one group in `partition_by`. Prepare strict UTF-8 JSON metadata with nonempty `title`, an ordered unique `tags` array containing only registered names and exactly one tag from that partition group, and optionally a timezone-aware RFC3339 `timestamp`. Stream it directly or use a stable file:

```text
<metadata-json> | cortex --json --workspace <kb> record add --source <file> [--conversion <file-or-dir>] --metadata -
```

Cortex copies the original source bytes under `original/`. A conversion file is copied by basename; a conversion directory contributes its contents, including empty directories, under `representations/markdown-conversion/`. Treat conversion content as opaque and do not rewrite it.

Use the returned two-component `data.record` (`<partition-tag>/<unit-folder>`) as the stable operand for later edits. Cortex alone derives that path. Do not derive a different name, move a record after changing its title, create caller-managed staging files in the KB, or write profile/record bytes directly. See `docs/global-knowledge.md` for the authoritative unit and bundle model.
