import json
import os
import sys
import typing as t
from pathlib import Path

import yaml
from pydantic.v1 import BaseModel

PIPELINE_DIR = Path(
    os.getenv(
        "PIPELINE_DIR",
        (
            Path(os.getenv("LOCALAPPDATA")) / ".pipeline/"
            if (sys.platform == "win32" or sys.platform == "cygwin")
            else Path.home() / ".pipeline/"
        ),
    )
)


class _RemoteModel(BaseModel):
    alias: t.Optional[str]
    url: str
    token: t.Optional[str]


class _ConfigurationModel(BaseModel):
    active_remote: t.Optional[str]
    remotes: t.Optional[t.List[_RemoteModel]]


class Configuration:
    def __init__(
        self,
    ) -> None:
        self._config: _ConfigurationModel | None = None

        PIPELINE_DIR.mkdir(exist_ok=True)
        (PIPELINE_DIR / "files").mkdir(exist_ok=True)
        self._debug_mode = os.environ.get("PIPELINE_DEBUG", "0") == "1"

    @property
    def active_remote(self) -> _RemoteModel | None:
        if self._config is not None and self._config.remotes is not None:
            if self._config.active_remote is not None:
                for remote in self._config.remotes:
                    if remote.alias == self._config.active_remote:
                        return remote
            else:
                if len(self._config.remotes) > 0:
                    new_remote_alias = self._config.remotes[0].alias
                    if new_remote_alias is None:
                        raise ValueError("Remote alias cannot be None")
                    self.set_active_remote(new_remote_alias)

                    return self._config.remotes[0]

                print("No active remote set, please run 'pipeline cluster use <name>'")
        return None

    def set_debug_mode(self, debug: bool) -> None:
        self._debug_mode = debug

    def is_debugging(self) -> bool:
        return self._debug_mode

    @property
    def files_cache(self) -> Path:
        return PIPELINE_DIR / "files"

    @property
    def remotes(self) -> t.List[_RemoteModel] | None:
        if self._config is None:
            return None
        return self._config.remotes

    def load(self) -> None:
        path = PIPELINE_DIR / "config.yaml"
        if not path.exists():
            self._config = _ConfigurationModel(
                active_remote=None,
                remotes=None,
            )
            return

        try:
            with open(path, "r") as configuration_file:
                self._config = _ConfigurationModel.parse_obj(
                    yaml.load(
                        configuration_file,
                        Loader=yaml.FullLoader,
                    ),
                )
        except Exception:
            raise Exception("Invalid configuration file at '~/.pipeline/config.yaml'")

    def save(self) -> None:
        if self._config is None:
            raise ValueError("No configuration loaded")

        path = PIPELINE_DIR / "config.yaml"

        PIPELINE_DIR.mkdir(exist_ok=True)

        with open(path, "w") as configuration_file:
            yaml.dump(
                json.loads(
                    self._config.json(
                        exclude_none=True,
                    )
                ),
                configuration_file,
            )

    def set_active_remote(self, alias: str) -> None:
        if self._config is None:
            raise ValueError("No configuration loaded")

        if self._config.remotes is None:
            raise ValueError("No remotes configured")

        if not any([remote.alias == alias for remote in self._config.remotes]):
            raise ValueError(f"Remote with alias '{alias}' does not exist")

        self._config.active_remote = alias

        self.save()

    def add_remote(
        self,
        alias: str,
        url: str,
        token: str,
    ) -> None:
        if self._config is None:
            raise ValueError("No configuration loaded")

        if self._config.remotes is None:
            self._config.remotes = []

        if any([remote.alias == alias for remote in self._config.remotes]):
            raise ValueError(f"Remote with alias '{alias}' already exists")

        self._config.remotes.append(
            _RemoteModel(
                alias=alias,
                url=url,
                token=token,
            )
        )

        if self._config.active_remote is None:
            self._config.active_remote = alias

        self.save()

    def remove_remote(self, alias: str) -> None:
        if self._config is None:
            raise ValueError("No configuration loaded")

        if self._config.remotes is None:
            return

        alias_index_array = [
            idx for idx, obj in enumerate(self._config.remotes) if obj.alias == alias
        ]
        if len(alias_index_array) == 0:
            raise ValueError(f"Remote with alias '{alias}' does not exist")

        self._config.remotes.pop(alias_index_array[0])


current_configuration = Configuration()
current_configuration.load()
