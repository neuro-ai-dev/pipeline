import re
import typing as t

from pydantic.v1 import validator

from pipeline.cloud.schemas import BaseModel

POINTER_REGEX = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9-._/]*[a-zA-Z0-9]:[0-9A-Za-z][0-9A-Za-z-_.]{0,127}[0-9A-Za-z]$"  # noqa
)


class PointerGet(BaseModel):
    id: str
    pointer: str
    pipeline_id: str
    locked: bool


class PointerCreate(BaseModel):
    pointer_or_pipeline_id: str
    pointer: str
    locked: t.Optional[bool]

    @validator("pointer")
    def validate_pointer(cls, v):
        if POINTER_REGEX.match(v) is None:
            raise ValueError("Invalid pointer name")
        return v


class PointerPatch(BaseModel):
    pointer_or_pipeline_id: t.Optional[str]
    locked: t.Optional[bool]
