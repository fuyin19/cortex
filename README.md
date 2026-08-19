# Cortex Record KB 5.0

Cortex 5 is a minimal, portable record knowledge base. A workspace has three JSON profiles and direct tag-selected partition directories; there is no manifest, index, hidden identity, external state, artifact registry, plan, journal, or receipt. The authoritative definitions of a minimum knowledge unit and basic bundle are in [Global Knowledge](docs/global-knowledge.md).

```text
<workspace>/
  profiles/
    record-schema.json
    tags.json
    layout.json
  <partition-tag>/
    <title-slug>/
      record.json
      original/<source-basename>
      representations/markdown-conversion/...   # optional
```

## Commands

```powershell
cortex --json --workspace <kb> manage init
cortex --json --workspace <kb> manage status
cortex --json --workspace <kb> manage validate
cortex --json --workspace <kb> manage config show --profile tags
cortex --json --workspace <kb> manage config set --profile tags --file <tags.json-or->
cortex --json --workspace <kb> manage config show --profile layout
cortex --json --workspace <kb> manage config set --profile layout --file <layout.json-or->
cortex --json --workspace <kb> record add --source <file> [--conversion <file-or-dir>] --metadata <json-or->
cortex --json --workspace <kb> record edit --record <partition>/<unit> --metadata <json-or->
```

Add and edit metadata require `title` and `tags`; `timestamp` is optional and defaults to the current UTC time with six fractional digits. Supplied timezone-aware RFC3339 timestamps are stored verbatim. An edit replaces metadata only and never moves the record directory or rewrites custody bytes.

Initialization creates only the profiles and is valid but nonoperational. Replace Tag Profile 2 with nonempty named groups, then set Layout Profile 2 `partition_by` to one of those group names. Each record must contain exactly one tag from that group. Cortex creates the corresponding partition only with its first unit. Unit names use `title-slug`, a 96-byte default component cap, partition-local collision handling, and `numeric-suffix` duplicates by default.

`record add` returns the stable two-component POSIX operand `<partition-tag>/<unit-folder>`. Editing a title does not move the unit, and changing the partition tag is rejected.

Every initialized mutation takes one nonblocking OS lock on `profiles/record-schema.json`. A competing writer receives `busy/5`. Reads never lock or mutate the workspace and do not promise a consistent snapshot during concurrent writes.

## Development

```powershell
python -m pip install -e .
python -m pytest
python -m compileall -q src tests
```
