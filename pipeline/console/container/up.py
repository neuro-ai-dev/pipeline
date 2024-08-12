import subprocess
from argparse import Namespace
from pathlib import Path

import docker
import docker.errors
import yaml
from docker.types import DeviceRequest, LogConfig

from pipeline.util.logging import _print

from .schemas import PipelineConfig


def up_container(namespace: Namespace):
    _print("Starting container...", "INFO")
    config_file = Path(getattr(namespace, "file", "./pipeline.yaml"))

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

    image = pipeline_name
    additional_container = None
    additional_network = None
    extras = pipeline_config.extras or {}
    try:
        is_using_cog = extras.get("model_framework", {}).get("framework") == "cog"
    except Exception:
        pass

    if is_using_cog:
        try:
            additional_network = docker_client.networks.create(name="pipeline-net")
        except Exception as e:
            _print(f"Couldn't create network pipeline-net:\n{e}", "ERROR")
            return
        try:
            additional_container = _run_additional_container(
                docker_client=docker_client,
                image=f"{pipeline_name}--cog",
                ports=[5000],
                gpu_ids=gpu_ids,
                network=additional_network.name,
            )
        except docker.errors.NotFound as e:
            _print(f"Cog container did not start successfully:\n{e}", "ERROR")
            return

    volumes: list | None = None

    port = int(getattr(namespace, "port", "14300"))

    run_command = [
        "uvicorn",
        "pipeline.container.startup:create_app",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
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

    if is_using_cog:
        # our pipeline container in this instance is a wrapper pipeline that
        # already exists
        # TODO - update reference
        image = "localhost:5001/cog-wrapper-pipeline"
        environment_variables["MODEL_FRAMEWORK"] = "cog"
        environment_variables["COG_API_URL"] = (
            f"http://{additional_container.name}:5000"
        )

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

    # Stop container on python exit
    try:
        container = docker_client.containers.run(
            image=image,
            name=image.split("/")[-1],
            ports={f"{port}/tcp": int(port)},
            stderr=True,
            stdout=True,
            log_config=lc,
            remove=True,
            auto_remove=True,
            detach=True,
            device_requests=(
                [DeviceRequest(device_ids=gpu_ids, capabilities=[["gpu"]])]
                # GPUs to be used by additional container if specified
                if gpu_ids and not additional_container
                else None
            ),
            command=run_command,
            volumes=volumes,
            environment=environment_variables,
            network=additional_network.name if additional_network else None,
        )
    except docker.errors.NotFound as e:
        _print(f"Container did not start successfully:\n{e}", "ERROR")
        return

    _print(
        f"Container started on port {port}.\n\n\t\tView the live docs:\n\n\t\t\t http://localhost:{port}/redoc\n\n\t\tor live play:\n\n\t\t\t http://localhost:{port}/play\n",  # noqa
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
        except docker.errors.NotFound:
            _print("Container did not start successfully", "ERROR")
            break

    # TODO - handle clean up better (eg if exception is raised somewhere)
    if additional_container:
        additional_container.stop()
        # additional_container.remove()
    if additional_network:
        additional_network.remove()


def _run_additional_container(
    docker_client: docker.DockerClient,
    image: str,
    ports: list[int] | None = None,
    gpu_ids: list | None = None,
    env_vars: dict[str, str] | None = None,
    network: str | None = None,
):
    ports = ports or []
    lc = LogConfig(
        type=LogConfig.types.JSON,
        config={
            "max-size": "1g",
        },
    )
    container = docker_client.containers.run(
        image=image,
        name=image.split("/")[-1],
        ports={f"{port}/tcp": port for port in ports},
        stderr=True,
        stdout=True,
        log_config=lc,
        remove=True,
        auto_remove=True,
        detach=True,
        device_requests=(
            [DeviceRequest(device_ids=gpu_ids, capabilities=[["gpu"]])]
            if gpu_ids
            else None
        ),
        environment=env_vars or {},
        network=network,
    )
    return container
