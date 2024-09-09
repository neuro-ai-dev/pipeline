from fastapi import APIRouter, Request, Response

from pipeline.cloud.schemas import pipelines as pipeline_schemas
from pipeline.container.manager import Manager

router = APIRouter(prefix="/container")


@router.get(
    "/state",
    tags=["container"],
    status_code=200,
    response_model=pipeline_schemas.PipelineContainerState,
    responses={
        503: {
            "description": "Pipeline is loading",
            "model": pipeline_schemas.PipelineContainerState,
        },
        500: {
            "description": "Pipeline failed to load",
            "model": pipeline_schemas.PipelineContainerState,
        },
    },
)
async def is_ready(request: Request, response: Response):
    run_manager: Manager = request.app.state.manager
    if run_manager.pipeline_state in [
        pipeline_schemas.PipelineState.loading,
        pipeline_schemas.PipelineState.not_loaded,
    ]:
        response.status_code = 503
    elif run_manager.pipeline_state in [
        pipeline_schemas.PipelineState.startup_failed,
        pipeline_schemas.PipelineState.load_failed,
    ]:
        response.status_code = 500

    return pipeline_schemas.PipelineContainerState(
        state=run_manager.pipeline_state,
        message=run_manager.pipeline_state_message,
        current_run=run_manager.current_run,
    )


@router.get(
    "/pipeline",
    tags=["container"],
    response_model=pipeline_schemas.Pipeline,
)
async def get_pipeline(request: Request):
    run_manager: Manager = request.app.state.manager
    if run_manager.pipeline_state == pipeline_schemas.PipelineState.load_failed:
        raise Exception("Pipeline was never loaded")

    return run_manager.get_pipeline()
