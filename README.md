# Cortex Record KB 5.0

Cortex 5 is a minimal, portable record knowledge base. A workspace has three JSON profiles and one flat records directory; there is no manifest, index, hidden identity, external state, artifact registry, plan, journal, or receipt.

```text
<workspace>/
  profiles/
    record-schema.json
    tags.json
    layout.json
  records/
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
cortex --json --workspace <kb> record edit --record <folder> --metadata <json-or->
```

Add and edit metadata require `title` and `tags`; `timestamp` is optional and defaults to the current UTC time with six fractional digits. Supplied timezone-aware RFC3339 timestamps are stored verbatim. An edit replaces metadata only and never moves the record directory or rewrites custody bytes.

The default tag list is empty. Replace the complete `tags` profile before adding tagged records. Layout defaults to `records`, `title-slug`, a 96-byte component cap, and `numeric-suffix` duplicates. Changing `records_root` is allowed only while its current directory is exactly empty.

Every initialized mutation takes one nonblocking OS lock on `profiles/record-schema.json`. A competing writer receives `busy/5`. Reads never lock or mutate the workspace and do not promise a consistent snapshot during concurrent writes.

## Development

```powershell
python -m pip install -e .
python -m pytest
python -m compileall -q src tests
```
