import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from serverless_core_api.config import get_settings
from serverless_core_api.routers import admin, health, internal
from serverless_core_api.vast import VastClient

logger = logging.getLogger("serverless_core_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.vast = VastClient(settings.vast_api_key)

    try:
        ok = await app.state.vast.ping()
        logger.info("vast.ai credentials: %s", "OK" if ok else "FAILED")
    except Exception as e:
        logger.warning("vast.ai ping errored at startup: %s", e)

    try:
        yield
    finally:
        await app.state.vast.aclose()


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    app = FastAPI(title="serverless-core", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(internal.router)
    return app


app = create_app()
