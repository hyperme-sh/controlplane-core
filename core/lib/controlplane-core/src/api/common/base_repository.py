from __future__ import annotations

import uuid
from typing import Any, Dict, Generic, List, Optional, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.common.utils import get_logger
from api.infrastructure.persistence.db_session import get_db_session

logger = get_logger(__name__)

T = TypeVar("T")


class BaseRepository(Generic[T]):
    def __init__(self, model: type[T]):
        self.model = model

    @property
    def session(self) -> AsyncSession:
        return get_db_session()

    async def single_or_none(self, **kwargs: Any) -> Optional[T]:
        stmt: Select = select(self.model).filter_by(**kwargs)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get(self, id: str) -> Optional[T]:
        uid = uuid.UUID(str(id))
        stmt = select(self.model).where(self.model.id == uid)  # type: ignore[attr-defined]
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def search(self, query: Dict[str, Any], limit: int = 100) -> List[T]:
        stmt = select(self.model).filter_by(**query).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def create(self, data: dict) -> T:
        obj = self.model(**data)  # type: ignore[call-arg]
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def update(self, id: str, data: dict) -> Optional[T]:
        obj = await self.get(id)
        if not obj:
            return None
        for k, v in data.items():
            if v is not None and hasattr(obj, k):
                setattr(obj, k, v)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def delete(self, id: str) -> bool:
        obj = await self.get(id)
        if not obj:
            return False
        await self.session.delete(obj)
        await self.session.flush()
        logger.info(f"Row with id: {id} deleted successfully.")
        return True

    async def count(self, params: Optional[Dict[str, Any]] | None = None) -> int:
        stmt = select(func.count()).select_from(self.model)
        if params:
            stmt = stmt.filter_by(**params)
        res = await self.session.execute(stmt)
        return int(res.scalar_one())

    async def aggregate(self, pipeline: List[dict], projection_model: Any | None = None) -> Any:
        raise NotImplementedError("Mongo aggregate not supported; use explicit SQL.")
