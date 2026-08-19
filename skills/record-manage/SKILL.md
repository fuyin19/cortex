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

Tag Profile 2 contains nonempty named groups; tags are globally unique. Layout Profile 2 links `partition_by` to one group. Initialization leaves that link null and is nonoperational until tags are configured and the link is set. Every complete profile replacement is cross-validated against the other profile and all existing units.

Edit metadata without moving or recopying a record:

```text
<complete-metadata> | cortex --json --workspace <kb> record edit --record <partition>/<unit> --metadata -
```

Title and ordinary-tag edits do not move a unit; a partition-tag change is rejected. Reads do not lock and may observe either side of a concurrent atomic replacement. If a mutation returns `busy`, retry after the other writer finishes. Never create a second lock, hidden state, plans, journals, indexes, or recovery artifacts. `docs/global-knowledge.md` is authoritative for the unit and bundle model.
