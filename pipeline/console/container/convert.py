import subprocess
from argparse import Namespace

import yaml

from pipeline.container import docker_templates
from pipeline.util.frameworks import get_cog_image_name
from pipeline.util.logging import _print

from .schemas import PipelineConfig, PythonRuntime, RuntimeConfig


def convert(namespace: Namespace) -> None:
    framework = namespace.type

    _print(f"Initializing new pipeline from {framework}...", "INFO")

    pipeline_name = getattr(namespace, "name", None)
    if not pipeline_name:
        pipeline_name = input("Enter a name for your pipeline: ")

    if framework == "cog":
        config = convert_cog(pipeline_name)
    else:
        raise NotImplementedError(f"Framework {framework} not supported")

    with open(getattr(namespace, "file", "./pipeline.yaml"), "w") as f:
        f.write(yaml.dump(config.dict(), sort_keys=False))

    with open("./README.md", "w") as f:
        f.write(docker_templates.readme_template)

    _print(f"Successfully generated a new pipeline from {framework}.", "SUCCESS")
    _print(
        "Be sure to update the pipeline.yaml with the accelerators required by your "
        "pipeline",
        "WARNING",
    )


def convert_cog(pipeline_name: str) -> PipelineConfig:

    # check cog command exists
    try:
        subprocess.run(["cog", "--version"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        _print(
            "cog not found, please install cog first: https://github.com/replicate/cog",
            "ERROR",
        )
        raise

    # build cog image
    # tag image with a standardised name
    cog_image_name = get_cog_image_name(pipeline_name)
    subprocess.run(
        ["cog", "build", "-t", cog_image_name],
        check=True,
        # capture_output=True,
    )

    # Generate a pipeline config. Note that most of these fields will not be
    # used when wrapping a Cog pipeline
    config = PipelineConfig(
        # not used
        runtime=RuntimeConfig(
            container_commands=[],
            python=PythonRuntime(
                version="3.10",
                requirements=[],
            ),
        ),
        accelerators=[],
        # not used
        pipeline_graph="",
        pipeline_name=pipeline_name,
        accelerator_memory=None,
        # use a format which permits extra framework-specific options
        extras={"model_framework": {"framework": "cog", "save_output_files": False}},
        readme="README.md",
    )
    return config
