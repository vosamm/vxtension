"""Export settings and FFmpeg argument planning, independent of the desktop UI."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction


RATE_MODES = {"VBR · 1패스": "vbr1", "VBR · 2패스 (소프트웨어)": "vbr2",
              "CBR · 일정 비트레이트": "cbr", "화질 기준 · 자동 비트레이트": "quality"}
RATE_SOURCES = {"원본 영상 비트레이트 기준": "source", "직접 지정": "manual"}
SPEEDS = {"빠르게": "veryfast", "균형": "medium", "압축 효율 우선 · 느리게": "slow"}
PIXELS = {"재생 호환 · 8비트 4:2:0": "yuv420p", "10비트 4:2:0 (HEVC/VP9)": "yuv420p10le",
          "원본 색상 형식 (소프트웨어)": "source"}
RESOLUTIONS = {"원본 유지": "source", "1920×1080 이내": "1080", "1280×720 이내": "720"}
FRAME_RATES = {"원본 유지": "source", "23.976": "24000/1001", "24": "24", "25": "25",
               "29.97": "30000/1001", "30": "30", "50": "50", "59.94": "60000/1001", "60": "60"}
ENCODERS = {"자동 · 사용 가능한 하드웨어 우선": "auto", "소프트웨어 · CPU": "software",
            "Intel Quick Sync": "qsv", "NVIDIA NVENC": "nvenc", "AMD AMF": "amf"}


def describe_pixels(pixel_format: str) -> str:
    match = re.fullmatch(r"yuv(420|422|444)p(10le)?", pixel_format)
    if match:
        return f"{'10' if match[2] else '8'}비트 {':'.join(match[1])}"
    return pixel_format


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    size: int
    width: int
    height: int
    fps: str
    video_codec: str
    pixel_format: str
    video_bitrate: int | None
    audio_codec: str
    sample_rate: int
    channels: int

    @classmethod
    def from_probe(cls, data: dict) -> MediaInfo:
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        if video is None:
            raise ValueError("영상 트랙이 없는 파일입니다.")
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        container = data.get("format", {})

        def number(value, default=0):
            try:
                parsed = float(value)
                return parsed if math.isfinite(parsed) and parsed >= 0 else default
            except (ValueError, TypeError):
                return default

        tags = video.get("tags", {})
        rate = number(video.get("bit_rate") or tags.get("BPS") or tags.get("BPS-eng"))
        return cls(number(container.get("duration") or video.get("duration")), int(number(container.get("size"))),
                   int(video.get("width", 0)), int(video.get("height", 0)),
                   video.get("avg_frame_rate", "0/1"), video.get("codec_name", "?"), video.get("pix_fmt", "?"),
                   int(rate) if rate else None, audio.get("codec_name", ""),
                   int(number(audio.get("sample_rate"))), int(audio.get("channels", 0)))

    def summary(self) -> str:
        try:
            fps = f"{float(Fraction(self.fps)):.3f}fps"
        except (ValueError, ZeroDivisionError):
            fps = "프레임률 미확인"
        rate = f"영상 {self.video_bitrate / 1e6:.2f}Mbps" if self.video_bitrate else "영상 비트레이트 정보 없음"
        sound = f"{self.audio_codec} · {self.sample_rate}Hz · {self.channels}채널" if self.audio_codec else "오디오 없음"
        return (f"{self.width}×{self.height} · {fps} · {self.duration:.2f}초\n"
                f"{self.video_codec.upper()} · {describe_pixels(self.pixel_format)} · {rate}\n"
                f"{sound} · {self.size / 1e9:.3f}GB")


@dataclass(frozen=True)
class ExportSettings:
    rate_mode: str = "vbr1"
    bitrate_source: str = "source"
    target_mbps: float = 60.0
    maximum_mbps: float = 90.0
    quality_crf: int = 14
    encoder: str = "software"
    speed: str = "medium"
    pixel_format: str = "yuv420p"
    resolution: str = "source"
    fps: str = "source"
    audio_kbps: int = 320
    sample_rate: int = 0
    channels: int = 0

    def rates(self, media: MediaInfo) -> tuple[int, int]:
        if self.bitrate_source == "source":
            if not media.video_bitrate:
                raise ValueError("원본 영상의 비트레이트 정보가 없습니다. 비트레이트를 직접 지정하세요.")
            target = media.video_bitrate
            maximum = math.ceil(target * 1.25)
        else:
            target = round(self.target_mbps * 1e6)
            maximum = round(self.maximum_mbps * 1e6)
        return target, target if self.rate_mode == "cbr" else maximum

    def pixels(self, media: MediaInfo) -> str:
        return media.pixel_format if self.pixel_format == "source" else self.pixel_format

    def validate(self, media: MediaInfo, video_codec: str, audio_codec: str) -> None:
        if self.rate_mode not in RATE_MODES.values() or self.bitrate_source not in RATE_SOURCES.values():
            raise ValueError("올바른 비트레이트 방식을 선택하세요.")
        if self.encoder not in ("software", "qsv", "nvenc", "amf") or self.speed not in SPEEDS.values():
            raise ValueError("올바른 인코더와 속도를 선택하세요.")
        if self.pixel_format not in PIXELS.values() or self.resolution not in RESOLUTIONS.values() or self.fps not in FRAME_RATES.values():
            raise ValueError("올바른 출력 영상 설정을 선택하세요.")
        if not 0 <= self.quality_crf <= 51:
            raise ValueError("화질 값은 0~51 사이여야 합니다.")
        if self.audio_kbps not in (128, 192, 256, 320) or self.sample_rate not in (0, 44100, 48000) or self.channels not in (0, 1, 2):
            raise ValueError("올바른 오디오 설정을 선택하세요.")
        if self.rate_mode != "quality":
            if not all(math.isfinite(v) and 0.1 <= v <= 1000 for v in (self.target_mbps, self.maximum_mbps)):
                raise ValueError("비트레이트는 0.1~1000Mbps 사이로 입력하세요.")
            target, maximum = self.rates(media)
            if not 100000 <= target <= maximum <= 1000000000:
                raise ValueError("최대 비트레이트는 목표 이상이어야 하며, 범위는 0.1~1000Mbps입니다.")
        if self.encoder != "software" and (self.rate_mode in ("vbr2", "quality") or video_codec == "libvpx-vp9"):
            raise ValueError("2패스·화질 기준·VP9 출력은 소프트웨어 인코더를 선택하세요.")
        if self.encoder != "software" and self.pixel_format == "source":
            raise ValueError("원본 색상 형식 출력은 소프트웨어 인코더를 선택하세요.")
        pixels = self.pixels(media)
        allowed = {f"yuv{chroma}p{depth}" for chroma in ("420", "422", "444") for depth in ("", "10le")}
        if pixels not in allowed:
            raise ValueError("원본 색상 형식을 이 인코더로 보존할 수 없습니다. 출력 색상 형식을 직접 선택하세요.")
        if self.pixel_format == "yuv420p10le" and video_codec == "libx264":
            raise ValueError("10비트 4:2:0 출력은 HEVC 또는 VP9 형식을 선택하세요.")
        if media.audio_codec and audio_codec == "libopus" and self.audio_kbps > 256 * (self.channels or media.channels):
            raise ValueError("WebM 모노 오디오 비트레이트는 256kbps 이하를 선택하세요.")

    def estimate_bytes(self, media: MediaInfo) -> int | None:
        if self.rate_mode == "quality":
            return None
        video_rate, _ = self.rates(media)
        audio_rate = self.audio_kbps * 1000 if media.audio_codec else 0
        return round((video_rate + audio_rate) * media.duration / 8)


def video_arguments(settings: ExportSettings, media: MediaInfo, codec: str, pass_number: int = 0) -> list[str]:
    """Build encoder-specific rate control without silently substituting modes."""
    encoder = codec if settings.encoder == "software" else ("h264" if codec == "libx264" else "hevc") + "_" + settings.encoder
    pixels = settings.pixels(media)
    if settings.encoder != "software":
        pixels = "p010le" if pixels == "yuv420p10le" else "nv12"
    arguments = ["-c:v", encoder, "-pix_fmt", pixels]
    filters = []
    if settings.resolution == "source":
        filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")
    else:
        width, height = (1920, 1080) if settings.resolution == "1080" else (1280, 720)
        filters.append(f"scale=w='min(iw,{width})':h='min(ih,{height})':force_original_aspect_ratio=decrease:force_divisible_by=2:flags=lanczos")
    if settings.fps != "source":
        filters.append("fps=" + settings.fps)
    arguments += ["-vf", ",".join(filters)]
    if settings.encoder in ("software", "qsv"):
        if codec == "libvpx-vp9":
            arguments += ["-deadline", "good", "-cpu-used", {"veryfast": "4", "medium": "2", "slow": "1"}[settings.speed]]
        else:
            arguments += ["-preset", settings.speed]
    elif settings.encoder == "nvenc":
        arguments += ["-preset", {"veryfast": "p2", "medium": "p4", "slow": "p7"}[settings.speed]]
    elif settings.encoder == "amf":
        arguments += ["-quality", {"veryfast": "speed", "medium": "balanced", "slow": "quality"}[settings.speed]]
    if settings.rate_mode == "quality":
        arguments += ["-crf", str(settings.quality_crf + (9 if codec == "libvpx-vp9" else 0))]
        if codec == "libvpx-vp9":
            arguments += ["-b:v", "0"]
    else:
        target, maximum = settings.rates(media)
        arguments += ["-b:v", str(target), "-maxrate", str(maximum), "-bufsize", str(maximum * 2)]
        if settings.rate_mode == "cbr":
            arguments += ["-minrate", str(target)]
            if settings.encoder == "software" and codec == "libx264":
                arguments += ["-x264-params", "nal-hrd=cbr:filler=1"]
            elif settings.encoder == "software" and codec == "libx265":
                arguments += ["-x265-params", "strict-cbr=1:hrd=1"]
        if settings.encoder in ("nvenc", "amf"):
            mode = "cbr" if settings.rate_mode == "cbr" else ("vbr" if settings.encoder == "nvenc" else "vbr_peak")
            arguments += ["-rc", mode]
    if pass_number:
        if codec == "libx265":
            arguments += ["-x265-params", f"pass={pass_number}:stats=vxtension-x265.log"]
        else:
            arguments += ["-pass", str(pass_number), "-passlogfile", "vxtension-pass"]
    return arguments
