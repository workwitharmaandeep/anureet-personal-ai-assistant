"""
Floating Animated Desktop Avatar Widget & Expandable Dark HUD for Anureet.
Renders the Chibi 3D Anureet model in pure built-in Tkinter + Pillow with
state animations (idle, listening, thinking, speaking), live instruction captions,
reliable offline SAPI5/Edge voice output, global hotkey (Ctrl+Space), and
cross-process sync with voice_assistant.py.
"""

import argparse
import ctypes
import logging
import math
import queue
import random
import sys
import threading
import time
import tkinter as tk
import webbrowser
from ctypes import wintypes
from pathlib import Path
from tkinter import scrolledtext
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageEnhance, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    Image = None
    ImageEnhance = None
    ImageTk = None
    PIL_AVAILABLE = False

from agent_core import agent
from config import config
from tools.memory_store import get_latest_widget_events, get_widget_head_id

logger = logging.getLogger("AnureetWidget")

TRANSPARENT_KEY = "#010203"
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
HOTKEY_ID = 0x414E  # 'AN'

MOD_FLAGS = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "control": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
    "super": 0x0008,
}

SPECIAL_VKS = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
}

STATE_PALETTES: Dict[str, Dict[str, str]] = {
    "idle": {
        "outer": "#0284c7",
        "mid": "#38bdf8",
        "core": "#e0f2fe",
        "accent": "#22d3ee",
        "badge_bg": "#082f49",
        "label": "Idle",
    },
    "listening": {
        "outer": "#059669",
        "mid": "#10b981",
        "core": "#d1fae5",
        "accent": "#34d399",
        "badge_bg": "#064e3b",
        "label": "Listening...",
    },
    "thinking": {
        "outer": "#7c3aed",
        "mid": "#f59e0b",
        "core": "#fef3c7",
        "accent": "#c084fc",
        "badge_bg": "#3b0764",
        "label": "Thinking...",
    },
    "speaking": {
        "outer": "#0891b2",
        "mid": "#22d3ee",
        "core": "#ffffff",
        "accent": "#67e8f9",
        "badge_bg": "#164e63",
        "label": "Speaking...",
    },
}


def parse_hotkey_spec(spec: str) -> Optional[Tuple[int, int]]:
    """Parse a hotkey string such as 'ctrl+space' or 'ctrl+alt+a' into Win32 (modifiers, vk)."""
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    if not parts:
        return None

    mods = 0
    vk: Optional[int] = None
    for part in parts:
        if part in MOD_FLAGS:
            mods |= MOD_FLAGS[part]
        elif part in SPECIAL_VKS:
            vk = SPECIAL_VKS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            return None

    if vk is None:
        return None
    return mods, vk


def _prepare_sprite_image(path: Path, target_size: Tuple[int, int]) -> Optional[Image.Image]:
    """Load avatar PNG and threshold soft outer alpha cleanly so Tkinter transparency has zero fringes."""
    if not PIL_AVAILABLE or not path.exists():
        return None
    try:
        raw = Image.open(path).convert("RGBA")
        resized = raw.resize(target_size, Image.Resampling.LANCZOS)
        r, g, b, a = resized.split()
        # Any semi-transparent pixel is premultiplied slightly against dark cosmos and hard-gated cleanly
        clean_a = a.point(lambda p: 255 if p >= 64 else 0)
        return Image.merge("RGBA", (r, g, b, clean_a))
    except Exception as e:
        logger.warning(f"Failed to load avatar sprite from {path}: {e}")
        return None


