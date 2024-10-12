import io
import json
import typing as t
from datetime import datetime
from enum import Enum
from urllib.parse import quote, unquote

from pydantic.v1 import root_validator, validator

from pipeline.cloud.schemas import BaseModel


class RunState(str, Enum):
    created = "created"
    routing = "routing"
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    no_resources_available = "no_resources_available"
    node_preempted = "node_preempted"
    unknown = "unknown"

    @staticmethod
    def is_terminal(state: "RunState") -> bool:
        return state in RunState.terminal_states()

    @classmethod
    def terminal_states(cls) -> list["RunState"]:
        return [
            RunState.completed,
            RunState.failed,
            RunState.no_resources_available,
            RunState.node_preempted,
        ]

    @classmethod
    def __get_validators__(cls):
        cls.lookup = {v: k.value for v, k in cls.__members__.items()}
        cls.value_lookup = {k.value: v for v, k in cls.__members__.items()}
        yield cls.validate

    @classmethod
    def validate(cls, v):
        try:
            v = int(v)
        except Exception:
            ...

        if isinstance(v, str):
            try:
                state = cls.lookup[v]
            except KeyError:
                state = cls.unknown
            return state
        elif isinstance(v, str):
            try:
                state = getattr(cls, cls.value_lookup[v])
            except KeyError:
                state = cls.unknown
            return state
        else:
            raise ValueError(f"Invalid value: {v}")


class RunErrorType(Enum):
    input_error = 1
    unroutable = 2
    graph_error = 3
    runtime_error = 4


class RunError(BaseModel):
    exception: str
    traceback: t.Optional[str]


class RunFileType(Enum):
    input = "input"
    output = "output"


class RunFile(BaseModel):
    id: str
    run_id: str
    io_type: RunFileType
    path: str

    class Config:
        # use_enum_values = True
        orm_mode = True


class RunIOType(str, Enum):
    integer = "integer"
    string = "string"
    fp = "fp"
    dictionary = "dictionary"
    boolean = "boolean"
    none = "none"
    array = "array"
    pkl = "pkl"
    file = "file"
    stream = "stream"

    @classmethod
    def from_object(cls, obj: t.Any):
        # Get the enum type for the object.
        from pipeline.objects.graph import File

        if isinstance(obj, int) or obj is int:
            return cls.integer
        elif isinstance(obj, float) or obj is float:
            return cls.fp
        elif isinstance(obj, str) or obj is str:
            return cls.string
        elif isinstance(obj, bool) or obj is bool:
            return cls.boolean
        elif obj is None:
            return cls.none
        elif isinstance(obj, dict) or obj is dict:
            if obj is dict:
                return cls.dictionary
            # The below try/except means that dict inputs
            # with file-like values will be treated as pkl
            try:
                json.dumps(obj)
            except (TypeError, OverflowError):
                return cls.pkl
            return cls.dictionary
        elif isinstance(obj, list) or obj is list:
            if obj is list:
                return cls.array
            try:
                json.dumps(obj)
            except (TypeError, OverflowError):
                return cls.pkl
            return cls.array
        # Also check if class name == Stream
        elif hasattr(obj, "iterable") or (
            hasattr(obj, "__name__") and obj.__name__ == "Stream"
        ):
            return cls.stream
        elif (
            isinstance(obj, io.BufferedIOBase)
            or isinstance(obj, io.IOBase)
            or obj is File
            or isinstance(obj, File)
        ):
            return cls.file
        else:
            return cls.pkl

    @staticmethod
    def to_object(io_type: "RunIOType"):
        if isinstance(io_type, str):
            io_type = RunIOType(io_type)

        if io_type == RunIOType.integer:
            return int
        elif io_type == RunIOType.fp:
            return float
        elif io_type == RunIOType.string:
            return str
        elif io_type == RunIOType.boolean:
            return bool
        elif io_type == RunIOType.none:
            return None
        elif io_type == RunIOType.dictionary:
            return dict
        elif io_type == RunIOType.array:
            return list
        else:
            raise ValueError(f"Invalid io_type: {io_type}")


