# Cortex 6 global knowledge

A standalone `--workspace` is exactly one Bundle. Its root contains only `profiles/` and direct record-unit directories. A registered KB root contains canonical Registry 1, a stable zero-byte `.cortex.lock`, direct-child Bundles, and unrelated ordinary entries ignored by discovery. Direct managed operations re-resolve the selected Bundle under the shared nonblocking writer lock.

Layout 3 fields are exactly `version: 3`, `unit_name_tag_group` (null or nonempty string), `unit_name_strategy: tag-title-date`, strict-integer `max_component_length` from 16 through 200 (default 96), and `duplicate_name_strategy: reject`. Null is empty-initialization-only. Once a record exists, Layout bytes are immutable. Tag replacements must preserve every existing record's exact selected naming tag and derived unit name; unrelated safe Tag 2 changes remain possible.

The date is the lexical date from an aware RFC3339 record timestamp. Title semantics are Python 3.11/UCD 14.0.0: NFC, `strip`, lowercase, separator scanning over whitespace, category Cc, ASCII `<>:"/\\|?*`, and ASCII hyphen; collapse separators, strip `. -`, truncate only the title by whole UTF-8 codepoints, strip again, then join exact tag, title, and YYYYMMDD. The final component is safety/length checked without escaping or device-name prefixing.

The unit tree SHA-256 domain is `CORTEX_UNIT_TREE_V1\0`, then u64be unit-name UTF-8 length and bytes. Root is omitted. Descendant directories/files sort by raw relative POSIX UTF-8 bytes. A directory contributes `0x44 + u64be(path length) + path`; a file contributes `0x46 + u64be(path length) + path + u64be(size) + raw bytes`. Modes and times are excluded. Two no-follow passes must match.

Delete is deliberately not crash atomic. It creates no recovery state and reports partial failure honestly. Cortex does not claim protection for ACLs, hard links, handle identity, or noncooperating external-filesystem TOCTOU races.
