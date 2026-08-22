# Registry and agent runtime

A KB root contains canonical Registry 1, a stable zero-byte `.cortex.lock`, direct-child Bundles, and unrelated ordinary entries ignored by discovery. Registered Bundle mutations and record authorization retain the same nonblocking exclusive root lock; standalone Bundles lock `profiles/record-schema.json`.

Both repository skills carry the same complete Cortex 7.0.0 wheel, isolated runner, and Windows convenience launcher. `CORTEX_PYTHON` must be a lexical absolute ordinary non-reparse file, reached through non-reparse ancestors, that is the same filesystem entry as the running Python 3.11/UCD 14 executable. POSIX invokes `"$CORTEX_PYTHON" -I`; Windows may use `run_cortex.cmd`. No PATH, installed global command, ambient-package, network, sibling-skill, or update fallback exists. Human stdout/stderr is UTF-8 while compact JSON Results retain ASCII escaping.

Only `cortex-build` carries the non-core `batch_record_add.py` helper. It validates one exact v1 job completely, then sequentially invokes the same interpreter and verified runner for each `record add`; it has no concurrency, rollback, inference, deletion, or persistent job state.
