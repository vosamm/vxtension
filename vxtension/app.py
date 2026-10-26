"""Tk desktop interface; all conversion events return through the UI queue."""

from __future__ import annotations

import os
import logging
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from vxtension import __version__
from vxtension.diagnostics import configure_logging, error_message
from vxtension.engine import Cancelled, Converter, DEFAULT_QUALITY, FORMATS, QUALITY, discover_hardware, find_binary
from vxtension.export import (ENCODERS, FRAME_RATES, PIXELS, RATE_MODES, RATE_SOURCES,
                             RESOLUTIONS, SPEEDS, ExportSettings, MediaInfo, describe_pixels)
from vxtension.preferences import load_output_directory, prepare_output_directory, save_output_directory

logger = logging.getLogger(__name__)


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("vxtension · 영상 변환기")
        self.geometry("1020x720")
        self.minsize(640, 420)
        self.configure(bg="#10141e")
        self.files: dict[str, Path] = {}
        self.errors: dict[str, str] = {}
        self.events: queue.Queue = queue.Queue()
        self.converter: Converter | None = None
        self.worker: threading.Thread | None = None
        self.closing = False
        self.busy = False
        self.metadata: dict[str, MediaInfo] = {}
        self.metadata_errors: dict[str, str] = {}
        self.probe_jobs: queue.Queue = queue.Queue()
        self.probe_converter = Converter()
        self.shutdown = threading.Event()
        self.hardware: dict[str, set[str]] = {}
        self.hardware_ready = False
        self._refreshing = False
        self.started_at = 0.0
        self.overall = 0.0
        self.format = tk.StringVar(value=next(iter(FORMATS)))
        self.quality = tk.StringVar(value=DEFAULT_QUALITY)
        self.rate_mode = tk.StringVar(value=next(iter(RATE_MODES)))
        self.rate_source = tk.StringVar(value=next(iter(RATE_SOURCES)))
        self.encoder = tk.StringVar(value=next(iter(ENCODERS)))
        self.speed = tk.StringVar(value="균형")
        self.target_mbps = tk.StringVar(value="60")
        self.maximum_mbps = tk.StringVar(value="90")
        self.pixel_format = tk.StringVar(value=next(iter(PIXELS)))
        self.resolution = tk.StringVar(value="원본 유지")
        self.fps = tk.StringVar(value="원본 유지")
        self.audio_kbps = tk.StringVar(value="320")
        self.sample_rate = tk.StringVar(value="원본 유지")
        self.channels = tk.StringVar(value="원본 유지")
        self.source_summary = tk.StringVar(value="목록에서 파일을 선택하면 원본 스펙을 표시합니다.")
        self.export_summary = tk.StringVar(value="파일을 추가하면 출력 설정과 예상 용량을 확인할 수 있습니다.")
        self.hardware_status = tk.StringVar(value="사용 가능한 하드웨어 인코더 확인 중…")
        self.timing = tk.StringVar(value="")
        self.output = tk.StringVar(value=str(load_output_directory()))
        self.status = tk.StringVar(value="변환할 영상 파일을 추가하세요.")
        self._style()
        self._layout()
        for variable in (self.format, self.quality, self.rate_mode, self.rate_source, self.encoder, self.speed,
                         self.target_mbps, self.maximum_mbps, self.pixel_format, self.resolution, self.fps,
                         self.audio_kbps, self.sample_rate, self.channels):
            variable.trace_add("write", self._refresh_export)
        self._refresh_export()
        threading.Thread(target=self._probe_files, daemon=True).start()
        threading.Thread(target=self._detect_hardware, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self._poll)

    def _style(self) -> None:
        self.option_add("*Font", ("맑은 고딕", 10))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#10141e")
        style.configure("TLabel", background="#10141e", foreground="#dce3f2")
        style.configure("Title.TLabel", font=("맑은 고딕", 26, "bold"), foreground="#ffffff")
        style.configure("Muted.TLabel", foreground="#99a8c1")
        style.configure("TButton", background="#273247", foreground="#ffffff", padding=(16, 10), borderwidth=0)
        style.map("TButton", background=[("active", "#384963"), ("disabled", "#202735")],
                  foreground=[("disabled", "#718098")])
        style.configure("Accent.TButton", background="#366cf0", font=("맑은 고딕", 11, "bold"))
        style.map("Accent.TButton", background=[("active", "#4c80ff"), ("disabled", "#25324d")])
        style.configure("Treeview", background="#192130", fieldbackground="#192130", foreground="#dce3f2",
                        rowheight=38, borderwidth=0)
        style.configure("Treeview.Heading", background="#253046", foreground="#bac8df", padding=(8, 10))
        style.map("Treeview", background=[("selected", "#2b4678")])
        style.configure("TEntry", fieldbackground="#192130", foreground="#dce3f2", padding=8)
        style.configure("TCombobox", padding=8, foreground="#10141e")
        style.configure("Horizontal.TProgressbar", background="#5a86ff", troughcolor="#253046", borderwidth=0)

    def report_callback_exception(self, exc_type, value, traceback) -> None:
        logger.error("Desktop callback failed", exc_info=(exc_type, value, traceback))
        messagebox.showerror("오류", error_message(value), parent=self)

    def _layout(self) -> None:
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        # Only the body scrolls; conversion controls keep their own fixed row.
        viewport = ttk.Frame(shell)
        viewport.grid(row=0, column=0, sticky="nsew")
        viewport.columnconfigure(0, weight=1)
        viewport.rowconfigure(0, weight=1)
        self.body_canvas = tk.Canvas(viewport, bg="#10141e", highlightthickness=0,
                                     borderwidth=0, width=1, height=1)
        self.body_canvas.grid(row=0, column=0, sticky="nsew")
        body_scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=self.body_canvas.yview)
        body_scrollbar.grid(row=0, column=1, sticky="ns")
        self.body_canvas.configure(yscrollcommand=body_scrollbar.set)
        root = ttk.Frame(self.body_canvas, padding=(12, 8, 12, 16))
        self.body = root
        self.body_window = self.body_canvas.create_window(0, 0, window=root, anchor="nw")
        root.bind("<Configure>", self._resize_body)
        self.body_canvas.bind("<Configure>", self._resize_body)
        # Toplevel bindings also receive wheel events from child controls.
        self.bind("<MouseWheel>", self._scroll_body)
        self.bind("<Button-4>", self._scroll_body)
        self.bind("<Button-5>", self._scroll_body)

        ttk.Label(root, text="vxtension", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, text="영상 형식과 인코딩을 간단하게.", style="Muted.TLabel").pack(anchor="w", pady=(2, 22))
        toolbar = ttk.Frame(root)
        toolbar.pack(fill="x", pady=(0, 12))
        self.add_button = ttk.Button(toolbar, text="+ 영상 추가", command=self.add_files)
        self.add_button.pack(side="left")
        self.remove_button = ttk.Button(toolbar, text="선택 제거", command=self.remove_files)
        self.remove_button.pack(side="left", padx=8)
        self.clear_button = ttk.Button(toolbar, text="목록 비우기", command=self.clear_files)
        self.clear_button.pack(side="left")
        self.detail_hint = ttk.Label(root, text="파일을 두 번 누르면 결과 또는 오류를 확인합니다.",
                                     style="Muted.TLabel", wraplength=500)
        self.detail_hint.pack(anchor="w", pady=(0, 8))
        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(frame, columns=("name", "size", "status"), show="headings", selectmode="extended", height=4)
        for key, text, width in (("name", "파일 이름", 280), ("size", "크기", 90), ("status", "상태", 140)):
            self.tree.heading(key, text=text, anchor="w")
            self.tree.column(key, width=width, minwidth=80, anchor="w", stretch=(key == "name"))
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.show_details)
        self.tree.bind("<<TreeviewSelect>>", self._refresh_export)
        ttk.Label(root, text="원본 스펙", font=("맑은 고딕", 11, "bold")).pack(anchor="w", pady=(16, 4))
        self.source_label = ttk.Label(root, textvariable=self.source_summary, wraplength=500)
        self.source_label.pack(anchor="w")
        settings = ttk.Frame(root)
        settings.pack(fill="x", pady=(18, 12))
        settings.columnconfigure(0, weight=1, uniform="settings")
        settings.columnconfigure(1, weight=1, uniform="settings")
        fields = [
            ("format", "출력 형식 / 코덱", self.format, list(FORMATS)),
            ("rate_mode", "비트레이트 인코딩 방식", self.rate_mode, list(RATE_MODES)),
            ("encoder", "인코더", self.encoder, list(ENCODERS)[:2]),
            ("speed", "인코딩 속도 / 압축 효율", self.speed, list(SPEEDS)),
            ("rate_source", "비트레이트 기준", self.rate_source, list(RATE_SOURCES)),
            ("quality", "화질 기준 모드의 압축 품질", self.quality, list(QUALITY)),
            ("target", "목표 영상 비트레이트 (Mbps)", self.target_mbps, None),
            ("maximum", "최대 영상 비트레이트 (Mbps)", self.maximum_mbps, None),
            ("resolution", "출력 해상도", self.resolution, list(RESOLUTIONS)),
            ("fps", "출력 프레임률", self.fps, list(FRAME_RATES)),
            ("pixel", "출력 비트 수 / 색상 형식", self.pixel_format, list(PIXELS)),
            ("audio", "오디오 비트레이트 (kbps)", self.audio_kbps, ["128", "192", "256", "320"]),
            ("sample_rate", "오디오 샘플률 (Hz)", self.sample_rate, ["원본 유지", "48000", "44100"]),
            ("channels", "오디오 채널", self.channels, ["원본 유지", "스테레오", "모노"]),
        ]
        self.setting_widgets = {}
        for index, (key, caption, variable, values) in enumerate(fields):
            block = ttk.Frame(settings)
            block.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, 12) if index % 2 == 0 else (0, 0), pady=(0, 10))
            ttk.Label(block, text=caption).pack(anchor="w", pady=(0, 4))
            if values is None:
                widget = ttk.Entry(block, textvariable=variable, width=16)
            else:
                widget = ttk.Combobox(block, textvariable=variable, values=values, state="readonly", width=16)
                for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                    widget.bind(sequence, lambda event: (self._scroll_body(event), "break")[1])
            widget.pack(fill="x")
            self.setting_widgets[key] = widget
        self.format_box = self.setting_widgets["format"]
        self.quality_box = self.setting_widgets["quality"]
        self.hardware_label = ttk.Label(root, textvariable=self.hardware_status, style="Muted.TLabel", wraplength=500)
        self.hardware_label.pack(anchor="w", pady=(0, 8))
        self.export_label = ttk.Label(root, textvariable=self.export_summary, wraplength=500)
        self.export_label.pack(anchor="w", pady=(0, 14))
        ttk.Label(root, text="저장 폴더").pack(anchor="w", pady=(0, 6))
        destination = ttk.Frame(root)
        destination.pack(fill="x")
        destination.columnconfigure(0, weight=1)
        self.output_entry = ttk.Entry(destination, textvariable=self.output)
        self.output_entry.grid(row=0, column=0, sticky="ew")
        self.browse_button = ttk.Button(destination, text="폴더 선택", command=self.browse)
        self.browse_button.grid(row=0, column=1, padx=(8, 0))
        ttk.Button(destination, text="폴더 열기", command=self.open_output).grid(row=0, column=2, padx=(8, 0))
        self.safety_hint = ttk.Label(root, text="재인코딩은 무손실이 아닙니다. 8비트 4:2:0은 재생 호환성을 우선하며, 원본 색상 형식은 기존 재생 문제가 남을 수 있습니다. HEVC는 재생 환경에 따라 별도 코덱이 필요합니다.",
                                     style="Muted.TLabel", wraplength=500)
        self.safety_hint.pack(anchor="w", pady=(8, 0))

        footer = ttk.Frame(shell, padding=(12, 12, 12, 0))
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(footer, maximum=100)
        self.progress.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.status_label = ttk.Label(footer, textvariable=self.status, wraplength=500)
        self.status_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 10))
        self.timing_label = ttk.Label(footer, textvariable=self.timing, style="Muted.TLabel", wraplength=500)
        self.timing_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 8))
        footer.bind("<Configure>", self._resize_footer)
        self.start_button = ttk.Button(footer, text="변환 시작", style="Accent.TButton", command=self.start)
        self.start_button.grid(row=3, column=2, sticky="e")
        self.cancel_button = ttk.Button(footer, text="취소", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=3, column=1, sticky="e", padx=8)
        ttk.Label(footer, text=f"v{__version__}  ·  FFmpeg", style="Muted.TLabel").grid(row=3, column=0, sticky="w")

    def _resize_footer(self, event) -> None:
        for label in (self.status_label, self.timing_label):
            label.configure(wraplength=max(100, event.width - 24))

    def _resize_body(self, _event=None) -> None:
        width = max(1, self.body_canvas.winfo_width())
        height = max(self.body.winfo_reqheight(), self.body_canvas.winfo_height())
        self.body_canvas.itemconfigure(self.body_window, width=width, height=height)
        self.body_canvas.configure(scrollregion=(0, 0, width, height))
        for label in (self.detail_hint, self.safety_hint, self.source_label, self.export_label, self.hardware_label):
            label.configure(wraplength=max(100, width - 24))

    def _scroll_body(self, event) -> str | None:
        # The video list has its own wheel scrolling. Do not scroll both areas.
        if event.widget == self.tree or not str(event.widget).startswith(str(self.body_canvas)):
            return None
        if self.body.winfo_height() <= self.body_canvas.winfo_height():
            return None
        if getattr(event, "num", None) == 4:
            steps = -1
        elif getattr(event, "num", None) == 5:
            steps = 1
        elif event.delta:
            steps = -int(event.delta / 120) if abs(event.delta) >= 120 else (-1 if event.delta > 0 else 1)
        else:
            return None
        self.body_canvas.yview_scroll(steps, "units")
        return "break"

    def _probe_files(self) -> None:
        while True:
            job = self.probe_jobs.get()
            if job is None or self.closing:
                return
            item, path = job
            if item not in self.files:
                continue
            try:
                info = self.probe_converter.info(path)
                self.events.put(("metadata", item, info, ""))
            except Exception as exc:
                logger.exception("Source inspection failed: %s", path)
                self.events.put(("metadata", item, None, error_message(exc)))

    def _detect_hardware(self) -> None:
        try:
            self.events.put(("hardware", discover_hardware(self.shutdown), ""))
        except Exception as exc:
            self.events.put(("hardware", {}, str(exc)))

    def _export_settings(self) -> ExportSettings:
        mode = RATE_MODES[self.rate_mode.get()]
        source = RATE_SOURCES[self.rate_source.get()]
        backend = ENCODERS[self.encoder.get()]
        codec = FORMATS[self.format.get()][1]
        if backend == "auto":
            backend = "software"
            if mode not in ("vbr2", "quality") and self.pixel_format.get() != "원본 색상 형식 (소프트웨어)":
                backend = next((name for name in ("qsv", "nvenc", "amf") if codec in self.hardware.get(name, set())), "software")
        elif backend != "software" and codec not in self.hardware.get(backend, set()):
            raise ValueError("선택한 출력 코덱에서 이 하드웨어 인코더를 사용할 수 없습니다.")
        manual = mode != "quality" and source == "manual"
        try:
            target = float(self.target_mbps.get()) if manual else 60
            maximum = float(self.maximum_mbps.get()) if manual and mode != "cbr" else (target if manual else 90)
        except ValueError:
            raise ValueError("목표·최대 비트레이트에 숫자를 입력하세요.") from None
        return ExportSettings(
            rate_mode=mode, bitrate_source=source,
            target_mbps=target, maximum_mbps=maximum,
            quality_crf=QUALITY[self.quality.get()], encoder=backend, speed=SPEEDS[self.speed.get()],
            pixel_format=PIXELS[self.pixel_format.get()], resolution=RESOLUTIONS[self.resolution.get()], fps=FRAME_RATES[self.fps.get()],
            audio_kbps=int(self.audio_kbps.get()), sample_rate=0 if self.sample_rate.get() == "원본 유지" else int(self.sample_rate.get()),
            channels={"원본 유지": 0, "스테레오": 2, "모노": 1}[self.channels.get()],
        )

    def _refresh_export(self, *_args) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            mode = RATE_MODES[self.rate_mode.get()]
            manual = RATE_SOURCES[self.rate_source.get()] == "manual"
            codec = FORMATS[self.format.get()][1]
            values = [label for label, name in ENCODERS.items()
                      if name in ("auto", "software") or codec in self.hardware.get(name, set())]
            self.setting_widgets["encoder"].configure(values=values)
            for key, widget in self.setting_widgets.items():
                enabled = not self.busy
                if key == "quality":
                    enabled &= mode == "quality"
                elif key == "rate_source":
                    enabled &= mode != "quality"
                elif key == "target":
                    enabled &= manual and mode != "quality"
                elif key == "maximum":
                    enabled &= manual and mode in ("vbr1", "vbr2")
                state = ("readonly" if isinstance(widget, ttk.Combobox) else "normal") if enabled else "disabled"
                widget.configure(state=state)
            selected = self.tree.selection()
            item = selected[0] if selected else next(iter(self.files), None)
            media = self.metadata.get(item)
            if media is None:
                self.source_summary.set(self.metadata_errors.get(item, "원본 정보를 분석 중입니다." if self.files else "목록에서 파일을 선택하면 원본 스펙을 표시합니다."))
                self.export_summary.set("원본 분석 후 출력 설정과 예상 용량을 표시합니다.")
                return
            self.source_summary.set(media.summary())
            if not manual and mode != "quality":
                target = f"{media.video_bitrate / 1e6:.6f}".rstrip("0").rstrip(".") if media.video_bitrate else "정보 없음"
                maximum = (target if mode == "cbr" else f"{media.video_bitrate * 1.25 / 1e6:.6f}".rstrip("0").rstrip(".")) if media.video_bitrate else "정보 없음"
                if self.target_mbps.get() != target:
                    self.target_mbps.set(target)
                if self.maximum_mbps.get() != maximum:
                    self.maximum_mbps.set(maximum)
            settings = self._export_settings()
            _, video_codec, audio_codec = FORMATS[self.format.get()]
            settings.validate(media, video_codec, audio_codec)
            backend_label = next(label for label, value in ENCODERS.items() if value == settings.encoder)
            description = f"출력: {self.format.get()} · {describe_pixels(settings.pixels(media))} · {backend_label}\n"
            if mode == "quality":
                description += "화질을 기준으로 압축합니다. 비트레이트와 용량은 변환 후 확정됩니다."
            else:
                target, maximum = settings.rates(media)
                estimate = settings.estimate_bytes(media)
                description += f"선택 파일 목표 {target / 1e6:.2f}Mbps · 최대 {maximum / 1e6:.2f}Mbps · 예상 약 {estimate / 1e9:.3f}GB\n"
                if len(self.metadata) == len(self.files):
                    total = sum(settings.estimate_bytes(info) for info in self.metadata.values())
                    description += f"전체 {len(self.files)}개 예상 약 {total / 1e9:.3f}GB. "
                description += "목표 평균 기준의 추정이며 실제 비트레이트와 컨테이너에 따라 달라집니다."
            if mode == "vbr2":
                description += "\n2패스는 전체 영상을 두 번 처리하므로 시간이 더 걸립니다."
            self.export_summary.set(description)
        except (ValueError, KeyError) as exc:
            self.export_summary.set("설정 확인: " + str(exc))
        finally:
            self._refreshing = False

    def add_files(self) -> None:
        paths = filedialog.askopenfilenames(title="변환할 영상 선택", filetypes=[
            ("영상 파일", "*.mp4 *.mkv *.mov *.mxf *.avi *.webm *.wmv *.m4v *.mpeg *.mpg *.ts *.mts *.m2ts *.flv *.3gp"),
            ("모든 파일", "*.*"),
        ])
        existing = {str(path).casefold() for path in self.files.values()}
        for name in paths:
            path = Path(name).resolve()
            if str(path).casefold() in existing:
                continue
            try:
                size = path.stat().st_size / 1024 / 1024
            except OSError as exc:
                messagebox.showerror("파일 추가 실패", str(exc))
                continue
            item = self.tree.insert("", "end", values=(path.name, f"{size:,.1f} MiB", "분석 중"))
            self.files[item] = path
            self.probe_jobs.put((item, path))
            existing.add(str(path).casefold())
        self.status.set(f"{len(self.files)}개 파일 · 변환 준비")
        if self.files and not self.tree.selection():
            self.tree.selection_set(next(iter(self.files)))
        self._refresh_export()

    def remove_files(self) -> None:
        for item in self.tree.selection():
            self.tree.delete(item)
            self.files.pop(item, None)
            self.errors.pop(item, None)
            self.metadata.pop(item, None)
            self.metadata_errors.pop(item, None)
        self.status.set(f"{len(self.files)}개 파일 · 변환 준비")
        self._refresh_export()

    def clear_files(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.files.clear()
        self.errors.clear()
        self.metadata.clear()
        self.metadata_errors.clear()
        self.progress["value"] = 0
        self.status.set("변환할 영상 파일을 추가하세요.")
        self._refresh_export()

    def browse(self) -> None:
        path = filedialog.askdirectory(title="저장 폴더 선택")
        if path:
            self.output.set(path)
            self._remember_output(Path(path))

    def _remember_output(self, directory: Path) -> None:
        try:
            save_output_directory(directory)
        except OSError:
            logger.warning("Could not remember the output folder: %s", directory, exc_info=True)

    def open_output(self) -> None:
        try:
            directory = Path(self.output.get().strip()).expanduser()
            if not directory.is_dir():
                raise OSError("저장 폴더가 아직 없습니다. 변환 후 다시 열어주세요.")
            os.startfile(str(directory))
        except OSError as exc:
            messagebox.showerror("폴더 열기 실패", str(exc))

    def show_details(self, _event=None) -> None:
        selection = self.tree.selection()
        if selection:
            item = selection[0]
            info = self.metadata[item].summary() if item in self.metadata else self.metadata_errors.get(item, "분석 중")
            messagebox.showinfo("파일 정보", self.errors.get(item, str(self.files[item]) + "\n\n" + info))

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for widget in (self.add_button, self.remove_button, self.clear_button,
                       self.browse_button, self.output_entry, self.start_button):
            widget.configure(state="disabled" if busy else "normal")
        for widget in (self.format_box, self.quality_box):
            widget.configure(state="disabled" if busy else "readonly")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self._refresh_export()

    def start(self) -> None:
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo("영상 추가", "먼저 변환할 영상을 추가하세요.")
            return
        if not self.output.get().strip():
            messagebox.showerror("저장 폴더", "저장 폴더를 선택하세요.")
            return
        try:
            find_binary("ffmpeg")
            find_binary("ffprobe")
            if self.encoder.get() == next(iter(ENCODERS)) and not self.hardware_ready:
                raise ValueError("하드웨어 인코더 확인이 끝날 때까지 기다리거나 소프트웨어 인코더를 선택하세요.")
            settings = self._export_settings()
            for item in self.files:
                if item not in self.metadata:
                    raise ValueError(self.metadata_errors.get(item, "원본 파일 분석이 끝날 때까지 기다려주세요."))
                settings.validate(self.metadata[item], *FORMATS[self.format.get()][1:])
            directory = prepare_output_directory(Path(self.output.get().strip()))
            self.output.set(str(directory))
            self._remember_output(directory)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.exception("Export preparation failed")
            messagebox.showerror("변환 준비 실패", error_message(exc))
            return
        jobs = list(self.files.items())
        format_name, quality = self.format.get(), self.quality.get()
        self.converter = Converter()
        self.running_settings = settings
        self.started_at = time.monotonic()
        self.overall = 0
        self.timing.set("경과 00:00 · 예상 남은 시간 계산 중")
        self.errors.clear()
        self.progress["value"] = 0
        for item, _ in jobs:
            self.tree.set(item, "status", "대기")
        self._set_busy(True)
        self.status.set(f"{len(jobs)}개 파일 변환 준비 중…")
        self.worker = threading.Thread(target=self._convert, args=(jobs, directory, format_name, quality, settings), daemon=True)
        self.worker.start()

    def _convert(self, jobs: list, directory: Path, format_name: str, quality: str, settings: ExportSettings) -> None:
        assert self.converter is not None
        completed, failed = 0, 0
        weights = [max(self.metadata[item].duration, 0.01) for item, _ in jobs]
        total_weight, done_weight = sum(weights), 0.0
        try:
            for index, (item, source) in enumerate(jobs):
                if self.converter.cancelled.is_set():
                    break
                self.events.put(("active", item, index + 1, len(jobs), source.name))
                try:
                    def report(value: float) -> None:
                        self.events.put(("progress", item, value, (done_weight + weights[index] * value / 100) / total_weight * 100))
                    target = self.converter.convert(source, directory, format_name, quality, report, settings=settings)
                    completed += 1
                    detail = str(target)
                    try:
                        detail += "\n\n원본\n" + self.metadata[item].summary() + "\n\n결과\n" + self.converter.info(target).summary()
                    except (Cancelled, OSError, ValueError, RuntimeError):
                        pass
                    self.events.put(("result", item, "완료", detail))
                except Cancelled:
                    self.events.put(("result", item, "취소됨", "변환을 취소했습니다."))
                    break
                except Exception as exc:
                    failed += 1
                    logger.exception("Export failed: source=%s directory=%s format=%s settings=%s", source, directory, format_name, settings)
                    self.events.put(("result", item, "실패", error_message(exc)))
                done_weight += weights[index]
                self.events.put(("advance", done_weight / total_weight * 100))
        finally:
            self.events.put(("done", completed, failed, self.converter.cancelled.is_set()))

    def _poll(self) -> None:
        # Bound processing so a busy encoder cannot starve window events.
        for _ in range(200):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "metadata":
                _, item, info, error = event
                if item in self.files:
                    if info is None:
                        self.metadata_errors[item] = error
                        self.tree.set(item, "status", "분석 실패")
                    else:
                        self.metadata[item] = info
                        self.tree.set(item, "status", "대기")
                    self._refresh_export()
            elif kind == "hardware":
                _, self.hardware, error = event
                self.hardware_ready = True
                labels = [label for label, backend in ENCODERS.items() if backend in self.hardware]
                self.hardware_status.set("확인된 하드웨어: " + ", ".join(labels) if labels else "사용 가능한 하드웨어 인코더 없음 · 소프트웨어 사용")
                if error:
                    self.hardware_status.set("하드웨어 확인 실패 · 소프트웨어 선택 가능: " + error)
                self._refresh_export()
            elif kind == "active":
                _, item, index, total, name = event
                self.tree.set(item, "status", "변환 중")
                self.tree.see(item)
                self.status.set(f"{index}/{total} · {name}")
            elif kind == "progress":
                _, item, value, total = event
                phase = ("1/2패스 " if value < 50 else "2/2패스 ") if self.running_settings.rate_mode == "vbr2" else ""
                self.tree.set(item, "status", f"{phase}{value:.0f}%")
                self.overall = total
                self.progress["value"] = total
            elif kind == "advance":
                self.overall = event[1]
                self.progress["value"] = event[1]
            elif kind == "result":
                _, item, state, detail = event
                self.tree.set(item, "status", state)
                self.errors[item] = detail
            elif kind == "done":
                _, completed, failed, cancelled = event
                if cancelled:
                    for item in self.files:
                        if self.tree.set(item, "status") == "대기":
                            self.tree.set(item, "status", "취소됨")
                self._set_busy(False)
                label = "취소됨" if cancelled else "변환 종료"
                self.status.set(f"{label} · 성공 {completed}개 / 실패 {failed}개")
                self.timing.set("경과 " + self._clock(time.monotonic() - self.started_at))
                if self.closing:
                    self.destroy()
                    return
        if self.busy:
            elapsed = time.monotonic() - self.started_at
            remaining = self._clock(elapsed * (100 / self.overall - 1)) if 2 <= self.overall < 100 else "계산 중"
            self.timing.set(f"경과 {self._clock(elapsed)} · 예상 남은 시간 {remaining}")
        self.after(100, self._poll)

    @staticmethod
    def _clock(seconds: float) -> str:
        minutes, seconds = divmod(max(0, int(seconds)), 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"

    def cancel(self) -> None:
        if self.converter:
            self.converter.cancel()
            self.cancel_button.configure(state="disabled")
            self.status.set("취소 중… 임시 파일을 정리합니다.")

    def close(self) -> None:
        if self.busy:
            if not messagebox.askyesno("종료", "진행 중인 변환을 취소하고 종료할까요?"):
                return
            self.closing = True
            self.shutdown.set()
            self.probe_converter.cancel()
            self.probe_jobs.put(None)
            self.cancel()
        else:
            self.closing = True
            self.shutdown.set()
            self.probe_converter.cancel()
            self.probe_jobs.put(None)
            self.destroy()


def main() -> None:
    configure_logging()
    try:
        App().mainloop()
    except Exception:
        logger.exception("Application failed")
        raise
