from __future__ import annotations

from contextvars import ContextVar, Token

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_session_ctx: ContextVar[AsyncSession | None] = ContextVar("db_session", default=None)

_async_session_maker: async_sessionmaker[AsyncSession] | None = None


def configure_session_maker(maker: async_sessionmaker[AsyncSession]) -> None:
    global _async_session_maker
    _async_session_maker = maker


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    if _async_session_maker is None:
        raise RuntimeError("async_session_maker not configured")
    return _async_session_maker


def get_db_session() -> AsyncSession:
    s = _session_ctx.get()
    if s is None:
        raise RuntimeError("No database session in context; ensure DbSessionMiddleware is installed.")
    return s


def set_db_session(session: AsyncSession) -> Token:
    return _session_ctx.set(session)


def reset_db_session(token: Token) -> None:
    _session_ctx.reset(token)
