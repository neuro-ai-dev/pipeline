from __future__ import annotations

import mimetypes
import os
import time
import typing as t
import uuid
from base64 import b64decode
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

from pipeline import File
from pipeline.cloud.schemas import pipelines as pipeline_schemas
from pipeline.cloud.schemas import runs as run_schemas
from pipeline.cloud.schemas.pipelines import IOVariable
from pipeline.container.manager import Manager
from pipeline.exceptions import RunInputException


@dataclass
class CogInput:
    order: int
    name: str
    description: str
    python_type: type
    default: t.Any | None = None
    title: str | None = None
    format: str | None = None

    def to_io_schema(self) -> IOVariable:
        description = self.description
        if self.format:
            description += f" (format = {self.format})"
        return IOVariable(
            run_io_type=run_schemas.RunIOType.from_object(self.python_type),
            title=self.title,
            description=description,
            optional=True,
            default=self.default,
        )


@dataclass
class CogOutput:
    python_type: type
    format: str | None = None
    # only used if python_type=list
    list_items: CogOutput | None = None
    # only used if python_type=dict
    dict_items: dict[str, CogOutput] | None = None

    def to_io_schema(self) -> IOVariable:
        return IOVariable(
            run_io_type=run_schemas.RunIOType.from_object(self.python_type),
        )


