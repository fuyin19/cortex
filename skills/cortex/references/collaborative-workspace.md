
# Cortex Collaborative Workspace

Use this role only when the user explicitly invokes `cortex` and the router selects the Collaborative Workspace domain. Generic workspace, file-processing, KB, note, or coding requests are insufficient triggers.

Own exactly `collaborative_workspace.prepare`, `collaborative_workspace.status`, and `collaborative_workspace.validate`. Invoke the complete skill-local runtime through the absolute `CORTEX_PYTHON` Python 3.11/UCD 14 interpreter with `-I`, using one explicit lexical absolute `--root`:

```text
"$CORTEX_PYTHON" -I <ABSOLUTE-CORTEX-SKILL>/scripts/collaborative-workspace/run_collaborative_workspace.py --json prepare --root <absolute-collaborative-workspace>
"$CORTEX_PYTHON" -I <ABSOLUTE-CORTEX-SKILL>/scripts/collaborative-workspace/run_collaborative_workspace.py --json prepare --root <absolute-collaborative-workspace> --outdate <relative-source-path> [--outdate <relative-source-path> ...]
"$CORTEX_PYTHON" -I <ABSOLUTE-CORTEX-SKILL>/scripts/collaborative-workspace/run_collaborative_workspace.py --json status --root <absolute-collaborative-workspace>
"$CORTEX_PYTHON" -I <ABSOLUTE-CORTEX-SKILL>/scripts/collaborative-workspace/run_collaborative_workspace.py --json validate --root <absolute-collaborative-workspace>
```

Set `ANTI_ENTROPY_CORE_RUNNER` to the explicit absolute Core runner. Before `prepare`, set the explicit absolute runner and existing config operands required by every source route present: `FILE_CONVERSION_RUNNER` plus `FILE_CONVERSION_CONFIG` for PDF/Office, and `MARKDOWN_CONVERSION_RUNNER` plus `MARKDOWN_CONVERSION_CONFIG` for other supported formats. There is no PATH lookup, provider discovery, or fallback conversion.

`prepare` is the only writer. It creates a missing root, adopts a safe ordinary directory, returns an exact no-op for unchanged source records, or refreshes only `agent-workbench/ref/`. Both reference roles have `_outdated/`: outer history is human-owned and excluded from active projection; inner history is strict, generation-batched, and system-owned. Missing or changed active sources retire their old prepared KUs automatically. Use `--outdate` only for an exact unchanged active source the user explicitly asked to retire; this is the sole operation that moves raw outer references. Prepare never clears or modifies `agent-workbench/temp/` or `agent-workbench/output/`. A stale workspace with nonempty `temp/` returns `busy`; do not delete or move those files automatically. Report all returned blockers and warning codes. Do not infer duplicate retirement, and do not add sync, delete, clean, force, rebuild, watch, queue, journal, recovery, KB/Notes ingestion, output promotion, or task-lifecycle behavior.
