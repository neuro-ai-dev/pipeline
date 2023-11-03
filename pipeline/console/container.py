import json
import os
import shutil
import subprocess
import sys
import typing as t
from argparse import Namespace
from pathlib import Path

import docker
import yaml
from docker.errors import BuildError
from docker.types import DeviceRequest, LogConfig
from pydantic import BaseModel

from pipeline.cloud import http
from pipeline.cloud.compute_requirements import Accelerator
from pipeline.cloud.schemas import pipelines as pipelines_schemas
from pipeline.container import docker_templates
from pipeline.util.logging import _print


class PythonRuntime(BaseModel):
    python_version: str
    python_requirements: t.List[str] | None
    cuda_version: str | None = "11.4"

    class Config:
        extra = "forbid"


class RuntimeConfig(BaseModel):
    container_commands: t.List[str] | None
    python: PythonRuntime | None

    class Config:
        extra = "forbid"


class PipelineConfig(BaseModel):
    runtime: RuntimeConfig
    accelerators: t.List[Accelerator]
    accelerator_memory: int | None
    pipeline_graph: str
    pipeline_name: str = ""

    class Config:
        extra = "forbid"


# Define your data


PIPELINE_MODULE_NAME = "my_pipeline"
PIPELINE_GRAPH_NAME = "pipeline_graph"
PIPELINE_MODULE_FILE = PIPELINE_MODULE_NAME + ".py"
PIPELINE_YAML_FILE = "pipeline.yaml"

TEMPLATE_CODE = f"""
from pipeline import Pipeline, entity, pipe


@entity
class MyPipelineEntity:
    @pipe(run_once=True, on_startup=True)
    def load(self):
        pass


with Pipeline() as builder:
    pass

{PIPELINE_GRAPH_NAME} = builder.get_pipeline()
"""

YAML_DATA = {
    "runtime": {
        "container_commands": ["apt update -y", "apt install -y git"],
        "python": {"python_version": "3.10", "python_requirements": []},
    },
    "accelerators": [],
    "accelerator_memory": None,
    "pipeline_graph": f"{PIPELINE_MODULE_NAME}:{PIPELINE_GRAPH_NAME}",
    "pipeline_name": None,
}


def _init_container(namespace: Namespace):
    _print("Creating a new pipeline container project...", "INFO")
    path_attr = getattr(namespace, "path")
    path = Path(path_attr)
    if len(path.parts) < 1:
        _print("Specify a target directory the path.", "ERROR")
        return

    pipeline_module_path = os.path.join(path, PIPELINE_MODULE_FILE)
    yaml_file_path = os.path.join(path, PIPELINE_YAML_FILE)

    # Undo any actions in else clause
    try:
        os.makedirs(path)
        with open(pipeline_module_path, "w") as file:
            file.write(TEMPLATE_CODE)
        with open(yaml_file_path, "w") as file:
            yaml.dump(YAML_DATA, file, default_flow_style=False, sort_keys=False)
    except Exception as e:
        _print("Error creating project, cleaning up...", "ERROR")
        if os.path.exists(path):
            shutil.rmtree(path)
        raise e from e


