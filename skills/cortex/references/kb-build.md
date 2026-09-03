
# Cortex KB build

Use this role only when the user explicitly invokes `cortex` and the router selects KB build. Generic note, KB, or coding requests are insufficient triggers.

Use this skill only for `manage.init`, `manage.config.set`, and `registry.set`. Never add, edit, show, or delete records, and never use another runtime route as a build step. The embedded runtime remains the complete closed Cortex 8.1 CLI; these ownership boundaries are this skill's contract, not runtime route removal.

## Verified offline runtime

Set `CORTEX_PYTHON` to the lexical absolute path of the intended Python 3.11/UCD 14 executable. The launcher verifies that path is an ordinary non-reparse file reached through ordinary non-reparse ancestors and is the same filesystem entry as `sys.executable`. On POSIX invoke `"$CORTEX_PYTHON" -I <ABSOLUTE-CORTEX-SKILL>/scripts/kb/run_cortex.py`; on Windows use the identical convenience launcher `<ABSOLUTE-CORTEX-SKILL>\scripts\kb\run_cortex.cmd`. First require `--version` to emit exactly `cortex 8.1.1` on stdout and empty stderr. Do not use PATH or fall back to a global command, ambient package, installation, another skill, network, or update.

The installed launcher binds the sibling `anti-entropy-core` skill at `<cortex-skill-parent>/anti-entropy-core/scripts/knowledge_unit_runner.py`. An explicit `ANTI_ENTROPY_CORE_RUNNER` absolute path overrides that default; a present empty, relative, missing, linked/reparse, or nonregular value fails without fallback. Core-dependent operations preflight ABI `anti-entropy-core.runner/v1` and exact Core version `1.2.1` within 30 seconds before business writes, then retain that runner for the operation. Update Core and the consumer to their matching releases if this check fails. Direct source/library use requires the explicit runner setting; it does not infer installation roots.

## One active build session

Require exactly one active build session. Before any write, record the explicit lexical absolute `workspace`; whether this is a new or resumed Bundle; and, when registration is requested, the explicit lexical absolute `kb_root`, exact `bundle_id`, exact direct-child Registry `path`, and complete desired Registry 1 object. Do not discover, infer, switch, merge, or interleave build targets. Finish or abandon the active session before starting another.

Every command and operand is explicit and complete: use `--workspace <bundle>` for `manage.init` and `manage.config.set`, `--kb-root <root>` for `registry.set`, and an explicit ordinary JSON file containing the whole candidate profile or Registry. Never patch a JSON fragment or rely on an ambient selector. Temporary operand files are disposable inputs, not Bundle state.

Classify the explicit workspace before planning writes:

- **new**: the workspace is absent and may receive `manage.init` once;
- **resumed, empty configured**: an initialized valid Bundle has no record partitions and Layout 5 has a nonnull group;
- **resumed, empty null sentinel**: an initialized valid Bundle has no record partitions and Layout 5 has `partition_tag_group: null`;
- **resumed, populated**: a valid Bundle has one or more record partitions.

Do not treat an invalid, partially initialized, linked, nonordinary, or otherwise unclassifiable path as resumable.

## Keyed-monotonic candidate rule

Read current canonical profile and Registry bytes before constructing complete candidates. Reject every contraction or reassignment before the first write.

- Tag 2 is keyed by group `name`, then tag `tag`. Retain every existing group and tag in exact relative order and retain every existing tag's group membership. Append new groups to the `groups` array and new tags to the end of their group's `tags` array. Candidates may add groups or tags and may edit descriptions only; they must not remove, rename, move, reorder, or otherwise replace a keyed member.
- Registry 1 is keyed by bundle `id`. Retain every existing `id` to exact `path` mapping. Candidates may add id-to-path pairs and may edit descriptions only; they must not remove an id or reassign its path.
- For a **populated** Bundle, Tag 2 may change only under the keyed-monotonic rule and Layout 5 must remain byte-identical. The permitted build writes are a complete keyed-monotonic Tag 2 candidate and a keyed-monotonic Registry candidate.
- For **empty configured**, retain the exact `partition_tag_group`, `partition_name_strategy`, `unit_name_strategy`, and `duplicate_name_strategy`; retain Tag 2 under the keyed rule; and keep `max_component_length` the same or increase it within Layout 5 bounds.
- For **empty null sentinel**, retain both naming strategies and `duplicate_name_strategy`; retain Tag 2 under the keyed rule; then change null only to an explicit existing candidate Tag 2 group that contains at least one tag. Keep `max_component_length` the same or increase it within Layout 5 bounds. Null may not remain the final configured state when the session is intended for ingestion.

Compare canonical bytes as well as parsed values wherever byte identity is required. A proposed removal, rename, move, reorder, registry reassignment, strategy change, group change on configured Layout 5, maximum decrease, populated Layout 5 rewrite, or null-to-missing/empty group is a contraction. Report it and perform no write.

## Ordered execution

Plan the complete ordered step list before invoking the first write.

1. For a **new** workspace, invoke `manage.init` first, then continue as the resulting empty null-sentinel case.
2. For a **populated** Bundle, set the complete Tag 2 candidate when it differs; never write Layout 5.
3. For **empty configured** with a `max_component_length` increase, set the complete Layout 5 candidate before the complete Tag 2 candidate, so newly admitted longer keys validate. With the same maximum, set Tag 2 before Layout 5 when a profile write is needed.
4. For **empty null sentinel**, set the complete Tag 2 candidate first, then set Layout 5 from null to the explicit existing candidate group; this tags-before-layout order applies even when the maximum increases.
5. Invoke `registry.set` only after all requested Bundle profile steps succeed.

Omit a profile or Registry write when its complete candidate bytes already equal the current canonical bytes. Each invocation uses `--json`; accept only one well-formed core Result with matching command and exit code.

At the first non-`ok` Result or bootstrap/non-Result failure, stop immediately. Perform no later write, delete, cleanup mutation, rollback, compensating action, or retry. Report exactly `completed_steps` in order, the exact `failed_step`, `residual_path` for any workspace or operand residue that remains (otherwise null), whether the Bundle is an `orphan` relative to the requested Registry (true/false), and `result` containing the unchanged core Result when one was emitted (otherwise null). A late `registry.set` failure therefore leaves a successfully initialized/configured Bundle in place and reports it as an orphan; never delete it.

Owned command forms are:

```text
... --json --workspace <bundle> manage init
... --json --workspace <bundle> manage config set --profile tags --file <complete-tags-json>
... --json --workspace <bundle> manage config set --profile layout --file <complete-layout-json>
... --json --kb-root <root> registry set --file <complete-registry-json>
```

Do not add persistent session files, manifests, journals, receipts, or recovery state.
