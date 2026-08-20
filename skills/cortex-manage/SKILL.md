---
name: cortex-manage
description: Inspect and manage Cortex 6.0 Bundles, profiles, exact records, and registries through the closed CLI.
---

# Manage Cortex 6

Use this skill's complete local runtime through one absolute Python 3.11 interpreter and this skill's absolute runner path. Require exactly one `cortex 6.0.0` stdout line with empty stderr:

```text
"<ABSOLUTE-PYTHON-3.11>" -I "<ABSOLUTE-CORTEX-MANAGE-SKILL-DIR>/scripts/run_cortex.py" --version
```

The runner verifies its pinned wheel before import and never resolves a PATH command. Do not fall back to a bare `cortex`, `python -m cortex`, `PYTHONPATH`, an ambient package, a sibling skill, pip, network access, installation, caching, or an update/latest check. A skill update must install this entire skill directory, including `scripts/run_cortex.py`, `scripts/runtime-manifest.json`, and `scripts/vendor/`.

Use explicit Bundle selection. Layout 3 has only `tag-title-date` with duplicate `reject`; null naming group is valid only for an empty Bundle. After the first record Layout is immutable. Record edit may change only non-naming tags.

For an authorized exact record inspection run:

```text
"<ABSOLUTE-PYTHON-3.11>" -I "<ABSOLUTE-CORTEX-MANAGE-SKILL-DIR>/scripts/run_cortex.py" --json --workspace <bundle> record show --record <exact-unit>
```

Deletion is destructive. Show the exact unit, `tree_sha256`, metadata, and manifest to the user and obtain explicit authorization for that exact digest. Then run:

```text
"<ABSOLUTE-PYTHON-3.11>" -I "<ABSOLUTE-CORTEX-MANAGE-SKILL-DIR>/scripts/run_cortex.py" --json --workspace <bundle> record delete --record <exact-unit> --expected-tree-sha256 <lowercase64>
```

Never broaden to prefix/glob/batch deletion. A mismatch stops safely. `delete_incomplete` means manual recovery is required; do not invent trash, tombstones, journals, or direct filesystem repair.

The repository migration script is not an installed/public capability. The `project-summer` pilot requires a pinned Cortex 6.0.0 Candidate, read-only plan, exact 27/25/2 count gate, detached digest approval, and separate authority for build, cutover, and Registry adoption.