def _up_container(namespace: Namespace):
    _print("Starting container...", "INFO")
    config_file = Path("./pipeline.yaml")

    if not config_file.exists():
        raise FileNotFoundError(f"Config file {config_file} not found")

    config = config_file.read_text()
    pipeline_config_yaml = yaml.load(config, Loader=yaml.FullLoader)

    pipeline_config = PipelineConfig.parse_obj(pipeline_config_yaml)

    pipeline_name = pipeline_config.pipeline_name
    docker_client = docker.from_env()
    lc = LogConfig(
        type=LogConfig.types.JSON,
        config={
            "max-size": "1g",
        },
    )
    volumes: list | None = None
    run_command = [
        "uvicorn",
        "pipeline.container.startup:create_app",
        "--host",
        "0.0.0.0",
        "--port",
        "14300",
        "--factory",
    ]

    environment_variables = dict()

    if getattr(namespace, "debug", False):
        run_command.append("--reload")
        current_path = Path("./").expanduser().resolve()
        if volumes is None:
            volumes = []

        volumes.append(f"{current_path}:/app/")
        environment_variables["DEBUG"] = "1"
        environment_variables["LOG_LEVEL"] = "DEBUG"
        environment_variables["FASTAPI_ENV"] = "development"

    if extra_volumes := getattr(namespace, "volume", None):
        if volumes is None:
            volumes = []

        for volume in extra_volumes:
            if ":" not in volume:
                raise ValueError(
                    f"Invalid volume {volume}, must be in format host_path:container_path"  # noqa
                )

            local_path = Path(volume.split(":")[0]).expanduser().resolve()
            container_path = Path(volume.split(":")[1])

            volumes.append(f"{local_path}:{container_path}")

    gpu_ids: list | None = None
    try:
        gpu_ids = [
            f"{i}"
            for i in range(
                0,
                len(
                    subprocess.check_output(
                        [
                            "nvidia-smi",
                            "-L",
                        ]
                    )
                    .decode()
                    .splitlines()
                ),
            )
        ]
    except Exception:
        gpu_ids = None

    # Stop container on python exit
    container = docker_client.containers.run(
        pipeline_name,
        ports={"14300/tcp": 14300},
        stderr=True,
        stdout=True,
        log_config=lc,
        remove=True,
        auto_remove=True,
        detach=True,
        device_requests=[DeviceRequest(device_ids=gpu_ids, capabilities=[["gpu"]])]
        if gpu_ids
        else None,
        command=run_command,
        volumes=volumes,
        environment=environment_variables,
    )

    _print(
        "Container started on port 14300, view the live docs: http://localhost:14300/redoc",  # noqa
        "SUCCESS",
    )

    while True:
        try:
            for line in container.logs(stream=True):
                print(line.decode("utf-8").strip())
        except KeyboardInterrupt:
            _print("Stopping container...", "WARNING")
            container.stop()
            # container.remove()
            break


def _build_container(namespace: Namespace):
    _print("Starting build service...", "INFO")
    template = docker_templates.template_1

    config_file = Path("./pipeline.yaml")

    if not config_file.exists():
        raise FileNotFoundError(f"Config file {config_file} not found")

    config = config_file.read_text()
    pipeline_config_yaml = yaml.load(config, Loader=yaml.FullLoader)

    pipeline_config = PipelineConfig.parse_obj(pipeline_config_yaml)

    extra_paths = "COPY ./examples/docker/ ./\n"

    if not pipeline_config.runtime:
        raise ValueError("No runtime config found")
    if not pipeline_config.runtime.python:
        raise ValueError("No python runtime config found")

    python_runtime = pipeline_config.runtime.python
    dockerfile_str = template.format(
        python_version=python_runtime.python_version,
        python_requirements=" ".join(python_runtime.python_requirements)
        if python_runtime.python_requirements
        else "",
        container_commands="".join(
            [
                "RUN " + command + " \n"
                for command in pipeline_config.runtime.container_commands or []
            ]
        ),
        pipeline_path=pipeline_config.pipeline_graph,
        extra_paths=extra_paths,
        pipeline_name=pipeline_config.pipeline_name,
        pipeline_image=pipeline_config.pipeline_name,
    )

    dockerfile_path = Path("./pipeline.dockerfile")
    dockerfile_path.write_text(dockerfile_str)
    docker_client = docker.from_env()
    try:
        new_container, build_logs = docker_client.images.build(
            # fileobj=dockerfile_path.open("rb"),
            path="../../",
            quiet=True,
            # custom_context=True,
            dockerfile="./examples/docker/pipeline.dockerfile",
            # tag="test",
            rm=True,
        )
    except BuildError as e:
        for info in e.build_log:
            if "stream" in info:
                for line in info["stream"].splitlines():
                    print(line)
            elif "errorDetail" in info:
                print(info["errorDetail"]["message"])
            else:
                print(info)
        raise e

    created_image_full_id = new_container.id.split(":")[1]
    created_image_short_id = created_image_full_id[:12]

    _print(f"Built container {created_image_short_id}", "SUCCESS")

    pipeline_repo = (
        pipeline_config.pipeline_name.split(":")[0]
        if ":" in pipeline_config.pipeline_name
        else pipeline_config.pipeline_name
    )
    # pipeline_tag = (
    #     pipeline_config.pipeline_name.split(":")[1]
    #     if ":" in pipeline_config.pipeline_name
    #     else None
    # )

    new_container.tag(pipeline_repo)
    _print(f"Created tag {pipeline_repo}", "SUCCESS")

    new_container.tag(pipeline_repo, tag=created_image_short_id)
    _print(f"Created tag {pipeline_repo}:{created_image_short_id}", "SUCCESS")

    # if pipeline_tag:
    #     new_container.tag(pipeline_repo, tag=pipeline_tag)
    #     _print(f"Created tag {pipeline_repo}:{pipeline_tag}", "SUCCESS")


