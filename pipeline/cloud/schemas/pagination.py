import typing as t
from enum import Enum
from math import ceil

from pydantic.v1 import conint

from . import BaseModel, GenericModel

DataType = t.TypeVar("DataType")

PAGINATION_LIMIT = 10_000
DEFAULT_LIMIT = 1000


class Pagination(BaseModel):
    """Query parameters for requesting a set of items of a given resource,
    in paginated form."""

    #: Index of the first item to return within the total queryset
    skip: conint(ge=0)  # type: ignore

    #: Maximum number of items to return
    limit: conint(ge=1, le=PAGINATION_LIMIT)  # type: ignore


class Paginated(GenericModel, t.Generic[DataType]):
    """Response model for a paginated set of a given resource."""

    #: Index of the first item to return within the total queryset
    skip: conint(ge=0)  # type: ignore

    #: Maximum number of items the `data` field will contain
    limit: conint(ge=1, le=PAGINATION_LIMIT)  # type: ignore

    #: Number of items within the total queryset
    total: conint(ge=0)  # type: ignore

    #: Resource data, which should be immutable
    data: t.Sequence[DataType]

    @classmethod
    def of(cls, data: t.Sequence[DataType], pagination: Pagination, total: int):
        return Paginated(**pagination.dict(), total=total, data=data)


def get_default_pagination():
    return Pagination(skip=0, limit=DEFAULT_LIMIT)


class PagePosition(t.TypedDict):
    current: int
    total: int


def to_page_position(skip: int, items_per_page: int, total_items: int) -> PagePosition:
    """Converts cursor position into page position"""
    current_page = skip // items_per_page + 1
    total_pages = max(1, ceil(total_items / items_per_page))
    return PagePosition(current=current_page, total=total_pages)


class Order(str, Enum):
    descending = "descending"
    ascending = "ascending"
