from __future__ import annotations

from typing import Protocol, TypeVar

EntityT = TypeVar("EntityT")


class Repository(Protocol[EntityT]):
    """最小 Repository 契约；具体实现随 Phase 2 模型提供。"""

    async def get(self, entity_id: str) -> EntityT | None: ...

    async def add(self, entity: EntityT) -> EntityT: ...
