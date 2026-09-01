# Cortex 8 knowledge-unit verification matrix

The scenario identifiers and scope below are the frozen acceptance baseline. The evidence column names repository tests or task-local commands; it does not imply coverage beyond those checks.

| Scenario | Frozen semantic | Evidence |
|---|---|---|
| sc-001 | Cortex uses only the explicit absolute anti-entropy Core runner for base-envelope operations, with no vendored or runtime fallback authority. | Core envelope suite; `tests/test_knowledge_unit.py` |
| sc-002 | Default Markdown conversion publishes the complete base envelope. | Markdown conversion bundle tests |
| sc-003 | Default PDF conversion publishes the same complete base envelope. | PDF conversion bundle tests |
| sc-004 | Default file conversion delegates to a complete base-envelope result. | File conversion router tests |
| sc-005 | Producer and router paths have byte/shape parity for equivalent output. | File and Markdown conversion router-parity tests |
| sc-006 | Direct one-file Markdown and PDF modes remain byte-for-byte legacy behavior. | Direct-mode regression tests |
| sc-007 | Canonical v1 bytes, links, and manifest remain unchanged and exclude envelope files. | Canonical-schema and bundle manifest regressions |
| sc-008 | Portable components plus exact stem and case-folded name/suffix uniqueness fail closed. | Both knowledge-unit validator suites |
| sc-009 | Empty support directories use only zero-byte `.keep`; payload removes markers; empty nested directories reject. | Both knowledge-unit validator suites |
| sc-010 | Extra controls, unsafe paths, links, reparse points, nonregular nodes, and containment escapes are zero-write failures. | Validator negative cases and producer transactional tests |
| sc-011 | Guides must be present and exact; missing or tampered bytes reject without repair. | Exact-resource and tamper tests in both repositories |
| sc-012 | Control injection rejects and opaque container members are not scanned. | Validator control tests and opaque-payload regressions |
| sc-013 | Output collision, rename, and overwrite guards remain fail-closed. | Producer collision/publication regressions |
| sc-014 | Producer failure at each transactional boundary leaves no partial published unit. | Producer failure-injection regressions |
| sc-015 | Init emits Layout 5; Layout 4 and mixed-layout runtime state reject without fallback. | `tests/test_cortex7.py`; `tests/test_knowledge_unit.py` |
| sc-016 | The profile naming schema addresses only partition and record directories, not record contents. | Cortex naming and arbitrary representation/source/asset tests |
| sc-017 | Source-only add creates one root representation and complete empty support state. | `tests/test_knowledge_unit.py` |
| sc-018 | Conversion-only add validates/completes an owned base envelope. | `tests/test_knowledge_unit.py` |
| sc-019 | Combined add fills or verifies the retained source by basename and SHA-256. | `tests/test_knowledge_unit.py` |
| sc-020 | `record.json` is Cortex-private: conversion collisions reject at the Cortex boundary. | `tests/test_knowledge_unit.py`; producer namespace test |
| sc-021 | Batch v1/v2 syntax is whole-job preflight; runtime failures preserve earlier successes and continue, with failed records zero-write. | `tests/test_skill_runtime.py` batch cases |
| sc-022 | Existing public routes, Result schema, Record 1, Tag 2, Registry 1, TreeV2, and Notes remain compatible. | Full Cortex regression suite |
| sc-023 | The sole dispatcher preserves 3→4 and adds deterministic, source-read-only, bounded 4→5 plan/build with no cutover. | `tests/test_bundle_migration.py` |
| sc-024 | The KB migration has exact 30/421 counts and candidate/worktree path-byte equality. | Task-local long-path migration verifier |
| sc-025 | Frozen versions and three byte-identical packaged Cortex 8 wheels are exact and offline. | Version tests; `tests/test_skill_runtime.py`; packager `--check` |
| sc-026 | Scoped EOL rules and a disposable Git clone preserve exact LF/marker behavior. | Generator `check-attr`/raw-byte checks; exact candidate materialization in a disposable clone is reserved for official Testing |
| sc-027 | An actual producer bundle is accepted by packaged Cortex end to end. | Task-local producer-to-packaged-runtime integration |
| sc-028 | Documentation and root guidance describe the explicit external Core boundary, runner requirement, and manage-owned alignment routes. | Documentation/guidance assertions and runtime contract tests |
| workspace-sc001 | Missing-root creation and safe adoption publish complete, Core-valid outer and inner contracts without changing raw ref or safe extras. | `tests/test_collaborative_workspace.py` |
| workspace-sc002 | Canonical inventory preserves complete basenames, copies KUs byte-for-byte, rejects unsupported/control inputs together, and receives provider output only from task-owned snapshots. | `tests/test_collaborative_workspace.py` |
| workspace-sc003 | Exact no-op is byte-identical; stale refresh replaces only prepared ref, preserves output, and refuses nonempty temp as busy. | `tests/test_collaborative_workspace.py` |
| workspace-sc004 | The isolated skill wheel, launcher, manifest, closed CLI, router entry, and explicit-only metadata are deterministic and tamper-evident. | `tests/test_collaborative_workspace_runtime.py` |
| sc-029 | The Chinese feasibility memo contains the frozen decision matrix, triggers, migration path, and no-central-runtime recommendation. | Candidate memo content/hash verification |
| sc-030 | Disposable multi-repository fault/resume, pre-cleanup verification, and cleanup leave only intended deliverables. | Task-local closeout verification |

Repository boundary vocabulary: candidate under KB root; candidate under source repo; candidate under KB repo; same-volume staging; plan/build only; no cutover.

