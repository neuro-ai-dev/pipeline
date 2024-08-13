import hashlib
import importlib
import os
import traceback
import typing as t
import urllib.parse
from abc import ABC, abstractmethod
from http.client import InvalidURL
from pathlib import Path
from types import NoneType, UnionType
from urllib import request
from urllib.parse import urlparse

import yaml
from loguru import logger

from pipeline.cloud.schemas import pipelines as pipeline_schemas
from pipeline.cloud.schemas import runs as run_schemas
from pipeline.exceptions import RunInputException, RunnableError
from pipeline.objects import Directory, File, Graph
from pipeline.objects.graph import InputSchema


def is_url(string):
    try:
        result = urlparse(string)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


def _get_url_or_path(input_schema: run_schemas.RunInput) -> str | None:
    return input_schema.file_url if input_schema.file_url else input_schema.file_path


class Manager(ABC):
    def __init__(self):
        self.pipeline_state: pipeline_schemas.PipelineState = (
            pipeline_schemas.PipelineState.not_loaded
        )
        self.pipeline_state_message: str | None = None
        self.current_run: str | None = None

    def startup_pipeline(self):
        if self.pipeline_state == pipeline_schemas.PipelineState.load_failed:
            return
        # add context to enable fetching of startup logs
        with logger.contextualize(pipeline_stage="startup"):
            self.startup()

    def run_pipeline(
        self, run_id: str | None, input_data: t.List[run_schemas.RunInput] | None
    ) -> t.Any:
        with logger.contextualize(run_id=run_id):
            self.current_run = run_id
            try:
                result = self.run(input_data)
            except RunInputException:
                raise
            except Exception as exc:
                raise RunnableError(exception=exc, traceback=traceback.format_exc())
            finally:
                self.current_run = None
            logger.info("Run successful")
            return result

    @abstractmethod
    def startup(self) -> None:
        raise NotImplementedError()

    @abstractmethod
    def run(self, input_data: t.List[run_schemas.RunInput] | None) -> t.Any:
        raise NotImplementedError()

    @abstractmethod
    def get_pipeline(self) -> pipeline_schemas.Pipeline:
        raise NotImplementedError()


