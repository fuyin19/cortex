---
name: wiki-build
description: Ingest external Markdown drafts as canonical references in one Cortex 4 OKF bundle.
---

# Build a Cortex 4 wiki

Use `cortex --workspace <bundle> ... --json` as the only mutation boundary. Verify `manage status --kind method` reports `cortex-okf-workspace-v4` and the `build.ingest.*` v2 features, then run `manage validate`.

Create a context with one or more stable source paths:

```text
cortex --workspace <bundle> build ingest --source <draft.md> [--source <draft2.md>] [--tag <registered-tag>] --json
```

The draft must have exactly five fields: blank `type`, generated nonempty `title`, blank `description`, empty `tags`, and an ISO date or timezone-aware RFC3339 `timestamp`. Cortex never edits the draft.

Keep the draft title to its semantic document title. Do not pre-add the selected `project-*` identifier or the final `YYYYMMDD`; publication adds those deterministic path components.

If Context2 contains unresolved candidates, choose only from those candidates, construct exactly `{"context_id":"ingest-context@...","items":[...]}` in memory, and stream it without a scratch file:

```text
<proposal-json> | cortex --workspace <bundle> build ingest --context <context-id> --proposal - --json
```

If incoming links do not close, show every proposed deterministic replacement and ask the user once whether to sanitize all of them. Only after confirmation repeat the same context/proposal command with `--sanitize-links`. This changes publication bodies only; Proposal2 records the exact before/after byte spans.

The MVP accepts links only in ordinary root-level paragraphs: single-line inline links/images, wikilinks/embeds, and root-level reference links/definitions. Clearly delimited code and recognized raw HTML are opaque. If Cortex returns `unsupported_markdown_link_context`, do not retry with sanitization: move the link out of its list, blockquote, lazy continuation, container-relative definition, ambiguous indentation, or ambiguous HTML context, then create the proposal again. No Proposal2 or Plan2 is published for that failure.

Review the complete plan or conflict set. For approved conflicts repeat every exact `--replace-conflict <id>`. Apply only the returned plan ID:

```text
cortex --workspace <bundle> build ingest --plan <plan-id> --apply --json
```

If apply returns `publication_access_blocked`, close applications or processes holding the bundle or its transaction directories, then retry that exact same plan ID. Never create a new plan, edit the journal, or move transaction directories manually.

Require VerificationReceipt2 and finish with `manage index`; apply already supplies the full validation proof, so do not add a redundant post-apply `manage validate`. Parse artifact IDs directly from `data.artifact_id`; never persist ResultEnvelopes as `.tmp-*`, invent tags or suffixes, patch frontmatter, or write the bundle directly.

Do not re-ingest existing canonical references merely to clean duplicated identifier prefixes or terminal dates. That is an in-bundle maintenance operation: hand it to `cortex:wiki-manage` and use `manage repair --phase reference-names` so the exact batch move, metadata synchronization, and link rewrites share one plan and receipt.
