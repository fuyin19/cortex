
# Cortex Notes build

Use this role only when the user explicitly invokes `cortex` and the router selects Notes build. Generic note, KB, or coding requests are insufficient triggers.

Own only `notes.registry.init`, `notes.bundle.init`, and `notes.bundle.config.set --profile tags`. Never invoke read or validation routes, and never add, inspect, edit, archive, or delete notes. Note 1 and Layout 1 are immutable. Expansion supplies one whole valid Tag 2 candidate; retained groups, tag keys, order, and membership never contract or move, while descriptions may change and new tags append.

Invoke `<ABSOLUTE-CORTEX-SKILL>/scripts/notes/run_notes.py` with the absolute `CORTEX_PYTHON` Python 3.11/UCD 14 interpreter and `-I`. Every root and operand is explicit and absolute. Initialize the Registry once, then initialize exactly `daily-notes`, `tools-feedback`, and `ideas`. Pass an explicit tools root only for tools-Bundle initialization or a tools tag expansion. After initialization, derive every action from the selected Bundle's validated profiles, never its id.

For a Tag 2 set, call `bundle config set --bundle <id> --profile tags --file <absolute-complete-json>`. Stop at the first non-`ok` Result. Report ordered `completed_steps`, `failed_step`, `residual_partitions`, and `profile_updated`; do not roll back, repair by inference, or invoke a removed partition-add route. A retry may resume only canonical empty unregistered skeletons named by its valid candidate.

The runtime is portable, offline, and skill-local. It creates only canonical profiles, partition/archive skeletons, and notes state; it does not initialize Git, install skills, migrate legacy bundles, or manage external plugins.