class PipelineManager(Manager):
    def __init__(self, pipeline_path: str):
        super().__init__()

        try:
            self._load(pipeline_path)
        except Exception:
            tb = traceback.format_exc()
            logger.exception("Exception raised when loading pipeline")
            self.pipeline_state = pipeline_schemas.PipelineState.load_failed
            self.pipeline_state_message = tb
            return

    def _load(self, pipeline_path: str):
        with logger.contextualize(pipeline_stage="loading"):
            logger.info("Loading pipeline")

            if ":" not in pipeline_path:
                raise ValueError(
                    "Invalid pipeline path, "
                    + "must be in format <module_or_file>:<pipeline>"
                )
            if len(pipeline_path.split(":")) != 2:
                raise ValueError(
                    "Invalid pipeline path, "
                    + "must be in format <module_or_file>:<pipeline>"
                )

            self.pipeline_path = pipeline_path
            self.pipeline_module_str, self.pipeline_name_str = pipeline_path.split(":")

            self.pipeline_state = pipeline_schemas.PipelineState.loading
            try:
                self.pipeline_module = importlib.import_module(self.pipeline_module_str)

                self.pipeline: Graph = getattr(
                    self.pipeline_module, self.pipeline_name_str
                )
            except ModuleNotFoundError as e:
                raise ValueError(
                    f"Could not load module {self.pipeline_module_str}, {e}"
                )
            except AttributeError:
                raise ValueError(
                    (
                        f"Could not find pipeline {self.pipeline_name_str} in module"
                        f" {self.pipeline_module_str}"
                    )
                )

            self.pipeline_name = os.environ.get("PIPELINE_NAME", "unknown")
            self.pipeline_image = os.environ.get("PIPELINE_IMAGE", "unknown")

            logger.info(f"Pipeline set to {self.pipeline_path}")

    def startup(self):
        logger.info("Starting pipeline")
        try:
            self.pipeline._startup()
        except Exception:
            tb = traceback.format_exc()
            logger.exception("Exception raised during pipeline startup")
            self.pipeline_state = pipeline_schemas.PipelineState.startup_failed
            self.pipeline_state_message = tb
        else:
            self.pipeline_state = pipeline_schemas.PipelineState.loaded
            logger.info("Pipeline started successfully")

    def _resolve_file_variable_to_local(
        self,
        file: File | Directory,
        *,
        use_tmp: bool = False,
    ) -> None:
        local_host_dir = "/tmp"

        if hasattr(file, "url") and file.url is not None:
            # Encode the URL to handle spaces and other non-URL-safe characters
            encoded_url = run_schemas.RunInput.encode_url(file.url.geturl())
            cache_name = hashlib.md5(file.url.geturl().encode()).hexdigest()

            file_name = file.url.geturl().split("/")[-1]
            local_path = f"{local_host_dir}/{cache_name}/{file_name}"
            file_path = Path(local_path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Use the encoded URL for retrieving the file
                request.urlretrieve(encoded_url, local_path)
            # This should not be raise due to encoded_url, but including it in case
            except InvalidURL:
                raise Exception("The file to download has an invalid URL.")
            except Exception:
                raise Exception("Error downloading file.")

        elif file.remote_id is not None or file.path is not None:
            local_path = Path(file.path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            raise Exception("File not found, must pass in URL, Path, or Remote ID.")

        if isinstance(file, Directory):
            raise NotImplementedError("Remote ID not implemented yet")

        file.path = Path(local_path)

    def _create_file_variable(
        self,
        *,
        path_or_url: str | None = None,
        is_directory: bool = False,
        use_tmp: bool = False,
    ) -> File | Directory:
        path: str | None = None
        url: str | None = None

        if is_url(path_or_url):
            url = str(urllib.parse.urlparse(path_or_url).geturl())
        else:
            path = path_or_url

        if path is None and url is None:
            raise Exception("Must provide either path or url")

        variable: File | Directory | None = None

        if is_directory:
            variable = Directory(path=path, url=url)
        else:
            variable = File(path=path, url=url)

        self._resolve_file_variable_to_local(variable, use_tmp=use_tmp)
        return variable

    def _parse_inputs(
        self, input_data: t.List[run_schemas.RunInput] | None
    ) -> t.List[t.Any]:
        inputs = []
        if input_data is None:
            input_data = []
            return input_data
        if len(input_data) > 0:
            for item in input_data:
                input_schema = run_schemas.RunInput.parse_obj(item)
                if input_schema.type == run_schemas.RunIOType.file:
                    if input_schema.file_path is None and input_schema.file_url is None:
                        raise RunInputException(
                            "A file must either have a path or url attribute"
                        )
                    path_or_url = _get_url_or_path(input_schema)
                    if path_or_url is None:
                        raise RunInputException(
                            "A file must either have a path or url attribute"
                        )

                    variable = self._create_file_variable(path_or_url=path_or_url)
                    inputs.append(
                        variable,
                    )

                else:
                    inputs.append(input_schema.value)
        graph_inputs = list(filter(lambda v: v.is_input, self.pipeline.variables))

        if len(inputs) != len(graph_inputs):
            raise RunInputException("Inputs do not match graph inputs")

        final_inputs = []

        for i, (user_input, model_input) in enumerate(zip(inputs, graph_inputs)):
            target_type = model_input.type_class
            if issubclass(target_type, InputSchema) and isinstance(user_input, dict):
                # schema_instance = target_type()
                annotations = target_type.__annotations__
                for key, value in annotations.items():
                    if isinstance(value, UnionType) or "typing.Optional" in str(value):
                        var_union_types = list(t.get_args(value))
                        if len(var_union_types) > 2:
                            raise RunInputException("Only support Union of 2 types")
                        if NoneType in var_union_types:
                            var_union_types.remove(NoneType)
                        else:
                            raise RunInputException("Only support Union with None")
                        var_type = var_union_types[0]
                    else:
                        var_type = value

                    if var_type == File:
                        if user_input.get(key) is None:
                            continue
                        file_schema = run_schemas.RunInput.parse_obj(user_input[key])
                        path_or_url = _get_url_or_path(file_schema)
                        variable = self._create_file_variable(
                            path_or_url=path_or_url,
                            use_tmp=True,
                        )
                        user_input[key] = variable

            final_inputs.append(user_input)
        return final_inputs

    def run(self, input_data: list[run_schemas.RunInput] | None) -> t.Any:
        logger.info("Running pipeline")
        args = self._parse_inputs(input_data)
        result = self.pipeline.run(*args)
        return result

    def get_pipeline(self) -> pipeline_schemas.Pipeline:
        input_variables: list[pipeline_schemas.IOVariable] = []
        output_variables: list[pipeline_schemas.IOVariable] = []

        for variable in self.pipeline.variables:
            if variable.is_input:
                input_variables.append(variable.to_io_schema())

            if variable.is_output:
                output_variables.append(variable.to_io_schema())

        # Load the YAML file to get the 'extras' field
        try:
            with open("/app/pipeline.yaml", "r") as file:
                pipeline_config = yaml.safe_load(file)
                extras = pipeline_config.get("extras", {})
        except Exception as e:
            raise Exception(f"Failed to load pipeline configuration: {str(e)}")

        return pipeline_schemas.Pipeline(
            name=self.pipeline_name,
            image=self.pipeline_image,
            input_variables=input_variables,
            output_variables=output_variables,
            extras=extras,
        )
