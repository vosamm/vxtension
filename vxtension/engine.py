"""FFmpeg conversion with exclusive output names and cancellable processes."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from contextlib import nullcontext
from pathlib import Path
from typing import Callable

from vxtension.export import ExportSettings, MediaInfo, video_arguments
from vxtension.preferences import prepare_output_directory


FORMATS = {
    "MP4 (H.264)": ("mp4", "libx264", "aac"),
    "MKV (H.264)": ("mkv", "libx264", "aac"),
    "WebM (VP9)": ("webm", "libvpx-vp9", "libopus"),
    "MOV (H.264)": ("mov", "libx264", "aac"),
    "MP4 (HEVC · H.265)": ("mp4", "libx265", "aac"),
}
DEFAULT_QUALITY = "최소 손실 · 큰 용량"
QUALITY = {DEFAULT_QUALITY: 14, "고화질 · 큰 용량": 18, "균형 · 권장": 23, "작은 용량": 28}


def audio_bitrate(quality: str, codec: str = "aac") -> str:
    return "320k" if quality == DEFAULT_QUALITY and codec == "aac" else "192k"


CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
logger = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


def find_binary(name: str) -> str:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    bundled = root / "vendor" / (name + (".exe" if os.name == "nt" else ""))
    found = str(bundled) if bundled.is_file() else shutil.which(name)
    if not found:
        raise RuntimeError(f"{name}를 찾을 수 없습니다. FFmpeg를 설치하거나 정식 설치본을 사용하세요.")
    try:
        return str(Path(found).resolve(strict=True))
    except OSError as exc:
        raise RuntimeError(f"{name} 실행 파일 경로가 유효하지 않습니다: {found}\nFFmpeg 설치 상태를 확인하세요.") from exc


def reserve_output(source: Path, directory: Path, extension: str) -> Path:
    """Reserve an unused name without overwriting source or existing files."""
    for number in range(10000):
        suffix = "_converted" if number == 0 else f"_converted_{number}"
        target = directory / f"{source.stem}{suffix}.{extension}"
        try:
            with target.open("xb"):
                pass
            return target
        except FileExistsError:
            continue
    raise RuntimeError("출력 파일 이름을 만들 수 없습니다.")


def discover_hardware(stop: threading.Event | None = None) -> dict[str, set[str]]:
    """Check real encoder initialization, not just FFmpeg's compiled-in list."""
    binary = find_binary("ffmpeg")
    result: dict[str, set[str]] = {}
    for backend in ("qsv", "nvenc", "amf"):
        for codec, prefix in (("libx264", "h264"), ("libx265", "hevc")):
            if stop is not None and stop.is_set():
                return result
            try:
                probe = subprocess.run([
                    binary, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=size=128x128:rate=30",
                    "-frames:v", "1", "-pix_fmt", "nv12", "-c:v", f"{prefix}_{backend}",
                    "-b:v", "1000000", "-an", "-f", "null", "-",
                ], capture_output=True, timeout=8, creationflags=CREATE_FLAGS)
                if probe.returncode == 0:
                    result.setdefault(backend, set()).add(codec)
            except subprocess.TimeoutExpired:
                continue
    return result


