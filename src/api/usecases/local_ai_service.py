from __future__ import annotations

import uuid
from typing import List

from sqlalchemy import desc, select

from controlplane_api.utils import get_logger, get_utc_now
from api.domain.dtos.ai_dto import AIHistoriesDto, AISessionByUserIdDto
from api.domain.entities.ai import ChatHistoryAI, ChatSessionAI
from api.domain.serialization import serialize_chat_history, serialize_chat_session
from api.infrastructure.persistence.db_session import get_db_session
from api.infrastructure.persistence.repositories.chat_history_ai_repository_impl import (
    ChatHistoryAIRepository,
)
from api.infrastructure.persistence.repositories.chat_session_ai_repository_impl import (
    ChatSessionAIRepository,
)

logger = get_logger(__name__)


class LocalAIService:
    def __init__(
        self,
        chat_session_repository: ChatSessionAIRepository,
        chat_history_repository: ChatHistoryAIRepository,
    ) -> None:
        self.chat_session_repository = chat_session_repository
        self.chat_history_repository = chat_history_repository

    async def is_session_exists(self, session_id: str) -> bool:
        return await self.chat_session_repository.is_session_exists(session_id)

    async def get_user_sessions(self, user_id: str, limit: int = 100) -> List[AISessionByUserIdDto]:
        uid = uuid.UUID(user_id)
        session = get_db_session()
        stmt = (
            select(ChatSessionAI)
            .where(ChatSessionAI.user_id == uid)
            .order_by(desc(ChatSessionAI.created_at))
            .limit(limit)
        )
        res = await session.execute(stmt)
        rows = list(res.scalars().all())
        results: List[AISessionByUserIdDto] = []
        for s in rows:
            sd = serialize_chat_session(s)
            hist = await self.chat_history_repository.get(str(s.history_id))
            sessions_payload = [serialize_chat_history(hist)] if hist else []
            sd["sessions"] = sessions_payload
            results.append(AISessionByUserIdDto(**sd))
        return results

    async def get_histories_by_session_id(self, user_id: str, session_id: str) -> List[AIHistoriesDto]:
        sid = uuid.UUID(session_id)
        uid = uuid.UUID(user_id)
        sess = await self.chat_session_repository.single_or_none(session_id=sid, user_id=uid)
        if not sess:
            return []
        history = await self.chat_history_repository.get(str(sess.history_id))
        if not history:
            return []
        d = serialize_chat_history(history)
        return [AIHistoriesDto(**d)]

    async def delete_session(self, user_id: str, session_id: str) -> None:
        sid = uuid.UUID(session_id)
        uid = uuid.UUID(user_id)
        sess = await self.chat_session_repository.single_or_none(session_id=sid, user_id=uid)
        if not sess:
            return
        if sess.history_id:
            await self.chat_history_repository.delete(str(sess.history_id))
        await self.chat_session_repository.delete(str(sess.id))

    async def save_user_query(
        self,
        user_id: str,
        query: str,
        response: str,
        session_id: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        tid = uuid.UUID(tenant_id) if tenant_id else None
        uid = uuid.UUID(user_id)
        entry = {
            "uid": str(uuid.uuid4()),
            "query": query,
            "response": response,
            "timestamp": get_utc_now().isoformat(),
        }
        lookup_sid = uuid.UUID(session_id) if session_id else None
        session = (
            await self.chat_session_repository.single_or_none(session_id=lookup_sid)
            if lookup_sid
            else None
        )
        if session is None:
            new_sid = lookup_sid or uuid.uuid4()
            hist = await self.chat_history_repository.create({"histories": [entry], "tenant_id": tid})
            await self.chat_session_repository.create(
                {
                    "session_id": new_sid,
                    "user_id": uid,
                    "history_id": hist.id,
                    "tenant_id": tid,
                }
            )
            return
        hist = await self.chat_history_repository.get(str(session.history_id))
        if not hist:
            return
        hlist = list(hist.histories or [])
        hlist.append(entry)
        hist.histories = hlist
        hist.updated_at = get_utc_now()
        await self.chat_session_repository.session.flush()
