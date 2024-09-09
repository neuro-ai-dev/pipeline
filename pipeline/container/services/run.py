import asyncio
import io
import os
import shutil
import uuid
from pathlib import Path

import httpx
from fastapi.concurrency import run_in_threadpool
from loguru import logger

from pipeline.cloud.schemas import runs as run_schemas
from pipeline.container.logging import redirect_stdout
from pipeline.container.manager import Manager
from pipeline.exceptions import RunInputException, RunnableError
from pipeline.objects.graph import File


async def execution_handler(execution_queue: asyncio.Queue, manager: Manager) -> None:
    with redirect_stdout():
        await run_in_threadpool(manager.startup_pipeline)
        while True:
            try:
                args, response_queue = await execution_queue.get()
                args: run_schemas.ContainerRunCreate
                input_data = args.inputs
                run_id = args.run_id
                with logger.contextualize(run_id=run_id):
                    try:
                        output = await run_in_threadpool(
                            manager.run_pipeline, run_id=run_id, input_data=input_data
                        )
                    except Exception as e:
                        logger.exception("Exception raised during pipeline execution")
                        output = e

                    response_schema, status_code = _generate_run_result(output)

                    if args.async_run is True:
                        # send response back to callback URL
                        assert args.callback_url is not None
                        # send result in an async task so it runs in parallel
                        # and we are free to process the next run
                        asyncio.create_task(
                            _send_async_result(
                                callback_url=args.callback_url,
                                response_schema=response_schema,
                            )
                        )
                    else:
                        response_queue.put_nowait((response_schema, status_code))
            except Exception:
                logger.exception("Got an error in the execution loop handler")


async def _send_async_result(
    callback_url: str, response_schema: run_schemas.ContainerRunResult
):
    logger.info("Sending async result...")
    async with httpx.AsyncClient(verify=False) as client:
        try:
            response = await client.post(
                callback_url, json=response_schema.dict(), timeout=10
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"Error sending async result: "
                f"{exc.response.status_code} - {exc.response.text}"
            )
        except httpx.RequestError as exc:
            logger.exception(f"Error sending async result: {exc}")
        else:
            logger.info("Sending async result successful")


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
