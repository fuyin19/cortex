# Cortex 5 record-KB architecture

The durable boundary is one directory containing exactly three profiles and direct nonempty tag partitions. [Global Knowledge](global-knowledge.md) is authoritative for the minimum knowledge unit and basic bundle. Profiles are whole-file configuration, and every unit contains canonical metadata, one byte-preserved original, and an optional opaque conversion namespace.

Writers serialize on the first byte of `profiles/record-schema.json` with a nonblocking operating-system lock. Add preflights its operands, rechecks after taking the lock, derives one partition tag, then stages either a unit sibling in an existing partition or a complete new partition at the bundle root. It validates the unit and publishes with a no-replace rename. Edit and profile updates publish one same-directory temporary file with `os.replace`.

Validation is read-only and reports every issue it can observe. It checks the exact root shape, fixed Record Profile, named Tag Profile groups, Layout Profile linkage, partition-to-tag equality, one partition tag per record, canonical `record.json` bytes, source custody shape, conversion namespace and path safety. Original and conversion file contents are opaque.

The implementation excludes general schema engines, artifact lineage, transaction journals, recovery, indexing, OKF policy, link parsing, repair, rename, and retag workflows.