class RunOutputFile(BaseModel):
    name: str
    path: str
    url: t.Optional[str]
    size: int


class RunOutput(BaseModel):
    type: RunIOType
    value: t.Optional[t.Any]
    file: t.Optional[RunOutputFile]


class RunResult(BaseModel):
    run_id: str
    outputs: t.List[RunOutput]

    def result_array(self) -> t.List[t.Any]:
        # return [output.value for output in self.outputs]
        output_array = []
        for output in self.outputs:
            if output.type == RunIOType.file:
                # output_array.append(output.value)
                from pipeline.objects.graph import File

                if output.file is not None and output.file.url is not None:
                    output_array.append(File(url=output.file.url))
                else:
                    raise Exception("Returned file missing information.")
            else:
                output_array.append(output.value)
        return output_array


class RunInput(BaseModel):
    type: RunIOType
    value: t.Any
    file_name: t.Optional[str]
    file_path: t.Optional[str]
    file_url: t.Optional[str]

    @validator("file_url", pre=True, always=True)
    def encode_url(cls, v):
        if v is not None:
            # check whether has already been encoded, to avoid
            # multiple encoding
            if v != unquote(v):
                return v
            return quote(v, safe="/:")
        return v

    @classmethod
    def encode_nested_urls(cls, value):
        if isinstance(value, dict):
            for key, val in value.items():
                if key == "file_url" and isinstance(val, str):
                    value[key] = cls.encode_url(val)
                elif isinstance(val, (dict, list)):
                    cls.encode_nested_urls(val)
        elif isinstance(value, list):
            for item in value:
                cls.encode_nested_urls(item)
        return value

    @root_validator(pre=True)
    def handle_nested_inputs(cls, values):
        if "value" in values:
            values["value"] = cls.encode_nested_urls(values["value"])
        return values


class ContainerRunErrorType(str, Enum):
    input_error = "input_error"
    cuda_oom = "cuda_oom"
    cuda_error = "cuda_error"
    oom = "oom"
    pipeline_error = "pipeline_error"
    startup_error = "startup_error"
    pipeline_loading = "pipeline_loading"
    timeout = "timeout"
    unknown = "unknown"


class ContainerRunError(BaseModel):
    type: ContainerRunErrorType
    message: str
    traceback: t.Optional[str]


class ContainerRunCreate(BaseModel):
    # run_id is optional since it's just used for attaching logs to a run
    run_id: str | None
    inputs: list[RunInput]
    async_run: bool = False
    callback_url: str | None


class ContainerRunResult(BaseModel):
    inputs: list[RunInput] | None
    outputs: list[RunOutput] | None
    error: ContainerRunError | None

    def outputs_formatted(self) -> t.List[t.Any]:
        outputs = self.outputs or []
        output_array = []
        for output in outputs:
            if output.type == RunIOType.file:
                from pipeline.objects.graph import File

                if output.file is not None and output.file.url is not None:
                    output_array.append(File(url=output.file.url))
                else:
                    raise Exception("Returned file missing information.")
            else:
                output_array.append(output.value)
        return output_array


class ClusterRunResult(ContainerRunResult):
    id: str

    created_at: datetime
    updated_at: datetime

    pipeline_id: str

    state: RunState

    # queue position if run is in 'queued' state
    queue_position: int | None = None

    class Config:
        orm_mode = True


class RunStateTransition(BaseModel):
    run_id: str
    new_state: RunState
    time: datetime


class RunStateTransitions(BaseModel):
    """View for all state transitions of a given run"""

    data: t.List[RunStateTransition]


class RunCreate(BaseModel):
    # pipeline id or pointer
    pipeline: str
    inputs: list[RunInput]
    async_run: bool = False
    # flag to determine whether the run will wait for compute resources to be
    # become available if none are currently running the pipeline
    wait_for_resources: bool | None = None
    # run_id is not used and should not be provided (it is kept here for
    # backwards compatibility)
    run_id: str | None = None
