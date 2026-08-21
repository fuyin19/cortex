# Registry and agent runtime

A KB root contains canonical Registry 1, a stable zero-byte `.cortex.lock`, direct-child Bundles, and unrelated ordinary entries ignored by discovery. Registered Bundle mutations and record authorization retain the same nonblocking exclusive root lock; standalone Bundles lock `profiles/record-schema.json`.

Both repository skills carry the same complete Cortex 7.0.0 wheel and isolated runner. The entry is absolute Python 3.11 with `-I`; no installed global command, ambient-package, network, sibling-skill, or update fallback exists.
