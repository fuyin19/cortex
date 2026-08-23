# Cortex Notes 1.0

Cortex Notes is a file-native capture layer parallel to the record KB. It uses an explicit absolute root, one strict Registry, three strict Bundles, and independent note directories containing exactly `note.md` and `note.json`.

- `daily-notes` partitions by the canonical `Asia/Hong_Kong` date (`YYYYMMDD`).
- `tools-feedback` starts with five exact tool-repository partitions and expands monotonically after direct-child Git-repository validation.
- `ideas` has `new-tools-and-functions` and `preliminary-concepts-and-proofs`.

Active notes live directly in a partition; archived notes live under its `archive/`. Edit changes Markdown only, archive is a one-way same-partition move, and confirmed delete is irreversible. Every existing-note mutation requires a fresh digest binding the root identity, selector, state, metadata, and body.

The runtime scans the filesystem directly and stays deliberately small: no database, central index, search, vector store, UI, server, listener, network client, cloud synchronization, Obsidian integration, cross-partition move, restore, trash, or tombstone.
