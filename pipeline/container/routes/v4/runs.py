import asyncio
import io
import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from loguru import logger

from pipeline.cloud.schemas import pipelines as pipeline_schemas
from pipeline.cloud.schemas import runs as run_schemas
from pipeline.container.manager import Manager
from pipeline.exceptions import RunInputException, RunnableError
from pipeline.objects.graph import File

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post(
    "",
    status_code=200,
    responses={
        500: {
            "description": "Pipeline failed",
            "model": run_schemas.ContainerRunResult,
        },
        400: {
            "description": "Invalid input data",
            "model": run_schemas.ContainerRunResult,
        },
    },
)
async def run(
    run_create: run_schemas.ContainerRunCreate,
    request: Request,
    response: Response,
) -> run_schemas.ContainerRunResult:
    run_id = run_create.run_id
    with logger.contextualize(run_id=run_id):
        manager: Manager = request.app.state.manager
        if result := _handle_pipeline_state_not_ready(manager):
            return result

        execution_queue: asyncio.Queue = request.app.state.execution_queue

        response_queue: asyncio.Queue = asyncio.Queue()
        execution_queue.put_nowait((run_create, response_queue))
        run_output = await response_queue.get()

        response_schema, response.status_code = _generate_run_result(run_output)
        return response_schema


@router.post(
    "/stream",
    status_code=200,
    responses={
        500: {
            "description": "Pipeline failed",
            "model": run_schemas.ContainerRunResult,
        },
        400: {
            "description": "Invalid input data",
            "model": run_schemas.ContainerRunResult,
        },
    },
)
async def stream_run(
    run_create: run_schemas.ContainerRunCreate,
    request: Request,
    response: Response,
):
    run_id = run_create.run_id
    with logger.contextualize(run_id=run_id):
        manager: Manager = request.app.state.manager
        if result := _handle_pipeline_state_not_ready(manager):
            return result

        execution_queue: asyncio.Queue = request.app.state.execution_queue

        response_queue: asyncio.Queue = asyncio.Queue()
        execution_queue.put_nowait((run_create, response_queue))
        run_output = await response_queue.get()

        response_schema, response.status_code = _generate_run_result(run_output)

        outputs = response_schema.outputs or []
        if not outputs:
            return response_schema

        if not any([output.type == run_schemas.RunIOType.stream for output in outputs]):
            raise TypeError("No streaming outputs found")

        static_outputs = [
            output for output in outputs if output.type != run_schemas.RunIOType.stream
        ]

        streaming_outputs = [
            output for output in outputs if output.type == run_schemas.RunIOType.stream
        ]

        async def stream_func():
            # Iterate over all streams, if done then add to static
            while len(streaming_outputs) > 0:
                streaming_outputs_to_remove = []
                next_streaming_outputs = []
                for output in streaming_outputs:
                    try:
                        if output.value is None:
                            raise Exception("Stream value was None")

                        next_value = output.value.__next__()
                        if next_value is not None:
                            next_streaming_outputs.append(
                                run_schemas.RunOutput(
                                    type=run_schemas.RunIOType.from_object(next_value),
                                    value=next_value,
                                    file=None,
                                )
                            )
                    except StopIteration:
                        streaming_outputs_to_remove.append(output)

                for output in streaming_outputs_to_remove:
                    streaming_outputs.remove(output)

                if not len(streaming_outputs):
                    break

                new_response_schema = run_schemas.ContainerRunResult(
                    inputs=response_schema.inputs,
                    outputs=[*static_outputs, *next_streaming_outputs],
                    error=response_schema.error,
                )

                # serialise response to str and add newline separator
                yield new_response_schema.json() + "\n"

                if await request.is_disconnected():
                    for output in streaming_outputs:
                        if output.value is not None and hasattr(
                            output.value.iterable, "end"
                        ):
                            output.value.iterable.end()
                    break

        return StreamingResponse(
            stream_func(),
            media_type="application/json",
            # hint to disable buffering
            headers={"X-Accel-Buffering": "no"},
        )


