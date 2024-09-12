import subprocess
from argparse import Namespace
from pathlib import Path

import docker
import docker.errors
import yaml
from docker.types import DeviceRequest, LogConfig

from pipeline.util.frameworks import get_cog_image_name, is_using_cog
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

    gpu_ids: list[str] | None = None
    try:
        gpu_ids = [
            str(i)
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

    environment_variables = {}
    image = pipeline_name
    additional_container = None
    additional_network = None
    is_cog = is_using_cog(pipeline_config.extras)

    try:

        if is_cog:
            try:
                additional_network = docker_client.networks.create(name="pipeline-net")
            except Exception as e:
                _print(f"Couldn't create network pipeline-net:\n{e}", "ERROR")
                return
            try:
                additional_container = _run_additional_container(
                    docker_client=docker_client,
                    image=get_cog_image_name(pipeline_name),
                    ports=[5000],
                    gpu_ids=gpu_ids,
                    network=additional_network.name,
                )
            except docker.errors.NotFound as e:
                _print(f"Cog container did not start successfully:\n{e}", "ERROR")
                return

            # our pipeline container in this instance is a wrapper pipeline that
            # already exists
            image = "mysticai/cog-wrapper-pipeline"
            environment_variables["MODEL_FRAMEWORK"] = "cog"
            environment_variables["COG_API_URL"] = (
                f"http://{additional_container.name}:5000"
            )
            extras = pipeline_config.extras or {}
            model_framework_options = extras.get("model_framework", {})
            save_output_files = model_framework_options.get("save_output_files", False)
            # the default is false
            if save_output_files:
                environment_variables["SAVE_OUTPUT_FILES"] = "true"

        port = int(getattr(namespace, "port", "14300"))
        debug = getattr(namespace, "debug", False)
        volumes = getattr(namespace, "volume", None)
        try:
            container = _run_pipeline_container(
                docker_client=docker_client,
                image=image,
                port=port,
                gpu_ids=gpu_ids if not additional_container else None,
                environment_variables=environment_variables,
                # if running Cog then we use GPUs for that container, not
                # pipeline wrapper
                network=additional_network.name if additional_network else None,
                extra_volumes=volumes,
                debug=debug,
            )
        except docker.errors.NotFound:
            return

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

    finally:
        # Ensure we always clean up additional resources
        if additional_container:
            additional_container.stop()
            # additional_container.remove()
        if additional_network:
            additional_network.remove()


def _run_pipeline_container(
    docker_client: docker.DockerClient,
    image: str,
    port: int,
    gpu_ids: list[str] | None,
    environment_variables: dict[str, str] | None,
    network: str | None = None,
    extra_volumes: list[str] | None = None,
    debug: bool = False,
):
    gpu_ids = gpu_ids or []
    environment_variables = environment_variables or {}
    lc = LogConfig(
        type=LogConfig.types.JSON,
        config={
            "max-size": "1g",
        },
    )
    volumes = []

    run_command = [
        "uvicorn",
        "pipeline.container.startup:create_app",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--factory",
    ]

    if debug:
        run_command.append("--reload")
        current_path = Path("./").expanduser().resolve()

        volumes.append(f"{current_path}:/app/")
        environment_variables["DEBUG"] = "1"
        environment_variables["LOG_LEVEL"] = "DEBUG"
        environment_variables["FASTAPI_ENV"] = "development"

    if extra_volumes:
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
                if gpu_ids
                else None
            ),
            command=run_command,
            volumes=volumes,
            environment=environment_variables,
            network=network,
        )
    except docker.errors.NotFound as e:
        _print(f"Container did not start successfully:\n{e}", "ERROR")
        raise

    _print(
        f"Container started on port {port}.\n\n\t\tView the live docs:\n\n\t\t\t http://localhost:{port}/redoc\n\n\t\tor live play:\n\n\t\t\t http://localhost:{port}/play\n",  # noqa
        "SUCCESS",
    )
    return container


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
