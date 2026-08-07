"""Application-level command dispatch.

The CLI is deliberately thin: this module maps the frozen public routes to the
core subsystems and is the only place where route-specific read/write behavior
is selected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import Status


@dataclass
class CommandOutcome:
    data_schema_id: str | None
    data: dict[str, Any] | None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    status: Status = Status.OK


def dispatch(route: str, args: Any, workspace: Path) -> CommandOutcome:
    """Dispatch one frozen leaf route.

    Imports stay local so contract and subsystem tests can run independently.
    """

    from .service import CortexService

    return CortexService(workspace).execute(route, args)