def _push_container(namespace: Namespace):
    """

    Upload protocol:
    1. Request upload URL from server, along with auth token
    2. Upload to URL with auth token
    3. Send complete request to server

    """

    config_file = Path("./pipeline.yaml")

    if not config_file.exists():
        raise FileNotFoundError(f"Config file {config_file} not found")

    config = config_file.read_text()
    pipeline_config_yaml = yaml.load(config, Loader=yaml.FullLoader)

    pipeline_config = PipelineConfig.parse_obj(pipeline_config_yaml)

    pipeline_name = (
        pipeline_config.pipeline_name.split(":")[0]
        if ":" in pipeline_config.pipeline_name
        else pipeline_config.pipeline_name
    )

    docker_client = docker.from_env()

    # docker_client.images.push(pipeline_name)
    start_upload_response = http.post(
        endpoint="/v4/registry/start-upload",
        json_data={
            "pipeline_name": pipeline_name,
            "pipeline_tag": None,
        },
    )
    start_upload_dict = start_upload_response.json()

    upload_registry = start_upload_dict.get("upload_registry", None)
    upload_token = start_upload_dict.get("bearer", None)

    if upload_token is None:
        raise ValueError("No upload token found")

    # get list of tags for image

    # Get image hash
    image_hash = docker_client.images.get(pipeline_name).id.split(":")[1]
    # print(image_hash)
    # exit()

    hash_tag = image_hash[:12]
    image_to_push = pipeline_name + ":" + hash_tag
    image_to_push_reg = upload_registry + "/" + image_to_push

    if upload_registry is None:
        _print("No upload registry found, not doing anything...", "WARNING")
    else:
        _print(f"Pushing image to upload registry {upload_registry}", "INFO")

        docker_client.images.get(pipeline_name).tag(image_to_push_reg)

        # Login to upload registry
        docker_client.login(
            username="pipeline",
            password=upload_token,
            registry="http://" + upload_registry,
        )

        resp = docker_client.images.push(
            image_to_push_reg,
            auth_config=dict(username="pipeline", password=upload_token),
            stream=True,
            decode=True,
        )

        all_ids = []

        current_index = 0

        for line in resp:
            if "error" in line:
                raise ValueError(line["error"])
            elif "status" in line:
                if "id" not in line or line["status"] != "Pushing":
                    continue

                if "id" in line and line["id"] not in all_ids:
                    all_ids.append(line["id"])
                    print("Adding")

                index_difference = all_ids.index(line["id"]) - current_index
                # print(index_difference)

                print_string = (
                    line["id"]
                    + " "
                    + line["progress"].replace("\n", "").replace("\r", "")
                )

                if index_difference > 0:
                    print_string += "\n" * index_difference + "\r"
                    # print("up")
                elif index_difference < 0:
                    print_string += "\033[A" * abs(index_difference) + "\r"
                    # print("down")
                else:
                    print_string += "\r"
                    # print("same")
                current_index = all_ids.index(line["id"])

                sys.stdout.write(print_string)
                sys.stdout.flush()

            # print(line)

    new_deployment_request = http.post(
        endpoint="/v4/pipelines",
        json_data=json.loads(
            pipelines_schemas.PipelineCreate(
                name=pipeline_name,
                image=image_to_push,
                input_variables=[],
                output_variables=[],
                minimum_cache_number=None,
                maximum_cache_number=None,
                gpu_memory_min=pipeline_config.accelerator_memory,
                accelerators=pipeline_config.accelerators,
                extras={},
            ).json()
        ),
    )

    new_deployment = pipelines_schemas.PipelineGet.parse_obj(
        new_deployment_request.json()
    )

    _print(
        f"Created new pipeline deployment for {new_deployment.name} -> {new_deployment.id} (image={new_deployment.image})",  # noqa
        "SUCCESS",
    )
