# Cortex 7 verification matrix

| Scenario | Frozen semantic |
|---|---|
| sc001 | Full unit preserves its exact canonical payload shape under one partition. |
| sc002 | Malformed conversion rejects without mutation. |
| sc003 | Markdown-only input creates the exact two-file unit under one partition. |
| sc004 | Non-Markdown source-only input rejects without mutation. |
| sc005 | Partition is the exact selected tag and unit naming remains tag-title-date. |
| sc006 | Python 3.11/UCD 14 naming and whole-codepoint truncation are deterministic. |
| sc007 | Exact and case-fold duplicate partitions/units reject without suffixes. |
| sc008 | Layout 3 and legacy flat runtime behavior reject without fallback. |
| sc009 | Cortex 7.0.0 exposes Record 1, Tag 2, Layout 4, Registry 1. |
| sc010 | Edit cannot change title, timestamp, or selected partition tag. |
| sc011 | Nonpartition tag edit preserves every payload byte. |
| sc012 | V2 digest independently binds partition then record and manifest. |
| sc013 | V2 digest changes on authorized path or byte change. |
| sc014 | Show requires exact separate partition and record operands. |
| sc015 | Matching digest deletes exactly one selected record. |
| sc016 | Bad/stale digest rejects; last-unit deletion removes the empty partition. |
| sc017 | Registered authorization refreshes and validates after root-lock acquisition. |
| sc018 | Root-lock contention returns busy/5 without mutation. |
| sc019 | Unsafe operands, links, reparse points, and nonregular entries reject. |
| sc020 | Delete failure reports honest partial state and residue status. |
| sc021 | Delete creates no stage, trash, journal, tombstone, or recovery artifact. |
| sc022 | Layout3→4 planning deterministically validates canonical Record 1/Tag 2/Layout 3 bytes and exact derived flat-unit names while remaining source-read-only. |
| sc023 | Planning aggregates profile, record, naming, and payload issues before any output. |
| sc024 | Build publishes only a separate absent candidate. |
| sc025 | Full units preserve exact unit name, record bytes, payload bytes, and paths. |
| sc026 | Source/conversion mismatch blocks before output. |
| sc027 | Markdown-only units preserve exact bytes and relative paths. |
| sc028 | No installed/public migration or cutover route exists. |
| sc029 | ibd-projects gates 30 partitions, 395 total, 25 full, 370 Markdown-only. |
| sc030 | All three canonical KB skills carry one pinned, byte-identical Cortex 7.0.0 runtime; only ingest carries the helper. |
| sc031 | Implementation, AGENTS, docs, all six canonical KB and Notes skills, invocation metadata, and capability fixtures agree. |
| sc032 | Full pytest, external-cache compileall, package, and runtime check pass. |
| sc033 | Candidate/staging under the actual Cortex repo, validated KB root, or derived KB repo, plus false/omitted boundary operands and wrong-volume staging, reject before output. |
| sc034 | The repository contains exactly one noninstalled migration utility and it exposes deterministic Layout3-to-Layout4 plan/build only, with no cutover function, subcommand, or product route. |

Repository boundary vocabulary: candidate under KB root; candidate under source repo; candidate under KB repo; same-volume staging; plan/build only; no cutover.

| Runtime scenario | Frozen semantic |
|---|---|
| runtime-sc001 | Each of three complete KB skill copies runs Cortex 7.0.0 independently. |
| runtime-sc002 | All three KB skills carry byte-identical runner, manifest, and wheel payloads. |
| runtime-sc003 | PATH Cortex 4 sentinels are never invoked. |
| runtime-sc004 | Hostile PYTHONPATH and ambient Cortex modules are ignored by isolated launch. |
| runtime-sc005 | Runtime launch performs no child install, network, cache, or update action. |
| runtime-sc006 | Missing, truncated, modified, linked, and wrong-version runtime inputs fail before CLI dispatch. |
| runtime-sc007 | Coordinated wheel and manifest tampering fails against the runner-pinned digest before import. |
| runtime-sc008 | Offline deterministic regeneration and Candidate parity checks pass. |
| runtime-sc009 | The embedded wheel has exact Cortex metadata, no dependencies, and no console script. |
| runtime-sc010 | A disposable wheel projection creates no command launcher. |
| runtime-sc011 | The Cortex 7 public routes, package version, and source CLI contract remain closed. |
| runtime-sc012 | Source and all three bundled KB runtimes produce equal Results and disposable Bundle trees. |
| runtime-sc013 | Skills, documentation, capability fixture, and runtime scenario mapping agree. |
| runtime-sc014 | CORTEX_PYTHON binds the exact Python 3.11/UCD 14 executable before dispatch; missing, relative, Python 3.12, and wrong-file values fail before mutation. |
| runtime-sc015 | Human stdout and stderr are UTF-8 while compact JSON Result bytes retain ASCII escaping and shape. |
| runtime-sc016 | The ingest skill helper accepts full and Markdown-only items and returns one ordered wrapper summary; manage and build have none. |
| runtime-sc017 | The ingest helper collects a valid middle Cortex failure and continues later batch items sequentially. |
| runtime-sc018 | The ingest helper rejects malformed jobs, duplicate ids, and relative item paths before any runner call or Bundle mutation. |

| Taxonomy scenario | Frozen semantic |
|---|---|
| taxonomy-sc001 | Canonical discovery exposes ingest, build, and manage with disjoint exact write ownership. |
| taxonomy-sc002 | Both legacy aliases are removed; repository taxonomy contains exactly six canonical KB and Notes skills. |
| taxonomy-sc003 | Build requires one explicit active session and full operands for new or resumed targets. |
| taxonomy-sc004 | Build candidates retain keyed Tags/membership and Registry id-to-path mappings and reject contraction prewrite. |
| taxonomy-sc005 | Populated bytes freeze; configured maximum expansion is Layout-before-Tags; null sentinel is Tags-before-Layout. |
| taxonomy-sc006 | First failure stops later mutation and reports completed_steps, failed_step, residual_path, orphan, and the core Result. |
| taxonomy-sc007 | Core routes, profiles, version, source tree, package metadata, and migration utility remain unchanged; legacy alias trees are removed and repository taxonomy is exactly six canonical skills. |
