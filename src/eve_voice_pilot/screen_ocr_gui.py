from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from .input_sender import parse_key_chord
from .screen_ocr_watcher import (
    DEFAULT_HOLD_SECONDS,
    DEFAULT_POLL_SECONDS,
    DEFAULT_STABLE_SAMPLES,
    DEFAULT_WINDOW_TITLE,
    OcrWatcherError,
    ScreenRegion,
    StableValueTracker,
    capture_region,
    maybe_send_hotkey,
    normalize_ocr_value,
    parse_region,
    read_ocr_text,
)


ROOT = Path(__file__).resolve().parents[2]
USER_SETTINGS_PATH = ROOT / "profiles" / "my_ocr_watcher_settings.json"

DEFAULT_REGION_TEXT = "100,200,260,40"
DEFAULT_HOTKEY = "CTRL+SHIFT+F9"
DEFAULT_LANG = "eng"
DEFAULT_PSM = "7"
DEFAULT_SCALE = "2.0"
DEFAULT_COOLDOWN = "1.5"

BUTTON_GUIDE: tuple[tuple[str, str], ...] = (
    ("Validate Settings", "Checks the region, hotkey, numeric fields, regex, and Tesseract path."),
    ("Read Once", "Runs one OCR read and prints the raw text plus the watched value."),
    ("Start Dry Run", "Watches for stable changes and logs the hotkey without sending it."),
    ("Start Live Watch", "Watches for stable changes and sends the hotkey when the value changes."),
    ("Stop", "Stops the current watch loop after the current OCR read finishes."),
    ("Test Hotkey", "Checks the active-window guard, then sends or dry-runs the current hotkey."),
    ("Show Region", "Draws a temporary overlay exactly where the OCR watcher will look."),
    ("Preview Region", "Captures the current region and opens a zoomed preview of what OCR sees."),
    ("Select Region", "Opens a crosshair overlay so you can drag the watched region with the mouse."),
    ("Set Top Left", "After 3 seconds, uses the mouse position as the region's top-left corner."),
    ("Set Bottom Right", "After 3 seconds, uses the mouse position to resize the region."),
    ("Mouse Position", "Logs the mouse coordinates after 3 seconds; the live coordinates also update in Settings."),
    ("Save Settings", f"Saves this panel to {USER_SETTINGS_PATH.name}."),
    ("Load Settings", f"Loads {USER_SETTINGS_PATH.name} if it exists."),
)


@dataclass(frozen=True)
class OcrGuiSettings:
    region: ScreenRegion
    hotkey: str
    pattern: str | None
    poll: float
    stable_samples: int
    cooldown: float
    hold_seconds: float
    lang: str
    psm: int
    scale: float
    tesseract_cmd: str
    tesseract_config: str
    window_title_contains: str
    keep_case: bool
    dry_run: bool
    verbose: bool

    @property
    def region_text(self) -> str:
        return f"{self.region.left},{self.region.top},{self.region.width},{self.region.height}"


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = (
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
)
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
user32.GetSystemMetrics.restype = ctypes.c_int

HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def default_settings_dict() -> dict[str, Any]:
    return {
        "region": DEFAULT_REGION_TEXT,
        "hotkey": DEFAULT_HOTKEY,
        "pattern": "",
        "poll": f"{DEFAULT_POLL_SECONDS:.2f}",
        "stable_samples": str(DEFAULT_STABLE_SAMPLES),
        "cooldown": DEFAULT_COOLDOWN,
        "hold_seconds": f"{DEFAULT_HOLD_SECONDS:.2f}",
        "lang": DEFAULT_LANG,
        "psm": DEFAULT_PSM,
        "scale": DEFAULT_SCALE,
        "tesseract_cmd": "",
        "tesseract_config": "",
        "window_title_contains": DEFAULT_WINDOW_TITLE,
        "keep_case": False,
        "dry_run": True,
        "verbose": True,
    }


def button_guide_lines() -> list[str]:
    return [f"{name}: {description}" for name, description in BUTTON_GUIDE]


def region_summary(region: ScreenRegion) -> str:
    return f"{region.left},{region.top},{region.width},{region.height}"


def region_from_points(start: tuple[int, int], end: tuple[int, int]) -> ScreenRegion:
    left = min(start[0], end[0])
    top = min(start[1], end[1])
    width = max(1, abs(end[0] - start[0]))
    height = max(1, abs(end[1] - start[1]))
    return ScreenRegion(left, top, width, height)


def virtual_screen_region() -> ScreenRegion:
    return ScreenRegion(
        left=user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        top=user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        width=max(1, user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)),
        height=max(1, user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)),
    )


def current_mouse_position() -> tuple[int, int]:
    point = POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        raise OSError(ctypes.get_last_error(), "Could not read the mouse position")
    return point.x, point.y


class OcrWatcherGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("EVE OCR Watcher")
        self.root.minsize(980, 720)

        defaults = default_settings_dict()
        self.region_var = tk.StringVar(value=str(defaults["region"]))
        self.hotkey_var = tk.StringVar(value=str(defaults["hotkey"]))
        self.pattern_var = tk.StringVar(value=str(defaults["pattern"]))
        self.poll_var = tk.StringVar(value=str(defaults["poll"]))
        self.stable_samples_var = tk.StringVar(value=str(defaults["stable_samples"]))
        self.cooldown_var = tk.StringVar(value=str(defaults["cooldown"]))
        self.hold_seconds_var = tk.StringVar(value=str(defaults["hold_seconds"]))
        self.lang_var = tk.StringVar(value=str(defaults["lang"]))
        self.psm_var = tk.StringVar(value=str(defaults["psm"]))
        self.scale_var = tk.StringVar(value=str(defaults["scale"]))
        self.tesseract_cmd_var = tk.StringVar(value=str(defaults["tesseract_cmd"]))
        self.tesseract_config_var = tk.StringVar(value=str(defaults["tesseract_config"]))
        self.window_title_var = tk.StringVar(value=str(defaults["window_title_contains"]))
        self.keep_case_var = tk.BooleanVar(value=bool(defaults["keep_case"]))
        self.dry_run_var = tk.BooleanVar(value=bool(defaults["dry_run"]))
        self.verbose_var = tk.BooleanVar(value=bool(defaults["verbose"]))
        self.status_var = tk.StringVar(value="Ready")
        self.mouse_position_var = tk.StringVar(value="Mouse: --,--")

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._status_queue: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._selection_start: tuple[int, int] | None = None
        self._selection_rect_id: int | None = None
        self._selection_label_id: int | None = None

        self._build_ui()
        self._drain_queues()
        self._track_mouse_position()
        self._try_auto_load_settings()
        self._log("Ready. Start with Read Once or Start Dry Run.")

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        settings_frame = ttk.LabelFrame(self.root, text="Settings")
        settings_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        for column in range(6):
            settings_frame.columnconfigure(column, weight=1)

        self._add_entry(settings_frame, 0, 0, "Region", self.region_var, 24)
        self._add_entry(settings_frame, 0, 2, "Hotkey", self.hotkey_var, 20)
        self._add_entry(settings_frame, 0, 4, "Regex pattern", self.pattern_var, 24)
        self._add_entry(settings_frame, 1, 0, "Poll seconds", self.poll_var, 10)
        self._add_entry(settings_frame, 1, 2, "Stable reads", self.stable_samples_var, 10)
        self._add_entry(settings_frame, 1, 4, "Cooldown", self.cooldown_var, 10)
        self._add_entry(settings_frame, 2, 0, "Hold seconds", self.hold_seconds_var, 10)
        self._add_entry(settings_frame, 2, 2, "Language", self.lang_var, 10)
        self._add_entry(settings_frame, 2, 4, "PSM", self.psm_var, 10)
        self._add_entry(settings_frame, 3, 0, "Scale", self.scale_var, 10)
        self._add_entry(settings_frame, 3, 2, "Active title", self.window_title_var, 18)

        ttk.Label(settings_frame, text="Tesseract exe").grid(row=4, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(settings_frame, textvariable=self.tesseract_cmd_var).grid(
            row=4,
            column=1,
            columnspan=4,
            sticky="ew",
            padx=6,
            pady=4,
        )
        ttk.Button(settings_frame, text="Browse", command=self._browse_tesseract).grid(
            row=4,
            column=5,
            sticky="ew",
            padx=6,
            pady=4,
        )

        ttk.Label(settings_frame, text="Tesseract config").grid(row=5, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(settings_frame, textvariable=self.tesseract_config_var).grid(
            row=5,
            column=1,
            columnspan=5,
            sticky="ew",
            padx=6,
            pady=4,
        )

        checkbox_frame = ttk.Frame(settings_frame)
        checkbox_frame.grid(row=6, column=0, columnspan=6, sticky="w", padx=6, pady=(2, 6))
        ttk.Checkbutton(checkbox_frame, text="Dry run", variable=self.dry_run_var).grid(row=0, column=0, padx=(0, 16))
        ttk.Checkbutton(checkbox_frame, text="Keep case", variable=self.keep_case_var).grid(row=0, column=1, padx=(0, 16))
        ttk.Checkbutton(checkbox_frame, text="Verbose", variable=self.verbose_var).grid(row=0, column=2, padx=(0, 16))
        ttk.Label(checkbox_frame, textvariable=self.status_var).grid(row=0, column=3, padx=(18, 0), sticky="w")
        ttk.Label(checkbox_frame, textvariable=self.mouse_position_var).grid(row=0, column=4, padx=(24, 0), sticky="w")

        button_frame = ttk.LabelFrame(self.root, text="Testing and Watch Buttons")
        button_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=6)
        for column in range(9):
            button_frame.columnconfigure(column, weight=1)

        self._button(button_frame, 0, 0, "Validate Settings", self._validate_settings)
        self._button(button_frame, 0, 1, "Select Region", self._select_region)
        self._button(button_frame, 0, 2, "Show Region", self._show_region_overlay)
        self._button(button_frame, 0, 3, "Preview Region", self._preview_region)
        self._button(button_frame, 0, 4, "Read Once", self._read_once)
        self._button(button_frame, 0, 5, "Start Dry Run", lambda: self._start_watch(force_dry_run=True))
        self._button(button_frame, 0, 6, "Start Live Watch", lambda: self._start_watch(force_dry_run=False))
        self._button(button_frame, 0, 7, "Stop", self._stop_worker)
        self._button(button_frame, 0, 8, "Test Hotkey", self._test_hotkey)
        self._button(button_frame, 1, 8, "Clear Log", self._clear_log)

        preset_frame = ttk.LabelFrame(self.root, text="Settings Presets")
        preset_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=6)
        for column in range(9):
            preset_frame.columnconfigure(column, weight=1)

        self._button(preset_frame, 0, 0, "Safe Defaults", self._safe_defaults)
        self._button(preset_frame, 0, 1, "Numbers Only", self._numbers_only)
        self._button(preset_frame, 0, 2, "Single Line", self._single_line)
        self._button(preset_frame, 0, 3, "Text Block", self._text_block)
        self._button(preset_frame, 0, 4, "Fast Poll", self._fast_poll)
        self._button(preset_frame, 0, 5, "Steady Poll", self._steady_poll)
        self._button(preset_frame, 0, 6, "Use EVE Guard", self._use_eve_guard)
        self._button(preset_frame, 0, 7, "No Guard", self._no_window_guard)
        self._button(preset_frame, 0, 8, "Save Settings", self._save_settings)

        self._button(preset_frame, 1, 0, "Load Settings", self._load_settings)
        self._button(preset_frame, 1, 1, "Set Top Left", self._set_top_left_later)
        self._button(preset_frame, 1, 2, "Set Bottom Right", self._set_bottom_right_later)
        self._button(preset_frame, 1, 3, "Mouse Position", self._log_mouse_position_later)

        bottom = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        bottom.grid(row=3, column=0, sticky="nsew", padx=10, pady=(6, 10))

        log_frame = ttk.LabelFrame(bottom, text="Output Log")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=18, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll.grid(row=0, column=1, sticky="ns")
        bottom.add(log_frame, weight=3)

        guide_frame = ttk.LabelFrame(bottom, text="Button Guide")
        guide_frame.columnconfigure(0, weight=1)
        guide_frame.rowconfigure(0, weight=1)
        guide_text = tk.Text(guide_frame, width=42, wrap="word", height=18)
        guide_text.insert("1.0", "\n\n".join(button_guide_lines()))
        guide_text.configure(state="disabled")
        guide_text.grid(row=0, column=0, sticky="nsew")
        bottom.add(guide_frame, weight=1)

    def _add_entry(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
        width: int,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=6, pady=4)
        ttk.Entry(parent, textvariable=variable, width=width).grid(
            row=row,
            column=column + 1,
            sticky="ew",
            padx=6,
            pady=4,
        )

    def _button(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        text: str,
        command: Callable[[], None],
    ) -> None:
        ttk.Button(parent, text=text, command=command).grid(row=row, column=column, sticky="ew", padx=5, pady=5)

    def _browse_tesseract(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose tesseract.exe",
            filetypes=(("Tesseract", "tesseract.exe"), ("Programs", "*.exe"), ("All files", "*.*")),
        )
        if path:
            self.tesseract_cmd_var.set(path)
            self._log(f"Tesseract path set: {path}")

    def _show_region_overlay(self) -> None:
        try:
            region = parse_region(self.region_var.get())
        except Exception as exc:
            self._log(f"Region problem: {exc}")
            self._set_status("Region needs attention")
            return

        overlay = tk.Toplevel(self.root)
        overlay.title("OCR Region")
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)

        transparent_color = "#010203"
        transparent_supported = True
        try:
            overlay.configure(bg=transparent_color)
            overlay.wm_attributes("-transparentcolor", transparent_color)
        except tk.TclError:
            transparent_supported = False
            overlay.configure(bg="#facc15")
            overlay.attributes("-alpha", 0.35)

        canvas = tk.Canvas(
            overlay,
            width=region.width,
            height=region.height,
            bg=transparent_color if transparent_supported else "#facc15",
            bd=0,
            highlightthickness=0,
        )
        canvas.pack(fill="both", expand=True)
        self._draw_region_overlay(canvas, region.width, region.height)

        self._refresh_region_overlay(overlay, canvas)
        overlay.after(5000, overlay.destroy)
        self._log(f"Showing OCR region for 5 seconds: {region_summary(region)}.")
        self._set_status("Showing region")

    def _refresh_region_overlay(self, overlay: tk.Toplevel, canvas: tk.Canvas) -> None:
        try:
            exists = overlay.winfo_exists()
        except tk.TclError:
            return
        if not exists:
            return
        try:
            region = parse_region(self.region_var.get())
        except Exception:
            try:
                overlay.after(150, lambda: self._refresh_region_overlay(overlay, canvas))
            except tk.TclError:
                pass
            return
        canvas.configure(width=region.width, height=region.height)
        self._draw_region_overlay(canvas, region.width, region.height)
        self._position_top_level(overlay, region)
        try:
            overlay.after(150, lambda: self._refresh_region_overlay(overlay, canvas))
        except tk.TclError:
            pass

    def _draw_region_overlay(self, canvas: tk.Canvas, width: int, height: int) -> None:
        canvas.delete("all")
        outline = "#ff2d55"
        accent = "#facc15"
        canvas.create_rectangle(2, 2, width - 3, height - 3, outline=outline, width=4)
        canvas.create_line(0, 0, min(28, width), min(28, height), fill=accent, width=3)
        canvas.create_line(width, 0, max(width - 28, 0), min(28, height), fill=accent, width=3)
        canvas.create_line(0, height, min(28, width), max(height - 28, 0), fill=accent, width=3)
        canvas.create_line(width, height, max(width - 28, 0), max(height - 28, 0), fill=accent, width=3)
        if width >= 140 and height >= 28:
            canvas.create_text(
                width // 2,
                height // 2,
                text="OCR Region",
                fill=accent,
                font=("Segoe UI", 11, "bold"),
            )

    def _select_region(self) -> None:
        bounds = virtual_screen_region()
        overlay = tk.Toplevel(self.root)
        overlay.title("Select OCR Region")
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.attributes("-alpha", 0.28)
        overlay.configure(bg="#111827", cursor="crosshair")

        canvas = tk.Canvas(
            overlay,
            width=bounds.width,
            height=bounds.height,
            bg="#111827",
            bd=0,
            highlightthickness=0,
            cursor="crosshair",
        )
        canvas.pack(fill="both", expand=True)
        help_text = "Drag a box around the OCR text. Release to use it. Press Esc to cancel."
        canvas.create_text(
            24,
            24,
            text=help_text,
            anchor="nw",
            fill="#ffffff",
            font=("Segoe UI", 16, "bold"),
        )
        self._selection_start = None
        self._selection_rect_id = None
        self._selection_label_id = None

        canvas.bind("<ButtonPress-1>", lambda event: self._begin_region_selection(event, canvas, bounds))
        canvas.bind("<B1-Motion>", lambda event: self._move_region_selection(event, canvas, bounds))
        canvas.bind("<ButtonRelease-1>", lambda event: self._finish_region_selection(event, overlay, canvas, bounds))
        overlay.bind("<Escape>", lambda _event: self._cancel_region_selection(overlay))
        self._position_top_level(overlay, bounds)
        overlay.focus_force()
        overlay.grab_set()
        self._log("Select Region started. Drag over the text you want OCR to read, or press Esc to cancel.")
        self._set_status("Selecting region")

    def _begin_region_selection(self, event: tk.Event, canvas: tk.Canvas, bounds: ScreenRegion) -> None:
        start = (event.x_root, event.y_root)
        self._selection_start = start
        x = start[0] - bounds.left
        y = start[1] - bounds.top
        if self._selection_rect_id is not None:
            canvas.delete(self._selection_rect_id)
        if self._selection_label_id is not None:
            canvas.delete(self._selection_label_id)
        self._selection_rect_id = canvas.create_rectangle(x, y, x, y, outline="#facc15", width=3)
        self._selection_label_id = canvas.create_text(
            x + 8,
            y + 8,
            text="1x1",
            anchor="nw",
            fill="#ffffff",
            font=("Segoe UI", 12, "bold"),
        )

    def _move_region_selection(self, event: tk.Event, canvas: tk.Canvas, bounds: ScreenRegion) -> None:
        if self._selection_start is None or self._selection_rect_id is None:
            return
        region = region_from_points(self._selection_start, (event.x_root, event.y_root))
        self._update_selection_canvas(canvas, bounds, region)

    def _finish_region_selection(
        self,
        event: tk.Event,
        overlay: tk.Toplevel,
        canvas: tk.Canvas,
        bounds: ScreenRegion,
    ) -> None:
        if self._selection_start is None:
            self._cancel_region_selection(overlay)
            return
        region = region_from_points(self._selection_start, (event.x_root, event.y_root))
        self._update_selection_canvas(canvas, bounds, region)
        overlay.grab_release()
        overlay.destroy()
        self.region_var.set(region_summary(region))
        self._selection_start = None
        self._selection_rect_id = None
        self._selection_label_id = None
        self._log(f"Selected OCR region: {region_summary(region)}.")
        self._set_status("Region selected")

    def _cancel_region_selection(self, overlay: tk.Toplevel) -> None:
        try:
            overlay.grab_release()
        except tk.TclError:
            pass
        overlay.destroy()
        self._selection_start = None
        self._selection_rect_id = None
        self._selection_label_id = None
        self._log("Select Region canceled.")
        self._set_status("Ready")

    def _update_selection_canvas(self, canvas: tk.Canvas, bounds: ScreenRegion, region: ScreenRegion) -> None:
        if self._selection_rect_id is None:
            return
        x1 = region.left - bounds.left
        y1 = region.top - bounds.top
        x2 = x1 + region.width
        y2 = y1 + region.height
        canvas.coords(self._selection_rect_id, x1, y1, x2, y2)
        label = f"{region_summary(region)}"
        if self._selection_label_id is not None:
            canvas.coords(self._selection_label_id, x1 + 8, y1 + 8)
            canvas.itemconfigure(self._selection_label_id, text=label)

    def _preview_region(self) -> None:
        try:
            region = parse_region(self.region_var.get())
        except Exception as exc:
            self._log(f"Region problem: {exc}")
            self._set_status("Region needs attention")
            return

        try:
            from PIL import Image, ImageTk
        except ImportError as exc:
            self._log(f"Preview needs Pillow: {exc}")
            self._set_status("Preview failed")
            return

        try:
            image = capture_region(region)
        except (OcrWatcherError, OSError) as exc:
            self._log(f"Preview capture error: {exc}")
            self._set_status("Preview failed")
            return

        preview = tk.Toplevel(self.root)
        preview.title(f"OCR Region Preview - {region_summary(region)}")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)

        display = image.copy()
        scale = max(1, min(4, int(720 / max(display.width, 1)), int(360 / max(display.height, 1))))
        if scale > 1:
            display = display.resize((display.width * scale, display.height * scale), Image.Resampling.NEAREST)

        photo = ImageTk.PhotoImage(display)
        ttk.Label(preview, text=f"Raw screen crop: {region_summary(region)}").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        image_label = ttk.Label(preview, image=photo)
        image_label.image_ref = photo
        image_label.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        ttk.Button(preview, text="Close", command=preview.destroy).grid(row=2, column=0, sticky="e", padx=10, pady=(4, 10))
        self._log(f"Preview opened for region: {region_summary(region)}.")
        self._set_status("Preview opened")

    def _position_top_level(self, window: tk.Toplevel, region: ScreenRegion) -> None:
        window.update_idletasks()
        hwnd = wintypes.HWND(window.winfo_id())
        ok = user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            region.left,
            region.top,
            region.width,
            region.height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )
        if not ok:
            window.geometry(f"{region.width}x{region.height}+{region.left}+{region.top}")

    def _validate_settings(self) -> None:
        try:
            settings = self._read_settings()
        except ValueError as exc:
            self._log(f"Settings problem: {exc}")
            self._set_status("Settings need attention")
            messagebox.showerror("Settings problem", str(exc), parent=self.root)
            return
        self._log(
            "Settings OK: "
            f"region={settings.region_text}, hotkey={settings.hotkey}, "
            f"poll={settings.poll:.2f}s, stable={settings.stable_samples}."
        )
        self._set_status("Settings OK")

    def _read_once(self) -> None:
        try:
            settings = self._read_settings()
        except ValueError as exc:
            self._log(f"Settings problem: {exc}")
            self._set_status("Settings need attention")
            return

        def worker() -> None:
            self._set_status_from_worker("Reading once...")
            try:
                raw_text = self._read_ocr(settings)
                value = normalize_ocr_value(raw_text, pattern=settings.pattern, keep_case=settings.keep_case)
                self._log_from_worker(f"Raw OCR: {raw_text.strip()!r}")
                self._log_from_worker(f"Watched value: {value!r}")
                self._set_status_from_worker("Read once complete")
            except (OcrWatcherError, OSError, ValueError) as exc:
                self._log_from_worker(f"Read once error: {exc}")
                self._set_status_from_worker("Read once failed")

        self._start_worker(worker, "ocr-read-once")

    def _start_watch(self, *, force_dry_run: bool) -> None:
        try:
            settings = self._read_settings(dry_run_override=force_dry_run)
        except ValueError as exc:
            self._log(f"Settings problem: {exc}")
            self._set_status("Settings need attention")
            return

        if not settings.dry_run and settings.window_title_contains == "":
            confirmed = messagebox.askyesno(
                "Start without window guard?",
                "Window guard is disabled. Start live hotkey sending anyway?",
                parent=self.root,
            )
            if not confirmed:
                self._log("Live watch canceled because the window guard is disabled.")
                return

        def worker() -> None:
            self._watch_worker(settings)

        self._start_worker(worker, "ocr-watch")

    def _watch_worker(self, settings: OcrGuiSettings) -> None:
        tracker = StableValueTracker(settings.stable_samples)
        last_fire_at = 0.0
        mode = "dry run" if settings.dry_run else "live"
        self._set_status_from_worker(f"Watching ({mode})")
        self._log_from_worker(
            f"Watching {settings.region_text}; hotkey={settings.hotkey}; "
            f"poll={settings.poll:.2f}s; stable={settings.stable_samples}; mode={mode}."
        )
        if settings.window_title_contains:
            self._log_from_worker(f"Window guard: active title must contain {settings.window_title_contains!r}.")
        else:
            self._log_from_worker("Window guard is disabled.")

        while not self._stop_event.is_set():
            try:
                raw_text = self._read_ocr(settings)
                value = normalize_ocr_value(raw_text, pattern=settings.pattern, keep_case=settings.keep_case)
                event = tracker.observe(value)
                if settings.verbose:
                    self._log_from_worker(f"Sample: {value or '<empty>'}")
                if event and event.initial:
                    self._log_from_worker(f"Baseline: {event.value}")
                elif event and event.changed:
                    self._log_from_worker(f"Changed: {event.previous} -> {event.value}")
                    now = time.monotonic()
                    if settings.cooldown > 0 and now - last_fire_at < settings.cooldown:
                        self._log_from_worker(
                            f"Cooldown: skipped send because the last send was {now - last_fire_at:.2f}s ago."
                        )
                    elif maybe_send_hotkey(
                        settings.hotkey,
                        hold_seconds=settings.hold_seconds,
                        dry_run=settings.dry_run,
                        expected_window_title=settings.window_title_contains,
                        log=self._log_from_worker,
                    ):
                        last_fire_at = now
            except (OcrWatcherError, OSError, ValueError) as exc:
                self._log_from_worker(f"Watch error: {exc}")
                self._set_status_from_worker("Watch failed")
                return

            if self._stop_event.wait(settings.poll):
                break

        self._log_from_worker("Watch stopped.")
        self._set_status_from_worker("Stopped")

    def _test_hotkey(self) -> None:
        try:
            settings = self._read_settings()
        except ValueError as exc:
            self._log(f"Settings problem: {exc}")
            self._set_status("Settings need attention")
            return

        if not settings.dry_run:
            confirmed = messagebox.askyesno(
                "Send test hotkey?",
                f"Send {settings.hotkey} now if the active-window guard allows it?",
                parent=self.root,
            )
            if not confirmed:
                self._log("Test hotkey canceled.")
                return

        try:
            maybe_send_hotkey(
                settings.hotkey,
                hold_seconds=settings.hold_seconds,
                dry_run=settings.dry_run,
                expected_window_title=settings.window_title_contains,
                log=self._log,
            )
        except OSError as exc:
            self._log(f"Hotkey test error: {exc}")
            self._set_status("Hotkey test failed")

    def _stop_worker(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            self._stop_event.set()
            self._log("Stop requested.")
            self._set_status("Stopping...")
            return
        self._log("Nothing is running.")
        self._set_status("Ready")

    def _start_worker(self, target: Callable[[], None], name: str) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            self._log("A task is already running. Press Stop before starting another one.")
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=target, name=name, daemon=True)
        self._worker_thread.start()

    def _read_ocr(self, settings: OcrGuiSettings) -> str:
        return read_ocr_text(
            settings.region,
            lang=settings.lang,
            psm=settings.psm,
            scale=settings.scale,
            tesseract_cmd=settings.tesseract_cmd,
            tesseract_config=settings.tesseract_config,
        )

    def _read_settings(self, *, dry_run_override: bool | None = None) -> OcrGuiSettings:
        try:
            region = parse_region(self.region_var.get())
        except Exception as exc:
            raise ValueError(str(exc)) from exc

        hotkey = self.hotkey_var.get().strip().upper()
        try:
            parse_key_chord(hotkey)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

        pattern = self.pattern_var.get().strip() or None
        if pattern:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"Regex pattern problem: {exc}") from exc

        poll = self._positive_float(self.poll_var.get(), "Poll seconds")
        stable_samples = self._positive_int(self.stable_samples_var.get(), "Stable reads")
        cooldown = self._non_negative_float(self.cooldown_var.get(), "Cooldown")
        hold_seconds = self._positive_float(self.hold_seconds_var.get(), "Hold seconds")
        psm = self._positive_int(self.psm_var.get(), "PSM")
        scale = self._positive_float(self.scale_var.get(), "Scale")

        tesseract_cmd = self.tesseract_cmd_var.get().strip()
        if tesseract_cmd and not Path(tesseract_cmd).exists():
            raise ValueError(f"Tesseract exe was not found: {tesseract_cmd}")

        dry_run = self.dry_run_var.get() if dry_run_override is None else dry_run_override
        return OcrGuiSettings(
            region=region,
            hotkey=hotkey,
            pattern=pattern,
            poll=poll,
            stable_samples=stable_samples,
            cooldown=cooldown,
            hold_seconds=hold_seconds,
            lang=self.lang_var.get().strip() or DEFAULT_LANG,
            psm=psm,
            scale=scale,
            tesseract_cmd=tesseract_cmd,
            tesseract_config=self.tesseract_config_var.get().strip(),
            window_title_contains=self.window_title_var.get().strip(),
            keep_case=self.keep_case_var.get(),
            dry_run=dry_run,
            verbose=self.verbose_var.get(),
        )

    def _safe_defaults(self) -> None:
        self._apply_settings(default_settings_dict())
        self._log("Applied safe defaults. Dry run is on.")

    def _numbers_only(self) -> None:
        self.pattern_var.set(r"([0-9,.]+)")
        self.psm_var.set("7")
        self.tesseract_config_var.set("-c tessedit_char_whitelist=0123456789,.")
        self._log("Applied Numbers Only preset.")

    def _single_line(self) -> None:
        self.pattern_var.set("")
        self.psm_var.set("7")
        self.tesseract_config_var.set("")
        self._log("Applied Single Line text preset.")

    def _text_block(self) -> None:
        self.pattern_var.set("")
        self.psm_var.set("6")
        self.tesseract_config_var.set("")
        self._log("Applied Text Block preset.")

    def _fast_poll(self) -> None:
        self.poll_var.set("0.25")
        self.stable_samples_var.set("1")
        self._log("Applied Fast Poll preset.")

    def _steady_poll(self) -> None:
        self.poll_var.set("0.50")
        self.stable_samples_var.set("2")
        self._log("Applied Steady Poll preset.")

    def _use_eve_guard(self) -> None:
        self.window_title_var.set(DEFAULT_WINDOW_TITLE)
        self._log("Active-window guard set to EVE.")

    def _no_window_guard(self) -> None:
        self.window_title_var.set("")
        self._log("Active-window guard disabled. Use this carefully.")

    def _set_top_left_later(self) -> None:
        self._log("Move the mouse to the region top-left. Capturing in 3 seconds.")
        self.root.after(3000, self._set_top_left_from_mouse)

    def _set_top_left_from_mouse(self) -> None:
        try:
            x, y = current_mouse_position()
            region = self._region_or_default()
            self.region_var.set(f"{x},{y},{region.width},{region.height}")
            self._log(f"Region top-left set to {x},{y}.")
        except (OSError, ValueError) as exc:
            self._log(f"Mouse position error: {exc}")

    def _set_bottom_right_later(self) -> None:
        self._log("Move the mouse to the region bottom-right. Capturing in 3 seconds.")
        self.root.after(3000, self._set_bottom_right_from_mouse)

    def _set_bottom_right_from_mouse(self) -> None:
        try:
            x, y = current_mouse_position()
            region = self._region_or_default()
            width = max(1, x - region.left)
            height = max(1, y - region.top)
            self.region_var.set(f"{region.left},{region.top},{width},{height}")
            self._log(f"Region bottom-right set to {x},{y}; size is {width}x{height}.")
        except (OSError, ValueError) as exc:
            self._log(f"Mouse position error: {exc}")

    def _log_mouse_position_later(self) -> None:
        self._log("Move the mouse to a spot to inspect. Logging position in 3 seconds.")
        self.root.after(3000, self._log_mouse_position)

    def _log_mouse_position(self) -> None:
        try:
            x, y = current_mouse_position()
            self._log(f"Mouse position: {x},{y}")
        except OSError as exc:
            self._log(f"Mouse position error: {exc}")

    def _track_mouse_position(self) -> None:
        try:
            x, y = current_mouse_position()
            self.mouse_position_var.set(f"Mouse: {x},{y}")
        except OSError:
            self.mouse_position_var.set("Mouse: unavailable")
        try:
            self.root.after(125, self._track_mouse_position)
        except tk.TclError:
            pass

    def _region_or_default(self) -> ScreenRegion:
        try:
            return parse_region(self.region_var.get())
        except Exception:
            return parse_region(DEFAULT_REGION_TEXT)

    def _save_settings(self) -> None:
        try:
            self._read_settings()
        except ValueError as exc:
            self._log(f"Settings problem: {exc}")
            self._set_status("Settings need attention")
            return
        USER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        USER_SETTINGS_PATH.write_text(json.dumps(self._settings_dict(), indent=2) + "\n", encoding="utf-8")
        self._log(f"Settings saved to {USER_SETTINGS_PATH}.")
        self._set_status("Settings saved")

    def _load_settings(self) -> None:
        if not USER_SETTINGS_PATH.exists():
            self._log(f"No saved settings found at {USER_SETTINGS_PATH}.")
            return
        try:
            data = json.loads(USER_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._log(f"Could not load settings: {exc}")
            self._set_status("Load failed")
            return
        self._apply_settings(data)
        self._log(f"Settings loaded from {USER_SETTINGS_PATH}.")
        self._set_status("Settings loaded")

    def _try_auto_load_settings(self) -> None:
        if not USER_SETTINGS_PATH.exists():
            return
        try:
            data = json.loads(USER_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._apply_settings(data)
        self._log(f"Loaded saved settings from {USER_SETTINGS_PATH}.")

    def _settings_dict(self) -> dict[str, Any]:
        return {
            "region": self.region_var.get(),
            "hotkey": self.hotkey_var.get(),
            "pattern": self.pattern_var.get(),
            "poll": self.poll_var.get(),
            "stable_samples": self.stable_samples_var.get(),
            "cooldown": self.cooldown_var.get(),
            "hold_seconds": self.hold_seconds_var.get(),
            "lang": self.lang_var.get(),
            "psm": self.psm_var.get(),
            "scale": self.scale_var.get(),
            "tesseract_cmd": self.tesseract_cmd_var.get(),
            "tesseract_config": self.tesseract_config_var.get(),
            "window_title_contains": self.window_title_var.get(),
            "keep_case": self.keep_case_var.get(),
            "dry_run": self.dry_run_var.get(),
            "verbose": self.verbose_var.get(),
        }

    def _apply_settings(self, data: dict[str, Any]) -> None:
        defaults = default_settings_dict()
        merged = {key: data.get(key, defaults[key]) for key in defaults}
        self.region_var.set(str(merged["region"]))
        self.hotkey_var.set(str(merged["hotkey"]))
        self.pattern_var.set(str(merged["pattern"]))
        self.poll_var.set(str(merged["poll"]))
        self.stable_samples_var.set(str(merged["stable_samples"]))
        self.cooldown_var.set(str(merged["cooldown"]))
        self.hold_seconds_var.set(str(merged["hold_seconds"]))
        self.lang_var.set(str(merged["lang"]))
        self.psm_var.set(str(merged["psm"]))
        self.scale_var.set(str(merged["scale"]))
        self.tesseract_cmd_var.set(str(merged["tesseract_cmd"]))
        self.tesseract_config_var.set(str(merged["tesseract_config"]))
        self.window_title_var.set(str(merged["window_title_contains"]))
        self.keep_case_var.set(bool(merged["keep_case"]))
        self.dry_run_var.set(bool(merged["dry_run"]))
        self.verbose_var.set(bool(merged["verbose"]))

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._set_status("Log cleared")

    def _log(self, message: str) -> None:
        self._log_queue.put(f"{time.strftime('%H:%M:%S')} {message}")

    def _log_from_worker(self, message: str) -> None:
        self._log(message)

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)

    def _set_status_from_worker(self, message: str) -> None:
        self._status_queue.put(message)

    def _drain_queues(self) -> None:
        while True:
            try:
                message = self._log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.configure(state="normal")
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

        while True:
            try:
                status = self._status_queue.get_nowait()
            except queue.Empty:
                break
            self.status_var.set(status)

        self.root.after(100, self._drain_queues)

    def _on_close(self) -> None:
        self._stop_event.set()
        self.root.destroy()

    @staticmethod
    def _positive_float(value: str, label: str) -> float:
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"{label} should be a number.") from exc
        if number <= 0:
            raise ValueError(f"{label} must be greater than 0.")
        return number

    @staticmethod
    def _non_negative_float(value: str, label: str) -> float:
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"{label} should be a number.") from exc
        if number < 0:
            raise ValueError(f"{label} must be 0 or greater.")
        return number

    @staticmethod
    def _positive_int(value: str, label: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"{label} should be a whole number.") from exc
        if number < 1:
            raise ValueError(f"{label} must be at least 1.")
        return number


def main() -> None:
    root = tk.Tk()
    app = OcrWatcherGui(root)
    root.protocol("WM_DELETE_WINDOW", app._on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
