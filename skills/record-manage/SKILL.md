---
name: record-manage
description: Initialize, inspect, validate, configure, and edit a Cortex 5 record KB.
---

# Manage a Cortex 5 record KB

Use only the closed `cortex --workspace <kb>` command surface. Initialize only an absent or exactly empty path, then inspect with `manage status` and `manage validate`.

Show or replace the complete tags/layout profile:

```text
cortex --json --workspace <kb> manage config show --profile tags
<tags-json> | cortex --json --workspace <kb> manage config set --profile tags --file -
cortex --json --workspace <kb> manage config show --profile layout
<layout-json> | cortex --json --workspace <kb> manage config set --profile layout --file -
```

Tag replacement must retain every tag referenced by existing records. A records-root rename is available only while the current root is exactly empty. Other layout changes affect future additions and must leave all current folder names within the configured byte cap.

Edit metadata without moving or recopying a record:

```text
<complete-metadata> | cortex --json --workspace <kb> record edit --record <folder> --metadata -
```

Reads do not lock and may observe either side of a concurrent atomic replacement. If a mutation returns `busy`, retry after the other writer finishes. Never create a second lock, hidden state, plans, journals, indexes, or recovery artifacts.
