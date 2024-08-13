import asyncio
import os
import traceback
import uuid
from contextlib import asynccontextmanager

import pkg_resources
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from pipeline.container.frameworks.cog import CogManager
from pipeline.container.logging import setup_logging
from pipeline.container.manager import PipelineManager
from pipeline.container.routes import router
from pipeline.container.services.run import execution_handler
from pipeline.container.status import router as status_router


def create_app() -> FastAPI:
    app = FastAPI(title="pipeline-container", lifespan=lifespan)

    setup_logging()

    setup_oapi(app)
    setup_middlewares(app)

    app.include_router(router)
    app.include_router(status_router)
    static_dir = pkg_resources.resource_filename(
        "pipeline", "container/frontend/static"
    )

    app.mount(
        "/static",
        StaticFiles(directory=static_dir),
        name="static",
    )

    return app


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.execution_queue = asyncio.Queue()

    model_framework = os.environ.get("MODEL_FRAMEWORK")
    if model_framework is None:
        pipeline_path = os.environ.get("PIPELINE_PATH")
        if not pipeline_path:
            raise ValueError("PIPELINE_PATH environment variable is not set")
        app.state.manager = PipelineManager(pipeline_path=pipeline_path)
    elif model_framework.lower() == "cog":
        logger.debug("Using Cog")
        app.state.manager = CogManager()
    else:
        raise NotImplementedError(f"Model framework {model_framework} not supported")

    task = asyncio.create_task(
        execution_handler(app.state.execution_queue, app.state.manager)
    )
    yield
    task.cancel()


def setup_middlewares(app: FastAPI) -> None:
    @app.middleware("http")
    async def _(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-Id") or str(
            uuid.uuid4()
        )

        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = request.state.request_id
        except Exception as e:
            logger.exception(e)
            response = JSONResponse(
                status_code=500,
                content={
                    "error": repr(e),
                    "traceback": str(traceback.format_exc()),
                },
            )
            response.headers["X-Request-Id"] = request.state.request_id
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def setup_oapi(app: FastAPI) -> None:
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title="pipeline-container",
            version="1.1.0",
            routes=app.routes,
            servers=[{"url": "http://localhost:14300"}],
        )
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi
