---
name: cortex-manage
description: Inspect and manage Cortex 7.0 Bundles, profiles, exact partitioned records, and registries through the closed CLI.
---

# Cortex manage

Use `<ABSOLUTE-PYTHON-3.11> -I <ABSOLUTE-SKILL>/scripts/run_cortex.py`; require `--version` to emit exactly `cortex 7.0.0` and empty stderr. Do not fall back to a global command, ambient package, installation, sibling skill, network, or update.

Record edit/show/delete require separate exact safe components:

```text
... record edit --partition <exact-tag> --record <exact-unit> --metadata <json>
... record show --partition <exact-tag> --record <exact-unit>
... record delete --partition <exact-tag> --record <exact-unit> --expected-tree-sha256 <lowercase64>
```

Treat `tree_sha256` as the V2 authorization token binding partition then unit. Never reuse a stale token. Delete may remove the partition when its last unit is deleted. Do not rename, move, batch, search, auto-tag, trash, tombstone, repair, migrate, or cut over.

The repository-only Layout3→4 utility is not a skill/public capability. Its `ibd-projects` gate is exactly 30 partitions, 395 total, 25 full, 370 Markdown-only with exact unit-name, record-byte, payload-byte, and relative-path preservation. Gate A candidate build and Gate B cutover remain separate external approvals.