class AnureetWidgetApp:
    """Floating animated Chibi 3D Anureet desktop avatar, live instruction bubble, and expandable dark HUD."""

    def __init__(
        self,
        on_ready: Optional[Callable[["AnureetWidgetApp"], None]] = None,
        auto_wake: bool = False,
        on_quit_all: Optional[Callable[[], None]] = None,
        server_port: int = 8000,
    ):
        self.root = tk.Tk()
        self.root.title("Anureet Widget")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT_KEY)
        if sys.platform == "win32":
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)

        self.stage_w = 188
        self.stage_h = 208
        self.orb_size = self.stage_w  # Preserved attribute compatibility
        self._close_badge_pos = (self.stage_w - 18, 18)
        self._on_quit_all = on_quit_all
        self._server_port = server_port
        self._quit_all_invoked = False
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.orb_x = max(12, screen_w - self.stage_w - 26)
        self.orb_y = max(12, screen_h - self.stage_h - 56)
        self.root.geometry(f"{self.stage_w}x{self.stage_h}+{self.orb_x}+{self.orb_y}")

        self.state = "idle"
        self.state_note = "ONLINE"
        self.phase = 0.0
        self.hud_visible = False
        self.tts_enabled = True
        self.wake_mode_enabled = False
        self._wake_stop_event = threading.Event()
        self._busy_event = threading.Event()
        self._mic_active_event = threading.Event()
        self._closed = False
        self._drag_origin = (0, 0)
        self._dragged = False
        self._hotkey_registered = False
        self._hotkey_label = config.widget_hotkey

        self._ui_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._voice_engine = None
        self._voice_lock = threading.Lock()
        self._eq_levels: List[float] = [0.2] * 11
        self._bubble_hide_after_id: Optional[str] = None
        self._last_instruction = ""
        self._last_response = ""

        try:
            self._last_event_id = get_widget_head_id()
        except Exception:
            self._last_event_id = 0

        self._sprite_frames: Dict[str, Any] = {}
        self._hud_thumb: Any = None
        self._load_avatar_assets()

        self._build_stage()
        self._build_bubble()
        self._build_hud()
        self._register_global_hotkey()

        # Initialize fast TTS immediately (init_stt=False) and prewarm Faster-Whisper separately.
        threading.Thread(target=self._prewarm_voice, daemon=True).start()

        self.root.after(40, self._animate_stage)
        self.root.after(50, self._drain_ui_queue)
        self.root.after(180, self._poll_external_events)
        if self._hotkey_registered:
            self.root.after(60, self._poll_hotkey_messages)
        if auto_wake:
            self.root.after(250, self.toggle_wake_mode)

        if on_ready:
            on_ready(self)

    def _load_avatar_assets(self) -> None:
        base_sprite = _prepare_sprite_image(Path(config.widget_avatar_path), (150, 150))
        if base_sprite is None:
            return
        self._sprite_frames["base"] = ImageTk.PhotoImage(base_sprite)
        bright = ImageEnhance.Brightness(base_sprite).enhance(1.12)
        self._sprite_frames["active"] = ImageTk.PhotoImage(bright)
        pulse_big = bright.resize((156, 156), Image.Resampling.LANCZOS)
        self._sprite_frames["speak_pulse"] = ImageTk.PhotoImage(pulse_big)
        thumb = base_sprite.resize((30, 30), Image.Resampling.LANCZOS)
        self._hud_thumb = ImageTk.PhotoImage(thumb)

    def _build_stage(self) -> None:
        self.canvas = tk.Canvas(
            self.root,
            width=self.stage_w,
            height=self.stage_h,
            bg=TRANSPARENT_KEY,
            highlightthickness=0,
            cursor="hand2",
        )
        self.canvas.pack(fill="both", expand=True)

        self.menu = tk.Menu(self.root, tearoff=0, bg="#111827", fg="#f8fafc", activebackground="#2563eb")
        self.menu.add_command(label="Open / Hide Chat HUD", command=self.toggle_hud)
        self.menu.add_command(label="Open Web UI", command=self.open_web_ui)
        self.menu.add_command(label="Voice Command (Mic)", command=self.trigger_voice_command)
        self.menu.add_command(label="Toggle Always-Listen ('Anureet')", command=self.toggle_wake_mode)
        self.menu.add_command(label="Morning Briefing", command=lambda: self.submit_query("morning briefing"))
        self.menu.add_command(label="Stop Speaking (Esc)", command=self.stop_speaking)
        self.menu.add_separator()
        self.menu.add_command(label="Close & Stop Everything", command=self.quit_all)

        self.canvas.bind("<ButtonPress-1>", self._on_orb_press)
        self.canvas.bind("<B1-Motion>", self._on_orb_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_orb_release)
        self.canvas.bind("<Button-3>", self._on_orb_right_click)
        self.canvas.bind("<Double-Button-1>", lambda _e: self.trigger_voice_command())

    def _build_bubble(self) -> None:
        """Compact floating glass instruction & response pill shown right over the avatar."""
        self.bubble = tk.Toplevel(self.root)
        self.bubble.overrideredirect(True)
        self.bubble.attributes("-topmost", True)
        self.bubble.configure(bg="#091224", highlightbackground="#38bdf8", highlightthickness=1)
        self.bubble.withdraw()

        self.bubble_w = 320
        wrap = tk.Frame(self.bubble, bg="#091224", padx=10, pady=8)
        wrap.pack(fill="both", expand=True)

        top_row = tk.Frame(wrap, bg="#091224")
        top_row.pack(fill="x")

        self.bubble_state_lbl = tk.Label(
            top_row,
            text="ANUREET",
            bg="#091224",
            fg="#38bdf8",
            font=("Segoe UI", 8, "bold"),
        )
        self.bubble_state_lbl.pack(side="left")

        close_btn = tk.Label(
            top_row,
            text="✕",
            bg="#091224",
            fg="#94a3b8",
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda _e: self._hide_bubble())

        self.bubble_user_lbl = tk.Label(
            wrap,
            text="",
            bg="#091224",
            fg="#e0f2fe",
            font=("Segoe UI", 9, "bold"),
            justify="left",
            anchor="w",
            wraplength=294,
        )
        self.bubble_user_lbl.pack(fill="x", pady=(4, 2))

        self.bubble_agent_lbl = tk.Label(
            wrap,
            text="",
            bg="#091224",
            fg="#cbd5e1",
            font=("Segoe UI", 9),
            justify="left",
            anchor="w",
            wraplength=294,
        )
        self.bubble_agent_lbl.pack(fill="x")

        for w in (self.bubble, wrap, self.bubble_state_lbl, self.bubble_user_lbl, self.bubble_agent_lbl):
            w.bind("<Button-1>", lambda _e: self._open_hud_from_bubble())

    def _build_hud(self) -> None:
        self.hud = tk.Toplevel(self.root)
        self.hud.title("Anureet HUD")
        self.hud.overrideredirect(True)
        self.hud.attributes("-topmost", True)
        self.hud.configure(bg="#0b1120", highlightbackground="#38bdf8", highlightthickness=1)
        self.hud.withdraw()
        self.hud.bind("<Escape>", lambda _e: self.stop_speaking())

        self.hud_w = 396
        self.hud_h = 500

        header = tk.Frame(self.hud, bg="#111827", padx=10, pady=8)
        header.pack(fill="x")

        if self._hud_thumb is not None:
            tk.Label(header, image=self._hud_thumb, bg="#111827").pack(side="left", padx=(0, 6))

        tk.Label(
            header,
            text="ANUREET // HUD",
            bg="#111827",
            fg="#38bdf8",
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        self.status_lbl = tk.Label(
            header,
            text="Idle",
            bg="#111827",
            fg="#94a3b8",
            font=("Segoe UI", 8),
        )
        self.status_lbl.pack(side="left", padx=8)

        tk.Button(
            header,
            text="✕",
            command=self.quit_all,
            bg="#991b1b",
            fg="#ffffff",
            relief="flat",
            width=3,
            cursor="hand2",
        ).pack(side="right", padx=(4, 0))

        tk.Button(
            header,
            text="–",
            command=self.toggle_hud,
            bg="#1e293b",
            fg="#e2e8f0",
            relief="flat",
            width=3,
            cursor="hand2",
        ).pack(side="right", padx=(4, 0))

        self.mute_btn = tk.Button(
            header,
            text="🔊",
            command=self.toggle_tts,
            bg="#1e293b",
            fg="#e2e8f0",
            relief="flat",
            width=3,
            cursor="hand2",
        )
        self.mute_btn.pack(side="right", padx=(4, 0))

        self.wake_btn = tk.Button(
            header,
            text="👂",
            command=self.toggle_wake_mode,
            bg="#1e293b",
            fg="#cbd5e1",
            relief="flat",
            width=3,
            cursor="hand2",
        )
        self.wake_btn.pack(side="right", padx=(4, 0))

        tk.Button(
            header,
            text="⏹",
            command=self.stop_speaking,
            bg="#7f1d1d",
            fg="#fecaca",
            relief="flat",
            width=3,
            cursor="hand2",
        ).pack(side="right")

        self.chat_box = scrolledtext.ScrolledText(
            self.hud,
            wrap="word",
            bg="#0f172a",
            fg="#e2e8f0",
            insertbackground="#38bdf8",
            relief="flat",
            font=("Segoe UI", 9),
            padx=10,
            pady=8,
            state="disabled",
        )
        self.chat_box.pack(fill="both", expand=True, padx=8, pady=(6, 4))
        self.chat_box.tag_configure("user", foreground="#38bdf8", font=("Segoe UI", 9, "bold"))
        self.chat_box.tag_configure("agent", foreground="#a7f3d0", font=("Segoe UI", 9, "bold"))
        self.chat_box.tag_configure("meta", foreground="#94a3b8", font=("Segoe UI", 8, "italic"))

        chips = tk.Frame(self.hud, bg="#0b1120", padx=8, pady=2)
        chips.pack(fill="x")
        for label, cmd in (
            ("Briefing", "morning briefing"),
            ("Status", "system status"),
            ("Timers", "list timers"),
            ("Todos", "list todos"),
        ):
            tk.Button(
                chips,
                text=label,
                command=lambda q=cmd: self.submit_query(q),
                bg="#1e293b",
                fg="#cbd5e1",
                activebackground="#334155",
                relief="flat",
                font=("Segoe UI", 8),
                padx=7,
                pady=2,
                cursor="hand2",
            ).pack(side="left", padx=(0, 5))

        footer = tk.Frame(self.hud, bg="#0b1120", padx=8, pady=8)
        footer.pack(fill="x")

        self.entry = tk.Entry(
            footer,
            bg="#1e293b",
            fg="#f8fafc",
            insertbackground="#38bdf8",
            relief="flat",
            font=("Segoe UI", 10),
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))
        self.entry.bind("<Return>", lambda _e: self._on_send_clicked())

        tk.Button(
            footer,
            text="🎤",
            command=self.trigger_voice_command,
            bg="#065f46",
            fg="#d1fae5",
            relief="flat",
            width=3,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=(0, 4))

        tk.Button(
            footer,
            text="Send",
            command=self._on_send_clicked,
            bg="#2563eb",
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left")

        hotkey_hint = self._hotkey_label.upper() if self._hotkey_registered else "Click Avatar"
        self._append_chat(
            "Anureet",
            f"Ready ({hotkey_hint} for HUD, Double-Click/🎤 for Mic, 👂 for Wake Word, Esc stops voice).",
        )

    def _position_bubble(self) -> None:
        self.bubble.update_idletasks()
        req_h = max(72, self.bubble.winfo_reqheight())
        screen_w = self.root.winfo_screenwidth()
        x = min(max(12, self.orb_x + self.stage_w - self.bubble_w), screen_w - self.bubble_w - 12)
        y = self.orb_y - req_h - 8
        if y < 12:
            y = max(12, self.orb_y + self.stage_h + 8)
        self.bubble.geometry(f"{self.bubble_w}x{req_h}+{x}+{y}")

    def _show_bubble(self, status_title: str, user_text: str = "", agent_text: str = "", auto_hide_ms: int = 0) -> None:
        def _apply():
            if self._closed:
                return
            if self._bubble_hide_after_id is not None:
                try:
                    self.root.after_cancel(self._bubble_hide_after_id)
                except Exception:
                    pass
                self._bubble_hide_after_id = None

            self.bubble_state_lbl.configure(text=status_title)
            if user_text:
                shown_u = user_text if len(user_text) <= 130 else user_text[:127] + "..."
                self.bubble_user_lbl.configure(text=f"You: {shown_u}")
            elif not self.bubble_user_lbl.cget("text"):
                self.bubble_user_lbl.configure(text="")

            if agent_text:
                shown_a = agent_text if len(agent_text) <= 210 else agent_text[:207] + "..."
                self.bubble_agent_lbl.configure(text=f"Anureet: {shown_a}")
            else:
                self.bubble_agent_lbl.configure(text="")

            self._position_bubble()
            self.bubble.deiconify()
            self.bubble.lift()

            if auto_hide_ms > 0:
                self._bubble_hide_after_id = self.root.after(auto_hide_ms, self._hide_bubble)

        self._ui_queue.put(_apply)

    def _hide_bubble(self) -> None:
        self._bubble_hide_after_id = None
        if not self._closed:
            self.bubble.withdraw()

    def _open_hud_from_bubble(self) -> None:
        if not self.hud_visible:
            self.toggle_hud()

    def _position_hud(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = min(max(12, self.orb_x + self.stage_w - self.hud_w), screen_w - self.hud_w - 12)
        y = max(12, self.orb_y - self.hud_h - 10)
        if y < 12:
            y = min(screen_h - self.hud_h - 12, self.orb_y + self.stage_h + 10)
        self.hud.geometry(f"{self.hud_w}x{self.hud_h}+{x}+{y}")

    def toggle_hud(self) -> None:
        if self.hud_visible:
            self.hud.withdraw()
            self.hud_visible = False
        else:
            self._position_hud()
            self.hud.deiconify()
            self.hud.lift()
            self.entry.focus_set()
            self.hud_visible = True

    def toggle_tts(self) -> None:
        self.tts_enabled = not self.tts_enabled
        if not self.tts_enabled:
            self.stop_speaking()
        self.mute_btn.configure(text="🔊" if self.tts_enabled else "🔇")

    def toggle_wake_mode(self) -> None:
        self.wake_mode_enabled = not self.wake_mode_enabled
        if self.wake_mode_enabled:
            self._wake_stop_event.clear()
            self.wake_btn.configure(bg="#065f46", fg="#d1fae5")
            self._append_chat("System", "Always-Listen Wake Word mode enabled ('Anureet ...').", tag_hint="meta")
            self._show_bubble("👂 WAKE WORD ACTIVE", user_text="Say 'Anureet' + your command", auto_hide_ms=4500)
            threading.Thread(target=self._wake_listener_loop, daemon=True).start()
        else:
            self._wake_stop_event.set()
            self.wake_btn.configure(bg="#1e293b", fg="#cbd5e1")
            self._append_chat("System", "Always-Listen Wake Word mode paused.", tag_hint="meta")
            self._set_state("idle", "ONLINE")

    def stop_speaking(self) -> None:
        if self._voice_engine is not None:
            self._voice_engine.stop_speaking()
        self._set_state("idle", "ONLINE")

    def _set_state(self, state: str, note: Optional[str] = None) -> None:
        def _apply():
            if self._closed:
                return
            self.state = state if state in STATE_PALETTES else "idle"
            label = note or STATE_PALETTES[self.state]["label"]
            self.state_note = label.upper()[:18]
            self.status_lbl.configure(text=label[:34])
        self._ui_queue.put(_apply)

    def _append_chat(self, speaker: str, text: str, tag_hint: Optional[str] = None) -> None:
        def _apply():
            if self._closed:
                return
            self.chat_box.configure(state="normal")
            if tag_hint == "meta":
                self.chat_box.insert("end", f"{text}\n", "meta")
            else:
                spk_tag = "user" if speaker == "You" else "agent"
                self.chat_box.insert("end", f"{speaker}: ", spk_tag)
                self.chat_box.insert("end", f"{text}\n\n")
            self.chat_box.see("end")
            self.chat_box.configure(state="disabled")
        self._ui_queue.put(_apply)

    def _get_voice_engine(self):
        with self._voice_lock:
            if self._voice_engine is None:
                try:
                    from voice_assistant import AnureetVoiceEngine
                    # Load TTS instantaneously without blocking on Faster-Whisper
                    self._voice_engine = AnureetVoiceEngine(init_stt=False)
                except Exception as e:
                    self._append_chat("System", f"Voice engine unavailable: {e}", tag_hint="meta")
            return self._voice_engine

    def _prewarm_voice(self) -> None:
        engine = self._get_voice_engine()
        if engine:
            try:
                engine.ensure_stt_loaded()
            except Exception as e:
                logger.debug(f"Background STT warmup skipped: {e}")

    def _on_send_clicked(self) -> None:
        query = self.entry.get().strip()
        if not query:
            return
        self.entry.delete(0, "end")
        self.submit_query(query)

    def submit_query(self, query: str, done_event: Optional[threading.Event] = None) -> None:
        self.stop_speaking()
        self._busy_event.set()
        self._last_instruction = query
        self._last_response = ""
        self._append_chat("You", query)
        self._set_state("thinking", "Thinking...")
        self._show_bubble("⚡ PROCESSING", user_text=query)

        def _worker():
            try:
                def on_log(msg: str):
                    self._set_state("thinking", msg)

                res = agent.process_query(query, log_callback=on_log, session_id="widget")
                reply = res.get("response_text", "")
                tools = [t.get("tool") for t in res.get("executed_tools", []) if t.get("tool")]
                if tools:
                    self._append_chat("System", f"[Tools: {', '.join(tools)}]", tag_hint="meta")
                if reply:
                    self._last_response = reply
                    self._append_chat("Anureet", reply)
                if self.tts_enabled and reply:
                    engine = self._get_voice_engine()
                    if engine:
                        self._set_state("speaking", "Speaking...")
                        self._show_bubble("🗣️ SPEAKING", user_text=query, agent_text=reply)
                        engine.speak(reply)
                if reply:
                    self._show_bubble("✅ COMPLETED", user_text=query, agent_text=reply, auto_hide_ms=7500)
            except Exception as e:
                logger.error(f"Widget query execution failed: {e}")
                self._append_chat("System", f"Error: {e}", tag_hint="meta")
                self._show_bubble("⚠️ ERROR", user_text=query, agent_text=str(e), auto_hide_ms=6000)
            finally:
                self._busy_event.clear()
                if done_event is not None:
                    done_event.set()
                self._set_state("idle", "ONLINE")

        threading.Thread(target=_worker, daemon=True).start()

    def trigger_voice_command(self) -> None:
        if self._busy_event.is_set() or self._mic_active_event.is_set():
            return
        self.stop_speaking()
        self._mic_active_event.set()
        self._set_state("listening", "Listening...")
        self._show_bubble("🎙️ LISTENING...", user_text="Speak your instruction now...")

        def _worker():
            try:
                engine = self._get_voice_engine()
                if not engine:
                    self._set_state("idle", "Voice unavailable")
                    self._show_bubble("⚠️ VOICE UNAVAILABLE", agent_text="Voice engine failed to initialize.", auto_hide_ms=4000)
                    return
                heard = engine.listen_once()
                if not heard:
                    self._append_chat("System", "No speech detected.", tag_hint="meta")
                    self._show_bubble("🎙️ NO SPEECH", user_text="(Timed out / no speech detected)", auto_hide_ms=3000)
                    self._set_state("idle", "ONLINE")
                    return
                self._ui_queue.put(lambda: self.submit_query(heard))
            finally:
                self._mic_active_event.clear()

        threading.Thread(target=_worker, daemon=True).start()

    def _wake_listener_loop(self) -> None:
        engine = self._get_voice_engine()
        if not engine:
            return
        engine.ensure_stt_loaded()
        active_until = 0.0
        while self.wake_mode_enabled and not self._wake_stop_event.is_set() and not self._closed:
            try:
                # Never listen while an instruction is being processed or spoken aloud.
                while (
                    (self._busy_event.is_set() or self._mic_active_event.is_set())
                    and self.wake_mode_enabled
                    and not self._wake_stop_event.is_set()
                    and not self._closed
                ):
                    time.sleep(0.1)

                if not self.wake_mode_enabled or self._wake_stop_event.is_set() or self._closed:
                    break

                now = time.time()
                if now < active_until and not self._busy_event.is_set():
                    self._set_state("listening", "Listening...")
                text = engine.listen_once(timeout=5, phrase_time_limit=10)
                if (
                    not text
                    or self._wake_stop_event.is_set()
                    or self._busy_event.is_set()
                    or self._mic_active_event.is_set()
                ):
                    if time.time() >= active_until > 0 and not self._busy_event.is_set():
                        active_until = 0.0
                        self._set_state("idle", "ONLINE")
                    continue
                wake_found, cmd = engine.extract_wake_command(text, active_until)
                if wake_found:
                    if not cmd or len(cmd) < 2:
                        self._busy_event.set()
                        try:
                            self._set_state("speaking", "Speaking...")
                            self._show_bubble("🎙️ AWAKE", user_text="Anureet", agent_text="Yes, I am listening!")
                            if self.tts_enabled:
                                engine.speak("Yes, I am listening!")
                        finally:
                            self._busy_event.clear()
                        active_until = time.time() + 45.0
                        time.sleep(0.35)
                        if self.wake_mode_enabled and not self._closed:
                            self._set_state("listening", "Listening...")
                    else:
                        done = threading.Event()
                        self._busy_event.set()
                        self._ui_queue.put(lambda c=cmd, d=done: self.submit_query(c, done_event=d))
                        while not done.wait(0.15):
                            if self._wake_stop_event.is_set() or self._closed:
                                break
                        active_until = time.time() + 45.0
                        time.sleep(0.35)
            except Exception as e:
                logger.debug(f"Wake loop cycle skipped: {e}")
                time.sleep(0.5)

    def _poll_external_events(self) -> None:
        """Reflect commands spoken to voice_assistant.py or sent to server.py live on the widget."""
        if self._closed:
            return
        try:
            events = get_latest_widget_events(after_id=self._last_event_id)
            for ev in events:
                self._last_event_id = max(self._last_event_id, int(ev.get("id", 0)))
                ev_state = ev.get("state", "idle")
                user_text = ev.get("user_text", "")
                agent_text = ev.get("agent_text", "")
                source = ev.get("source", "voice")
                if ev_state == "shutdown":
                    self.root.after(200, self.quit_all)
                    return
                if source == "widget":
                    continue

                if ev_state == "thinking":
                    self._set_state("thinking", "Thinking...")
                    if user_text:
                        self._append_chat("You", f"[{source}] {user_text}")
                        self._show_bubble("⚡ THINKING", user_text=user_text)
                elif ev_state == "speaking":
                    self._set_state("speaking", "Speaking...")
                    if agent_text:
                        self._append_chat("Anureet", agent_text)
                        self._show_bubble("🗣️ SPEAKING", user_text=user_text, agent_text=agent_text)
                elif ev_state == "listening":
                    self._set_state("listening", "Listening...")
                    self._show_bubble("🎙️ LISTENING...", user_text=user_text or "Listening for follow-up...")
                else:
                    self._set_state("idle", "ONLINE")
                    if user_text and agent_text:
                        if self._last_instruction != user_text or self._last_response != agent_text:
                            self._last_instruction = user_text
                            self._last_response = agent_text
                            self._show_bubble("✅ COMPLETED", user_text=user_text, agent_text=agent_text, auto_hide_ms=7000)
        except Exception:
            pass
        if not self._closed:
            self.root.after(180, self._poll_external_events)

    def _on_orb_press(self, event) -> None:
        self._drag_origin = (event.x_root, event.y_root)
        self._dragged = False

    def _on_orb_drag(self, event) -> None:
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        if abs(dx) >= 4 or abs(dy) >= 4:
            self._dragged = True
        self.orb_x += dx
        self.orb_y += dy
        self._drag_origin = (event.x_root, event.y_root)
        self.root.geometry(f"{self.stage_w}x{self.stage_h}+{self.orb_x}+{self.orb_y}")
        if self.hud_visible:
            self._position_hud()
        if self.bubble.state() == "normal":
            self._position_bubble()

    def _on_orb_release(self, event) -> None:
        if not self._dragged:
            bx, by = self._close_badge_pos
            if (event.x - bx) ** 2 + (event.y - by) ** 2 <= 14 ** 2:
                self.quit_all()
                return
            self.toggle_hud()

    def _on_orb_right_click(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _animate_stage(self) -> None:
        if self._closed:
            return
        state = self.state
        self.phase += 0.14 if state == "idle" else 0.36
        pal = STATE_PALETTES.get(state, STATE_PALETTES["idle"])
        cx = self.stage_w / 2
        cy = 88.0

        self.canvas.delete("all")

        # 1. Cosmic base glow disc behind Anureet so silhouette pops cleanly over any wallpaper
        base_r = 66 + 2.0 * math.sin(self.phase)
        self.canvas.create_oval(
            cx - base_r, cy - base_r, cx + base_r, cy + base_r,
            fill="#071324", outline=pal["outer"], width=2,
        )

        # 2. State-specific holographic background effects
        if state == "listening":
            for k in range(3):
                prog = ((self.phase * 0.28 + k / 3.0) % 1.0)
                rr = 42 + prog * 44
                ring_color = pal["core"] if prog < 0.35 else (pal["accent"] if prog < 0.7 else pal["outer"])
                self.canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=ring_color, width=2)
        elif state == "thinking":
            deg1 = (self.phase * 72) % 360
            deg2 = (-self.phase * 96) % 360
            self.canvas.create_arc(
                cx - 76, cy - 76, cx + 76, cy + 76,
                start=deg1, extent=95, style="arc", outline=pal["mid"], width=3,
            )
            self.canvas.create_arc(
                cx - 76, cy - 76, cx + 76, cy + 76,
                start=(deg1 + 180) % 360, extent=95, style="arc", outline=pal["accent"], width=3,
            )
            for s in range(4):
                ang = math.radians(deg2 + s * 90)
                sx = cx + 76 * math.cos(ang)
                sy = cy + 76 * math.sin(ang)
                self.canvas.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill=pal["core"], outline="")
        elif state == "speaking":
            for k in range(2):
                prog = ((self.phase * 0.45 + k * 0.5) % 1.0)
                rr = 54 + prog * 28
                self.canvas.create_oval(
                    cx - rr, cy - rr, cx + rr, cy + rr,
                    outline=pal["accent"] if prog < 0.5 else pal["outer"], width=2,
                )
        else:
            for s in range(2):
                ang = self.phase * 0.6 + s * math.pi
                sx = cx + 70 * math.cos(ang)
                sy = cy + 70 * math.sin(ang)
                self.canvas.create_oval(sx - 2.5, sy - 2.5, sx + 2.5, sy + 2.5, fill=pal["core"], outline="")

        # 3. Render Chibi Anureet 3D Model Sprite with smooth breathing/speaking bounce
        if state == "speaking":
            bob_y = -abs(math.sin(self.phase * 1.6)) * 4.5
            sprite = (
                self._sprite_frames.get("speak_pulse")
                if math.sin(self.phase * 2.2) > 0.15
                else self._sprite_frames.get("active")
            )
        elif state == "listening":
            bob_y = math.sin(self.phase * 1.1) * 2.5
            sprite = self._sprite_frames.get("active")
        elif state == "thinking":
            bob_y = math.sin(self.phase * 0.9) * 3.0
            sprite = self._sprite_frames.get("active")
        else:
            bob_y = math.sin(self.phase) * 3.0
            sprite = self._sprite_frames.get("base")

        if sprite is not None:
            self.canvas.create_image(cx, cy - 2 + bob_y, image=sprite, anchor="center")
        else:
            # Fallback vector core if image asset missing
            r_core = 26 + 3 * math.sin(self.phase)
            self.canvas.create_oval(cx - r_core, cy - r_core, cx + r_core, cy + r_core, fill=pal["mid"], outline=pal["core"], width=2)

        # 4. Live animated equalizer / waveform bars across pedestal bottom
        eq_y = 166
        bar_w = 5
        gap = 4
        n_bars = len(self._eq_levels)
        start_x = cx - ((n_bars * bar_w + (n_bars - 1) * gap) / 2)
        for i in range(n_bars):
            if state == "speaking":
                target = 0.25 + 0.75 * abs(math.sin(self.phase * 1.9 + i * 0.7)) * (0.65 + 0.35 * random.random())
            elif state == "listening":
                target = 0.20 + 0.65 * abs(math.sin(self.phase * 1.4 + i * 0.55))
            elif state == "thinking":
                wave = 0.5 + 0.5 * math.sin(self.phase * 1.7 - i * 0.65)
                target = 0.18 + 0.45 * wave
            else:
                target = 0.14 + 0.10 * math.sin(self.phase + i * 0.5)
            self._eq_levels[i] = self._eq_levels[i] * 0.55 + target * 0.45
            bar_h = max(3.0, self._eq_levels[i] * 18.0)
            x0 = start_x + i * (bar_w + gap)
            bar_color = pal["core"] if (state == "speaking" and i % 2 == 0) else pal["mid"]
            self.canvas.create_rectangle(
                x0, eq_y - bar_h / 2, x0 + bar_w, eq_y + bar_h / 2,
                fill=bar_color, outline="",
            )

        # 5. State status badge pill
        pill_w = 132
        self.canvas.create_rectangle(
            cx - pill_w / 2, 180, cx + pill_w / 2, 200,
            fill=pal["badge_bg"], outline=pal["mid"], width=1,
        )
        self.canvas.create_text(
            cx, 190,
            text=f"● {self.state_note}",
            fill=pal["core"],
            font=("Segoe UI", 8, "bold"),
        )

        # 6. Top-right quick Close & Stop Everything badge
        bx, by = self._close_badge_pos
        self.canvas.create_oval(bx - 10, by - 10, bx + 10, by + 10, fill="#7f1d1d", outline="#fca5a5", width=1)
        self.canvas.create_text(bx, by, text="✕", fill="#ffffff", font=("Segoe UI", 8, "bold"))

        self.root.after(40, self._animate_stage)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                fn = self._ui_queue.get_nowait()
                fn()
            except queue.Empty:
                break
        if not self._closed:
            self.root.after(50, self._drain_ui_queue)

    def _register_global_hotkey(self) -> None:
        if sys.platform != "win32" or not hasattr(ctypes, "windll"):
            return
        candidates = [config.widget_hotkey, "ctrl+alt+space"]
        user32 = ctypes.windll.user32
        for spec in candidates:
            parsed = parse_hotkey_spec(spec)
            if not parsed:
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, HOTKEY_ID, mods, vk):
                self._hotkey_registered = True
                self._hotkey_label = spec
                return

    def _poll_hotkey_messages(self) -> None:
        if self._closed:
            return
        if sys.platform == "win32" and hasattr(ctypes, "windll"):
            msg = wintypes.MSG()
            user32 = ctypes.windll.user32
            while user32.PeekMessageW(ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
                if msg.wParam == HOTKEY_ID:
                    self.toggle_hud()
        self.root.after(60, self._poll_hotkey_messages)

    def open_web_ui(self) -> None:
        webbrowser.open(f"http://localhost:{self._server_port}")

    def quit_all(self) -> None:
        """Stop the widget, voice engine, gateway server, and launcher-owned runtime cleanly."""
        if not self._quit_all_invoked:
            self._quit_all_invoked = True
            if self._on_quit_all is not None:
                try:
                    self._on_quit_all()
                except Exception as e:
                    logger.debug(f"on_quit_all callback error: {e}")
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.wake_mode_enabled = False
        self._wake_stop_event.set()
        if self._hotkey_registered and sys.platform == "win32" and hasattr(ctypes, "windll"):
            try:
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
            except Exception:
                pass
        if self._voice_engine is not None:
            self._voice_engine.stop_speaking()
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Anureet Floating Desktop Widget")
    parser.add_argument("--wake", action="store_true", help="Enable Always-Listen Wake Word mode on startup")
    parser.add_argument("--port", type=int, default=8000, help="Local server port for Open Web UI")
    args = parser.parse_args(argv)
    AnureetWidgetApp(auto_wake=args.wake, server_port=args.port).run()


if __name__ == "__main__":
    main()
