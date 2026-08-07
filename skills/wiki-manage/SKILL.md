---
name: wiki-manage
description: Initialize, validate, configure, repair, index, rename, or retag one Cortex 4 OKF bundle.
---

# Manage a Cortex 4 wiki

Validation, link repair, rename, and retag use the same bounded root-level Markdown link dialect as ingest. Treat `unsupported_markdown_link_context` as a source-structure error: move the link out of the reported container or ambiguous context; never bypass it with sanitization or direct bundle edits.

Use `cortex --workspace <bundle> ... --json` as the sole mutation boundary. Verify the Cortex 4 method catalog and run `manage status` before work.

Initialize an absent or empty root with a complete TagSchema2, optionally streamed through stdin:

```text
<schema-json> | cortex --workspace <bundle> manage init --tag-schema - --json
cortex --workspace <bundle> manage init --plan <plan-id> --apply --json
```

The native schema is exactly `profiles/tag-schema.json`. Reference may be active; concept and entity remain explicitly unconfigured. The identifier dimension is `user_or_llm` with cardinality 1..1; other dimensions are derived. Tags are globally unique and all matching is exact NFC/trim/casefold.

For a compatible direct schema update:

```text
<schema-json> | cortex --workspace <bundle> manage config set --file - --json
cortex --workspace <bundle> manage config set --plan <plan-id> --apply --json
```

Review the plan and require all existing references to remain valid. Cortex does not copy the bundle, silently retag references, or maintain alternate bundle identities.

Use `manage validate` for full validation, `manage index` for a direct external derived index, and exact plan/apply for `manage repair`, `manage rename`, and `manage retag`. Never edit external state, journals, plans, indexes, or bundle bytes by hand. Do not create caller-managed temporary envelopes.
