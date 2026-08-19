# Cortex 5 verification matrix

| Scenario | Evidence |
|---|---|
| init and closed CLI | exact four-part tree; seven business routes; direct Result/status mapping |
| metadata | exact record fields; strict timestamp/tags; generated UTC; canonical JSON bytes |
| naming and layout | Unicode slug, UTF-8 cap, case-fold collision, suffix/reject, edit without move |
| profiles | fixed record schema; ordered exact tags; complete tags/layout replacement; no orphaning |
| custody | source and conversion file/directory bytes; relative paths and empty directories preserved |
| safety | no symlink/reparse/nonregular/unsafe/reserved/colliding paths; opaque nested content accepted |
| concurrency | add, edit, tag set, and layout set return deterministic `busy/5` under the one lock |
| failure atomicity | validation failures preserve valid state; staged add cleanup; atomic file replacement |
| read behavior | status and validate are side-effect free and report version/valid/count/full issues |
| hygiene | v5 version/package agreement; no v4 schemas, OKF, transaction, artifact, or removed route residue |

Terminal verification is a fresh `python -m pytest` followed by `python -m compileall -q src tests`.
