from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.app.agent_routes import router as agent_router
from server.app.http_routes import router as http_router
from server.app.ws_agent import router as ws_router
from server.app.ws_viewer import router as viewer_router
from server.config.settings import load_settings
from server.sessions.registry import SessionRegistry
from server.storage.db import init_db


def create_app(db_path: str | None = None) -> FastAPI:
    settings = load_settings()
    _db_path = db_path if db_path is not None else settings.db_path

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db(_db_path)
        app.state.registry = SessionRegistry()
        yield
        app.state.registry.close_all()

    app = FastAPI(title="RF Platform", version="0.3.0", lifespan=lifespan)
    app.state.settings = settings  # available immediately, before lifespan runs
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(http_router)
    app.include_router(agent_router)
    app.include_router(ws_router)
    app.include_router(viewer_router)
    return app
