"""Closed Cortex 4 public surface."""

METHOD_ID = "cortex-okf-workspace-v4"
METHOD_VERSION = "4.0.0"
OKF_VERSION = "0.1"

SCHEMA_IDS = {
    "result-envelope": "urn:cortex:schema:result-envelope:1.0.0",
    "method-catalog": "urn:cortex:schema:method-catalog:1.0.0",
    "workspace-status": "urn:cortex:schema:workspace-status:1.0.0",
    "tag-schema": "urn:cortex:schema:tag-schema:2.0.0",
    "ingest-context": "urn:cortex:schema:ingest-context:2.0.0",
    "ingest-proposal-input": "urn:cortex:schema:ingest-proposal-input:1.0.0",
    "ingest-proposal": "urn:cortex:schema:ingest-proposal:2.0.0",
    "ingest-conflict": "urn:cortex:schema:ingest-conflict:2.0.0",
    "mutation-plan": "urn:cortex:schema:mutation-plan:2.0.0",
    "validation-report": "urn:cortex:schema:validation-report:2.0.0",
    "apply-journal": "urn:cortex:schema:apply-journal:2.0.0",
    "verification-receipt": "urn:cortex:schema:verification-receipt:2.0.0",
}

PUBLIC_LEAF_ROUTES = (
    "build.ingest",
    "manage.init",
    "manage.status",
    "manage.config",
    "manage.validate",
    "manage.index",
    "manage.repair",
    "manage.rename",
    "manage.retag",
)

REPAIR_PHASES = ("structural", "link-closure", "reference-names")

FEATURE_IDS = (
    "workspace.bundle-root-v1",
    "workspace.external-state-v1",
    "build.ingest.reference-batch-v1",
    "build.ingest.context-proposal-v2",
    "build.ingest.conflict-replace-v2",
    "build.ingest.exact-plan-apply-v2",
    "manage.tag-schema.direct-v1",
    "manage.reference-maintenance-v1",
    "manage.reference-name-standardization-v1",
    "manage.index.external-state-v1",
)

INDEX_BYTES = b'---\nokf_version: "0.1"\n---\n\n# Knowledge Index\n'
TAG_SCHEMA_PATH = "profiles/tag-schema.json"
STATE_OWNER = {"kind": "cortex-state-root", "schema_version": "1.0.0"}
