
# Cortex KB manage

Use this role only when the user explicitly invokes `cortex` and the router selects KB manage. Generic note, KB, or coding requests are insufficient triggers.

Use this skill for `align.plan`, `align.apply`, `registry.show`, `registry.validate`, `registry.resolve`, `manage.status`, `manage.validate`, `manage.config.show`, and exact `record.show`, `record.edit`, or `record.delete`. Never invoke `manage.init`, `manage.config.set`, `registry.set`, `record.add`, or the batch helper. The embedded runtime remains the complete closed Cortex 8.1 CLI; these ownership boundaries are this skill's contract, not runtime route removal.

## Verified offline runtime

Set `CORTEX_PYTHON` to the lexical absolute path of the intended Python 3.11/UCD 14 executable. The launcher verifies that path is an ordinary non-reparse file reached through ordinary non-reparse ancestors and is the same filesystem entry as `sys.executable`. On POSIX invoke `"$CORTEX_PYTHON" -I <ABSOLUTE-CORTEX-SKILL>/scripts/kb/run_cortex.py`; on Windows use the identical convenience launcher `<ABSOLUTE-CORTEX-SKILL>\scripts\kb\run_cortex.cmd`. First require `--version` to emit exactly `cortex 8.1.0` on stdout and empty stderr. Do not use PATH or fall back to a global command, ambient package, installation, another skill, network, or update.

Set `ANTI_ENTROPY_CORE_RUNNER` to the absolute path to
`anti-entropy-core/scripts/knowledge_unit_runner.py`. Every manage, Registry,
and alignment operation uses that runner and has no local Envelope fallback.

Alignment is owned here because it inspects or updates existing record units.
Use `align plan` with one explicit `--workspace`, inspect the returned plan,
then use `align apply --plan <plan-file>` only when the user approved that
exact plan. Alignment only delegates the minimal Envelope repair supported by
Core; it does not create backup, rollback, receipt, or recovery state.

Require an explicit selector on every operation: `--kb-root` alone for Registry reads; explicit `--workspace`, or explicit `--kb-root` plus `--bundle-id`, for Bundle reads and exact record operations. Never discover or infer a Bundle.

Owned read and validation forms include:

```text
... --json --kb-root <root> registry show
... --json --kb-root <root> registry validate
... --json --kb-root <root> registry resolve --bundle-id <id>
... --json --workspace <bundle> manage status
... --json --workspace <bundle> manage validate
... --json --workspace <bundle> manage config show --profile <record|tags|layout>
... --json --workspace <bundle> align plan
... --json align apply --plan <plan-file>
```

Record edit/show/delete require separate exact safe components:

```text
... --json --workspace <bundle> record edit --partition <exact-tag> --record <exact-unit> --metadata <json>
... --json --workspace <bundle> record show --partition <exact-tag> --record <exact-unit>
... --json --workspace <bundle> record delete --partition <exact-tag> --record <exact-unit> --expected-tree-sha256 <lowercase64>
```

Treat `tree_sha256` as the V2 authorization token binding partition then unit. Never reuse a stale token. Before edit, supply complete metadata and retain title, timestamp, and the selected partition tag exactly; only nonpartition tag membership may change. Delete may remove the partition when its last unit is deleted. Report the core Result honestly, including partial-delete residue.

Do not rename, move, add, batch, search, auto-tag, trash, tombstone, initialize, configure, register, repair, migrate, or cut over. The repository-only adjacent-edge migration dispatcher is not a skill/public capability.