class Converter:
    def __init__(self) -> None:
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.kill()

    def _start(self, arguments: list[str], cwd: Path | None = None) -> subprocess.Popen:
        with self._lock:
            if self.cancelled.is_set():
                raise Cancelled()
            executable = Path(arguments[0]).resolve()
            if not executable.is_file():
                raise RuntimeError(f"변환 실행 파일을 찾을 수 없습니다:\n{executable}\nFFmpeg 설치 상태를 확인하세요.")
            if cwd is not None and not cwd.is_dir():
                raise RuntimeError(f"변환 작업 폴더를 찾을 수 없습니다:\n{cwd}\n다시 변환을 시작하세요.")
            try:
                self._process = subprocess.Popen(
                    [str(executable), *arguments[1:]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                    creationflags=CREATE_FLAGS, cwd=cwd,
                )
            except OSError as exc:
                logger.exception("Process launch failed: executable=%s cwd=%s", executable, cwd)
                raise RuntimeError(f"변환 프로그램 실행에 실패했습니다: {exc}\n실행 파일: {executable}\n"
                                   f"작업 폴더: {cwd or Path.cwd()}") from exc
            return self._process

    def info(self, source: Path) -> MediaInfo:
        if not source.is_file():
            raise RuntimeError(f"원본 또는 결과 파일을 찾을 수 없습니다:\n{source}")
        process = self._start([
            find_binary("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source),
        ])
        try:
            output, _ = process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise RuntimeError("영상 정보를 읽는 시간이 초과되었습니다.") from None
        finally:
            with self._lock:
                self._process = None
        if self.cancelled.is_set():
            raise Cancelled()
        if process.returncode:
            raise RuntimeError("영상을 읽을 수 없습니다. 파일 형식과 손상 여부를 확인하세요.")
        return MediaInfo.from_probe(json.loads(output))

    def duration(self, source: Path) -> float:
        return self.info(source).duration

    def _encode(self, arguments: list[str], cwd: Path | None, duration: float,
                progress: Callable[[float], None], offset: float, span: float) -> None:
        process = self._start(arguments, cwd)
        tail: deque[str] = deque(maxlen=35)
        try:
            assert process.stdout is not None
            for line in process.stdout:
                tail.append(line.rstrip())
                if line.startswith("out_time_us=") and duration:
                    try:
                        fraction = min(0.99, max(0.0, float(line.split("=", 1)[1]) / duration / 1000000))
                        progress(offset + fraction * span)
                    except ValueError:
                        pass
            process.wait()
            if self.cancelled.is_set():
                raise Cancelled()
            if process.returncode:
                raise RuntimeError("인코딩에 실패했습니다. 하드웨어·색상 형식·비트레이트 지원 범위를 확인하세요.\n" + "\n".join(tail))
        finally:
            with self._lock:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if process.stdout:
                    process.stdout.close()
                self._process = None

    def convert(self, source: Path, directory: Path, format_name: str, quality: str,
                progress: Callable[[float], None], *, settings: ExportSettings | None = None) -> Path:
        source, directory = source.resolve(), directory.resolve()
        extension, video_codec, audio_codec = FORMATS[format_name]
        if settings is None:
            settings = ExportSettings(rate_mode="quality", quality_crf=QUALITY[quality],
                                      audio_kbps=int(audio_bitrate(quality, audio_codec)[:-1]))
        ffmpeg = find_binary("ffmpeg")
        media = self.info(source)
        settings.validate(media, video_codec, audio_codec)
        if self.cancelled.is_set():
            raise Cancelled()
        directory = prepare_output_directory(directory)
        target = reserve_output(source, directory, extension)
        temporary: Path | None = None
        success = False
        try:
            fd, name = tempfile.mkstemp(prefix=".vxtension-", suffix=f".{extension}", dir=directory)
            os.close(fd)
            temporary = Path(name)
            base = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(source),
                    "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn"]
            two_pass = settings.rate_mode == "vbr2"
            # Single-pass exports don't need a changed working directory. Keep
            # two-pass statistics in the OS temp area, away from synced outputs.
            context = tempfile.TemporaryDirectory(prefix="vxtension-pass-") if two_pass else nullcontext(None)
            with context as pass_folder:
                workdir = Path(pass_folder) if pass_folder else None
                if two_pass:
                    first = base + video_arguments(settings, media, video_codec, 1)
                    first += ["-an", "-progress", "pipe:1", "-nostats", "-f", "null", os.devnull]
                    self._encode(first, workdir, media.duration, progress, 0, 50)
                    progress(50)
                arguments = base + video_arguments(settings, media, video_codec, 2 if two_pass else 0)
                arguments += ["-c:a", audio_codec, "-b:a", f"{settings.audio_kbps}k"]
                if settings.sample_rate:
                    arguments += ["-ar", str(settings.sample_rate)]
                if settings.channels:
                    arguments += ["-ac", str(settings.channels)]
                if extension in ("mp4", "mov"):
                    arguments += ["-movflags", "+faststart"]
                if video_codec == "libx265" and extension in ("mp4", "mov"):
                    arguments += ["-tag:v", "hvc1"]
                arguments += ["-progress", "pipe:1", "-nostats", str(temporary)]
                self._encode(arguments, workdir, media.duration, progress, 50 if two_pass else 0, 50 if two_pass else 100)
            if self.cancelled.is_set():
                raise Cancelled()
            if not temporary.is_file():
                raise RuntimeError(f"변환된 임시 파일을 찾을 수 없어 결과를 저장하지 못했습니다:\n{temporary}")
            try:
                os.replace(temporary, target)
            except OSError as exc:
                raise RuntimeError(f"변환 결과 저장에 실패했습니다: {exc}\n임시 파일: {temporary}\n저장 위치: {target}") from exc
            success = True
            progress(100)
            return target
        finally:
            with self._lock:
                if self._process and self._process.poll() is None:
                    self._process.kill()
                    self._process.wait()
                if self._process and self._process.stdout:
                    self._process.stdout.close()
                self._process = None
            if temporary:
                temporary.unlink(missing_ok=True)
            if not success:
                target.unlink(missing_ok=True)
