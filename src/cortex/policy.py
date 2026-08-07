"""Private dependency boundary for preserved legacy OKF validation helpers.

Cortex 4 does not expose policy routes or import this module from its service.
The small value types keep the byte-preserved ``validation.py`` importable
without restoring the removed management subsystem.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_json_bytes, sha256_digest
from .errors import CortexError, Status


@dataclass(frozen=True)
class EffectivePolicy:
    state: str
    digest: str
    manifest_path: str | None
    manifest: Mapping[str, Any] | None = None
    error: str | None = None

    def report_value(self) -> dict[str, str | None]:
        return {"state":self.state,"digest":self.digest,"manifest_path":self.manifest_path}


@dataclass(frozen=True)
class TagValue:
    tag: str
    label: str
    aliases: tuple[str,...]
    derived_tags: tuple[str,...]


@dataclass(frozen=True)
class TagDimension:
    name: str
    assignment: str
    minimum: int
    maximum: int
    values: tuple[TagValue,...]


@dataclass(frozen=True)
class TagSchema:
    digest: str
    dimensions: tuple[TagDimension,...]
    statuses: Mapping[str,str]
    identifier_dimensions: Mapping[str,str|None]


def resolve_effective_policy(_root: str | Path) -> EffectivePolicy:
    digest=sha256_digest(canonical_json_bytes({"state":"absent"}))
    return EffectivePolicy("absent",digest,None)


def load_tag_schema(_root: str | Path, _policy: EffectivePolicy) -> TagSchema:
    raise CortexError("Portable legacy policy is absent",status=Status.UNSUPPORTED,code="policy_unavailable")


def resolve_tag_value(dimension: TagDimension, value: str) -> TagValue:
    key=unicodedata.normalize("NFC",value).strip().casefold()
    matches=[item for item in dimension.values if key in {unicodedata.normalize("NFC",candidate).strip().casefold() for candidate in (item.tag,item.label,*item.aliases)}]
    if len(matches)!=1:raise CortexError("Tag value is not unique",status=Status.POLICY_BLOCKED,code="unknown_tag_assignment")
    return matches[0]


__all__=["EffectivePolicy","TagDimension","TagSchema","TagValue","load_tag_schema","resolve_effective_policy","resolve_tag_value"]
