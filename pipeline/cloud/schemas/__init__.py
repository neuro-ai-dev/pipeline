import os
from datetime import datetime, timezone

import humps
from pydantic.v1 import BaseModel as _BaseModel
from pydantic.v1 import Extra, validator
from pydantic.v1.generics import GenericModel as _GenericModel

CAMEL_CASE_ALIASES = bool(os.environ.get("CAMEL_CASE_ALIASES", False))


def _generate_alias(s):
    return humps.camelize(s) if CAMEL_CASE_ALIASES else s


class BaseConfig:
    alias_generator = _generate_alias
    allow_population_by_field_name = True
    # Encode datetime objects to JSON as integer timestamps
    json_encoders = {datetime: datetime.timestamp}
    orm_mode = True


class BaseModel(_BaseModel):
    """Base model for schemas."""

    class Config(BaseConfig):
        pass

    @validator("*", pre=True)
    def convert_to_utc(cls, value):
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        return value


class GenericModel(_GenericModel):
    """Base model for schemas with generic typing."""

    class Config(BaseConfig):
        pass


class Patchable(BaseModel):
    """Tag type for marking schemas for patching.

    A Patchable schema is one used for updating other schemas.
    """

    class Config:
        # Forbid fields not specified in the model; this prevents silently
        # failing updates to patch non-matching fields
        extra = Extra.forbid
