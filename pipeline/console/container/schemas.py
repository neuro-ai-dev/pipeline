import typing as t

from pydantic.v1 import BaseModel

from pipeline.cloud.compute_requirements import Accelerator
from pipeline.cloud.schemas import cluster as cluster_schemas


class PythonRuntime(BaseModel):
    version: str
    requirements: list[str] | None

    class Config:
        extra = "forbid"


class RuntimeConfig(BaseModel):
    container_commands: list[str] | None
    python: PythonRuntime | None

    class Config:
        extra = "forbid"


class PipelineConfig(BaseModel):
    runtime: RuntimeConfig
    accelerators: list[Accelerator] = []
    accelerator_memory: int | None
    pipeline_graph: str
    pipeline_name: str = ""
    description: str | None = None
    readme: str | None = None
    extras: dict[str, t.Any] | None
    cluster: cluster_schemas.PipelineClusterConfig | None = None
    scaling_config_name: str | None = None

    class Config:
        extra = "forbid"
