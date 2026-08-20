# Cortex 6 verification matrix

This table reproduces the frozen EvalSpec semantics without renumbering or reinterpretation. Primary dynamic evidence is in `tests/test_cortex6.py`; Registry continuity remains in `tests/test_registry.py`.

| Scenario | Frozen semantic requirement |
|---|---|
| sc001 | Full unit has the exact flat shape and preserves accepted payload names and bytes. |
| sc002 | Malformed full conversion is rejected without mutation. |
| sc003 | Markdown-only input produces the exact two-file unit. |
| sc004 | Non-Markdown source-only input is rejected without mutation. |
| sc005 | Direct unit naming follows exact tag-title-date semantics. |
| sc006 | Unicode normalization, case folding, whole-codepoint truncation, and maximum-byte handling are deterministic under Python 3.11/UCD 14.0.0. |
| sc007 | Exact and case-fold duplicate unit names are rejected without a suffix. |
| sc008 | Layout 2 and legacy partition behavior are rejected without fallback. |
| sc009 | Distribution, Python range, product version, and profile versions equal the Cortex 6 contract. |
| sc010 | Edits to title, full timestamp, or selected naming tag are rejected. |
| sc011 | A non-naming tag edit succeeds while every payload byte and payload SHA-256 remains unchanged. |
| sc012 | The unit-tree token is independently reproducible from the normative grammar. |
| sc013 | The unit-tree token changes when an authorized path or byte changes. |
| sc014 | `record show` accepts only one exact safe unit component and returns metadata from the hashed `record.json`. |
| sc015 | A matching tree token deletes exactly the selected single record. |
| sc016 | Malformed, mismatched, and stale tree tokens reject without deletion. |
| sc017 | Show/delete acquire the writer lock before Registry refresh and Bundle/unit revalidation. |
| sc018 | Lock contention for show/delete returns `busy/5` without mutation. |
| sc019 | Unsafe components, links/reparse points, and nonregular entries reject while an external sentinel remains unchanged. |
| sc020 | An after-start delete failure reports primary `delete_incomplete`, and residue-scan failure adds secondary `residue_unreadable` with the exact partial fields. |
| sc021 | Delete creates no stage, trash, journal, tombstone, or recovery artifact. |
| sc022 | Migration planning is deterministic and source-read-only and embeds explicit canonical Record 1, Tag 2, and Layout 3 profiles. |
| sc023 | Migration aggregates a deterministic error set before creating output. |
| sc024 | Migration build publishes only a separate absent output and leaves every source byte unchanged. |
| sc025 | Equality-gated full legacy units lift to the exact flat Cortex 6 shape. |
| sc026 | Original/conversion-source mismatch blocks migration before output. |
| sc027 | Markdown-only legacy units lift to the exact Cortex 6 Markdown-only shape. |
| sc028 | No public migration route or installed migration entry point exists. |
| sc029 | The project-summer pilot gate requires exactly 27 total, 25 full, and 2 Markdown-only records and stops on reproducible drift. |
| sc030 | Both repository skills require one exact pinned Cortex 6.0.0 executable and forbid fallback. |
| sc031 | Implementation, AGENTS, documentation, both skills, and the capability fixture agree on the complete contract. |
| sc032 | Full pytest, compileall, and one-to-one sc001-sc032 semantic mapping gates pass. |

Additional repair evidence covers repository-bound migration imports against a hostile ambient `cortex`, no-follow profile operands, exact owned-stage collision handling, linked/reparse legacy wrappers with external sentinels, and pre-stage/pre-output rejection of converter payload `record.md + record.json`.

All dynamic tests use disposable temporary directories; none reads or mutates the corpus. Required terminal gates are `python -m pytest` and `python -m compileall -q src tests tools`.
