# Cortex 4 contributor guide

Cortex is an Open Knowledge Format (OKF)-native, single-bundle knowledge workspace. The portable bundle is the durable source of truth; external `.cortex/` state is rebuildable lineage and generated state.

## Architecture contract

- `--workspace` is exactly one OKF bundle root. Do not introduce a workspace manifest, nested bundle registry, active pointer, or compatibility adapter.
- Store locks, artifacts, journals, staging trees, backups, and indexes at `<bundle-parent>/.cortex/b-<full-path-identity>/`; never write `.cortex` into the portable bundle.
- Keep every mutation `plan -> preclaim checks -> durable claim -> stage -> full validate -> two-directory publish -> receipt`.
- Never let an LLM invent names, paths, complete tags, policy, plans, or replacement text. It may select only unresolved typed candidates supplied by Cortex.
- Use the shared deterministic filename, bounded root-level Markdown link classifier, native-path, and capacity-preflight contracts.
- Preserve strict closure for the supported link dialect. Incoming drafts may be sanitized only after explicit `--sanitize-links`; the source is immutable and Proposal2 records every byte-span replacement. Unsupported or ambiguous container syntax must fail before Proposal2 or Plan2 and must never be guessed through.
- Direct TagSchema2 updates are allowed only when all existing references remain valid. They do not silently retag or copy the bundle.

## Public interface

The method is `cortex-okf-workspace-v4` / `4.0.0`. The only public routes are:

- `build.ingest`
- `manage.init`, `manage.status`, `manage.config`, `manage.validate`, `manage.index`
- `manage.repair`, `manage.rename`, `manage.retag`

The closed public registry contains exactly twelve schemas declared in `src/cortex/constants.py`. JSON mode emits exactly one ResultEnvelope. Status and exit code must agree. Artifact operands accept content-addressed IDs only.

Discoverable user workflows are limited to `cortex:wiki-build` and `cortex:wiki-manage`.

## Ingest contract

- A draft is strict UTF-8 Markdown with exactly `type`, `title`, `description`, `tags`, and `timestamp`; type/description are blank and tags is empty.
- Ingest is `Context2 -> Proposal2 -> Plan2 -> exact apply`. Context and proposal generation never mutate the bundle.
- Canonical output is `references/<identifier>-<normalized-title>-<YYYYMMDD>.md`, with deterministic five-field frontmatter and complete ordered tags.
- Conflicts return the complete set of content-addressed Conflict2 artifacts and whole-file diffs. Replacement requires that exact current set.
- `--proposal -`, `manage init --tag-schema -`, and `manage config set --file -` accept one strict UTF-8 JSON stdin consumer. Do not create caller-managed `.tmp-*` files or a scratch subsystem.

## Development workflow

1. Preserve unrelated and pre-existing dirty changes.
2. Update schemas, constants, deterministic code, both skills, and positive/negative tests together.
3. Test stale digests, collision, lock, traversal, path capacity, interruption, partial apply, exact conflict sets, stdin, and sanitization lineage.
4. Run `python -m pytest` and `python -m compileall -q src tests` before handoff.

Do not commit, push, merge, release, deploy, mutate a real vault, or admit sensitive corpus material without explicit authorization.
