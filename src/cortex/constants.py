"""The deliberately small Cortex 8 public contract."""

from __future__ import annotations

VERSION = "8.0.0"
DIST_NAME = "cortex-record-kb"

PUBLIC_ROUTES = (
    "align.plan",
    "align.apply",
    "registry.show",
    "registry.validate",
    "registry.resolve",
    "registry.set",
    "manage.init",
    "manage.status",
    "manage.validate",
    "manage.config.show",
    "manage.config.set",
    "record.add",
    "record.edit",
    "record.show",
    "record.delete",
)

REGISTRY_VERSION = 1
REGISTRY_FILENAME = "registry.json"
ROOT_LOCK_FILENAME = ".cortex.lock"

RECORD_SCHEMA = {
    "version": 1,
    "fields": {
        "title": "string",
        "timestamp": "string",
        "tags": "string-list",
    },
    "required": ["title", "timestamp", "tags"],
}

DEFAULT_TAGS = {"version": 2, "groups": []}
DEFAULT_LAYOUT = {
    "version": 5,
    "partition_tag_group": None,
    "partition_name_strategy": "tag",
    "unit_name_strategy": "tag-title-date",
    "max_component_length": 96,
    "duplicate_name_strategy": "reject",
}

PROFILE_FILENAMES = ("record-schema.json", "tags.json", "layout.json")
RECORD_FIELDS = ("title", "timestamp", "tags")
