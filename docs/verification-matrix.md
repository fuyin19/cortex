# Cortex 5.1 verification matrix

| Scenario | Evidence |
|---|---|
| Registry grammar | canonical v1; ID/path/description shape; exact and case-fold uniqueness; traversal/nesting/staging/reparse rejection |
| Registry authority | valid targets; orphan reporting; immutable existing ID/path pairs; description updates and additions |
| Explicit selection | resolve exact ID; managed routes require root plus ID; unknown/missing ID fails; init remains workspace-only |
| Profile authority | show-only Record declaration; Tag 2 and Layout 2 linkage, both unit-name strategies, composite reject requirement and capacity checks, and whole-file write enforcement |
| Locking | two Bundles share root busy/5; direct and managed calls share it; standalone uses Record Profile; reads are lock-free |
| Atomicity | invalid first set leaves no state; registry/profile/edit replacement cleanup; record staging cleanup |
| Record behavior | exact metadata fields; missing tag rejection; partition derivation; legacy golden names; composite normalization/date/truncation/duplicate handling; stable edit path; custody bytes |
| Concurrency and output | two-process composite collision and retry; no orphan stages; final-rename collision seam; CP936 JSON round trip; human stdout placement and encoding safety |
| Static surface | exact ResultEnvelope; compact ASCII JSON; 5.1.0 versions; ordered capability strategies; two path-pinned cortex skills; no migration/rename/delete/default/search/batch route |

Terminal verification is a fresh `python -m pytest` followed by `python -m compileall -q src tests`, with caches and basetemp confined to the isolated worktree.
