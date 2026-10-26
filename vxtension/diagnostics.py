"""Small local rotating log for errors reported from the desktop app."""

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

_log_path: Path | None = None


def configure_logging(directory: Path | None = None) -> Path | None:
    global _log_path
    if _log_path:
        return _log_path
    root = directory or Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "vxtension" / "logs"
    try:
        root.mkdir(parents=True, exist_ok=True)
        path = root / "vxtension.log"
        handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger = logging.getLogger("vxtension")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        _log_path = path
    except OSError:
        return None
    return _log_path


def error_message(error: Exception) -> str:
    text = str(error)
    if _log_path:
        text += f"\n\n상세 오류 기록: {_log_path}"
    return text
