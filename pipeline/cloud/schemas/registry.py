from pydantic.v1 import BaseModel


class RegistryInformation(BaseModel):
    url: str
    special_auth: bool
