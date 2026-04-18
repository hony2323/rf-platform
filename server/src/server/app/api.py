from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from server.storage.db import init_db


def create_app(db_path: str = "rf_platform.db") -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db(db_path)
        yield

    app = FastAPI(title="RF Platform", version="0.3.0", lifespan=lifespan)
    return app
