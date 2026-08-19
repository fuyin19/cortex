# Cortex 5.1 architecture

[Global Knowledge](global-knowledge.md) is authoritative for unit, Bundle, Registry, and authority boundaries. A Bundle remains a self-contained three-profile workspace with direct nonempty tag partitions. The optional containing KB root has exactly one canonical registry and one stable zero-byte writer lock; nested registries and nested Bundle paths are rejected.

Registry reads are side-effect-free. `registry set` validates the complete candidate and every target before initial adoption, so a static invalid candidate leaves no lock or registry. Adoption creates `.cortex.lock` without replacement, locks it, revalidates, and creates `registry.json` without replacement. Later replacements take that same nonblocking lock, re-read the registry, enforce immutable ID/path pairs, and publish a same-directory temporary with `os.replace`.

Managed mutations resolve an explicit ID, take the root lock, and resolve and validate again while locked. Direct Bundle calls use the parent root lock whenever present, regardless of registration; otherwise they lock the first byte of `profiles/record-schema.json`. Exactly one lock is selected per mutation. Concurrent adoption with an already-running standalone writer is outside the guarantee. Reads do not lock and may observe either side of an atomic replacement.

Record add stages one unit sibling or one complete partition and publishes with a same-parent no-replace rename. Record edit and Tag/Layout replacement use same-directory temporary files and `os.replace`. Profile and tree policy is revalidated while locked. Record Profile is show-only and fixed to the supported three-field dialect.

The product provides no migration, Bundle publication, rename, delete, move, batch, index, search, journal, receipt, recovery, or hidden-state route. A one-time migration must remain separately authorized external orchestration over exact Cortex calls and disposable inputs; it is not product behavior.
