# Cortex Record KB 5.1

Cortex is a small, single-writer record knowledge base. Each Bundle owns its three profiles and direct tag-selected partitions. An optional KB root adds only canonical `registry.json`, a stable zero-byte `.cortex.lock`, and registered direct-child Bundles. The authoritative definitions are in [Global Knowledge](docs/global-knowledge.md).

```text
<kb-root>/                         <bundle>/
  registry.json                     profiles/
  .cortex.lock                        record-schema.json
  <direct-child-bundle>/              tags.json
                                      layout.json
                                    <partition-tag>/<title-slug>/
                                      record.json
                                      original/<source-basename>
                                      representations/markdown-conversion/... # optional
```

Registry commands use `--kb-root`; managed Bundle commands add an explicit `--bundle-id`. There is no default or automatic Bundle selection.

```powershell
cortex --json --kb-root <root> registry show
cortex --json --kb-root <root> registry validate
cortex --json --kb-root <root> registry resolve --bundle-id <id>
Get-Content registry.json | cortex --json --kb-root <root> registry set --file -
cortex --json --kb-root <root> --bundle-id <id> manage status
cortex --json --kb-root <root> --bundle-id <id> manage validate
cortex --json --kb-root <root> --bundle-id <id> manage config show --profile record|tags|layout
cortex --json --kb-root <root> --bundle-id <id> manage config set --profile tags|layout --file <json-or->
cortex --json --kb-root <root> --bundle-id <id> record add --source <file> [--conversion <file-or-dir>] --metadata <json-or->
cortex --json --kb-root <root> --bundle-id <id> record edit --record <partition>/<unit> --metadata <json-or->
```

Direct `--workspace <bundle>` remains supported. `manage init` is workspace-only. Registry entries are immutable ID/path pairs: whole-file set may add pairs and change descriptions, but cannot remove or reassign a pair. Registry targets must validate, and complete unregistered direct-child Bundles are reported as orphans.

Record Schema 1 is the Bundle's declaration within Cortex's supported dialect and currently fixes exactly `title`, `timestamp`, and `tags`; it is show-only. Tag Profile 2 and Layout Profile 2 are Bundle-owned policy and are enforced on every write. Cortex never adds a missing tag silently.

Registered-root mutations use the single `.cortex.lock`, including direct workspace calls to any sibling Bundle under that adopted root. Standalone mutations use `profiles/record-schema.json`. Both locks are nonblocking; contention returns `busy/5`. Reads do not lock or write.

Development verification:

```powershell
python -m pytest
python -m compileall -q src tests
```
