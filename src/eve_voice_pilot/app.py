from __future__ import annotations

from pathlib import Path
import queue
import shutil
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, simpledialog, ttk
import sounddevice as sd

from .commands import (
    DEFAULT_HOLD_SECONDS,
    DEFAULT_PRESS_COUNT,
    DEFAULT_RESPONSE_CALL_SIGN,
    DEFAULT_REPEAT_GAP_SECONDS,
    CommandProfile,
    VoiceCommand,
    find_exact_phrase_match,
    response_call_signs,
    strip_response_call_sign,
)
from .config import load_settings, save_settings
from .hotkey import GlobalHotkey
from .input_sender import active_window_title, parse_key_chord, send_key_chord
from .local_transcription import LocalVoskTranscriber
from .speech_responses import (
    DEFAULT_OPENAI_TTS_MODEL,
    DEFAULT_OPENAI_TTS_VOICE,
    DEFAULT_POWER_BALLAD_INSTRUCTIONS,
    DEFAULT_RESPONSE_ENGINE,
    DEFAULT_RESPONSE_SUFFIX,
    OPENAI_TTS_VOICES,
    RESPONSE_ENGINES,
    SpeechResponseManager,
)
from .transcription import (
    RealtimeTranscriber,
    audio_rms,
    block_size_for_rate,
    capture_rate_for_device,
    default_input_device_index,
    list_input_devices,
    resolve_input_device_label,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = ROOT / "profiles" / "eve_sample.json"
USER_PROFILE = ROOT / "profiles" / "my_eve_commands.json"
DEFAULT_HOTKEY = "PAUSE"
ENGINE_LOCAL = "Local (offline)"
ENGINE_OPENAI = "OpenAI realtime"
SPEECH_ENGINES = [ENGINE_LOCAL, ENGINE_OPENAI]


class CommandDialog(simpledialog.Dialog):
    def __init__(self, parent, title: str, command: VoiceCommand | None = None):
        self.command = command
        self.result_command: VoiceCommand | None = None
        super().__init__(parent, title)

    def body(self, master):
        master.columnconfigure(1, weight=1)
        ttk.Label(master, text="Name").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(master, text="Spoken phrases").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(master, text="Keybind").grid(row=2, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(master, text="Hold seconds").grid(row=3, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(master, text="Press count").grid(row=4, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(master, text="Delay between presses").grid(row=5, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(master, text="Speak response").grid(row=6, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(master, text="Response voice label").grid(row=7, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(master, text="Response text").grid(row=8, column=0, sticky="w", padx=6, pady=5)

        self.name_var = tk.StringVar(value=self.command.name if self.command else "")
        self.phrases_var = tk.StringVar(value=", ".join(self.command.phrases) if self.command else "")
        self.key_var = tk.StringVar(value=self.command.key if self.command else "")
        self.hold_var = tk.StringVar(value=f"{self.command.hold_seconds:.2f}" if self.command else f"{DEFAULT_HOLD_SECONDS:.2f}")
        self.press_count_var = tk.StringVar(value=str(self.command.press_count) if self.command else str(DEFAULT_PRESS_COUNT))
        self.repeat_gap_var = tk.StringVar(
            value=f"{self.command.repeat_gap_seconds:.2f}" if self.command else f"{DEFAULT_REPEAT_GAP_SECONDS:.2f}"
        )
        self.speak_response_var = tk.BooleanVar(value=bool(self.command and self.command.response_suffix.strip()))
        self.response_suffix_var = tk.StringVar(value=self.command.response_suffix if self.command else DEFAULT_RESPONSE_SUFFIX)
        self.response_text_var = tk.StringVar(value=self.command.response_text if self.command else "")

        name_entry = ttk.Entry(master, textvariable=self.name_var, width=42)
        ttk.Entry(master, textvariable=self.phrases_var, width=42).grid(row=1, column=1, sticky="ew", padx=6, pady=5)
        ttk.Entry(master, textvariable=self.key_var, width=20).grid(row=2, column=1, sticky="w", padx=6, pady=5)
        ttk.Entry(master, textvariable=self.hold_var, width=10).grid(row=3, column=1, sticky="w", padx=6, pady=5)
        ttk.Entry(master, textvariable=self.press_count_var, width=10).grid(row=4, column=1, sticky="w", padx=6, pady=5)
        ttk.Entry(master, textvariable=self.repeat_gap_var, width=10).grid(row=5, column=1, sticky="w", padx=6, pady=5)
        ttk.Checkbutton(master, variable=self.speak_response_var).grid(row=6, column=1, sticky="w", padx=6, pady=5)
        ttk.Entry(master, textvariable=self.response_suffix_var, width=20).grid(row=7, column=1, sticky="w", padx=6, pady=5)
        ttk.Entry(master, textvariable=self.response_text_var, width=42).grid(row=8, column=1, sticky="ew", padx=6, pady=5)
        name_entry.grid(row=0, column=1, sticky="ew", padx=6, pady=5)
        return name_entry

    def validate(self) -> bool:
        name = self.name_var.get().strip()
        phrases = [item.strip() for item in self.phrases_var.get().split(",") if item.strip()]
        key = self.key_var.get().strip().upper()
        if not name:
            messagebox.showerror("Missing name", "Give the command a short name.", parent=self)
            return False
        if not phrases:
            messagebox.showerror("Missing phrase", "Add at least one spoken phrase.", parent=self)
            return False
        try:
            parse_key_chord(key)
        except ValueError as exc:
            messagebox.showerror("Key problem", str(exc), parent=self)
            return False
        try:
            hold_seconds = float(self.hold_var.get())
        except ValueError:
            messagebox.showerror("Hold problem", "Hold seconds should be a number, like 0.10.", parent=self)
            return False
        if not 0.01 <= hold_seconds <= 2.0:
            messagebox.showerror("Hold problem", "Hold seconds should be between 0.01 and 2.0.", parent=self)
            return False
        try:
            press_count = int(self.press_count_var.get())
        except ValueError:
            messagebox.showerror("Press count problem", "Press count should be a whole number, like 1 or 2.", parent=self)
            return False
        if press_count != 1:
            messagebox.showerror(
                "Press count problem",
                "Voice commands must send one key or key chord one time. Use 1.",
                parent=self,
            )
            return False
        try:
            repeat_gap_seconds = float(self.repeat_gap_var.get())
        except ValueError:
            messagebox.showerror("Delay problem", "Delay between presses should be a number, like 0.10.", parent=self)
            return False
        if not 0.0 <= repeat_gap_seconds <= 2.0:
            messagebox.showerror("Delay problem", "Delay between presses should be between 0.00 and 2.0.", parent=self)
            return False
        response_suffix = self.response_suffix_var.get().strip()
        response_text = self.response_text_var.get().strip()
        if self.speak_response_var.get() and not response_suffix:
            response_suffix = DEFAULT_RESPONSE_SUFFIX
        if not self.speak_response_var.get():
            response_suffix = ""
            response_text = ""
        self.result_command = VoiceCommand(
            name=name,
            phrases=phrases,
            key=key,
            hold_seconds=hold_seconds,
            press_count=press_count,
            repeat_gap_seconds=repeat_gap_seconds,
            response_suffix=response_suffix,
            response_text=response_text,
        )
        return True


class EveVoicePilotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EVE Voice Pilot")
        self._set_initial_window_size()
        self._setup_fonts()

        self.settings = load_settings()
        self.profile_path = Path(self.settings.get("profile_path", str(USER_PROFILE)))
        self.profile = self._load_profile()
        self.hotkey: GlobalHotkey | None = None
        self.transcriber: RealtimeTranscriber | LocalVoskTranscriber | None = None
        self.transcriber_api_key = ""
        self.transcriber_engine = ""
        self.transcriber_command_signature: tuple[tuple[str, tuple[str, ...], str, float, int, float], ...] = ()
        self.transcriber_input_device_index: int | None = None
        self.transcriber_response_call_sign = ""
        self.transcriber_lock = threading.RLock()
        self.listening_thread: threading.Thread | None = None
        self.mic_test_thread: threading.Thread | None = None
        self.stop_listening = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.speech_responses = SpeechResponseManager(self.log_threadsafe)
        self.input_devices = list_input_devices()
        self.armed = False
        self.command_sort_column: str | None = None
        self.command_sort_descending = False
        self.command_heading_labels: dict[str, str] = {}

        self._build_ui()
        self._refresh_commands()
        self._configure_speech_responses()
        self.speech_responses.prepare_commands_async(self.profile.commands)
        self._register_hotkey()
        self.after(500, self._warm_connection_if_possible)
        self.after(50, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_profile(self) -> CommandProfile:
        if not USER_PROFILE.exists() and DEFAULT_PROFILE.exists():
            USER_PROFILE.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(DEFAULT_PROFILE, USER_PROFILE)
        path = self.profile_path if self.profile_path.exists() else USER_PROFILE
        try:
            self.profile_path = path
            return CommandProfile.load(path)
        except Exception:
            return CommandProfile.load(DEFAULT_PROFILE)

    def _set_initial_window_size(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = max(900, min(1180, screen_width - 80))
        height = max(620, min(760, screen_height - 100))
        x = max(0, min(40, screen_width - width))
        y = max(0, min(40, screen_height - height))
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(980, width), min(620, height))

    def _setup_fonts(self) -> None:
        families = set(tkfont.families(self))
        ui_family = next(
            (family for family in ("Bahnschrift SemiCondensed", "Bahnschrift", "Cascadia Mono", "Consolas") if family in families),
            "Segoe UI",
        )
        mono_family = next((family for family in ("Cascadia Mono", "Consolas") if family in families), ui_family)

        self.ui_font = (ui_family, 10)
        self.ui_font_bold = (ui_family, 10, "bold")
        self.status_font = (ui_family, 19, "bold")
        self.log_font = (mono_family, 10)

        for font_name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(font_name).configure(family=ui_family, size=10)
            except tk.TclError:
                pass

        style = ttk.Style(self)
        style.configure(".", font=self.ui_font)
        style.configure("TButton", font=self.ui_font)
        style.configure("TCheckbutton", font=self.ui_font)
        style.configure("TEntry", font=self.ui_font)
        style.configure("TCombobox", font=self.ui_font)
        style.configure("TLabel", font=self.ui_font)
        style.configure("TLabelframe.Label", font=self.ui_font_bold)
        style.configure("Treeview", font=self.ui_font, rowheight=27)
        style.configure("Treeview.Heading", font=self.ui_font_bold)

    def _input_device_labels(self) -> list[str]:
        return [device.label for device in self.input_devices]

    def _preferred_input_device_label(self) -> str:
        labels = self._input_device_labels()
        saved_label = str(self.settings.get("input_device", "")).strip()
        if saved_label in labels:
            return saved_label

        saved_index = resolve_input_device_label(saved_label)
        if saved_index is not None:
            for device in self.input_devices:
                if device.index == saved_index:
                    return device.label

        default_index = default_input_device_index()
        if default_index is not None:
            for device in self.input_devices:
                if device.index == default_index:
                    return device.label
        return labels[0] if labels else ""

    def _selected_input_device_index(self) -> int | None:
        return resolve_input_device_label(self.mic_var.get())

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=14)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(top, text="Status").grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.status_var, font=self.status_font).grid(row=0, column=1, sticky="w", padx=14)

        self.start_button = ttk.Button(top, text="Arm Listening", command=self.arm_listening)
        self.stop_button = ttk.Button(top, text="Pause", command=self.stop, state="disabled")
        self.start_button.grid(row=0, column=2, padx=4)
        self.stop_button.grid(row=0, column=3, padx=4)

        main = ttk.PanedWindow(self, orient="vertical")
        main.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        upper = ttk.PanedWindow(main, orient="horizontal")
        command_frame = ttk.LabelFrame(main, text="Commands", padding=8)
        main.add(upper, weight=2)
        main.add(command_frame, weight=3)

        left = ttk.LabelFrame(upper, text="Readout", padding=10)
        right = ttk.Frame(upper, padding=8)
        upper.add(left, weight=4)
        upper.add(right, weight=5)

        left.rowconfigure(0, weight=0)
        left.rowconfigure(1, weight=0)
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=0)
        left.columnconfigure(1, weight=1)

        self.last_heard_var = tk.StringVar(value="Nothing yet")
        self.last_action_var = tk.StringVar(value="No action yet")
        ttk.Label(left, text="Last heard").grid(row=0, column=0, sticky="w")
        ttk.Label(left, textvariable=self.last_heard_var, wraplength=640).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(left, text="Last action").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        ttk.Label(left, textvariable=self.last_action_var, wraplength=640).grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))

        command_results = ttk.LabelFrame(left, text="Sent Commands", padding=8)
        command_results.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(14, 0))
        command_results.rowconfigure(0, weight=1)
        command_results.columnconfigure(0, weight=1)

        self.command_result_text = tk.Text(command_results, height=7, wrap="word", state="disabled", font=self.log_font)
        command_result_scroll = ttk.Scrollbar(command_results, orient="vertical", command=self.command_result_text.yview)
        self.command_result_text.configure(yscrollcommand=command_result_scroll.set)
        self.command_result_text.grid(row=0, column=0, sticky="nsew")
        command_result_scroll.grid(row=0, column=1, sticky="ns")

        command_frame.rowconfigure(0, weight=1)
        command_frame.columnconfigure(0, weight=1)

        command_table = ttk.Frame(command_frame)
        command_table.grid(row=0, column=0, sticky="nsew")
        command_table.rowconfigure(0, weight=1)
        command_table.columnconfigure(0, weight=1)

        self.command_tree = ttk.Treeview(
            command_table,
            columns=("phrases", "key", "presses", "hold", "response"),
            show="tree headings",
            height=18,
        )
        command_y_scroll = ttk.Scrollbar(command_table, orient="vertical", command=self.command_tree.yview)
        command_x_scroll = ttk.Scrollbar(command_table, orient="horizontal", command=self.command_tree.xview)
        self.command_tree.configure(yscrollcommand=command_y_scroll.set, xscrollcommand=command_x_scroll.set)

        self.command_heading_labels = {
            "#0": "Name",
            "phrases": "Spoken phrases",
            "key": "Keybind",
            "presses": "Presses",
            "hold": "Hold",
            "response": "Response",
        }
        for column, label in self.command_heading_labels.items():
            self.command_tree.heading(column, text=label, command=lambda selected=column: self._sort_commands_by(selected))
        self.command_tree.column("#0", width=230, minwidth=170, stretch=True)
        self.command_tree.column("phrases", width=460, minwidth=320, stretch=True)
        self.command_tree.column("key", width=150, minwidth=115, stretch=False)
        self.command_tree.column("presses", width=98, minwidth=82, stretch=False)
        self.command_tree.column("hold", width=82, minwidth=70, stretch=False)
        self.command_tree.column("response", width=110, minwidth=90, stretch=False)
        self.command_tree.grid(row=0, column=0, sticky="nsew")
        command_y_scroll.grid(row=0, column=1, sticky="ns")
        command_x_scroll.grid(row=1, column=0, sticky="ew")

        command_buttons = ttk.Frame(command_frame)
        command_buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        command_buttons.columnconfigure(3, weight=1)
        ttk.Button(command_buttons, text="Add", command=self.add_command).grid(row=0, column=0, sticky="w")
        ttk.Button(command_buttons, text="Edit", command=self.edit_command).grid(row=0, column=1, sticky="w", padx=(6, 0))
        ttk.Button(command_buttons, text="Delete", command=self.delete_command).grid(row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Button(command_buttons, text="Test Selected", command=self.test_selected).grid(row=0, column=4, sticky="e", padx=(0, 6))
        ttk.Button(command_buttons, text="Save Commands", command=self.save_profile).grid(row=0, column=5, sticky="e")

        settings = ttk.LabelFrame(right, text="Settings", padding=10)
        settings.grid(row=0, column=0, sticky="new")
        settings.columnconfigure(1, weight=1)

        saved_engine = str(self.settings.get("speech_engine", ENGINE_LOCAL)).strip()
        if saved_engine not in SPEECH_ENGINES:
            saved_engine = ENGINE_LOCAL
        self.engine_var = tk.StringVar(value=saved_engine)
        self.api_key_var = tk.StringVar(value=self.settings.get("api_key", ""))
        self.remember_key_var = tk.BooleanVar(value=bool(self.settings.get("api_key_protected")))
        saved_hotkey = str(self.settings.get("hotkey", DEFAULT_HOTKEY)).strip().upper() or DEFAULT_HOTKEY
        if saved_hotkey in {"F9", "F12"}:
            saved_hotkey = DEFAULT_HOTKEY
        self.hotkey_var = tk.StringVar(value=saved_hotkey)
        self.mic_var = tk.StringVar(value=self._preferred_input_device_label())
        self.mic_level_var = tk.DoubleVar(value=0)
        self.mic_status_var = tk.StringVar(value="Use Test Mic and speak normally.")
        self.practice_mode_var = tk.BooleanVar(value=self.settings.get("practice_mode", True))
        self.require_target_var = tk.BooleanVar(value=self.settings.get("require_target", True))
        self.target_title_var = tk.StringVar(value=self.settings.get("target_title", "EVE"))
        saved_response_engine = str(self.settings.get("response_engine", DEFAULT_RESPONSE_ENGINE)).strip()
        if saved_response_engine not in RESPONSE_ENGINES:
            saved_response_engine = DEFAULT_RESPONSE_ENGINE
        self.response_engine_var = tk.StringVar(value=saved_response_engine)
        self.response_voice_var = tk.StringVar(value=str(self.settings.get("response_voice", DEFAULT_OPENAI_TTS_VOICE)).strip() or DEFAULT_OPENAI_TTS_VOICE)
        self.response_style_var = tk.StringVar(
            value=str(self.settings.get("response_style", DEFAULT_POWER_BALLAD_INSTRUCTIONS)).strip()
            or DEFAULT_POWER_BALLAD_INSTRUCTIONS
        )
        self.response_call_sign_var = tk.StringVar(
            value=str(self.settings.get("response_call_sign", DEFAULT_RESPONSE_CALL_SIGN)).strip() or DEFAULT_RESPONSE_CALL_SIGN
        )

        ttk.Label(settings, text="Speech engine").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Combobox(settings, textvariable=self.engine_var, values=SPEECH_ENGINES, state="readonly").grid(
            row=0,
            column=1,
            sticky="ew",
            pady=5,
        )

        ttk.Label(settings, text="OpenAI API key").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(settings, textvariable=self.api_key_var, show="*", width=28).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(settings, text="Remember on this PC", variable=self.remember_key_var).grid(row=2, column=1, sticky="w")

        ttk.Label(settings, text="Microphone").grid(row=3, column=0, sticky="w", pady=5)
        self.mic_combo = ttk.Combobox(settings, textvariable=self.mic_var, values=self._input_device_labels(), state="readonly")
        self.mic_combo.grid(row=3, column=1, sticky="ew", pady=5)
        mic_buttons = ttk.Frame(settings)
        mic_buttons.grid(row=4, column=0, columnspan=2, sticky="ew")
        self.mic_test_button = ttk.Button(mic_buttons, text="Test Mic", command=self.test_microphone)
        self.mic_test_button.pack(side="left")
        ttk.Button(mic_buttons, text="Refresh Mics", command=self.refresh_microphones).pack(side="left", padx=5)
        ttk.Progressbar(settings, variable=self.mic_level_var, maximum=100).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(settings, textvariable=self.mic_status_var, wraplength=360).grid(row=6, column=0, columnspan=2, sticky="ew", pady=(2, 8))

        ttk.Label(settings, text="Hotkey").grid(row=7, column=0, sticky="w", pady=5)
        ttk.Entry(settings, textvariable=self.hotkey_var, width=16).grid(row=7, column=1, sticky="w", pady=5)

        ttk.Checkbutton(settings, text="Practice mode", variable=self.practice_mode_var).grid(row=8, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(settings, text="Only when this window title is active", variable=self.require_target_var).grid(row=9, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Entry(settings, textvariable=self.target_title_var).grid(row=10, column=0, columnspan=2, sticky="ew", pady=4)

        ttk.Label(settings, text="Voice responses").grid(row=11, column=0, sticky="w", pady=5)
        ttk.Combobox(settings, textvariable=self.response_engine_var, values=RESPONSE_ENGINES, state="readonly").grid(
            row=11,
            column=1,
            sticky="ew",
            pady=5,
        )
        ttk.Label(settings, text="Response call sign").grid(row=12, column=0, sticky="w", pady=5)
        ttk.Entry(settings, textvariable=self.response_call_sign_var).grid(row=12, column=1, sticky="ew", pady=5)
        ttk.Label(settings, text="OpenAI voice").grid(row=13, column=0, sticky="w", pady=5)
        ttk.Combobox(settings, textvariable=self.response_voice_var, values=OPENAI_TTS_VOICES).grid(row=13, column=1, sticky="ew", pady=5)
        ttk.Label(settings, text="Voice style").grid(row=14, column=0, sticky="w", pady=5)
        ttk.Entry(settings, textvariable=self.response_style_var).grid(row=14, column=1, sticky="ew", pady=5)
        ttk.Button(settings, text="Regenerate Voice Clips", command=self.regenerate_voice_clips).grid(
            row=15,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(6, 0),
        )
        ttk.Button(settings, text="Save Settings", command=self.save_settings).grid(row=16, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        log_frame = ttk.LabelFrame(right, text="System Log", padding=8)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=6, wrap="word", state="disabled", font=self.log_font)
        self.log_text.grid(row=0, column=0, sticky="nsew")

    def _register_hotkey(self) -> None:
        if self.hotkey:
            self.hotkey.stop()
        self.hotkey = GlobalHotkey(
            self.hotkey_var.get().strip().upper() or DEFAULT_HOTKEY,
            callback=lambda: self.events.put(("hotkey", None)),
            on_error=lambda message: self.events.put(("error", message)),
        )
        self.hotkey.start()

    def _refresh_commands(self) -> None:
        self.command_tree.delete(*self.command_tree.get_children())
        for index, command in enumerate(self.profile.commands):
            self.command_tree.insert(
                "",
                "end",
                iid=str(index),
                text=command.name,
                values=(
                    ", ".join(command.phrases),
                    command.key,
                    self._presses_label(command),
                    f"{command.hold_seconds:.2f}s",
                    command.response_suffix,
                ),
            )
        self._apply_command_sort()

    def _presses_label(self, command: VoiceCommand) -> str:
        if command.press_count <= 1:
            return "1"
        return f"{command.press_count} / {command.repeat_gap_seconds:.2f}s"

    def _sort_commands_by(self, column: str) -> None:
        if self.command_sort_column == column:
            self.command_sort_descending = not self.command_sort_descending
        else:
            self.command_sort_column = column
            self.command_sort_descending = False
        self._apply_command_sort()

    def _apply_command_sort(self) -> None:
        if not self.command_sort_column:
            self._refresh_command_headings()
            return

        item_ids = list(self.command_tree.get_children(""))
        item_ids.sort(key=lambda item_id: int(item_id))
        item_ids.sort(
            key=lambda item_id: self._command_sort_value(item_id, self.command_sort_column or "#0"),
            reverse=self.command_sort_descending,
        )
        for position, item_id in enumerate(item_ids):
            self.command_tree.move(item_id, "", position)
        self._refresh_command_headings()

    def _command_sort_value(self, item_id: str, column: str) -> str | float:
        if column == "#0":
            return str(self.command_tree.item(item_id, "text")).casefold()

        values = self.command_tree.item(item_id, "values")
        if column == "presses":
            try:
                return float(str(values[2]).split("/", maxsplit=1)[0].strip())
            except (IndexError, ValueError):
                return 0.0
        if column == "hold":
            try:
                return float(str(values[3]).rstrip("s"))
            except (IndexError, ValueError):
                return 0.0
        try:
            value = values[{"phrases": 0, "key": 1, "response": 4}[column]]
        except (IndexError, KeyError):
            return ""
        return str(value).casefold()

    def _refresh_command_headings(self) -> None:
        for column, label in self.command_heading_labels.items():
            suffix = ""
            if self.command_sort_column == column:
                suffix = " v" if self.command_sort_descending else " ^"
            self.command_tree.heading(column, text=f"{label}{suffix}", command=lambda selected=column: self._sort_commands_by(selected))

    def _selected_index(self) -> int | None:
        selection = self.command_tree.selection()
        if not selection:
            return None
        return int(selection[0])

    def add_command(self) -> None:
        dialog = CommandDialog(self, "Add Command")
        if dialog.result_command:
            self.profile.commands.append(dialog.result_command)
            self._refresh_commands()
            self.speech_responses.prepare_command_async(dialog.result_command)

    def edit_command(self) -> None:
        index = self._selected_index()
        if index is None:
            messagebox.showinfo("Pick a command", "Select a command first.", parent=self)
            return
        dialog = CommandDialog(self, "Edit Command", self.profile.commands[index])
        if dialog.result_command:
            self.profile.commands[index] = dialog.result_command
            self._refresh_commands()
            self.speech_responses.prepare_command_async(dialog.result_command)

    def delete_command(self) -> None:
        index = self._selected_index()
        if index is None:
            messagebox.showinfo("Pick a command", "Select a command first.", parent=self)
            return
        command = self.profile.commands[index]
        if messagebox.askyesno("Delete command", f"Delete {command.name}?", parent=self):
            del self.profile.commands[index]
            self._refresh_commands()

    def save_profile(self) -> None:
        self.profile.save(self.profile_path)
        self._close_transcriber()
        self._configure_speech_responses()
        self.speech_responses.prepare_commands_async(self.profile.commands)
        self.log(f"Saved commands to {self.profile_path}")
        self._warm_connection_if_possible()

    def refresh_microphones(self) -> None:
        previous = self.mic_var.get()
        self.input_devices = list_input_devices()
        labels = self._input_device_labels()
        self.mic_combo.configure(values=labels)
        if previous in labels:
            self.mic_var.set(previous)
        else:
            self.mic_var.set(self._preferred_input_device_label())
        self.mic_status_var.set("Microphone list refreshed.")

    def test_microphone(self) -> None:
        if self.armed or (self.listening_thread and self.listening_thread.is_alive()):
            messagebox.showinfo("Pause first", "Pause Armed Listening before testing the microphone.", parent=self)
            return
        if self.mic_test_thread and self.mic_test_thread.is_alive():
            return

        device_index = self._selected_input_device_index()
        self.mic_level_var.set(0)
        self.mic_status_var.set("Testing microphone. Speak normally for a few seconds.")
        self.mic_test_button.configure(state="disabled")
        self.mic_test_thread = threading.Thread(target=self._mic_test_worker, args=(device_index,), name="mic-test-worker", daemon=True)
        self.mic_test_thread.start()

    def _mic_test_worker(self, device_index: int | None) -> None:
        audio_queue: queue.Queue[bytes] = queue.Queue()

        def audio_callback(indata, frames, time_info, status) -> None:
            audio_queue.put(bytes(indata))

        try:
            capture_rate = capture_rate_for_device(device_index)
            block_size = block_size_for_rate(capture_rate)
            peak_rms = 0.0
            with sd.RawInputStream(
                samplerate=capture_rate,
                device=device_index,
                channels=1,
                dtype="int16",
                blocksize=block_size,
                callback=audio_callback,
            ):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        raw = audio_queue.get(timeout=0.15)
                    except queue.Empty:
                        continue
                    rms = audio_rms(raw)
                    peak_rms = max(peak_rms, rms)
                    level = min(100, rms / 2500 * 100)
                    self.events.put(("mic_level", (level, rms)))
            if peak_rms < 450:
                result = "Mic test done. The level looks low; pick a different mic or raise input volume."
            elif peak_rms > 9000:
                result = "Mic test done. The level looks very loud; lower input volume if recognition is messy."
            else:
                result = "Mic test done. The level looks usable."
            if capture_rate < 16000:
                result += " This mic reports a low sample rate; choose a higher-quality headset input if one is listed."
            self.events.put(("mic_test_done", f"{result} Peak RMS {peak_rms:.0f}."))
        except Exception as exc:
            self.events.put(("mic_test_done", f"Mic test failed: {exc}"))

    def save_settings(self) -> None:
        try:
            parse_key_chord(self.hotkey_var.get().strip().upper() or DEFAULT_HOTKEY)
        except ValueError as exc:
            messagebox.showerror("Hotkey problem", str(exc), parent=self)
            return
        settings = {
            "api_key": self.api_key_var.get().strip(),
            "speech_engine": self.engine_var.get(),
            "hotkey": self.hotkey_var.get().strip().upper() or DEFAULT_HOTKEY,
            "input_device": self.mic_var.get().strip(),
            "practice_mode": self.practice_mode_var.get(),
            "require_target": self.require_target_var.get(),
            "target_title": self.target_title_var.get().strip() or "EVE",
            "response_engine": self.response_engine_var.get(),
            "response_call_sign": self.response_call_sign_var.get().strip() or DEFAULT_RESPONSE_CALL_SIGN,
            "response_voice": self.response_voice_var.get().strip() or DEFAULT_OPENAI_TTS_VOICE,
            "response_style": self.response_style_var.get().strip() or DEFAULT_POWER_BALLAD_INSTRUCTIONS,
            "profile_path": str(self.profile_path),
        }
        try:
            save_settings(settings, self.remember_key_var.get())
        except OSError as exc:
            messagebox.showerror("Save problem", str(exc), parent=self)
            return
        if self.transcriber_engine and self.transcriber_engine != settings["speech_engine"]:
            self._close_transcriber()
        if self.transcriber_api_key and self.transcriber_api_key != settings["api_key"]:
            self._close_transcriber()
        selected_device = self._selected_input_device_index()
        if self.transcriber and self.transcriber_input_device_index != selected_device:
            self._close_transcriber()
        if self.transcriber and self.transcriber_response_call_sign != settings["response_call_sign"]:
            self._close_transcriber()
        self._configure_speech_responses()
        self.speech_responses.prepare_commands_async(self.profile.commands)
        self._register_hotkey()
        self.log("Saved settings.")
        self._warm_connection_if_possible()

    def _configure_speech_responses(self) -> None:
        self.speech_responses.configure(
            engine=self.response_engine_var.get(),
            api_key=self.api_key_var.get().strip(),
            model=DEFAULT_OPENAI_TTS_MODEL,
            voice=self.response_voice_var.get().strip() or DEFAULT_OPENAI_TTS_VOICE,
            instructions=self.response_style_var.get().strip() or DEFAULT_POWER_BALLAD_INSTRUCTIONS,
        )

    def regenerate_voice_clips(self) -> None:
        self._configure_speech_responses()
        self.speech_responses.prepare_commands_async(self.profile.commands, force=True)
        self.log("Regenerating voice response clips.")

    def arm_listening(self) -> None:
        if self.armed:
            return
        self.armed = True
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.log(f"Armed listening on. Press {self.hotkey_var.get().strip().upper() or DEFAULT_HOTKEY} again, or Pause, to stop.")
        self._start_listening_cycle()

    def _start_listening_cycle(self) -> None:
        if not self.armed:
            return
        if self.listening_thread and self.listening_thread.is_alive():
            return
        self.stop_listening.clear()
        self.status_var.set("Connecting")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        api_key = self.api_key_var.get().strip()
        speech_engine = self.engine_var.get()
        commands = list(self.profile.commands)
        practice_mode = self.practice_mode_var.get()
        require_target = self.require_target_var.get()
        target_title = self.target_title_var.get().strip() or "EVE"
        input_device_index = self._selected_input_device_index()
        response_call_sign = self.response_call_sign_var.get().strip() or DEFAULT_RESPONSE_CALL_SIGN
        self.listening_thread = threading.Thread(
            target=self._listen_worker,
            args=(api_key, speech_engine, commands, practice_mode, require_target, target_title, input_device_index, response_call_sign),
            name="listen-worker",
            daemon=True,
        )
        self.listening_thread.start()

    def stop(self) -> None:
        self.armed = False
        if self.listening_thread and self.listening_thread.is_alive():
            self.stop_listening.set()
            self.status_var.set("Pausing")
            return
        self.status_var.set("Paused")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._close_transcriber()

    def _listen_worker(
        self,
        api_key: str,
        speech_engine: str,
        commands: list[VoiceCommand],
        practice_mode: bool,
        require_target: bool,
        target_title: str,
        input_device_index: int | None,
        response_call_sign: str,
    ) -> None:
        fast_result: dict | None = None
        call_signs = response_call_signs(response_call_sign)

        def on_partial_match(transcript: str) -> bool:
            nonlocal fast_result
            if fast_result:
                match, command_transcript, response_requested = self._match_spoken_command(transcript, commands, call_signs)
                if (
                    match
                    and match.command.name == fast_result["match"].command.name
                    and response_requested
                ):
                    fast_result.update({
                        "transcript": transcript,
                        "command_transcript": command_transcript,
                        "response_requested": True,
                    })
                    return True
                return False
            match, command_transcript, response_requested = self._match_spoken_command(transcript, commands, call_signs)
            if not match:
                return False
            result = self._send_or_practice_worker(match.command, practice_mode, require_target, target_title)
            fast_result = {
                "transcript": transcript,
                "command_transcript": command_transcript,
                "match": match,
                "result": result,
                "response_requested": response_requested,
            }
            return not self._should_listen_for_response_call_sign(match.command, response_requested, call_signs)

        try:
            transcriber = self._get_transcriber(api_key, speech_engine, commands, input_device_index, response_call_sign)
            transcript = transcriber.record_until_stopped(
                self.stop_listening,
                on_ready=self._listening_ready,
                on_partial_match=on_partial_match,
            )
            if fast_result:
                self._update_fast_result_response_request(fast_result, transcript, commands, call_signs)
                self.events.put(("fast_transcript", fast_result))
            else:
                self.events.put(("transcript", transcript))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _should_listen_for_response_call_sign(
        self,
        command: VoiceCommand,
        response_requested: bool,
        call_signs: list[str],
    ) -> bool:
        return bool(call_signs) and not response_requested and bool(command.response_suffix.strip())

    def _update_fast_result_response_request(
        self,
        fast_result: dict,
        transcript: str,
        commands: list[VoiceCommand],
        call_signs: list[str],
    ) -> None:
        if not transcript.strip():
            return
        match, command_transcript, response_requested = self._match_spoken_command(transcript, commands, call_signs)
        if not match or not response_requested:
            return
        previous_match = fast_result.get("match")
        if previous_match and match.command.name == previous_match.command.name:
            fast_result.update({
                "transcript": transcript,
                "command_transcript": command_transcript,
                "response_requested": True,
            })

    def _get_transcriber(
        self,
        api_key: str,
        speech_engine: str,
        commands: list[VoiceCommand],
        input_device_index: int | None,
        response_call_sign: str,
    ) -> RealtimeTranscriber | LocalVoskTranscriber:
        command_signature = self._command_signature(commands)
        with self.transcriber_lock:
            if (
                not self.transcriber
                or self.transcriber_engine != speech_engine
                or self.transcriber_api_key != api_key
                or self.transcriber_input_device_index != input_device_index
                or self.transcriber_command_signature != command_signature
                or self.transcriber_response_call_sign != response_call_sign
            ):
                self._close_transcriber()
                if speech_engine == ENGINE_LOCAL:
                    self.transcriber = LocalVoskTranscriber(
                        commands,
                        self.log_threadsafe,
                        input_device_index=input_device_index,
                        response_call_signs=response_call_signs(response_call_sign),
                    )
                else:
                    self.transcriber = RealtimeTranscriber(api_key, self.log_threadsafe, input_device_index=input_device_index)
                self.transcriber_api_key = api_key
                self.transcriber_engine = speech_engine
                self.transcriber_command_signature = command_signature
                self.transcriber_input_device_index = input_device_index
                self.transcriber_response_call_sign = response_call_sign
            return self.transcriber

    def _command_signature(self, commands: list[VoiceCommand]) -> tuple[tuple[str, tuple[str, ...], str, float, int, float], ...]:
        return tuple(
            (command.name, tuple(command.phrases), command.key, command.hold_seconds, command.press_count, command.repeat_gap_seconds)
            for command in commands
        )

    def _close_transcriber(self) -> None:
        with self.transcriber_lock:
            if self.transcriber:
                self.transcriber.close()
            self.transcriber = None
            self.transcriber_api_key = ""
            self.transcriber_engine = ""
            self.transcriber_command_signature = ()
            self.transcriber_input_device_index = None
            self.transcriber_response_call_sign = ""

    def _warm_connection_if_possible(self) -> None:
        api_key = self.api_key_var.get().strip()
        speech_engine = self.engine_var.get()
        if speech_engine == ENGINE_OPENAI and not api_key:
            return
        if self.listening_thread and self.listening_thread.is_alive():
            return
        commands = list(self.profile.commands)
        input_device_index = self._selected_input_device_index()
        response_call_sign = self.response_call_sign_var.get().strip() or DEFAULT_RESPONSE_CALL_SIGN
        threading.Thread(
            target=self._warm_connection_worker,
            args=(api_key, speech_engine, commands, input_device_index, response_call_sign),
            name="warm-speech-worker",
            daemon=True,
        ).start()

    def _warm_connection_worker(
        self,
        api_key: str,
        speech_engine: str,
        commands: list[VoiceCommand],
        input_device_index: int | None,
        response_call_sign: str,
    ) -> None:
        try:
            self._get_transcriber(api_key, speech_engine, commands, input_device_index, response_call_sign).warm_up()
        except Exception as exc:
            self.events.put(("log", f"Could not warm {speech_engine} connection: {exc}"))

    def _listening_ready(self) -> None:
        self.events.put(("status", "Listening"))

    def _match_spoken_command(
        self,
        transcript: str,
        commands: list[VoiceCommand],
        call_signs: list[str] | None = None,
    ):
        cleaned_transcript, response_requested = strip_response_call_sign(
            transcript,
            call_signs if call_signs is not None else response_call_signs(self.response_call_sign_var.get()),
        )
        return find_exact_phrase_match(cleaned_transcript, commands), cleaned_transcript, response_requested

    def _handle_transcript(self, transcript: str) -> None:
        self.last_heard_var.set(transcript or "(No speech recognized)")
        if not transcript.strip():
            self.last_action_var.set("No action.")
            return
        match, _, response_requested = self._match_spoken_command(transcript, self.profile.commands)
        if not match:
            self.last_action_var.set("No exact command matched.")
            return

        action = f"{match.command.name} -> {match.command.action_summary}"
        self.last_action_var.set(action)
        self.log(f"Matched exact phrase {match.phrase!r}: {action}")
        result = self._send_or_practice(match.command)
        status = self._command_result_status(result)
        if status == "valid - sent":
            self.record_command_result(status, transcript, f"{match.command.name} ({match.command.action_summary})")
        if self._should_play_response(match.command, response_requested, result):
            self.speech_responses.play(match.command)

    def _send_or_practice(self, command: VoiceCommand) -> str:
        result = self._send_or_practice_worker(
            command,
            self.practice_mode_var.get(),
            self.require_target_var.get(),
            self.target_title_var.get().strip() or "EVE",
        )
        self.log(result)
        return result

    def _send_or_practice_worker(
        self,
        command: VoiceCommand,
        practice_mode: bool,
        require_target: bool,
        target_title: str,
    ) -> str:
        key = command.key
        if practice_mode:
            return f"Practice mode: would send {command.action_summary}."

        if require_target:
            title = active_window_title()
            required = target_title.lower() or "eve"
            if required not in title.lower():
                return f"Did not send {key}; active window is {title!r}."

        try:
            for press_index in range(command.press_count):
                send_key_chord(key, press_seconds=command.hold_seconds)
                if press_index < command.press_count - 1:
                    time.sleep(command.repeat_gap_seconds)
            return f"Sent {command.action_summary}."
        except Exception as exc:
            return f"Could not send {key}: {exc}"

    def test_selected(self) -> None:
        index = self._selected_index()
        if index is None:
            messagebox.showinfo("Pick a command", "Select a command first.", parent=self)
            return
        command = self.profile.commands[index]
        result = self._send_or_practice(command)
        if self._command_result_status(result) == "valid - sent" or result.startswith("Practice mode:"):
            self.speech_responses.play(command)

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "hotkey":
                if self.armed:
                    self.stop()
                else:
                    self.arm_listening()
            elif event == "log":
                self.log(str(payload))
            elif event == "status":
                if self.armed:
                    self.status_var.set(str(payload))
            elif event == "mic_level":
                level, rms = payload
                self.mic_level_var.set(float(level))
                self.mic_status_var.set(f"Level {float(level):.0f}%  RMS {float(rms):.0f}")
            elif event == "mic_test_done":
                self.mic_test_button.configure(state="normal")
                self.mic_status_var.set(str(payload))
            elif event == "transcript":
                self._handle_transcript(str(payload))
                self._finish_listening_cycle()
            elif event == "fast_transcript":
                self._handle_fast_transcript(payload)
                self._finish_listening_cycle()
            elif event == "error":
                self.armed = False
                self.status_var.set("Paused")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self._close_transcriber()
                self.log(str(payload))
        self.after(50, self._poll_events)

    def _finish_listening_cycle(self) -> None:
        if self.armed:
            self.status_var.set("Armed")
            self.start_button.configure(state="disabled")
            self.stop_button.configure(state="normal")
            self.after(60, self._start_listening_cycle)
            return

        self.status_var.set("Paused")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._close_transcriber()

    def _handle_fast_transcript(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        transcript = str(payload.get("transcript", "")).strip()
        response_requested = bool(payload.get("response_requested"))
        match = payload.get("match")
        result = str(payload.get("result", ""))
        self.last_heard_var.set(transcript or "(Fast command)")
        if match:
            action = f"{match.command.name} -> {match.command.action_summary}"
            self.last_action_var.set(action)
            self.log(f"Fast matched {match.phrase!r}: {action}")
            status = self._command_result_status(result)
            if status == "valid - sent":
                self.record_command_result(status, transcript, f"{match.command.name} ({match.command.action_summary})")
            if self._should_play_response(match.command, response_requested, result):
                self.speech_responses.play(match.command)
        if result:
            self.log(result)

    def _should_play_response(self, command: VoiceCommand, response_requested: bool, result: str) -> bool:
        if not response_requested:
            return False
        return bool(command.response_suffix.strip()) and (result.startswith("Sent ") or result.startswith("Practice mode:"))

    def _command_result_status(self, result: str) -> str:
        if result.startswith("Sent "):
            return "valid - sent"
        return "valid - not sent"

    def record_command_result(self, status: str, heard: str, detail: str = "") -> None:
        heard_text = " ".join((heard or "").strip().split()) or "no speech recognized"
        line = f"{status} | {heard_text}"
        if detail:
            line += f" -> {detail}"

        self.command_result_text.configure(state="normal")
        self.command_result_text.insert("end", line + "\n")
        self._trim_text_widget(self.command_result_text, max_lines=200)
        self.command_result_text.see("end")
        self.command_result_text.configure(state="disabled")

    def log_threadsafe(self, message: str) -> None:
        self.events.put(("log", message))

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _trim_text_widget(self, widget: tk.Text, max_lines: int) -> None:
        line_count = int(widget.index("end-1c").split(".", 1)[0])
        if line_count > max_lines:
            widget.delete("1.0", f"{line_count - max_lines + 1}.0")

    def _on_close(self) -> None:
        self.armed = False
        if self.hotkey:
            self.hotkey.stop()
        self.stop_listening.set()
        self.speech_responses.stop()
        self._close_transcriber()
        self.destroy()


def main() -> None:
    app = EveVoicePilotApp()
    app.mainloop()
