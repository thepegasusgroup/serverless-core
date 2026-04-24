import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client

from serverless_core_api.anthropic_client import AnthropicBatchClient
from serverless_core_api.config import get_settings
from serverless_core_api.routers import admin, health, internal, proxy
from serverless_core_api.services.dataset_poller import run_forever as dataset_run_forever
from serverless_core_api.services.idle_pauser import run_forever as idle_run_forever
from serverless_core_api.services.replicator import run_forever as replicator_run_forever
from serverless_core_api.services.status_poller import poll_forever
from serverless_core_api.vast import VastClient

logger = logging.getLogger("serverless_core_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.vast = VastClient(settings.vast_api_key)
    app.state.sb_service = create_client(
        settings.supabase_url, settings.supabase_service_role_key
    )

    # Optional: Anthropic Batches API client for /admin/datasets.
    # Unset when ANTHROPIC_API_KEY is missing — dataset endpoints 503 gracefully.
    app.state.anthropic = (
        AnthropicBatchClient(settings.anthropic_api_key)
        if settings.anthropic_api_key
        else None
    )
    logger.info(
        "Anthropic integration: %s",
        "enabled" if app.state.anthropic else "disabled (ANTHROPIC_API_KEY unset)",
    )

    try:
        ok = await app.state.vast.ping()
        logger.info("vast.ai credentials: %s", "OK" if ok else "FAILED")
    except Exception as e:
        logger.warning("vast.ai ping errored at startup: %s", e)

    poller_task = asyncio.create_task(
        poll_forever(app.state.vast, app.state.sb_service)
    )
    idle_task = asyncio.create_task(
        idle_run_forever(app.state.vast, app.state.sb_service)
    )
    # No-op when no model has auto_replicate=true — safe to always run.
    replicator_task = asyncio.create_task(
        replicator_run_forever(app.state.vast, app.state.sb_service, settings)
    )
    # No-op when anthropic is None — safe to always run.
    dataset_task = asyncio.create_task(
        dataset_run_forever(app.state.sb_service, app.state.anthropic)
    )

    try:
        yield
    finally:
        for t in (poller_task, idle_task, replicator_task, dataset_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
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
    app.include_router(proxy.router)
    return app


app = create_app()
