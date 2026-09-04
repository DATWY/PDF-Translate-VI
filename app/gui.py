#!/usr/bin/env python3
"""Desktop front end for the PDF translation runner.

Features:
- Smart PDF inspection (page count, file size, scan detection).
- Live ETA calculation and title bar progress.
- 2-Tab modern GUI (Dashboard & Settings/Glossary).
- Customizable glossary preservation.
- Config persistence in JSON.
- Direct 1-click open for translated PDF and folder.
"""

from __future__ import annotations

import ctypes
import faulthandler
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# Bulletproof logging to pdf-translate.log
_GLOBAL_LOG_FILE = Path(os.path.expanduser("~")) / ".cache" / "pdf2zh" / "pdf-translate.log"
try:
    _GLOBAL_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _log_fh = open(_GLOBAL_LOG_FILE, "a", encoding="utf-8", buffering=1)
    faulthandler.enable(_log_fh)

    def _global_excepthook(exc_type, exc_value, exc_tb):
        err_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _log_fh.write(f"\n[{timestamp}] UNHANDLED ERROR:\n{err_str}\n")
        _log_fh.flush()
        if sys.__excepthook__:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _global_excepthook
    threading.excepthook = lambda args: _global_excepthook(args.exc_type, args.exc_value, args.exc_traceback)
except Exception:
    pass

from scripts.translate_pdf import (  # noqa: E402
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_THREADS,
    TARGET_LANGUAGES,
    TranslationError,
    inspect_pdf,
    translate_pdf,
)

FONT_DIRECTORY = APP_ROOT / "app" / "fonts"
ASSET_DIRECTORY = APP_ROOT / "app" / "assets"
CONFIG_FILE = Path(os.path.expanduser("~")) / ".cache" / "pdf2zh" / "gui_settings.json"

UI_FONT = "Be Vietnam Pro"
MONO_FONT = "JetBrains Mono"
FALLBACK_UI_FONT = "Segoe UI"
FALLBACK_MONO_FONT = "Consolas"

LANGUAGE_NAMES = {
    "vi": "Tiếng Việt",
    "en": "English (Tiếng Anh)",
    "fr": "Français (Tiếng Pháp)",
    "de": "Deutsch (Tiếng Đức)",
    "es": "Español (Tây Ban Nha)",
    "it": "Italiano (Tiếng Ý)",
    "pt": "Português (Bồ Đào Nha)",
    "id": "Bahasa Indonesia",
    "ms": "Bahasa Melayu",
    "nl": "Nederlands (Hà Lan)",
    "pl": "Polski (Ba Lan)",
    "tr": "Türkçe (Thổ Nhĩ Kỳ)",
    "af": "Afrikaans", "ca": "Català", "cs": "Čeština", "cy": "Cymraeg",
    "da": "Dansk", "et": "Eesti", "eu": "Euskara", "fi": "Suomi",
    "ga": "Gaeilge", "gl": "Galego", "hr": "Hrvatski", "hu": "Magyar",
    "is": "Íslenska", "lt": "Lietuvių", "lv": "Latviešu", "mt": "Malti",
    "no": "Norsk", "ro": "Română", "sk": "Slovenčina", "sl": "Slovenščina",
    "sq": "Shqip", "sv": "Svenska", "sw": "Kiswahili", "tl": "Tagalog",
}

STATUS_MARKS = {
    "queued": "•",
    "running": "▶",
    "done": "✓",
    "partial": "!",
    "failed": "✕",
    "skipped": "–",
    "cancelled": "⏹",
}
STATUS_COLORS = {
    "queued": ("gray50", "gray60"),
    "running": ("#1f6feb", "#58a6ff"),
    "done": ("#1a7f37", "#3fb950"),
    "partial": ("#9a6700", "#d29922"),
    "failed": ("#cf222e", "#f85149"),
    "skipped": ("gray50", "gray60"),
    "cancelled": ("#9a6700", "#d29922"),
}

EXPORT_NAMES = {
    "mono": "Chỉ tiếng Việt (Đơn ngữ) - Nhanh nhất",
    "dual": "Song ngữ đối chiếu (Anh - Việt)",
    "both": "Xuất cả 2 file (Đơn ngữ + Song ngữ)",
}
EXPORT_CODES = {v: k for k, v in EXPORT_NAMES.items()}

ENGINE_DISPLAY_NAMES = {
    "google": "Google Translate (Mặc định - Miễn phí)",
    "ollama": "Ollama (Offline AI / Riêng tư)",
    "deepl": "DeepL Translate (Cần API Key)",
}
ENGINE_CODES = {v: k for k, v in ENGINE_DISPLAY_NAMES.items()}


