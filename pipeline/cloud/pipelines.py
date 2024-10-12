import io
import os
import typing as t

from pydantic.v1 import ValidationError

from pipeline.cloud import http
from pipeline.cloud.files import resolve_run_input_file_object
from pipeline.cloud.schemas.runs import (
    ClusterRunResult,
    RunCreate,
    RunInput,
    RunIOType,
    RunState,
)
from pipeline.objects import File
from pipeline.objects.graph import InputSchema
from pipeline.util.logging import _print
from pipeline.util.streaming import handle_stream_response


class NoResourcesAvailable(Exception):
    """Exception raised when there are no available resources to route the run
    request to.
    """

    def __init__(
        self,
        run_result: ClusterRunResult,
        message=(
            "This pipeline is currently starting up. This is normal behaviour "
            "for pipelines that are new or have not been run in a while. Please "
            "wait a few minutes before next run."
        ),
    ):
        self.message = message
        self.run_result = run_result
        super().__init__(self.message, self.run_result)

    def __str__(self):
        return self.message


def _data_to_run_input(data: t.Tuple) -> t.List[RunInput]:
    input_array = []

    for item in data:
        if isinstance(item, io.IOBase):
            path = os.path.abspath(item.name)
            item = File(path=path)
        input_type = RunIOType.from_object(item)
        if input_type == RunIOType.file or isinstance(item, File):
            input_schema = resolve_run_input_file_object(item)
            input_array.append(input_schema)
            continue
        elif isinstance(item, InputSchema):
            item_dict = item.to_dict()
            output_dict = dict()
            output_dict.update(item_dict)
            for pair_key, pair_value in item_dict.items():
                if isinstance(pair_value, io.IOBase):
                    path = os.path.abspath(pair_value.name)
                    pair_value = File(path=path)
                pair_value_type = RunIOType.from_object(pair_value)
                if pair_value_type == RunIOType.file:
                    new_schema = resolve_run_input_file_object(pair_value)
                    output_dict[pair_key] = new_schema.dict()
                elif pair_value_type == RunIOType.pkl:
                    raise Exception("Generic python objects are not supported yet.")

            input_schema = RunInput(
                type=RunIOType.dictionary,
                value=output_dict,
                file_path=None,
                file_name=None,
                file_url=None,
            )
            input_array.append(input_schema)

            continue
        elif input_type == RunIOType.pkl:
            raise NotImplementedError("Python object input not yet supported")

        input_schema = RunInput(
            type=input_type,
            value=item,
            file_path=None,
            file_name=None,
            file_url=None,
        )
        input_array.append(input_schema)

    return input_array


def _run_pipeline(run_create_schema: RunCreate):
    res = http.post(
        "/v4/runs",
        json_data=run_create_schema.dict(),
        handle_error=False,
    )
    try:
        result = ClusterRunResult.parse_raw(res.text)
    except ValidationError:
        http.raise_if_http_status_error(res)
        raise

    if result.state == RunState.no_resources_available:
        error = NoResourcesAvailable(run_result=result)
        _print(
            f"{error.message}\nRun result:\n{error.run_result.json(indent=2)}",
            level="ERROR",
        )
        raise error
    return result


def run_pipeline(
    pipeline: str,
    *data,
    async_run: bool = False,
    wait_for_resources: bool | None = None,
):
    run_create_schema = RunCreate(
        pipeline=pipeline,
        inputs=_data_to_run_input(data),
        async_run=async_run,
        wait_for_resources=wait_for_resources,
    )

    run_get = _run_pipeline(run_create_schema)
    return run_get


def _stream_pipeline(
    run_create_schema: RunCreate,
) -> t.Generator[ClusterRunResult, t.Any, None]:
    with http.stream(
        method="POST",
        endpoint="/v4/runs/stream",
        json_data=run_create_schema.dict(),
        handle_error=False,
    ) as response:
        for result_json in handle_stream_response(response):
            try:
                result = ClusterRunResult.parse_obj(result_json)
            except ValidationError:
                _print(
                    f"Unexpected result from streaming run:\n"
                    f"Status code = {response.status_code}\n{result_json}"
                )
                return
            except Exception:
                http.raise_if_http_status_error(response)
                raise

            if result.state == RunState.no_resources_available:
                error = NoResourcesAvailable(run_result=result)
                _print(
                    f"{error.message}\nRun result:\n{result.json(indent=2)}",
                    level="ERROR",
                )
                raise error
            yield result


def stream_pipeline(pipeline: str, *data, wait_for_resources: bool | None = None):
    run_create_schema = RunCreate(
        pipeline=pipeline,
        inputs=_data_to_run_input(data),
        wait_for_resources=wait_for_resources,
    )

    for result in _stream_pipeline(run_create_schema):
        yield result
