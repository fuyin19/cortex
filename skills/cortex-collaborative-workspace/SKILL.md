---
name: cortex-collaborative-workspace
description: Explicit invocation only for preparing, checking status, or validating one Cortex Collaborative Workspace and its Agent Workbench.
---

# Cortex Collaborative Workspace

Use this skill only when the user explicitly names `cortex-collaborative-workspace`, or explicitly invokes `cortex` and the router selects the Collaborative Workspace domain. Generic workspace, file-processing, KB, note, or coding requests are insufficient triggers.

Own exactly `collaborative_workspace.prepare`, `collaborative_workspace.status`, and `collaborative_workspace.validate`. Invoke the complete skill-local runtime through the absolute `CORTEX_PYTHON` Python 3.11/UCD 14 interpreter with `-I`, using one explicit lexical absolute `--root`:

```text
<runner> --json prepare --root <absolute-collaborative-workspace>
<runner> --json status --root <absolute-collaborative-workspace>
<runner> --json validate --root <absolute-collaborative-workspace>
```

Set `ANTI_ENTROPY_CORE_RUNNER` to the explicit absolute Core runner. Before `prepare`, set the explicit absolute runner and existing config operands required by every source route present: `FILE_CONVERSION_RUNNER` plus `FILE_CONVERSION_CONFIG` for PDF/Office, and `MARKDOWN_CONVERSION_RUNNER` plus `MARKDOWN_CONVERSION_CONFIG` for other supported formats. There is no PATH lookup, provider discovery, or fallback conversion.

`prepare` is the only writer. It creates a missing root, adopts a safe ordinary directory, returns an exact no-op for unchanged source records, or refreshes only `agent-workbench/ref/`. It never writes outer `ref/`, and never clears or modifies `agent-workbench/temp/` or `agent-workbench/output/`. A stale workspace with nonempty `temp/` returns `busy`; do not delete or move those files automatically. Report all returned blockers and warning codes. Do not add delete, clean, force, rebuild, watch, queue, journal, recovery, KB/Notes ingestion, output promotion, or task-lifecycle behavior.
