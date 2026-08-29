import logging
import os
import re
import socket
from logging.config import dictConfig
from subprocess import run

from rcon.types import ServerInfo
from rcon.user_config.rcon_server_settings import RconServerSettingsUserConfig

try:
    TAG_VERSION = (
        run(["git", "describe", "--tags"], check=False, capture_output=True)
        .stdout.decode()
        .strip()
    )
except Exception:  # noqa
    TAG_VERSION = "unknown"

_ENVIRONMENT: str | None = None


def _get_environment() -> str:
    # Deferred until the first log line is actually emitted rather than run
    # at import time: every one of the 14+ supervisord-managed processes
    # per game server imports this module, and eagerly calling
    # load_from_db() here meant each of them paid a live Postgres round
    # trip (and opened a DB connection/engine) before doing anything else,
    # just to compute a cosmetic log-line prefix.
    global _ENVIRONMENT
    if _ENVIRONMENT is None:
        try:
            config = RconServerSettingsUserConfig.load_from_db()
            _ENVIRONMENT = re.sub(
                "[^0-9a-zA-Z]+", "", (config.short_name or "default").strip()
            )[:64]
        except Exception:  # noqa
            _ENVIRONMENT = "undefined"
    return _ENVIRONMENT


class _EnvironmentFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.environment = _get_environment()
        return True


def get_server_info() -> ServerInfo:
    return ServerInfo.from_env()


def check_config() -> ServerInfo:
    server_info = get_server_info()
    for k, v in server_info.as_dict().items():
        if not v:
            raise ValueError(f"{k} environment variable must be set")
    return server_info


# TODO add sentry
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "environment": {
            "()": _EnvironmentFilter,
        },
    },
    "formatters": {
        "console": {
            "()": "colorlog.ColoredFormatter",
            "format": (
                f"[%(asctime)s][%(log_color)s%(levelname)s%(reset)s][%(environment)s][{TAG_VERSION}] "
                "%(name)s %(filename)s:%(funcName)s:%(lineno)d | %(message)s"
            ),
            "log_colors": {
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
            "style": "%",
        },
        "file": {
            "format": (
                f"[%(asctime)s][%(levelname)s][%(environment)s][{TAG_VERSION}] "
                "%(name)s %(filename)s:%(funcName)s:%(lineno)d | %(message)s"
            ),
        },
    },
    "handlers": {
        "console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "console",
            "filters": ["environment"],
        },
        "file": {
            "level": "DEBUG",
            "class": "logging.FileHandler",
            "formatter": "file",
            "filters": ["environment"],
            "filename": os.path.join(
                os.getenv("LOGGING_PATH", ""),
                os.getenv("LOGGING_FILENAME", f"{socket.gethostname()}.log"),
            ),
        },
    },
    "loggers": {
        __package__: {
            "handlers": ["console", "file"],
            "level": os.getenv("LOGGING_LEVEL", "DEBUG"),
            "propagate": False,
        },
        "__main__": {
            "handlers": ["console", "file"],
            "level": os.getenv("LOGGING_LEVEL", "DEBUG"),
            "propagate": False,
        },
        "rcon.rcon": {
            "level": os.getenv("COMMANDS_LOGLEVEL", os.getenv("LOGGING_LEVEL", "INFO")),
        },
        "rcon.commands": {
            "level": os.getenv("COMMANDS_LOGLEVEL", os.getenv("LOGGING_LEVEL", "INFO")),
        },
        # TODO fix that
        "rcon.automods.automod": {
            "handlers": ["console", "file"],
            "level": os.getenv("LOGGING_LEVEL", "DEBUG"),
            "propagate": False,
        },
    },
}

dictConfig(LOGGING)
