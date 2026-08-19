# Cortex 5 record-KB architecture

The durable boundary is one directory containing exactly three profiles and the layout-selected records root. Profiles are whole-file configuration, and every record is one flat child directory containing canonical metadata, one byte-preserved original, and an optional opaque conversion namespace.

Writers serialize on the first byte of `profiles/record-schema.json` with a nonblocking operating-system lock. Add preflights its operands, rechecks after taking the lock, assembles one reserved sibling directory, validates it, and publishes with a no-replace rename. Edit and same-root profile updates publish one same-directory temporary file with `os.replace`. An empty-root layout rename publishes the directory first, replaces the layout profile second, and makes an ordinary rollback attempt if the profile replacement fails.

Validation is read-only and reports every issue it can observe. It checks the exact root shape, fixed record schema, tag and layout profiles, flat record directories, canonical `record.json` bytes, source custody shape, conversion namespace and path safety. Original and conversion file contents are opaque.

The implementation excludes general schema engines, artifact lineage, transaction journals, recovery, indexing, OKF policy, link parsing, repair, rename, and retag workflows.
