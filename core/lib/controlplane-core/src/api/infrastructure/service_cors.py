"""Optional browser CORS for standalone microservices (console calls gateway + service URLs)."""

from __future__ import annotations

import os

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

# Local Next/Vite dev; override per service via ``*_CORS_ORIGINS`` env in production.
DEFAULT_DEV_CORS_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:5173,http://127.0.0.1:5173"
)


def parse_cors_origins(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    return [o.strip() for o in str(raw).split(",") if o.strip()]


def add_optional_service_cors(
    app: FastAPI,
    *,
    env_var: str,
    default_origins: str | None = DEFAULT_DEV_CORS_ORIGINS,
) -> None:
    """If ``env_var`` or ``default_origins`` lists origins, register CORSMiddleware; else allow all."""
    raw = os.environ.get(env_var) or default_origins
    origins = parse_cors_origins(raw)
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
