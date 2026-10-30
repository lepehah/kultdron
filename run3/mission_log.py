#!/usr/bin/env python3
"""
Общий лог миссии: пишет и в консоль, и в файл.
На плате:  /home/sverk/mission/logs/mission.log
На ноуте:  .../1234/logs/ui_server.log
"""
from __future__ import annotations

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_LOGGER: Optional[logging.Logger] = None
_LOG_PATH: Optional[Path] = None


def log_dir(base: Optional[Path] = None) -> Path:
    root = Path(base) if base else Path(__file__).resolve().parent
    path = root / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup(name: str = "mission", filename: Optional[str] = None,
          base_dir: Optional[str] = None) -> Path:
    """Включить лог. Возвращает путь к файлу."""
    global _LOGGER, _LOG_PATH
    directory = log_dir(Path(base_dir) if base_dir else None)
    _LOG_PATH = directory / (filename or f"{name}.log")

    logger = logging.getLogger("sverk_mission")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(
        _LOG_PATH, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    _LOGGER = logger

    def _hook(exc_type, exc, tb):
        if _LOGGER:
            _LOGGER.error(
                "Неперехваченное исключение %s: %s\n%s",
                exc_type.__name__,
                exc,
                "".join(traceback.format_exception(exc_type, exc, tb)),
            )
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook
    logger.info("Лог открыт: %s", _LOG_PATH)
    return _LOG_PATH


def path() -> Optional[Path]:
    return _LOG_PATH


def _log() -> logging.Logger:
    if _LOGGER is None:
        setup()
    assert _LOGGER is not None
    return _LOGGER


def debug(msg, *args):
    _log().debug(msg, *args)


def info(msg, *args):
    _log().info(msg, *args)


def warning(msg, *args):
    _log().warning(msg, *args)


def error(msg, *args):
    _log().error(msg, *args)


def exception(msg, *args):
    _log().exception(msg, *args)
