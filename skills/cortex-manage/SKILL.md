---
name: cortex-manage
description: Deprecated compatibility alias for Cortex management; invoke explicitly only and use cortex-kb-manage for new work.
disable-model-invocation: true
---

# Cortex manage

Set `CORTEX_PYTHON` to the lexical absolute path of the intended Python 3.11/UCD 14 executable. The launcher verifies that path is an ordinary non-reparse file reached through ordinary non-reparse ancestors and is the same filesystem entry as `sys.executable`. On POSIX invoke `"$CORTEX_PYTHON" -I <ABSOLUTE-SKILL>/scripts/run_cortex.py`; on Windows use the identical convenience launcher `<ABSOLUTE-SKILL>\scripts\run_cortex.cmd`. Require `--version` to emit exactly `cortex 7.0.0` and empty stderr. Do not use PATH or fall back to a global command, ambient package, installation, sibling skill, network, or update.

Record edit/show/delete require separate exact safe components:

```text
... record edit --partition <exact-tag> --record <exact-unit> --metadata <json>
... record show --partition <exact-tag> --record <exact-unit>
... record delete --partition <exact-tag> --record <exact-unit> --expected-tree-sha256 <lowercase64>
```

Treat `tree_sha256` as the V2 authorization token binding partition then unit. Never reuse a stale token. Delete may remove the partition when its last unit is deleted. Do not rename, move, batch, search, auto-tag, trash, tombstone, repair, migrate, or cut over.

The repository-only Layout3→4 utility is not a skill/public capability. Its `ibd-projects` gate is exactly 30 partitions, 395 total, 25 full, 370 Markdown-only with exact unit-name, record-byte, payload-byte, and relative-path preservation. Gate A candidate build and Gate B cutover remain separate external approvals.
