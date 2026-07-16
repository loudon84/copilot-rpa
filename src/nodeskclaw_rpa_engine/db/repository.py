from __future__ import annotations

from typing import Protocol, TypeVar

EntityT = TypeVar("EntityT")


class Repository(Protocol[EntityT]):
    """Minimum repository contract; concrete repositories arrive with Phase 2 models."""

    async def get(self, entity_id: str) -> EntityT | None: ...

    async def add(self, entity: EntityT) -> EntityT: ...
