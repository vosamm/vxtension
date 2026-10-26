"""Remember the output folder and honor Windows Desktop redirection."""

import ctypes
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def _windows_desktop() -> Path | None:
    if os.name != "nt":
        return None
    pointer = ctypes.c_void_p()
    ole = ctypes.WinDLL("ole32")
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    initialized = ole.CoInitializeEx(None, 2)
    try:
        shell = ctypes.WinDLL("shell32")
        get_path = shell.SHGetKnownFolderPath
        get_path.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        get_path.restype = ctypes.c_long
        identifier = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641").bytes_le)
        result = get_path(ctypes.byref(identifier), 0, None, ctypes.byref(pointer))
        return Path(ctypes.wstring_at(pointer)) if result == 0 and pointer.value else None
    finally:
        if pointer.value:
            ole.CoTaskMemFree(pointer)
        if initialized in (0, 1):
            ole.CoUninitialize()


def default_output_directory() -> Path:
    try:
        desktop = _windows_desktop()
        if desktop and desktop.is_dir():
            return desktop
    except (OSError, AttributeError):
        logger.warning("Windows Desktop lookup failed", exc_info=True)
    desktop = Path.home() / "Desktop"
    return desktop if desktop.is_dir() else Path.home()


def preferences_file() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "vxtension" / "settings.json"


def load_output_directory(file: Path | None = None) -> Path:
    try:
        data = json.loads((file or preferences_file()).read_text(encoding="utf-8"))
        value = data.get("output_directory") if isinstance(data, dict) else None
        if isinstance(value, str) and value.strip():
            directory = Path(value)
            if directory.is_absolute() and directory.is_dir():
                return directory
    except (OSError, ValueError):
        pass
    return default_output_directory()


def save_output_directory(directory: Path, file: Path | None = None) -> None:
    file = file or preferences_file()
    temporary = None
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".settings-", suffix=".tmp",
                                         dir=file.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"output_directory": str(directory.resolve())}, handle, ensure_ascii=False)
        os.replace(temporary, file)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def prepare_output_directory(directory: Path) -> Path:
    """Fail before encoding if the output location cannot accept files."""
    directory = directory.expanduser().resolve()
    try:
        if not directory.is_dir():
            directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".vxtension-check-", dir=directory):
            pass
    except OSError as exc:
        raise RuntimeError(f"저장 폴더를 준비할 수 없습니다. ‘폴더 선택’에서 다른 저장 위치를 선택하세요.\n저장 위치: {directory}\n{exc}") from exc
    return directory
