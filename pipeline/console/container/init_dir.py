from argparse import Namespace

import yaml

from pipeline.container import docker_templates
from pipeline.util.logging import _print

from .schemas import PipelineConfig, PythonRuntime, RuntimeConfig


def init_dir(namespace: Namespace) -> None:
    _print("Initializing directory...", "INFO")

    pipeline_name = getattr(namespace, "name", None)

    if not pipeline_name:
        pipeline_name = input("Enter a name for your pipeline: ")

    python_template = docker_templates.pipeline_template_python

    default_config = PipelineConfig(
        runtime=RuntimeConfig(
            container_commands=[
                "apt-get update",
                "apt-get install -y git",
            ],
            python=PythonRuntime(
                version="3.10",
                requirements=[
                    "pipeline-ai",
                ],
            ),
        ),
        accelerators=[],
        pipeline_graph="new_pipeline:my_new_pipeline",
        pipeline_name=pipeline_name,
        accelerator_memory=None,
        extras={},
        readme="README.md",
    )
    with open(getattr(namespace, "file", "./pipeline.yaml"), "w") as f:
        f.write(yaml.dump(default_config.dict(), sort_keys=False))

    with open("./new_pipeline.py", "w") as f:
        f.write(python_template)

    with open("./README.md", "w") as f:
        f.write(docker_templates.readme_template)

    _print("Initialized directory.", "SUCCESS")
