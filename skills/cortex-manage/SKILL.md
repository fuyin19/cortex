---
name: cortex-manage
description: Inspect and manage Cortex 5.1 registries, Bundles, profiles, and record metadata through the closed CLI.
---

# Manage Cortex

Resolve the installed Cortex application command exactly once. Canonicalize it to one canonical absolute path, require that path to be an available ordinary non-reparse executable, and apply the platform's executable check. Probe the exact quoted path with `--version`; require exit 0, stdout consisting of exactly one `cortex 5.1.0` line, and empty stderr. Reuse that same quoted absolute path for every call below. Stop if any check fails: do not fall back, re-resolve, invoke a bare PATH command, set `PYTHONPATH`, use `python -m`, or try an alternate command.

```text
"<CORTEX-ABSOLUTE-EXECUTABLE>" --version
```

Use Cortex as the only mutation boundary. Do not select a default Bundle or infer one from content. Registry and managed Bundle operations require an explicit KB root; every managed Bundle operation also requires the user's exact Bundle ID.

```text
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> registry show
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> registry validate
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> registry resolve --bundle-id <id>
<complete-registry-json> | "<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> registry set --file -
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage status
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage validate
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage config show --profile record
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage config show --profile tags
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage config show --profile layout
```

Registry set replaces the whole Registry v1 document. Preserve every existing ID/path pair: pairs cannot be removed or reassigned; only descriptions may change and new pairs may be added. Never invent an ID, path, or description. Initialization is deliberately direct and workspace-only, so use it only for an exact path supplied and authorized by the user:

```text
"<CORTEX-ABSOLUTE-EXECUTABLE>" --json --workspace <exact-new-bundle-path> manage init
```

Tag and Layout profiles are Bundle-owned policy. Show the current complete profile before proposing a whole-file replacement. Record Profile 1 is show-only. Tag changes need explicit confirmation and must never create a tag silently. Layout Profile 2 supports `title-slug` (the unchanged default, with `numeric-suffix` or `reject`) and opt-in `partition-title-date` (only with `reject`). The composite strategy uses the exact partition tag, normalized semantic title, and lexical timestamp date, and every partition tag must leave room for both hyphens, eight date digits, and at least one title byte. Layout changes are cross-validated against all units; do not propose a flatten, rename, delete, or migration.

```text
<complete-profile> | "<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> manage config set --profile tags|layout --file -
<complete-metadata> | "<CORTEX-ABSOLUTE-EXECUTABLE>" --json --kb-root <root> --bundle-id <id> record edit --record <partition>/<unit> --metadata -
```

Reads are lock-free. Mutations use Cortex's one selected nonblocking lock and may return `busy/5`. Never write files directly or create another lock, registry, hidden state directory, journal, receipt, recovery state, index, or migration artifact.
