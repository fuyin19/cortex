---
name: cortex-manage
description: Inspect and manage Cortex 5.1 registries, Bundles, profiles, and record metadata through the closed CLI.
---

# Manage Cortex

Use Cortex as the only mutation boundary. Do not select a default Bundle or infer one from content. Registry and managed Bundle operations require an explicit KB root; every managed Bundle operation also requires the user's exact Bundle ID.

```text
cortex --json --kb-root <root> registry show
cortex --json --kb-root <root> registry validate
cortex --json --kb-root <root> registry resolve --bundle-id <id>
<complete-registry-json> | cortex --json --kb-root <root> registry set --file -
cortex --json --kb-root <root> --bundle-id <id> manage status
cortex --json --kb-root <root> --bundle-id <id> manage validate
cortex --json --kb-root <root> --bundle-id <id> manage config show --profile record
cortex --json --kb-root <root> --bundle-id <id> manage config show --profile tags
cortex --json --kb-root <root> --bundle-id <id> manage config show --profile layout
```

Registry set replaces the whole Registry v1 document. Preserve every existing ID/path pair: pairs cannot be removed or reassigned; only descriptions may change and new pairs may be added. Never invent an ID, path, or description. Initialization is deliberately direct and workspace-only, so use it only for an exact path supplied and authorized by the user:

```text
cortex --json --workspace <exact-new-bundle-path> manage init
```

Tag and Layout profiles are Bundle-owned policy. Show the current complete profile before proposing a whole-file replacement. Record Profile 1 is show-only. Tag changes need explicit confirmation and must never create a tag silently. Layout changes remain constrained to the supported tag-partition/title-slug dialect and are cross-validated against all units.

```text
<complete-profile> | cortex --json --kb-root <root> --bundle-id <id> manage config set --profile tags|layout --file -
<complete-metadata> | cortex --json --kb-root <root> --bundle-id <id> record edit --record <partition>/<unit> --metadata -
```

Reads are lock-free. Mutations use Cortex's one selected nonblocking lock and may return `busy/5`. Never write files directly or create another lock, registry, hidden state directory, journal, receipt, recovery state, index, or migration artifact.
