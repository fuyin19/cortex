
# Cortex Notes manage

Use this role only when the user explicitly invokes `cortex` and the router selects Notes manage. Generic note, KB, or coding requests are insufficient triggers.

Own `notes.registry.show`, `notes.registry.resolve`, `notes.registry.validate`, `notes.bundle.show`, `notes.bundle.resolve`, `notes.bundle.validate`, `notes.bundle.config.show`, `notes.note.list`, `notes.note.show`, `notes.note.edit`, `notes.note.archive`, and `notes.note.delete`. Never initialize storage, set profiles, or add notes. Read and validate Note 1, Tag 2, and Layout 1, then dispatch solely from those profiles. Never dispatch from a Bundle id and never use a tools root for reads or existing-note management.

Invoke the complete skill-local runtime through the absolute `CORTEX_PYTHON` Python 3.11/UCD 14 interpreter with `-I`. Existing-note mutations require the fresh lowercase `tree_sha256` returned for the same canonical root, bundle, partition, note id, archive state, and bytes. Edit changes `note.md` only. Archive moves the whole unit once within its partition; there is no restore or move operation.

Hard delete is irreversible. Before calling `notes.note.delete`, show the exact note coordinates and ask for a natural-language confirmation. Call the runtime exactly once only after an unambiguous affirmative response for the unchanged target and digest, passing `--confirmed yes`; otherwise make zero delete calls. Report any exact partial residue from a non-`ok` Result and do not create recovery artifacts.