def _handle_pipeline_state_not_ready(
    manager: Manager,
) -> run_schemas.ContainerRunResult | None:
    if manager.pipeline_state == pipeline_schemas.PipelineState.loading:
        logger.info("Pipeline loading")
        return run_schemas.ContainerRunResult(
            outputs=None,
            inputs=None,
            error=run_schemas.ContainerRunError(
                type=run_schemas.ContainerRunErrorType.pipeline_loading,
                message="Pipeline is still loading",
                traceback=None,
            ),
        )

    if manager.pipeline_state == pipeline_schemas.PipelineState.load_failed:
        logger.info("Pipeline failed to load")
        return run_schemas.ContainerRunResult(
            outputs=None,
            inputs=None,
            error=run_schemas.ContainerRunError(
                type=run_schemas.ContainerRunErrorType.startup_error,
                message="Pipeline failed to load",
                traceback=manager.pipeline_state_message,
            ),
        )

    if manager.pipeline_state == pipeline_schemas.PipelineState.startup_failed:
        logger.info("Pipeline failed to startup")
        return run_schemas.ContainerRunResult(
            outputs=None,
            inputs=None,
            error=run_schemas.ContainerRunError(
                type=run_schemas.ContainerRunErrorType.startup_error,
                message="Pipeline failed to startup",
                traceback=manager.pipeline_state_message,
            ),
        )


def _generate_run_result(run_output) -> tuple[run_schemas.ContainerRunResult, int]:
    if isinstance(run_output, RunInputException):
        return (
            run_schemas.ContainerRunResult(
                outputs=None,
                inputs=None,
                error=run_schemas.ContainerRunError(
                    type=run_schemas.ContainerRunErrorType.input_error,
                    message=run_output.message,
                    traceback=None,
                ),
            ),
            400,
        )
    elif isinstance(run_output, RunnableError):
        return (
            run_schemas.ContainerRunResult(
                outputs=None,
                inputs=None,
                error=run_schemas.ContainerRunError(
                    type=run_schemas.ContainerRunErrorType.pipeline_error,
                    message=repr(run_output.exception),
                    traceback=run_output.traceback,
                ),
            ),
            200,
        )
    elif isinstance(run_output, Exception):
        return (
            run_schemas.ContainerRunResult(
                outputs=None,
                inputs=None,
                error=run_schemas.ContainerRunError(
                    type=run_schemas.ContainerRunErrorType.unknown,
                    message=str(run_output),
                    traceback=None,
                ),
            ),
            500,
        )
    else:
        outputs = _parse_run_outputs(run_output)
        return (
            run_schemas.ContainerRunResult(
                outputs=outputs,
                error=None,
                inputs=None,
            ),
            200,
        )


def _parse_run_outputs(run_outputs):
    outputs = []
    for output in run_outputs:
        output_type = run_schemas.RunIOType.from_object(output)
        # if single file
        if output_type == run_schemas.RunIOType.file:
            file_schema = _save_run_file(output)
            outputs.append(
                run_schemas.RunOutput(type=output_type, value=None, file=file_schema)
            )
        # else if list of files
        elif (
            output_type == run_schemas.RunIOType.pkl
            and isinstance(output, list)
            and all([isinstance(item, (File, io.BufferedIOBase)) for item in output])
        ):
            file_list = []
            for file in output:
                file_schema = _save_run_file(file)
                file_list.append(
                    run_schemas.RunOutput(
                        type=run_schemas.RunIOType.file,
                        value=None,
                        file=file_schema,
                    )
                )
            outputs.append(
                run_schemas.RunOutput(
                    type=run_schemas.RunIOType.array,
                    value=file_list,
                    file=None,
                )
            )
        else:
            outputs.append(
                run_schemas.RunOutput(type=output_type, value=output, file=None)
            )
    return outputs


def _save_run_file(file: File | io.BufferedIOBase) -> run_schemas.RunOutputFile:
    # ensure we save the file somewhere unique on the file system so it doesn't
    # get overwritten by another run
    uuid_path = str(uuid.uuid4())
    output_path = Path(f"/tmp/run_files/{uuid_path}")
    output_path.mkdir(parents=True, exist_ok=True)

    if isinstance(file, File):
        # should always exist
        assert file.path
        file_name = file.path.name
        output_file = output_path / file_name
        file_size = os.stat(file.path).st_size
        # copy file to new output location
        shutil.copyfile(file.path, output_file)
    else:
        file_name = getattr(
            file,
            "name",
            str(uuid.uuid4()),
        )
        try:
            file_size = file.seek(0, os.SEEK_END)
        except Exception:
            file_size = -1
            logger.warning(f"Could not get size of type {type(file)}")
        # write file to new output location
        output_file = output_path / file_name
        output_file.write_bytes(file.read())

    file_schema = run_schemas.RunOutputFile(
        name=file_name, path=str(output_file), size=file_size, url=None
    )
    return file_schema