class CogManager(Manager):

    TYPES_MAP = {
        "integer": int,
        "number": float,
        "string": str,
        "boolean": bool,
    }

    def __init__(self):
        super().__init__()

        base_url = os.environ.get("COG_API_URL", "http://localhost:5000")
        self.api_client = httpx.Client(base_url=base_url)

        self.cog_model_inputs: list[CogInput] | None = None
        # Cog models always have a single output (but can be a list or dict)
        self.cog_model_output: CogOutput | None = None
        save_output_files = os.environ.get("SAVE_OUTPUT_FILES", "")
        self.save_output_files = save_output_files.lower() == "true"
        logger.debug(f"save_output_files = {self.save_output_files}")

    def startup(self):
        logger.info("Waiting for Cog pipeline to startup...")

        try:
            self._wait_for_cog_startup(until_fully_ready=False)
            self.cog_model_inputs, self.cog_model_output = (
                self._get_cog_model_inputs_and_output()
            )
            self._wait_for_cog_startup(until_fully_ready=True)
        except Exception as exc:
            logger.exception("Pipeline failed to startup")
            self.pipeline_state = pipeline_schemas.PipelineState.startup_failed
            self.pipeline_state_message = str(exc)
        else:
            self.pipeline_state = pipeline_schemas.PipelineState.loaded
            logger.info("Pipeline started successfully")

    def _wait_for_cog_startup(self, until_fully_ready: bool = True):
        max_retries = 150
        i = 0
        while i < max_retries:
            i += 1
            status = None
            # try to call health-check endpoint, ingoring any Exceptions, as
            # API may not yet be available
            try:
                response = self.api_client.get("/health-check")
                result = response.json()
                status = result["status"]
            except Exception as e:
                logger.info(f"Exception caught when polling /health-check : {e}")
                pass

            if status == "READY":
                logger.info("Cog model ready")
                return
            elif status == "STARTING":
                logger.info("Cog model starting...")
                if not until_fully_ready:
                    return
            elif status == "SETUP_FAILED":
                logs = result["setup"].get("logs")
                raise Exception(f"Cog model setup failed: {logs}")
            time.sleep(5)
            logger.info("Sleeping for 5s...")
        raise Exception("Cog model failed to load")

    def _get_cog_model_inputs_and_output(self):
        """Returns inputs in same order as they are defined in Cog predict function"""
        logger.info("Getting Cog model inputs and output...")
        response = self.api_client.get("/openapi.json")
        schema = response.json()
        inputs = (
            schema.get("components", {})
            .get("schemas", {})
            .get("Input", {})
            .get("properties", {})
        )

        cog_inputs: list[CogInput] = []
        for name, val in inputs.items():
            if "type" not in val:
                logger.info(f"No 'type' found for input '{name}'; assuming string")
            try:
                order = val["x-order"]
            except KeyError:
                raise ValueError(f"No x-order found for input '{name}'")
            try:
                python_type = self.TYPES_MAP[val.get("type", "string")]
            except KeyError:
                raise ValueError(f"Unknown type found: {val['type']}")
            cog_inputs.append(
                CogInput(
                    name=name,
                    order=order,
                    python_type=python_type,
                    description=val.get("description", ""),
                    title=val.get("title", name),
                    default=val.get("default", None),
                    format=val.get("format", None),
                )
            )

        # Now order the inputs based on the x-order attribute.
        # We must do this since pipeline doesn't use named inputs, so we need to
        # make sure they match up.
        cog_inputs.sort(key=lambda x: x.order)

        # Cog models always have a single output (but can be a list or dict)
        schema_output = (
            schema.get("components", {}).get("schemas", {}).get("Output", {})
        )
        schema_output_type = schema_output.get("type")
        if not schema_output_type:
            raise ValueError("Could not find output type in cog OpenAPI schema")
        # for now, keep it simple
        if schema_output_type == "array":
            list_items = schema_output.get("items", {})
            list_schema_type = list_items.get("type")
            python_list_type = self.TYPES_MAP.get(list_schema_type)
            if not python_list_type:
                raise ValueError(f"Unknown model ouput type found: {list_schema_type}")
            cog_output = CogOutput(
                python_type=list,
                list_items=CogOutput(
                    python_type=python_list_type, format=list_items.get("format")
                ),
            )
        elif schema_output_type == "object":
            dict_items = schema_output.get("properties", {})
            dict_outputs = {}
            for name, val in dict_items.items():
                python_type = self.TYPES_MAP.get(val.get("type", "string"))
                if not python_type:
                    raise ValueError(f"Unknown model ouput type found: {val['type']}")
                dict_outputs[name] = CogOutput(
                    python_type=python_type,
                    format=val.get("format"),
                )
            cog_output = CogOutput(python_type=dict, dict_items=dict_outputs)
        else:
            python_output_type = self.TYPES_MAP.get(schema_output_type)
            if not python_output_type:
                raise ValueError(
                    f"Unknown model ouput type found: {schema_output_type}"
                )
            cog_output = CogOutput(
                python_type=python_output_type, format=schema_output.get("format")
            )
        return cog_inputs, cog_output

    def _parse_inputs(
        self, input_data: list[run_schemas.RunInput] | None
    ) -> dict[str, t.Any]:
        # Return a dict of {name: value} for all inputs
        inputs = {}
        input_data = input_data or []
        # this shouldn't happen but keep as sense check
        assert self.cog_model_inputs is not None
        if len(input_data) != len(self.cog_model_inputs):
            raise RunInputException(
                f"Number of inputs ({len(input_data)}) does not match Cog model "
                f"inputs ({len(self.cog_model_inputs)})"
            )
        for run_input, cog_input in zip(input_data, self.cog_model_inputs):
            if run_input.type == run_schemas.RunIOType.file:
                # TODO - decide what we want to do here
                raise NotImplementedError("File input not implemented yet")
            # ignore things like empty URLs, which raise validation errors in Cog's API
            if cog_input.format and not run_input.value:
                continue
            inputs[cog_input.name] = run_input.value
        return inputs

    def run(self, input_data: list[run_schemas.RunInput] | None) -> t.Any:
        logger.info("Running Cog pipeline")
        logger.debug(f"raw inputs = {input_data}")
        inputs = self._parse_inputs(input_data)
        logger.debug(f"parsed inputs = {inputs}")
        result = self._call_cog_prediction(inputs)
        logger.debug(f"{result=}")
        return result

    def _call_cog_prediction(self, input_data: dict[str, t.Any]):
        try:
            response = self.api_client.post(
                "/predictions", timeout=15 * 60, json={"input": input_data}
            )
            response.raise_for_status()
        except httpx.RequestError as exc:
            raise Exception("API call to /predictions failed") from exc
        except httpx.HTTPStatusError as exc:
            raise Exception(
                f"API call to /predictions failed: "
                f"{exc.response.status_code} - {exc.response.text}."
            )

        result = response.json()
        logger.debug(f"raw result = {result}")
        if result["status"] != "succeeded":
            raise Exception(f"Cog prediction failed: {result['error']}")
        output = result["output"]
        if self.save_output_files:
            output = self._save_output_files(
                output=output, cog_output_spec=self.cog_model_output
            )
        if not isinstance(output, list):
            output = [output]
        return output

    def _save_output_files(self, output: t.Any, cog_output_spec: CogOutput | None):
        if not cog_output_spec:
            return output

        if isinstance(output, list) and cog_output_spec.list_items:
            new_list_outputs: list[t.Any] = []
            for item in output:
                new_output = self._save_output_files(
                    output=item, cog_output_spec=cog_output_spec.list_items
                )
                new_list_outputs.append(new_output)
            return new_list_outputs

        if isinstance(output, dict) and cog_output_spec.dict_items:
            new_dict_outputs: dict[str, t.Any] = {}
            for key, value in output.items():
                new_output = self._save_output_files(
                    output=value, cog_output_spec=cog_output_spec.dict_items[key]
                )
                new_dict_outputs[key] = new_output
            return new_dict_outputs

        if (
            cog_output_spec is not None
            and cog_output_spec.python_type == str
            and cog_output_spec.format == "uri"
            and isinstance(output, str)
            and output.startswith("data:")
        ):
            logger.debug("Saving output file...")
            mime_type = mimetypes.guess_type(output, strict=False)[0]
            if not mime_type:
                raise ValueError(f"Unknown MIME type for output: {output[:20]}...")
            file_ext = mimetypes.guess_extension(mime_type, strict=False)
            if not file_ext:
                raise ValueError(f"Unknown file extension for MIME type: {mime_type}")
            output_path = Path(f"/tmp/outputs/{str(uuid.uuid4())}{file_ext}")
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Remove prefix, eg "data:image/webp;base64,"
            data = output.split("base64,", 1)[-1]
            output_path.write_bytes(b64decode(data))
            return File(path=output_path, allow_out_of_context_creation=True)
        else:
            # else return original output
            return output

    def get_pipeline(self):
        input_variables: list[pipeline_schemas.IOVariable] = []

        if self.cog_model_inputs is None:
            raise ValueError("Cog model inputs not found")
        if self.cog_model_output is None:
            raise ValueError("Cog model output not found")

        for input in self.cog_model_inputs:
            input_variables.append(input.to_io_schema())

        output_variables: list[IOVariable] = [self.cog_model_output.to_io_schema()]

        return pipeline_schemas.Pipeline(
            # TODO - update with real pipeline name? not sure if actually used anywhere?
            name="cog-wrapper-pipeline",
            image="unknown",
            input_variables=input_variables,
            output_variables=output_variables,
            # TODO - what's this actually used for?
            extras=None,
        )