def ensure_writable_streams() -> None:
    """Give the app real streams, because a windowed build has none."""
    for name in ("stdout", "stderr", "__stdout__", "__stderr__"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def use_bundled_assets() -> None:
    """Point the engine at the packaged model and font so no download is needed."""
    model = ASSET_DIRECTORY / "doclayout.onnx"
    font = ASSET_DIRECTORY / "GoNotoKurrent-Regular.ttf"
    if model.is_file():
        os.environ.setdefault("PDF_TRANSLATE_MODEL", str(model))
    if font.is_file():
        os.environ.setdefault("NOTO_FONT_PATH", str(font))


def load_bundled_fonts() -> bool:
    """Register the bundled fonts for this process only."""
    if sys.platform != "win32" or not FONT_DIRECTORY.is_dir():
        return False
    private = 0x10
    loaded = 0
    try:
        for font in FONT_DIRECTORY.glob("*.ttf"):
            loaded += ctypes.windll.gdi32.AddFontResourceExW(str(font), private, 0)
    except OSError:
        return False
    return loaded > 0


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"


def collect_pdfs(paths: list[Path]) -> list[Path]:
    """Expand dropped or picked paths into a deduplicated list of PDF files."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(sorted(p for p in path.iterdir() if _is_pdf(p)))
        elif path.suffix.lower() == ".pdf":
            found.append(path)
    unique: dict[Path, None] = {}
    for path in found:
        unique.setdefault(path.resolve(), None)
    return list(unique)


def format_duration(seconds: float) -> str:
    """Format seconds into human-readable minutes and seconds."""
    if seconds < 60:
        return f"{int(seconds)} giây"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins} phút {secs} giây" if secs else f"{mins} phút"


def get_hardware_acceleration_label() -> str:
    try:
        import onnxruntime
        providers = onnxruntime.get_available_providers()
        if "CUDAExecutionProvider" in providers:
            return "⚡ GPU (NVIDIA CUDA)"
        elif "DmlExecutionProvider" in providers:
            return "⚡ GPU (DirectML)"
        return "💻 CPU"
    except Exception:
        return "💻 CPU"


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self) -> None:
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        has_fonts = load_bundled_fonts()
        self.ui_font = UI_FONT if has_fonts else FALLBACK_UI_FONT
        self.mono_font = MONO_FONT if has_fonts else FALLBACK_MONO_FONT

        self.title("VI Translate — Dịch PDF Giữ Nguyên Bố Cục")
        self.geometry("760x780")
        self.minsize(640, 680)
        
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.config = self._load_config()

        self.files: list[Path] = []
        self.file_info: dict[Path, dict] = {}
        self.rows: dict[Path, ctk.CTkFrame] = {}
        self.row_labels: dict[Path, ctk.CTkLabel] = {}
        self.row_actions: dict[Path, ctk.CTkFrame] = {}
        self.states: dict[Path, str] = {}
        self.events: queue.Queue[tuple] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()

        self.batch_done = 0
        self.batch_total = 0
        self.start_time: float = 0
        self.last_output: Path | None = None
        self.outputs: dict[Path, Path] = {}

        self._build_ui()
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)
        self.after(100, self._drain_events)

    # -- config management -------------------------------------------------
    def _load_config(self) -> dict:
        defaults = {
            "target_language": "vi",
            "threads": 4,
            "auto_open": True,
            "custom_output_dir": "",
            "glossary": "Transformer, Attention, Deep Learning, API, Machine Learning, Loss function",
            "theme": "dark",
        }
        try:
            if CONFIG_FILE.is_file():
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    loaded = json.load(f)
                    defaults.update(loaded)
        except Exception:
            pass
        return defaults

    def _save_config(self) -> None:
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # -- UI Building -------------------------------------------------------
    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Top Banner / Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=20, pady=(16, 4), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title_lbl = ctk.CTkLabel(
            header,
            text="VI Translate",
            font=ctk.CTkFont(self.ui_font, size=24, weight="bold"),
        )
        title_lbl.grid(row=0, column=0, sticky="w")

        hw_lbl = ctk.CTkLabel(
            header,
            text=f"AI: {get_hardware_acceleration_label()}",
            font=ctk.CTkFont(self.mono_font, size=11, weight="bold"),
            text_color=("#1a7f37", "#3fb950"),
        )
        hw_lbl.grid(row=0, column=1, sticky="e")

        sub_lbl = ctk.CTkLabel(
            header,
            text="Dịch PDF học thuật & sách kỹ thuật • Giữ 100% công thức toán, bảng và hình ảnh",
            font=ctk.CTkFont(self.ui_font, size=12),
            text_color=("gray40", "gray70"),
        )
        sub_lbl.grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # Main Tabview
        self.tabview = ctk.CTkTabview(self, corner_radius=12)
        self.tabview.grid(row=1, column=0, padx=20, pady=(4, 12), sticky="nsew")

        self.tab_main = self.tabview.add("🚀 Dịch tài liệu")
        self.tab_settings = self.tabview.add("⚙️ Cài đặt & Thuật ngữ")

        self._build_main_tab()
        self._build_settings_tab()

    def _build_main_tab(self) -> None:
        tab = self.tab_main
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(3, weight=1)

        # Dropzone
        self.dropzone = ctk.CTkFrame(tab, corner_radius=16, border_width=3, border_color="#3fb950", height=160, fg_color="#1c1f24")
        self.dropzone.grid(row=0, column=0, padx=12, pady=(8, 12), sticky="ew")
        self.dropzone.grid_propagate(False)
        self.dropzone.grid_columnconfigure(0, weight=1)
        self.dropzone.grid_rowconfigure(0, weight=1)
        self.dropzone.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            self.dropzone,
            text="📄 Kéo thả file PDF hoặc thư mục vào đây",
            font=ctk.CTkFont(self.ui_font, size=18, weight="bold"),
            text_color="#c9d1d9"
        ).grid(row=0, column=0, pady=(20, 4), sticky="s")

        buttons = ctk.CTkFrame(self.dropzone, fg_color="transparent")
        buttons.grid(row=1, column=0, pady=(8, 20))
        ctk.CTkButton(
            buttons, text="📂 Chọn file PDF", width=140, height=36, command=self._pick_files,
            font=ctk.CTkFont(self.ui_font, size=13, weight="bold"),
            fg_color="#238636", hover_color="#2ea043"
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            buttons, text="📁 Chọn thư mục", width=140, height=36, command=self._pick_directory,
            font=ctk.CTkFont(self.ui_font, size=13, weight="bold"),
            fg_color="#21262d", hover_color="#30363d",
            border_width=1, border_color="#30363d",
            text_color="#c9d1d9",
        ).pack(side="left", padx=8)

        # Controls Row 1
        ctrl1 = ctk.CTkFrame(tab, fg_color="transparent")
        ctrl1.grid(row=1, column=0, padx=8, pady=4, sticky="ew")
        ctrl1.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            ctrl1, text="Dịch sang:", font=ctk.CTkFont(self.ui_font, size=13, weight="bold")
        ).grid(row=0, column=0, padx=(0, 6), sticky="w")

        lang_values = [LANGUAGE_NAMES[code] for code in TARGET_LANGUAGES if code in LANGUAGE_NAMES]
        if LANGUAGE_NAMES["vi"] in lang_values:
            lang_values.remove(LANGUAGE_NAMES["vi"])
            lang_values.insert(0, LANGUAGE_NAMES["vi"])

        self.language_menu = ctk.CTkOptionMenu(
            ctrl1, values=lang_values, width=175, font=ctk.CTkFont(self.ui_font, size=12)
        )
        saved_lang = self.config.get("target_language", DEFAULT_TARGET_LANGUAGE)
        self.language_menu.set(LANGUAGE_NAMES.get(saved_lang, LANGUAGE_NAMES[DEFAULT_TARGET_LANGUAGE]))
        self.language_menu.grid(row=0, column=1, sticky="w")

        ctk.CTkLabel(
            ctrl1, text="Trang:", font=ctk.CTkFont(self.ui_font, size=13)
        ).grid(row=0, column=2, padx=(12, 6), sticky="w")

        self.pages_entry = ctk.CTkEntry(
            ctrl1,
            placeholder_text="Tất cả (vd: 1-5, 8)",
            width=135,
            font=ctk.CTkFont(self.ui_font, size=12),
        )
        self.pages_entry.grid(row=0, column=3, padx=(0, 8), sticky="w")

        self.btn_start = ctk.CTkButton(
            ctrl1, text="▶ Dịch", width=90, command=self._start,
            font=ctk.CTkFont(self.ui_font, size=13, weight="bold"),
            fg_color="#1f6feb", hover_color="#1158c7",
        )
        self.btn_start.grid(row=0, column=4, padx=4)

        self.btn_cancel = ctk.CTkButton(
            ctrl1, text="⏹ Dừng", width=70, command=self._cancel,
            font=ctk.CTkFont(self.ui_font, size=13),
            fg_color=("gray70", "gray35"), hover_color=("#cf222e", "#f85149"),
            state="disabled",
        )
        self.btn_cancel.grid(row=0, column=5, padx=(4, 0))

        # Controls Row 2: Overwrite and Queue Actions
        ctrl2 = ctk.CTkFrame(tab, fg_color="transparent")
        ctrl2.grid(row=2, column=0, padx=8, pady=(2, 6), sticky="ew")
        ctrl2.grid_columnconfigure(0, weight=1)

        self.overwrite_check = ctk.CTkCheckBox(
            ctrl2, text="Ghi đè file đã dịch trước đó",
            font=ctk.CTkFont(self.ui_font, size=12),
        )
        self.overwrite_check.grid(row=0, column=0, sticky="w")

        act_frame = ctk.CTkFrame(ctrl2, fg_color="transparent")
        act_frame.grid(row=0, column=1, sticky="e")

        ctk.CTkButton(
            act_frame, text="Dọn file đã xong", width=110, height=24,
            command=self._clear_done,
            font=ctk.CTkFont(self.ui_font, size=11),
            fg_color="transparent", border_width=1,
            text_color=("gray30", "gray80"),
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            act_frame, text="Xóa danh sách", width=95, height=24,
            command=self._clear_all,
            font=ctk.CTkFont(self.ui_font, size=11),
            fg_color="transparent", border_width=1,
            text_color=("gray30", "gray80"),
        ).pack(side="left", padx=4)

        # Queue List Container
        self.queue_container = ctk.CTkFrame(tab, corner_radius=10)
        self.queue_container.grid(row=3, column=0, padx=8, pady=4, sticky="nsew")
        self.queue_container.grid_columnconfigure(0, weight=1)
        self.queue_container.grid_rowconfigure(1, weight=1)

        self.queue_header = ctk.CTkLabel(
            self.queue_container,
            text="Hàng đợi (0 file)",
            font=ctk.CTkFont(self.ui_font, size=12, weight="bold"),
            anchor="w",
        )
        self.queue_header.grid(row=0, column=0, padx=12, pady=(6, 2), sticky="w")

        self.queue_scroll = ctk.CTkScrollableFrame(self.queue_container, fg_color="transparent")
        self.queue_scroll.grid(row=1, column=0, padx=6, pady=(0, 6), sticky="nsew")
        self.queue_scroll.grid_columnconfigure(0, weight=1)

        # Bottom Progress & Status Section
        bottom_box = ctk.CTkFrame(tab, fg_color="transparent")
        bottom_box.grid(row=4, column=0, padx=8, pady=(4, 0), sticky="ew")
        bottom_box.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(bottom_box, height=8, corner_radius=4)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=0, column=0, sticky="ew", pady=(2, 4))

        self.status_lbl = ctk.CTkLabel(
            bottom_box, text="Chưa có file nào trong hàng đợi",
            font=ctk.CTkFont(self.ui_font, size=12),
        )
        self.status_lbl.grid(row=1, column=0, sticky="w")

        self.output_link_lbl = ctk.CTkLabel(
            bottom_box, text="",
            font=ctk.CTkFont(self.ui_font, size=12, underline=True),
            text_color=("#1f6feb", "#58a6ff"),
            cursor="hand2", wraplength=660, justify="left",
        )
        self.output_link_lbl.grid(row=2, column=0, sticky="w", pady=(2, 4))
        self.output_link_lbl.bind("<Button-1>", lambda _e: self._open(self.last_output))

    def _build_settings_tab(self) -> None:
        tab = self.tab_settings
        tab.grid_columnconfigure(0, weight=1)

        # Glossary Section
        g_frame = ctk.CTkFrame(tab, corner_radius=10)
        g_frame.grid(row=0, column=0, padx=10, pady=8, sticky="ew")
        g_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            g_frame,
            text="📚 Thuật ngữ Chuyên ngành Bảo tồn (Custom Glossary)",
            font=ctk.CTkFont(self.ui_font, size=13, weight="bold"),
        ).grid(row=0, column=0, padx=14, pady=(10, 2), sticky="w")

        ctk.CTkLabel(
            g_frame,
            text="Nhập các từ khóa chuyên ngành không muốn dịch (ngăn cách bằng dấu phẩy):",
            font=ctk.CTkFont(self.ui_font, size=11),
            text_color=("gray40", "gray70"),
        ).grid(row=1, column=0, padx=14, pady=(0, 6), sticky="w")

        self.glossary_text = ctk.CTkTextbox(g_frame, height=90, font=ctk.CTkFont(self.ui_font, size=12))
        self.glossary_text.insert("1.0", self.config.get("glossary", ""))
        self.glossary_text.grid(row=2, column=0, padx=14, pady=(0, 12), sticky="ew")

        # Preferences Section
        p_frame = ctk.CTkFrame(tab, corner_radius=10)
        p_frame.grid(row=1, column=0, padx=10, pady=8, sticky="ew")
        p_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            p_frame,
            text="⚙️ Tùy chọn Ứng dụng",
            font=ctk.CTkFont(self.ui_font, size=13, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, padx=14, pady=(10, 8), sticky="w")

        # Auto open switch
        self.auto_open_var = ctk.BooleanVar(value=self.config.get("auto_open", True))
        ctk.CTkSwitch(
            p_frame, text="Tự động mở file PDF sau khi dịch xong",
            variable=self.auto_open_var,
            font=ctk.CTkFont(self.ui_font, size=12),
        ).grid(row=1, column=0, columnspan=2, padx=14, pady=6, sticky="w")

        # Custom Output Folder
        ctk.CTkLabel(
            p_frame, text="Thư mục xuất kết quả:",
            font=ctk.CTkFont(self.ui_font, size=12),
        ).grid(row=2, column=0, padx=14, pady=6, sticky="w")

        out_box = ctk.CTkFrame(p_frame, fg_color="transparent")
        out_box.grid(row=2, column=1, padx=14, pady=6, sticky="ew")
        out_box.grid_columnconfigure(0, weight=1)

        self.out_dir_entry = ctk.CTkEntry(
            out_box,
            placeholder_text="Mặc định: thư mục 'translated' cạnh file nguồn",
            font=ctk.CTkFont(self.ui_font, size=11),
        )
        self.out_dir_entry.insert(0, self.config.get("custom_output_dir", ""))
        self.out_dir_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        ctk.CTkButton(
            out_box, text="Chọn...", width=70, command=self._pick_custom_out_dir,
            font=ctk.CTkFont(self.ui_font, size=11),
        ).grid(row=0, column=1)

        # Threads slider
        ctk.CTkLabel(
            p_frame, text="Số luồng dịch (Threads):",
            font=ctk.CTkFont(self.ui_font, size=12),
        ).grid(row=3, column=0, padx=14, pady=6, sticky="w")

        thread_val = self.config.get("threads", DEFAULT_THREADS)
        self.threads_slider = ctk.CTkSlider(
            p_frame, from_=1, to=512, number_of_steps=511,
            command=self._on_threads_change,
        )
        self.threads_slider.set(thread_val)
        self.threads_slider.grid(row=3, column=1, padx=14, pady=6, sticky="ew")
        self.threads_lbl = ctk.CTkLabel(
            p_frame, text=f"{thread_val} luồng", font=ctk.CTkFont(self.mono_font, size=12)
        )
        self.threads_lbl.grid(row=3, column=2, padx=(0, 14), sticky="w")

        # Export Format
        ctk.CTkLabel(
            p_frame, text="Định dạng xuất file:",
            font=ctk.CTkFont(self.ui_font, size=12),
        ).grid(row=4, column=0, padx=14, pady=6, sticky="w")

        cur_export = self.config.get("export_format", "mono")
        self.export_menu = ctk.CTkOptionMenu(
            p_frame,
            values=list(EXPORT_NAMES.values()),
            font=ctk.CTkFont(self.ui_font, size=12),
        )
        self.export_menu.set(EXPORT_NAMES.get(cur_export, EXPORT_NAMES["mono"]))
        self.export_menu.grid(row=4, column=1, padx=14, pady=6, sticky="ew")

        # Engine Selection
        ctk.CTkLabel(
            p_frame, text="Bộ máy dịch thuật:",
            font=ctk.CTkFont(self.ui_font, size=12),
        ).grid(row=5, column=0, padx=14, pady=6, sticky="w")

        cur_engine = self.config.get("engine", "google")
        self.engine_menu = ctk.CTkOptionMenu(
            p_frame,
            values=list(ENGINE_DISPLAY_NAMES.values()),
            font=ctk.CTkFont(self.ui_font, size=12),
        )
        self.engine_menu.set(ENGINE_DISPLAY_NAMES.get(cur_engine, ENGINE_DISPLAY_NAMES["google"]))
        self.engine_menu.grid(row=5, column=1, padx=14, pady=6, sticky="ew")

        # Ollama Model Name
        ctk.CTkLabel(
            p_frame, text="Mô hình Ollama (nếu dùng):",
            font=ctk.CTkFont(self.ui_font, size=12),
        ).grid(row=6, column=0, padx=14, pady=6, sticky="w")

        self.ollama_model_entry = ctk.CTkEntry(
            p_frame,
            placeholder_text="Mặc định: qwen2.5:7b (hoặc llama3.1:8b)",
            font=ctk.CTkFont(self.ui_font, size=11),
        )
        self.ollama_model_entry.insert(0, self.config.get("ollama_model", "qwen2.5:7b"))
        self.ollama_model_entry.grid(row=6, column=1, padx=14, pady=6, sticky="ew")

        # Save Button
        ctk.CTkButton(
            tab, text="💾 Lưu Cài Đặt", width=140, command=self._on_save_settings,
            font=ctk.CTkFont(self.ui_font, size=13, weight="bold"),
        ).grid(row=2, column=0, pady=16)

    def _on_threads_change(self, value: float) -> None:
        self.threads_lbl.configure(text=f"{int(value)} luồng")

    def _pick_custom_out_dir(self) -> None:
        chosen = ctk.filedialog.askdirectory()
        if chosen:
            self.out_dir_entry.delete(0, "end")
            self.out_dir_entry.insert(0, chosen)

    def _on_save_settings(self) -> None:
        self.config["glossary"] = self.glossary_text.get("1.0", "end").strip()
        self.config["auto_open"] = bool(self.auto_open_var.get())
        self.config["custom_output_dir"] = self.out_dir_entry.get().strip()
        self.config["threads"] = int(self.threads_slider.get())
        self.config["export_format"] = EXPORT_CODES.get(self.export_menu.get(), "mono")
        self.config["engine"] = ENGINE_CODES.get(self.engine_menu.get(), "google")
        self.config["ollama_model"] = self.ollama_model_entry.get().strip() or "qwen2.5:7b"
        self._save_config()
        self.status_lbl.configure(text="✅ Đã lưu cài đặt thành công!")
        self.tabview.set("🚀 Dịch tài liệu")

    # -- File Management ---------------------------------------------------
    def _on_drop(self, event) -> None:
        self._add([Path(item) for item in self.tk.splitlist(event.data)])

    def _pick_files(self) -> None:
        chosen = ctk.filedialog.askopenfilenames(filetypes=[("File PDF", "*.pdf")])
        self._add([Path(item) for item in chosen])

    def _pick_directory(self) -> None:
        chosen = ctk.filedialog.askdirectory()
        if chosen:
            self._add([Path(chosen)])

    def _add(self, paths: list[Path]) -> None:
        added = 0
        for path in collect_pdfs(paths):
            if path in self.rows:
                continue

            info = inspect_pdf(path)
            self.file_info[path] = info
            self.files.append(path)

            row_frame = ctk.CTkFrame(self.queue_scroll, corner_radius=8, fg_color=("gray92", "gray20"))
            row_frame.grid(sticky="ew", padx=4, pady=3)
            row_frame.grid_columnconfigure(0, weight=1)

            # Left description label
            meta_str = f"{info['pages']} trang • {info['size_mb']:.1f} MB"
            if info.get("is_scan"):
                meta_str += " • ⚠️ Ảnh scan (Cần OCR)"

            lbl_text = f"{STATUS_MARKS['queued']}  {path.name}  ({meta_str})"
            lbl = ctk.CTkLabel(
                row_frame,
                text=lbl_text,
                font=ctk.CTkFont(self.mono_font, size=11),
                text_color=STATUS_COLORS["queued"],
                anchor="w",
                justify="left",
                wraplength=520,
            )
            lbl.grid(row=0, column=0, padx=8, pady=6, sticky="w")

            # Right actions
            act_box = ctk.CTkFrame(row_frame, fg_color="transparent")
            act_box.grid(row=0, column=1, padx=6, pady=4, sticky="e")

            del_btn = ctk.CTkButton(
                act_box, text="✕", width=22, height=22,
                command=lambda p=path: self._remove_file(p),
                font=ctk.CTkFont(self.ui_font, size=11),
                fg_color="transparent", hover_color=("#cf222e", "#f85149"),
                text_color=("gray40", "gray70"),
            )
            del_btn.pack(side="right")

            self.rows[path] = row_frame
            self.row_labels[path] = lbl
            self.row_actions[path] = act_box
            self.states[path] = "queued"
            added += 1

        self._update_queue_header()
        if added > 0:
            self.status_lbl.configure(text=f"Đã thêm {added} file vào hàng đợi")

    def _remove_file(self, path: Path) -> None:
        if self.worker and self.worker.is_alive() and self.states.get(path) == "running":
            return
        if path in self.rows:
            self.rows[path].destroy()
            del self.rows[path]
        if path in self.row_labels:
            del self.row_labels[path]
        if path in self.row_actions:
            del self.row_actions[path]
        if path in self.states:
            del self.states[path]
        if path in self.outputs:
            del self.outputs[path]
        if path in self.files:
            self.files.remove(path)
        self._update_queue_header()

    def _update_queue_header(self) -> None:
        total = len(self.files)
        done = sum(1 for s in self.states.values() if s == "done")
        self.queue_header.configure(
            text=f"Hàng đợi ({total} file, đã xong {done}) — Nhấp vào file đã xong để mở"
        )

    def _clear_all(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        for r in self.rows.values():
            r.destroy()
        self.files.clear()
        self.file_info.clear()
        self.rows.clear()
        self.row_labels.clear()
        self.row_actions.clear()
        self.states.clear()
        self.outputs.clear()
        self.progress_bar.set(0)
        self.title("VI Translate — Dịch PDF Giữ Nguyên Bố Cục")
        self._update_queue_header()
        self.status_lbl.configure(text="Đã xóa toàn bộ hàng đợi")

    def _clear_done(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        done_files = [p for p in self.files if self.states.get(p) == "done"]
        for p in done_files:
            self._remove_file(p)
        self.status_lbl.configure(text=f"Đã dọn dẹp {len(done_files)} file đã xong")

    def _cancel(self) -> None:
        if self.worker and self.worker.is_alive():
            self.cancel_event.set()
            self.btn_cancel.configure(state="disabled", text="Đang dừng…")
            self.status_lbl.configure(text="⏳ Đang dừng sau trang hiện tại…")

    # -- Execution Worker --------------------------------------------------
    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        pending = [p for p in self.files if self.states.get(p) in ("queued", "failed", "cancelled")]
        if not pending:
            self.status_lbl.configure(text="Không còn file nào cần dịch trong hàng đợi")
            return

        names = {name: code for code, name in LANGUAGE_NAMES.items()}
        selected_lang_name = self.language_menu.get()
        language = names.get(selected_lang_name, "vi")
        self.config["target_language"] = language
        self._save_config()

        overwrite = bool(self.overwrite_check.get())
        pages_input = self.pages_entry.get().strip() or None

        if pages_input:
            if not re.fullmatch(r"[1-9]\d*(?:-[1-9]\d*)?(?:,[1-9]\d*(?:-[1-9]\d*)?)*", pages_input):
                self.status_lbl.configure(text="⚠️ Dải trang không hợp lệ! Vui lòng nhập dạng: 1-5 hoặc 1,3-5")
                return

        threads = int(self.threads_slider.get())
        glossary_raw = self.glossary_text.get("1.0", "end").strip()
        custom_out = self.out_dir_entry.get().strip() or None
        export_mode = self.config.get("export_format", "mono")
        engine = self.config.get("engine", "google")
        engine_model = self.config.get("ollama_model", "qwen2.5:7b")

        self.cancel_event.clear()
        self.btn_start.configure(state="disabled", text="Đang dịch…")
        self.btn_cancel.configure(state="normal", text="⏹ Dừng")
        self.progress_bar.set(0)
        self.batch_done, self.batch_total = 0, len(pending)
        self.start_time = time.time()

        self.worker = threading.Thread(
            target=self._run,
            args=(pending, language, overwrite, pages_input, threads, glossary_raw, custom_out, export_mode, engine, engine_model),
            daemon=True,
        )
        self.worker.start()

    def _run(
        self,
        files: list[Path],
        language: str,
        overwrite: bool,
        pages: str | None,
        threads: int,
        glossary: str,
        custom_out: str | None,
        export: str = "mono",
        engine: str = "google",
        engine_model: str | None = None,
    ) -> None:
        for index, path in enumerate(files, 1):
            if self.cancel_event.is_set():
                self.events.put(("status", path, "cancelled", "Đã hủy bởi người dùng", None))
                continue

            self.events.put(("status", path, "running", "", None))
            destination = Path(custom_out) if custom_out else path.parent / "translated"

            def report(done: int, total: int, _p: Path = path) -> None:
                self.events.put(("page", _p, done, total))

            try:
                result = translate_pdf(
                    path,
                    destination,
                    target_language=language,
                    pages=pages,
                    threads=threads,
                    overwrite=overwrite,
                    engine=engine,
                    engine_model=engine_model,
                    export=export,
                    glossary=glossary,
                    on_progress=report,
                )
                detail = (
                    f"{result.untranslated} đoạn chưa dịch được"
                    if result.untranslated
                    else str(result.path)
                )
                state = "partial" if result.untranslated else "done"
                self.events.put(("status", path, state, detail, result.path))
            except TranslationError as error:
                error_msg = str(error)
                if "already exists" in error_msg:
                    state = "skipped"
                    detail = "File đã tồn tại (chọn Ghi đè để dịch lại)"
                else:
                    state = "failed"
                    detail = error_msg
                    self._log_failure(destination, path, error)
                self.events.put(("status", path, state, detail, None))
            except Exception as error:  # noqa: BLE001
                self._log_failure(destination, path, error)
                self.events.put(("status", path, "failed", f"{type(error).__name__}: {error}", None))

            self.events.put(("progress", index / len(files), index, len(files)))

        self.events.put(("finished",))

    @staticmethod
    def _log_failure(destination: Path, source: Path, error: BaseException) -> Path | None:
        try:
            destination.mkdir(parents=True, exist_ok=True)
            log_file = destination / "pdf-translate.log"
            with log_file.open("a", encoding="utf-8") as log:
                log.write(f"\n{'=' * 70}\n{datetime.now():%Y-%m-%d %H:%M:%S}  {source}\n")
                traceback.print_exception(type(error), error, error.__traceback__, file=log)
            return log_file
        except OSError:
            return None

    def _drain_events(self) -> None:
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break

            if event[0] == "status":
                _, path, state, detail, produced = event
                if produced is not None:
                    self.outputs[path] = Path(produced)
                    self.last_output = Path(produced).parent
                    self._show_output_link()
                    if self.auto_open_var.get() and state == "done":
                        self._open(Path(produced))

                self.states[path] = state
                lbl = self.row_labels.get(path)
                if lbl:
                    if state == "done":
                        text = f"{STATUS_MARKS[state]}  {path.name}  ➔  Hoàn thành (Bấm để xem PDF)"
                    elif state == "cancelled":
                        text = f"{STATUS_MARKS[state]}  {path.name}  ➔  Đã dừng"
                    elif state == "failed":
                        text = f"{STATUS_MARKS[state]}  {path.name}  ➔  LỖI (Bấm để xem file log)"
                    else:
                        text = f"{STATUS_MARKS[state]}  {path.name}"

                    if state in ("failed", "skipped", "partial") and detail:
                        text += f"\n     {detail.splitlines()[0]}"
                    lbl.configure(text=text, text_color=STATUS_COLORS[state])

                r_frame = self.rows.get(path)
                if path in self.outputs:
                    if r_frame:
                        r_frame.configure(cursor="hand2")
                        r_frame.bind("<Button-1>", lambda _e, p=path: self._open(self.outputs.get(p)))
                        if lbl:
                            lbl.bind("<Button-1>", lambda _e, p=path: self._open(self.outputs.get(p)))
                elif state == "failed":
                    log_target = (Path(self.out_dir_entry.get().strip()) if self.out_dir_entry.get().strip() else path.parent / "translated") / "pdf-translate.log"
                    if r_frame:
                        r_frame.configure(cursor="hand2")
                        r_frame.bind("<Button-1>", lambda _e, lt=log_target: self._open(lt))
                        if lbl:
                            lbl.bind("<Button-1>", lambda _e, lt=log_target: self._open(lt))

                self._update_queue_header()

            elif event[0] == "page":
                _, path, done, total = event
                if self.states.get(path) == "running" and total:
                    total_pages = max(1, total // 2)
                    is_phase_1 = done <= total_pages
                    
                    phase_name = "Quét AI" if is_phase_1 else "Dịch PDF"
                    current_page = done if is_phase_1 else (done - total_pages)
                    current_page = min(current_page, total_pages)
                    
                    percent = int((done / total) * 100)
                    
                    lbl = self.row_labels.get(path)
                    if lbl:
                        lbl.configure(
                            text=f"{STATUS_MARKS['running']}  {path.name}  ➔  [{phase_name}] Trang {current_page}/{total_pages} ({percent}%)"
                        )

                    overall_frac = (self.batch_done + done / total) / max(self.batch_total, 1)
                    
                    if is_phase_1:
                        self.progress_bar.configure(progress_color=("#d29922", "#e3b341"))
                    else:
                        self.progress_bar.configure(progress_color=("#1a7f37", "#3fb950"))
                        
                    self.progress_bar.set(overall_frac)

                    # Calculate ETA
                    elapsed = max(0.1, time.time() - self.start_time)
                    pages_done = (self.batch_done * total) + done
                    total_pages_est = max(1, self.batch_total * total)
                    rate = pages_done / elapsed
                    remaining_pages = max(0, total_pages_est - pages_done)
                    eta_sec = remaining_pages / max(0.1, rate)

                    eta_str = f" • Xong trong: {format_duration(eta_sec)}" if eta_sec > 5 else ""
                    self.status_lbl.configure(
                        text=f"⏳ Đang chạy: {path.name} — [{phase_name}] Trang {current_page}/{total_pages} ({percent}%){eta_str}"
                    )
                    self.title(f"({int(overall_frac * 100)}%) VI Translate")

            elif event[0] == "progress":
                _, fraction, done_files, total_files = event
                self.batch_done, self.batch_total = done_files, total_files
                self.progress_bar.set(fraction)

            elif event[0] == "finished":
                self.btn_start.configure(state="normal", text="▶ Dịch")
                self.btn_cancel.configure(state="disabled", text="⏹ Dừng")
                self.title("VI Translate — Dịch PDF Giữ Nguyên Bố Cục")

                counts = {}
                for state in self.states.values():
                    counts[state] = counts.get(state, 0) + 1

                elapsed = format_duration(time.time() - self.start_time)
                if self.cancel_event.is_set():
                    summary = f"Đã dừng tiến trình ({elapsed})"
                else:
                    summary = f"🎉 Hoàn thành {counts.get('done', 0)}/{len(self.files)} file ({elapsed})"
                    if counts.get("partial"):
                        summary += f", {counts['partial']} file dịch thiếu"
                    if counts.get("failed"):
                        summary += f", {counts['failed']} file lỗi"
                    if counts.get("skipped"):
                        summary += f", {counts['skipped']} file bỏ qua"

                self.status_lbl.configure(text=summary)
                self._update_queue_header()

        self.after(100, self._drain_events)

    @staticmethod
    def _open(target: Path | None) -> None:
        if target is None or not Path(target).exists():
            return
        try:
            os.startfile(target)  # noqa: S606
        except OSError:
            pass

    def _show_output_link(self) -> None:
        if self.last_output is not None:
            self.output_link_lbl.configure(text=f"📂 Mở thư mục kết quả: {self.last_output}")


def main() -> None:
    ensure_writable_streams()
    use_bundled_assets()
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = App()
    if sys.argv[1:]:
        app._add([Path(argument) for argument in sys.argv[1:]])
    app.mainloop()


if __name__ == "__main__":
    main()
