"""The deliberately small Cortex 5 public contract."""

from __future__ import annotations

VERSION = "5.0.0"
DIST_NAME = "cortex-record-kb"

PUBLIC_ROUTES = (
    "manage.init",
    "manage.status",
    "manage.validate",
    "manage.config.show",
    "manage.config.set",
    "record.add",
    "record.edit",
)

RECORD_SCHEMA = {
    "version": 1,
    "fields": {
        "title": "string",
        "timestamp": "string",
        "tags": "string-list",
    },
    "required": ["title", "timestamp", "tags"],
}

DEFAULT_TAGS = {"version": 1, "tags": []}
DEFAULT_LAYOUT = {
    "version": 1,
    "records_root": "records",
    "folder_name_strategy": "title-slug",
    "max_component_length": 96,
    "duplicate_name_strategy": "numeric-suffix",
}

PROFILE_FILENAMES = ("record-schema.json", "tags.json", "layout.json")
RECORD_FIELDS = ("title", "timestamp", "tags")
