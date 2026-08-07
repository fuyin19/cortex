# Cortex 4 OKF architecture

## Durable boundary

`--workspace` names one portable OKF bundle. The path identity is derived from the final directory-handle path and binds one external state root. Moving the bundle creates a new identity; old state remains untouched.

The portable root contains `index.md`, `references/` and `profiles/tag-schema.json`. The external state root contains only ownership/identity markers, locks, content-addressed artifacts, journals, staging trees, backups and tree-bound indexes.

## Ingest lineage

```text
external drafts -> Context2 -> Proposal2 -> Conflict2 or Plan2
Plan2 -> durable claim -> stage -> full validation -> park root -> publish
      -> Report2 -> Receipt2 -> terminal Journal2
```

Context2 binds the bundle tree, schema and every source. ProposalInput1 can select only supplied unresolved candidates. Proposal2 freezes complete assignments, canonical publications, link policy and every deterministic body transformation. No caller supplies names, paths, complete tags or replacement text.

## Paths and publication

All filesystem work uses the native seam. Windows identity is handle-derived and extended paths do not depend on the host long-path registry setting. Planning and preclaim checks cover live, stage, backup, artifact, journal and index paths, portable component limits, host limits and same-volume publication.

Publication uses two directory renames. A journal is persisted before every filesystem transition. Recovery recognizes only exact claimed, staged, parked and published configurations. Any unknown combination retains all trees and reports ambiguity.

## Link closure

One bounded classifier/tokenizer serves incoming sanitization, validation, link repair and canonical path updates. It owns single-line root-level inline links/images, wikilinks/embeds and root-level reference links/definitions. Clearly delimited code and recognized raw HTML are opaque. Link-like syntax in list/blockquote containers, lazy continuations, container-relative definitions, ambiguous indentation or ambiguous HTML fails closed as `unsupported_markdown_link_context`; `--sanitize-links` cannot bypass that gate. External URIs, email and fragment-only destinations are unchanged, and the resolver sees all publications in the same batch. This is an explicit MVP dialect, not complete CommonMark parsing.

## Configuration

TagSchema2 defines reference identifier candidates and ordered derived tags. Concept and entity remain explicitly unconfigured. A direct schema transaction is admitted only when every current canonical reference remains valid without implicit content changes.

The public service imports only the Cortex 4 core. Three pre-existing low-level
OKF filesystem helpers remain byte-preserved and non-public; a minimal private
value-type dependency keeps their modules import-coherent without registering
routes, schemas, commands, or skills.
