# Cortex OKF 4.0

Cortex turns strict external Markdown drafts into canonical references inside one self-describing Open Knowledge Format bundle. The bundle is the durable truth. Identity-bound locks, artifacts, journals, staging trees, backups and indexes live outside it at `<bundle-parent>/.cortex/b-<identity>/`.

The public method `cortex-okf-workspace-v4` exposes nine routes: reference ingest plus initialize, status, direct tag-schema configuration, validation, external indexing, repair, rename and retag. User workflows are `wiki-build` and `wiki-manage`.

## Draft to reference

A draft contains exactly `type`, `title`, `description`, `tags` and `timestamp`. Type and description are blank, tags is empty, title is nonempty, and timestamp is an ISO date or timezone-aware RFC3339 value. Cortex preserves the timestamp and body unless the user explicitly confirms deterministic link sanitization.

```powershell
cortex --workspace <bundle> build ingest --source <draft.md> --tag <registered-tag> --json
cortex --workspace <bundle> build ingest --plan <mutation-plan-id> --apply --json
```

If a context needs choices, stream its headerless ProposalInput without a scratch file:

```powershell
<proposal-json> | cortex --workspace <bundle> build ingest --context <context-id> --proposal - --json
```

Unresolved incoming links block by default and include exact proposed replacements. After confirmation, repeat proposal planning with `--sanitize-links`. The source remains unchanged and Proposal2 records ordered UTF-8 byte spans and before/after text.

The MVP deliberately supports a bounded Markdown link dialect: single-line root-level inline links and images, wikilinks and embeds, plus root-level reference links and definitions. Clearly delimited code and recognized raw HTML are opaque. Link-like syntax in lists, blockquotes, lazy continuations, container-relative definitions, ambiguous indentation or ambiguous HTML blocks fails closed as `unsupported_markdown_link_context` before Proposal2 or Plan2 publication; sanitization cannot override it. Move such a link to an ordinary root-level paragraph before retrying. Cortex does not claim complete CommonMark parsing.

Canonical output is `references/<identifier>-<normalized-title>-<YYYYMMDD>.md`. Its title equals the filename stem, description is blank, and tags are the selected identifier followed by schema-declared derived tags. Destination conflicts return a complete content-addressed set and whole-file diffs; replacement requires every exact current conflict ID.

## Configuration and transactions

The sole portable policy file is `profiles/tag-schema.json`. A compatible schema update validates every existing reference before producing a plan:

```powershell
<schema-json> | cortex --workspace <bundle> manage config set --file - --json
cortex --workspace <bundle> manage config set --plan <plan-id> --apply --json
```

Every bundle mutation is exact plan/apply: preconditions and path capacity, durable one-use claim, staged full validation, two-directory publication and a VerificationReceipt2. Only exact nonterminal journals resume. Uncertain tree combinations are retained as `recovery_ambiguous`.

`manage index` writes only an external tree-digest-bound derivative. It never changes bundle bytes.

## Development

```powershell
python -m pip install -e .
python -m pytest
python -m compileall -q src tests
```

All JSON commands emit exactly one ResultEnvelope, and status always agrees with process exit code.