| Runtime scenario | Frozen semantic |
|---|---|
| runtime-sc001 | Each of three KB skill copies runs Cortex 8.0.0 with the explicitly configured external Core runner. |
| runtime-sc002 | All three KB skills carry byte-identical runner, manifest, and wheel payloads. |
| runtime-sc003 | PATH Cortex 4 sentinels are never invoked. |
| runtime-sc004 | Hostile PYTHONPATH and ambient Cortex modules are ignored by isolated launch. |
| runtime-sc005 | Runtime launch performs no child install, network, cache, or update action. |
| runtime-sc006 | Missing, truncated, modified, linked, and wrong-version runtime inputs fail before CLI dispatch. |
| runtime-sc007 | Coordinated wheel and manifest tampering fails against the runner-pinned digest before import. |
| runtime-sc008 | Offline deterministic regeneration and Candidate parity checks pass. |
| runtime-sc009 | The embedded wheel has exact Cortex metadata, no dependencies, and no console script. |
| runtime-sc010 | A disposable wheel projection creates no command launcher. |
| runtime-sc011 | The Cortex 8 public routes, package version, and source CLI contract remain closed. |
| runtime-sc012 | Source and all three bundled KB runtimes produce equal Results and disposable Bundle trees. |
| runtime-sc013 | Skills, documentation, capability fixture, and runtime scenario mapping agree. |
| runtime-sc014 | CORTEX_PYTHON binds the exact Python 3.11/UCD 14 executable, and non-init routes require an explicit absolute ANTI_ENTROPY_CORE_RUNNER; invalid configuration fails before mutation. |
| runtime-sc015 | Human stdout and stderr are UTF-8 while compact JSON Result bytes retain ASCII escaping and shape. |
| runtime-sc016 | The ingest skill helper accepts full and Markdown-only items and returns one ordered wrapper summary; manage and build have none. |
| runtime-sc017 | The ingest helper collects a valid middle Cortex failure and continues later batch items sequentially. |
| runtime-sc018 | The ingest helper rejects malformed jobs, duplicate ids, and relative item paths before any runner call or Bundle mutation. |
| runtime-sc019 | A populated Bundle accepts a complete keyed-monotonic Tag 2 expansion, preserves existing records and byte-identical Layout 5, and creates the new partition only on the next record add. |

| Taxonomy scenario | Frozen semantic |
|---|---|
| taxonomy-sc001 | Canonical discovery exposes ingest, build, and manage with disjoint exact write ownership. |
| taxonomy-sc002 | Both legacy aliases are removed; repository taxonomy keeps exactly six canonical KB/Notes roles plus one isolated Collaborative Workspace domain skill. |
| taxonomy-sc003 | Build requires one explicit active session and full operands for new or resumed targets. |
| taxonomy-sc004 | Build candidates retain keyed Tags/membership and Registry id-to-path mappings and reject contraction prewrite. |
| taxonomy-sc005 | Populated Tag 2 grows by keyed append while Layout 5 freezes; configured maximum expansion is Layout-before-Tags; null sentinel is Tags-before-Layout. |
| taxonomy-sc006 | First failure stops later mutation and reports completed_steps, failed_step, residual_path, orphan, and the core Result. |
| taxonomy-sc007 | KB core routes, profiles, version, source tree, package metadata, migration utility, and three KB wheels remain unchanged; repository taxonomy is one router, six KB/Notes roles, and one isolated Workspace domain skill. |
| taxonomy-sc008 | `cortex` is an instruction-only explicit router that selects exactly one KB/Notes × build/ingest/manage role or the one Collaborative Workspace skill, and packages no runtime. |
| taxonomy-sc009 | All eight skills disable implicit invocation; generic workspace, note, KB, and coding requests are insufficient triggers. |

| Notes 2 scenario | Frozen semantic |
|---|---|
| notes-sc001 | Every Bundle contains exactly canonical Note 1, Tag 2, and Layout 1 profiles; Note 1 bytes are identical. |
| notes-sc002 | Legacy `bundle.json`, extra profile keys, executable policy fields, and unsupported policies reject without fallback. |
| notes-sc003 | Validated profiles, not Bundle ids, dispatch date, safe-tag, and Git-admission behavior. |
| notes-sc004 | Aggregate and per-profile show/validate routes agree; Note and Layout profiles are immutable. |
| notes-sc005 | Whole Tag 2 candidates permit description edits and ordered append while rejecting contraction, rename, movement, irrelevant groups, unsafe names, and case/NFKC collisions. |
| notes-sc006 | Stable-root-lock expansion validates admission first, publishes canonical skeletons in tag order, and replaces Tag 2 last. |
| notes-sc007 | First failure stops without rollback and deterministically reports completed steps, failed step, residual partitions, and profile update state. |
| notes-sc008 | Only candidate-named canonical empty residue is resumable; retry is idempotent and normal operations report residue. |
| notes-sc009 | Daily add derives one `+08:00` timestamp, rejects a conflicting partition, creates one locked skeleton, and atomically publishes the note without profile changes. |
| notes-sc010 | Tools root is required only for tools initialization, tools tag append, and Git-admission note add; stale configured tools remain manageable. |
| notes-sc011 | Component, containment, exact-case repository, wrong-node, link, junction, reparse, reservation, and same-directory-temp boundaries fail closed. |
| notes-sc012 | Add/show/list/edit/archive/confirmed-delete metadata and digest lifecycle remains unchanged across all three layout fixtures. |
| notes-sc013 | Three deterministic Notes 2 wheels/manifests are byte-identical and the router is excluded from runtime packaging. |
| notes-sc014 | The isolated Notes state contains nine canonical profile files, no legacy bundle files, preserved markers/Registry/lock, and zero note units. |
