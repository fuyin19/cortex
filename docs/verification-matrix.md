# Cortex 5 verification matrix

| Scenario | Evidence |
|---|---|
| definitions and init | Global Knowledge matches runtime; profiles-only transitional tree; seven business routes |
| metadata | exact record fields; strict timestamp/tags; generated UTC; canonical JSON bytes |
| naming and layout | direct tag partitions; Unicode slug; UTF-8 cap; partition-local collision/suffix/reject; edit without move |
| profiles | fixed Record Profile; named Tag Profile groups; Layout Profile linkage; cross-profile/tree replacement guards |
| custody | source and conversion file/directory bytes; relative paths and empty directories preserved |
| safety | no symlink/reparse/nonregular/unsafe/reserved/colliding paths; opaque nested content accepted |
| concurrency | add, edit, tag set, and layout set return deterministic `busy/5` under the one lock |
| failure atomicity | validation failures preserve valid state; existing/new-partition stage cleanup; atomic file replacement |
| read behavior | status and validate are side-effect free and report version/valid/count/full issues |
| hygiene | v5 version/package agreement; no `records/`, v4 schemas, OKF, recovery, artifact, or removed route residue |

Terminal verification is a fresh `python -m pytest` followed by `python -m compileall -q src tests`.
