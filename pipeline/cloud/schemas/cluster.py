from pydantic.v1 import BaseModel


class PipelineClusterConfig(BaseModel):
    id: str
    node_pool: str


class PipelineClusterGetLean(BaseModel):
    """A lean representation of a cluster when returned from an API call"""

    id: str
    node_pool: str
    # Optional for backward compatibility
    name: str | None = None
    provider: str | None = None
