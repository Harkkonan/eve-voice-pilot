from __future__ import annotations

from pathlib import Path
import queue
import shutil
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
import winsound

from .commands import CommandProfile, VoiceCommand, find_command_match
from .config import load_settings, save_settings
from .hotkey import GlobalHotkey
from .input_sender import active_window_title, parse_key_chord, send_key_chord
from .transcription import RealtimeTranscriber


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = ROOT / "profiles" / "eve_sample.json"
USER_PROFILE = ROOT / "profiles" / "my_eve_commands.json"


class CommandDialog(simpledialog.Dialog):
    def __init__(self, parent, title: str, command: VoiceCommand | None = None):
        self.command = command
        self.result_command: VoiceCommand | None = None
        super().__init__(parent, title)

    def body(self, master):
        ttk.Label(master, text="Name").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(master, text="Spoken phrases").grid(row=1, column=0, sticky="w", padx=6, pady=5)
        ttk.Label(master, text="Key").grid(row=2, column=0, sticky="w", padx=6, pady=5)

        self.name_var = tk.StringVar(value=self.command.name if self.command else "")
        self.phrases_var = tk.StringVar(value=", ".join(self.command.phrases) if self.command else "")
        self.key_var = tk.StringVar(value=self.command.key if self.command else "")

        name_entry = ttk.Entry(master, textvariable=self.name_var, width=42)
        ttk.Entry(master, textvariable=self.phrases_var, width=42).grid(row=1, column=1, sticky="ew", padx=6, pady=5)
        ttk.Entry(master, textvariable=self.key_var, width=20).grid(row=2, column=1, sticky="w", padx=6, pady=5)
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
        self.result_command = VoiceCommand(name=name, phrases=phrases, key=key)
        return True


class EveVoicePilotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EVE Voice Pilot")
        self.geometry("860x620")
        self.minsize(760, 520)

        self.settings = load_settings()
        self.profile_path = Path(self.settings.get("profile_path", str(USER_PROFILE)))
        self.profile = self._load_profile()
        self.hotkey: GlobalHotkey | None = None
        self.listening_thread: threading.Thread | None = None
        self.stop_listening = threading.Event()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        self._build_ui()
        self._refresh_commands()
        self._register_hotkey()
        self.after(100, self._poll_events)
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

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        top = ttk.Frame(self, padding=14)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(top, text="Status").grid(row=0, column=0, sticky="w")
        ttk.Label(top, textvariable=self.status_var, font=("Segoe UI", 16, "bold")).grid(row=0, column=1, sticky="w", padx=10)

        self.start_button = ttk.Button(top, text="Start Listening", command=self.start_listening)
        self.stop_button = ttk.Button(top, text="Stop", command=self.stop, state="disabled")
        self.start_button.grid(row=0, column=2, padx=4)
        self.stop_button.grid(row=0, column=3, padx=4)

        main = ttk.PanedWindow(self, orient="horizontal")
        main.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 14))

        left = ttk.Frame(main, padding=8)
        right = ttk.Frame(main, padding=8)
        main.add(left, weight=3)
        main.add(right, weight=2)

        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        self.last_heard_var = tk.StringVar(value="Nothing yet")
        self.last_action_var = tk.StringVar(value="No action yet")
        ttk.Label(left, text="Last heard").grid(row=0, column=0, sticky="w")
        ttk.Label(left, textvariable=self.last_heard_var, wraplength=480).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(left, text="Last action").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        ttk.Label(left, textvariable=self.last_action_var, wraplength=480).grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))

        command_frame = ttk.LabelFrame(left, text="Commands", padding=8)
        command_frame.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(14, 0))
        command_frame.rowconfigure(0, weight=1)
        command_frame.columnconfigure(0, weight=1)

        self.command_tree = ttk.Treeview(command_frame, columns=("phrases", "key"), show="tree headings", height=12)
        self.command_tree.heading("#0", text="Name")
        self.command_tree.heading("phrases", text="Spoken phrases")
        self.command_tree.heading("key", text="Key")
        self.command_tree.column("#0", width=150, stretch=True)
        self.command_tree.column("phrases", width=280, stretch=True)
        self.command_tree.column("key", width=90, stretch=False)
        self.command_tree.grid(row=0, column=0, sticky="nsew")

        command_buttons = ttk.Frame(command_frame)
        command_buttons.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(command_buttons, text="Add", command=self.add_command).pack(side="left")
        ttk.Button(command_buttons, text="Edit", command=self.edit_command).pack(side="left", padx=5)
        ttk.Button(command_buttons, text="Delete", command=self.delete_command).pack(side="left")
        ttk.Button(command_buttons, text="Save Commands", command=self.save_profile).pack(side="right")
        ttk.Button(command_buttons, text="Test Selected", command=self.test_selected).pack(side="right", padx=5)

        settings = ttk.LabelFrame(right, text="Settings", padding=10)
        settings.grid(row=0, column=0, sticky="new")
        settings.columnconfigure(1, weight=1)

        self.api_key_var = tk.StringVar(value=self.settings.get("api_key", ""))
        self.remember_key_var = tk.BooleanVar(value=bool(self.settings.get("api_key_protected")))
        self.hotkey_var = tk.StringVar(value=self.settings.get("hotkey", "F12"))
        self.practice_mode_var = tk.BooleanVar(value=self.settings.get("practice_mode", True))
        self.require_target_var = tk.BooleanVar(value=self.settings.get("require_target", True))
        self.target_title_var = tk.StringVar(value=self.settings.get("target_title", "EVE"))

        ttk.Label(settings, text="OpenAI API key").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(settings, textvariable=self.api_key_var, show="*", width=28).grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(settings, text="Remember on this PC", variable=self.remember_key_var).grid(row=1, column=1, sticky="w")

        ttk.Label(settings, text="Hotkey").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(settings, textvariable=self.hotkey_var, width=16).grid(row=2, column=1, sticky="w", pady=5)

        ttk.Checkbutton(settings, text="Practice mode", variable=self.practice_mode_var).grid(row=3, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(settings, text="Only when this window title is active", variable=self.require_target_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Entry(settings, textvariable=self.target_title_var).grid(row=5, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Button(settings, text="Save Settings", command=self.save_settings).grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))

        log_frame = ttk.LabelFrame(right, text="Log", padding=8)
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")

    def _register_hotkey(self) -> None:
        if self.hotkey:
            self.hotkey.stop()
        self.hotkey = GlobalHotkey(
            self.hotkey_var.get().strip().upper() or "F12",
            callback=lambda: self.events.put(("hotkey", None)),
            on_error=lambda message: self.events.put(("error", message)),
        )
        self.hotkey.start()

    def _refresh_commands(self) -> None:
        self.command_tree.delete(*self.command_tree.get_children())
        for index, command in enumerate(self.profile.commands):
            self.command_tree.insert("", "end", iid=str(index), text=command.name, values=(", ".join(command.phrases), command.key))

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

    def edit_command(self) -> None:
        index = self._selected_index()
        if index is None:
            messagebox.showinfo("Pick a command", "Select a command first.", parent=self)
            return
        dialog = CommandDialog(self, "Edit Command", self.profile.commands[index])
        if dialog.result_command:
            self.profile.commands[index] = dialog.result_command
            self._refresh_commands()

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
        self.log(f"Saved commands to {self.profile_path}")

    def save_settings(self) -> None:
        try:
            parse_key_chord(self.hotkey_var.get().strip().upper() or "F12")
        except ValueError as exc:
            messagebox.showerror("Hotkey problem", str(exc), parent=self)
            return
        settings = {
            "api_key": self.api_key_var.get().strip(),
            "hotkey": self.hotkey_var.get().strip().upper() or "F12",
            "practice_mode": self.practice_mode_var.get(),
            "require_target": self.require_target_var.get(),
            "target_title": self.target_title_var.get().strip() or "EVE",
            "profile_path": str(self.profile_path),
        }
        try:
            save_settings(settings, self.remember_key_var.get())
        except OSError as exc:
            messagebox.showerror("Save problem", str(exc), parent=self)
            return
        self._register_hotkey()
        self.log("Saved settings.")

    def start_listening(self) -> None:
        if self.listening_thread and self.listening_thread.is_alive():
            return
        self.stop_listening.clear()
        self.status_var.set("Connecting")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.listening_thread = threading.Thread(target=self._listen_worker, name="listen-worker", daemon=True)
        self.listening_thread.start()

    def stop(self) -> None:
        if self.listening_thread and self.listening_thread.is_alive():
            self.stop_listening.set()
            self.status_var.set("Finishing")
            winsound.MessageBeep(winsound.MB_ICONASTERISK)

    def _listen_worker(self) -> None:
        try:
            transcriber = RealtimeTranscriber(self.api_key_var.get(), self.log_threadsafe)
            winsound.MessageBeep(winsound.MB_OK)
            transcript = transcriber.record_until_stopped(self.stop_listening)
            self.events.put(("transcript", transcript))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _handle_transcript(self, transcript: str) -> None:
        self.last_heard_var.set(transcript or "(No speech recognized)")
        match = find_command_match(transcript, self.profile.commands)
        if not match:
            self.last_action_var.set("No command matched.")
            self.log(f"Heard: {transcript!r}; no command matched.")
            return

        action = f"{match.command.name} -> {match.command.key}"
        self.last_action_var.set(action)
        self.log(f"Matched {match.phrase!r} at {match.score:.0%}: {action}")
        self._send_or_practice(match.command.key)

    def _send_or_practice(self, key: str) -> None:
        if self.practice_mode_var.get():
            self.log(f"Practice mode: would send {key}.")
            return

        if self.require_target_var.get():
            title = active_window_title()
            required = self.target_title_var.get().strip().lower() or "eve"
            if required not in title.lower():
                self.log(f"Did not send {key}; active window is {title!r}.")
                return

        try:
            send_key_chord(key)
            self.log(f"Sent {key}.")
        except Exception as exc:
            self.log(f"Could not send {key}: {exc}")

    def test_selected(self) -> None:
        index = self._selected_index()
        if index is None:
            messagebox.showinfo("Pick a command", "Select a command first.", parent=self)
            return
        self._send_or_practice(self.profile.commands[index].key)

    def _poll_events(self) -> None:
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "hotkey":
                if self.listening_thread and self.listening_thread.is_alive():
                    self.stop()
                else:
                    self.start_listening()
            elif event == "log":
                self.log(str(payload))
            elif event == "transcript":
                self.status_var.set("Ready")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self._handle_transcript(str(payload))
            elif event == "error":
                self.status_var.set("Ready")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.log(str(payload))
        self.after(100, self._poll_events)

    def log_threadsafe(self, message: str) -> None:
        self.events.put(("log", message))

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        if self.hotkey:
            self.hotkey.stop()
        self.stop_listening.set()
        self.destroy()


def main() -> None:
    app = EveVoicePilotApp()
    app.mainloop()
