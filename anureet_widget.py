"""
Floating Animated Desktop Avatar Widget & Expandable Dark HUD for Anureet.
Renders the Chibi 3D Anureet model in pure built-in Tkinter + Pillow with
state animations (idle, listening, thinking, speaking), live instruction captions,
reliable offline SAPI5/Edge voice output, global hotkey (Ctrl+Space), and
cross-process sync with voice_assistant.py.
"""

import argparse
import ctypes
import json
import logging
import math
import os
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

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
if sys.stdin is None:
    try:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")
    except Exception:
        pass

try:
    from PIL import Image, ImageEnhance, ImageTk, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    Image = None
    ImageEnhance = None
    ImageTk = None
    ImageDraw = None
    PIL_AVAILABLE = False

from agent_core import agent
from config import config
from tools.memory_store import get_latest_widget_events, get_widget_head_id, publish_widget_event

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


BASE_STAGE_W = 188
BASE_STAGE_H = 208
BASE_SPRITE_SIZE = 150
BASE_PULSE_SIZE = 156
MIN_WIDGET_SCALE = 0.40
MAX_WIDGET_SCALE = 4.50
BASE_HUD_W = 396
BASE_HUD_H = 500
MIN_HUD_W = 280
MIN_HUD_H = 260
WIDGET_PREFS_PATH = config.uploads_dir / "widget_prefs.json"


def clamp_widget_scale(scale: float, screen_w: int = 1920, screen_h: int = 1080) -> float:
    """Clamp avatar widget scale cleanly between tiny icon size and screen height bounds."""
    try:
        val = float(scale)
    except (TypeError, ValueError):
        return 1.0
    max_fit = min(
        MAX_WIDGET_SCALE,
        max(MIN_WIDGET_SCALE, (screen_w - 24) / float(BASE_STAGE_W)),
        max(MIN_WIDGET_SCALE, (screen_h - 32) / float(BASE_STAGE_H)),
    )
    return round(max(MIN_WIDGET_SCALE, min(max_fit, val)), 3)


def _load_widget_prefs(prefs_path: Path = WIDGET_PREFS_PATH) -> Dict[str, Any]:
    try:
        if prefs_path.exists():
            data = json.loads(prefs_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug(f"Could not read widget prefs: {e}")
    return {}


def _save_widget_prefs(prefs: Dict[str, Any], prefs_path: Path = WIDGET_PREFS_PATH) -> None:
    try:
        prefs_path.parent.mkdir(parents=True, exist_ok=True)
        prefs_path.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug(f"Could not save widget prefs: {e}")


class AnureetWidgetApp:
    """Floating animated Chibi 3D Anureet desktop avatar, live instruction bubble, and expandable dark HUD."""

    def __init__(
        self,
        on_ready: Optional[Callable[["AnureetWidgetApp"], None]] = None,
        auto_wake: bool = False,
        on_quit_all: Optional[Callable[[], None]] = None,
        server_port: int = 8000,
        prefs_path: Optional[Path] = None,
        prewarm_voice: bool = True,
    ):
        self.root = tk.Tk()
        self.root.title("Anureet Widget")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANSPARENT_KEY)
        if sys.platform == "win32":
            self.root.attributes("-transparentcolor", TRANSPARENT_KEY)

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self._prefs_path = Path(prefs_path) if prefs_path is not None else WIDGET_PREFS_PATH
        prefs = _load_widget_prefs(self._prefs_path)

        self.scale = clamp_widget_scale(prefs.get("scale", 1.0), screen_w, screen_h)
        self.stage_w = max(76, round(BASE_STAGE_W * self.scale))
        self.stage_h = max(84, round(BASE_STAGE_H * self.scale))
        self.orb_size = self.stage_w  # Preserved attribute compatibility
        self.is_minimized = False
        self._min_badge_pos = (18, 18)
        self._min_badge_r = 10
        self._saved_geometry_before_min: Optional[Tuple[int, int, int, int]] = None
        self._close_badge_pos = (18, 18)
        self._close_badge_r = 10
        self._resize_grip_pos = (18, 18)
        self._resize_grip_r = 10
        self._update_badge_positions()

        self._saved_hud_w = int(prefs.get("hud_w", BASE_HUD_W) or BASE_HUD_W)
        self._saved_hud_h = int(prefs.get("hud_h", BASE_HUD_H) or BASE_HUD_H)
        self.hud_chat_bar_visible = bool(prefs.get("hud_chat_bar_visible", True))
        self._chat_bar_var = tk.BooleanVar(value=self.hud_chat_bar_visible)
        self._on_quit_all = on_quit_all
        self._server_port = server_port
        self._quit_all_invoked = False

        self.orb_x = max(12, screen_w - self.stage_w - 26)
        self.orb_y = max(12, screen_h - self.stage_h - 56)
        self.root.geometry(f"{self.stage_w}x{self.stage_h}+{self.orb_x}+{self.orb_y}")

        self.state = "idle"
        self.state_note = "ONLINE"
        self.phase = 0.0
        self.hud_visible = False
        self.tts_enabled = True
        self.wake_mode_enabled = False
        self.continuous_mode_active = False
        self.continuous_mode_until = 0.0
        self._continuous_ticker_after_id: Optional[str] = None
        self._wake_stop_event = threading.Event()
        self._busy_event = threading.Event()
        self._mic_active_event = threading.Event()
        self._closed = False
        self._drag_origin = (0, 0)
        self._dragged = False
        self._interaction_mode = "move"  # "move" | "resize"
        self._hover_resize_grip = False
        self._prefs_save_after_id: Optional[str] = None
        self._animate_after_id: Optional[str] = None
        self._drain_after_id: Optional[str] = None
        self._hotkey_after_id: Optional[str] = None
        self._sprite_refresh_after_id: Optional[str] = None
        self._auto_wake_after_id: Optional[str] = None
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

        self._raw_avatar_rgba: Optional[Any] = None
        self._sprite_frames: Dict[str, Any] = {}
        self._rendered_sprite_scale: float = -1.0
        self._hud_thumb: Any = None
        self._load_avatar_assets()

        self._build_stage()
        self._build_bubble()
        self._build_hud()
        self._register_global_hotkey()

        # Initialize fast TTS immediately (init_stt=False) and prewarm Faster-Whisper separately.
        if prewarm_voice:
            threading.Thread(target=self._prewarm_voice, daemon=True).start()

        self._animate_after_id = self.root.after(40, self._animate_stage)
        self._drain_after_id = self.root.after(50, self._drain_ui_queue)
        self._start_event_listener()
        if self._hotkey_registered:
            self._hotkey_after_id = self.root.after(60, self._poll_hotkey_messages)
        if auto_wake:
            self._auto_wake_after_id = self.root.after(800, self.toggle_wake_mode)

        if on_ready:
            on_ready(self)

    def _update_badge_positions(self) -> None:
        s = self.scale
        close_r = max(7, min(16, round(10 * s)))
        grip_r = max(7, min(15, round(9 * s)))
        margin_top = max(close_r + 4, round(18 * s))
        margin_bot = max(grip_r + 3, round(12 * s))
        self._close_badge_r = close_r
        self._close_badge_pos = (max(close_r + 4, self.stage_w - margin_top), margin_top)
        self._resize_grip_r = grip_r
        self._resize_grip_pos = (max(grip_r + 4, self.stage_w - margin_bot), max(grip_r + 4, self.stage_h - margin_bot))

        spacing = max(close_r * 2 + 5, round(23 * s))
        min_bx = max(close_r + 4, self._close_badge_pos[0] - spacing)
        self._min_badge_r = close_r
        self._min_badge_pos = (min_bx, margin_top)

    def _load_avatar_assets(self, force_raw: bool = True) -> None:
        if not PIL_AVAILABLE:
            return
        avatar_path = Path(config.widget_avatar_path)
        if force_raw and self._raw_avatar_rgba is None and avatar_path.exists():
            try:
                self._raw_avatar_rgba = Image.open(avatar_path).convert("RGBA")
            except Exception as e:
                logger.warning(f"Failed to load avatar sprite from {avatar_path}: {e}")
                self._raw_avatar_rgba = None

        if self._raw_avatar_rgba is None:
            return

        sprite_px = max(24, round(BASE_SPRITE_SIZE * self.scale))
        pulse_px = max(26, round(BASE_PULSE_SIZE * self.scale))
        resized = self._raw_avatar_rgba.resize((sprite_px, sprite_px), Image.Resampling.LANCZOS)
        r, g, b, a = resized.split()
        base_sprite = Image.merge("RGBA", (r, g, b, a.point(lambda p: 255 if p >= 64 else 0)))

        self._sprite_frames["base"] = ImageTk.PhotoImage(base_sprite)
        bright = ImageEnhance.Brightness(base_sprite).enhance(1.12)
        self._sprite_frames["active"] = ImageTk.PhotoImage(bright)
        self._sprite_frames["blink"] = self._sprite_frames["base"]
        pulse_big = bright.resize((pulse_px, pulse_px), Image.Resampling.LANCZOS)
        self._sprite_frames["speak_pulse"] = ImageTk.PhotoImage(pulse_big)

        if self._hud_thumb is None:
            thumb_src = self._raw_avatar_rgba.resize((30, 30), Image.Resampling.LANCZOS)
            tr, tg, tb, ta = thumb_src.split()
            thumb = Image.merge("RGBA", (tr, tg, tb, ta.point(lambda p: 255 if p >= 64 else 0)))
            self._hud_thumb = ImageTk.PhotoImage(thumb)
        self._rendered_sprite_scale = round(self.scale, 3)

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
        self.menu.add_checkbutton(
            label="Show HUD Chat Bar",
            variable=self._chat_bar_var,
            command=lambda: self.toggle_hud_chat_bar(self._chat_bar_var.get()),
        )
        self.menu.add_command(label="Minimize Widget (–)", command=self.minimize_widget)
        self.menu.add_command(label="Restore Widget (▲)", command=self.restore_widget)
        self.menu.add_command(label="Open Web UI", command=self.open_web_ui)

        self.size_menu = tk.Menu(self.menu, tearoff=0, bg="#111827", fg="#f8fafc", activebackground="#2563eb")
        self.size_menu.add_command(label="Increase Size (+20%)", command=lambda: self.adjust_scale(1.2))
        self.size_menu.add_command(label="Decrease Size (-20%)", command=lambda: self.adjust_scale(0.833))
        self.size_menu.add_separator()
        for label, preset in (
            ("Tiny (60%)", 0.60),
            ("Compact (80%)", 0.80),
            ("Default (100%)", 1.00),
            ("Large (140%)", 1.40),
            ("Huge (190%)", 1.90),
            ("Giant (250%)", 2.50),
        ):
            self.size_menu.add_command(label=label, command=lambda p=preset: self.set_scale(p))
        self.size_menu.add_separator()
        self.size_menu.add_command(label="Reset Avatar & HUD Size", command=self.reset_sizes)
        self.menu.add_cascade(label="Widget Size (or Scroll / Drag ↘)", menu=self.size_menu)

        self.menu.add_command(label="Voice Command (Mic)", command=self.trigger_voice_command)
        self.menu.add_command(label="Toggle Always-Listen ('Anureet')", command=self.toggle_wake_mode)
        self.menu.add_command(label="1-Minute Continuous Chat (♾️)", command=lambda: self.start_continuous_mode(60.0))
        self.menu.add_command(label="Strict Command Mode ('Anureet ...')", command=self.stop_continuous_mode)
        self.menu.add_command(label="Morning Briefing", command=lambda: self.submit_query("morning briefing"))
        self.menu.add_command(label="Stop Speaking (Esc)", command=self.stop_speaking)
        self.menu.add_separator()
        self.menu.add_command(label="Close & Stop Everything", command=self.quit_all)

        self.canvas.bind("<ButtonPress-1>", self._on_orb_press)
        self.canvas.bind("<B1-Motion>", self._on_orb_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_orb_release)
        self.canvas.bind("<Motion>", self._on_orb_motion)
        self.canvas.bind("<Leave>", lambda _e: setattr(self, "_hover_resize_grip", False))
        self.canvas.bind("<MouseWheel>", self._on_orb_wheel)
        self.canvas.bind("<Button-4>", lambda _e: self.adjust_scale(1.08))
        self.canvas.bind("<Button-5>", lambda _e: self.adjust_scale(0.92))
        self.canvas.bind("<Button-3>", self._on_orb_right_click)
        self.canvas.bind("<Double-Button-1>", self._on_orb_double_click)

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

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.hud_w = max(MIN_HUD_W, min(screen_w - 24, self._saved_hud_w))
        self.hud_h = max(MIN_HUD_H, min(screen_h - 32, self._saved_hud_h))
        self._hud_custom_pos: Optional[Tuple[int, int]] = None
        self._hud_drag_origin = (0, 0)
        self._hud_resize_origin = (0, 0, self.hud_w, self.hud_h)
        self._sprite_refresh_after_id: Optional[str] = None
        self._last_sprite_rebuild_ts = 0.0

        header = tk.Frame(self.hud, bg="#111827", padx=10, pady=8, cursor="fleur")
        header.pack(fill="x")

        if self._hud_thumb is not None:
            thumb_lbl = tk.Label(header, image=self._hud_thumb, bg="#111827", cursor="fleur")
            thumb_lbl.pack(side="left", padx=(0, 6))
            thumb_lbl.bind("<ButtonPress-1>", self._on_hud_header_press)
            thumb_lbl.bind("<B1-Motion>", self._on_hud_header_drag)

        title_lbl = tk.Label(
            header,
            text="ANUREET // HUD",
            bg="#111827",
            fg="#38bdf8",
            font=("Segoe UI", 10, "bold"),
            cursor="fleur",
        )
        title_lbl.pack(side="left")

        self.status_lbl = tk.Label(
            header,
            text="Idle",
            bg="#111827",
            fg="#94a3b8",
            font=("Segoe UI", 8),
            cursor="fleur",
        )
        self.status_lbl.pack(side="left", padx=8)

        for drag_w in (header, title_lbl, self.status_lbl):
            drag_w.bind("<ButtonPress-1>", self._on_hud_header_press)
            drag_w.bind("<B1-Motion>", self._on_hud_header_drag)

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
            text="🗕",
            command=self.minimize_widget,
            bg="#1e293b",
            fg="#e2e8f0",
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

        self.chat_bar_btn = tk.Button(
            header,
            text="💬",
            command=self.toggle_hud_chat_bar,
            bg="#1e293b",
            fg="#38bdf8" if self.hud_chat_bar_visible else "#64748b",
            activebackground="#334155",
            relief="flat",
            width=3,
            cursor="hand2",
        )
        self.chat_bar_btn.pack(side="right", padx=(4, 0))

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

        self.continuous_btn = tk.Button(
            header,
            text="♾️",
            command=self.toggle_continuous_mode,
            bg="#1e293b",
            fg="#cbd5e1",
            relief="flat",
            width=4,
            cursor="hand2",
        )
        self.continuous_btn.pack(side="right", padx=(4, 0))

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

        tk.Button(
            chips,
            text="♾️ 1M Chat",
            command=lambda: self.start_continuous_mode(60.0),
            bg="#78350f",
            fg="#fef3c7",
            activebackground="#92400e",
            relief="flat",
            font=("Segoe UI", 8, "bold"),
            padx=7,
            pady=2,
            cursor="hand2",
        ).pack(side="left", padx=(0, 5))

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

        tk.Button(
            chips,
            text="+",
            command=lambda: self.adjust_scale(1.15),
            bg="#1e293b",
            fg="#38bdf8",
            activebackground="#334155",
            relief="flat",
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=2,
            cursor="hand2",
        ).pack(side="right", padx=(2, 0))

        self.size_pct_btn = tk.Button(
            chips,
            text=f"{round(self.scale * 100)}%",
            command=self.reset_sizes,
            bg="#0f172a",
            fg="#94a3b8",
            activebackground="#1e293b",
            relief="flat",
            font=("Segoe UI", 8),
            padx=5,
            pady=2,
            cursor="hand2",
        )
        self.size_pct_btn.pack(side="right", padx=(2, 0))

        tk.Button(
            chips,
            text="−",
            command=lambda: self.adjust_scale(0.85),
            bg="#1e293b",
            fg="#38bdf8",
            activebackground="#334155",
            relief="flat",
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=2,
            cursor="hand2",
        ).pack(side="right")

        self.hud_footer = tk.Frame(self.hud, bg="#0b1120", padx=8, pady=6)
        self.hud_footer.pack(fill="x")

        self.hud_chat_bar = tk.Frame(self.hud_footer, bg="#0b1120")
        self.hud_chat_bar.pack(side="left", fill="x", expand=True)

        self.entry = tk.Entry(
            self.hud_chat_bar,
            bg="#1e293b",
            fg="#f8fafc",
            insertbackground="#38bdf8",
            relief="flat",
            font=("Segoe UI", 10),
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 6))
        self.entry.bind("<Return>", lambda _e: self._on_send_clicked())

        self.mic_btn = tk.Button(
            self.hud_chat_bar,
            text="🎤",
            command=self.trigger_voice_command,
            bg="#065f46",
            fg="#d1fae5",
            relief="flat",
            width=3,
            pady=4,
            cursor="hand2",
        )
        self.mic_btn.pack(side="left", padx=(0, 4))

        self.send_btn = tk.Button(
            self.hud_chat_bar,
            text="Send",
            command=self._on_send_clicked,
            bg="#2563eb",
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=4,
            cursor="hand2",
        )
        self.send_btn.pack(side="left")

        self.hud_resize_grip = tk.Label(
            self.hud_footer,
            text="◢",
            bg="#0b1120",
            fg="#64748b",
            font=("Segoe UI", 10, "bold"),
            cursor="size_nw_se",
            padx=3,
        )
        self.hud_resize_grip.pack(side="right", anchor="se", padx=(4, 0))
        self.hud_resize_grip.bind("<ButtonPress-1>", self._on_hud_resize_press)
        self.hud_resize_grip.bind("<B1-Motion>", self._on_hud_resize_drag)

        # Build HUD Right-Click Context Menu
        self.hud_menu = tk.Menu(self.hud, tearoff=0, bg="#111827", fg="#f8fafc", activebackground="#2563eb")
        self.hud_menu.add_checkbutton(
            label="Show HUD Chat Bar",
            variable=self._chat_bar_var,
            command=lambda: self.toggle_hud_chat_bar(self._chat_bar_var.get()),
        )
        self.hud_menu.add_command(label="1-Minute Continuous Chat (♾️)", command=lambda: self.start_continuous_mode(60.0))
        self.hud_menu.add_command(label="Strict Command Mode ('Anureet ...')", command=self.stop_continuous_mode)
        self.hud_menu.add_command(label="Clear Chat History", command=self.clear_chat_history)
        self.hud_menu.add_separator()
        self.hud_menu.add_command(label="Voice Command (Mic)", command=self.trigger_voice_command)
        self.hud_menu.add_command(label="Toggle Voice (TTS)", command=self.toggle_tts)
        self.hud_menu.add_command(label="Toggle Always-Listen ('Anureet')", command=self.toggle_wake_mode)
        self.hud_menu.add_separator()
        self.hud_menu.add_command(label="Hide HUD", command=self.toggle_hud)
        self.hud_menu.add_command(label="Close & Stop Everything", command=self.quit_all)

        for w in (self.hud, header, title_lbl, self.status_lbl, self.chat_box, self.hud_footer):
            w.bind("<Button-3>", self._on_hud_right_click)

        self.hud.bind("<Control-b>", lambda _e: self.toggle_hud_chat_bar())
        self.hud.bind("<Control-B>", lambda _e: self.toggle_hud_chat_bar())
        self.hud.bind("<Control-h>", lambda _e: self.toggle_hud_chat_bar())
        self.hud.bind("<Control-H>", lambda _e: self.toggle_hud_chat_bar())

        if not self.hud_chat_bar_visible:
            self.hud_chat_bar.pack_forget()
            self.hud_footer.configure(pady=2)

        hotkey_hint = self._hotkey_label.upper() if self._hotkey_registered else "Click Avatar"
        self._append_chat(
            "Anureet",
            f"Ready ({hotkey_hint} for HUD, Scroll or Drag ↘ to resize, Double-Click/🎤 for Mic, Esc stops voice).",
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
        self.hud_w = max(MIN_HUD_W, min(screen_w - 24, self.hud_w))
        self.hud_h = max(MIN_HUD_H, min(screen_h - 32, self.hud_h))
        if self._hud_custom_pos is not None:
            x, y = self._hud_custom_pos
        else:
            x = self.orb_x + self.stage_w - self.hud_w
            y = self.orb_y - self.hud_h - 10
            if y < 12:
                y = self.orb_y + self.stage_h + 10
        x = max(12, min(screen_w - self.hud_w - 12, x))
        y = max(12, min(screen_h - self.hud_h - 12, y))
        self.hud_x = x
        self.hud_y = y
        self.hud.geometry(f"{self.hud_w}x{self.hud_h}+{x}+{y}")

    def _on_hud_header_press(self, event) -> None:
        self._hud_drag_origin = (event.x_root, event.y_root)

    def _on_hud_header_drag(self, event) -> None:
        dx = event.x_root - self._hud_drag_origin[0]
        dy = event.y_root - self._hud_drag_origin[1]
        self._hud_drag_origin = (event.x_root, event.y_root)
        cur_x = getattr(self, "hud_x", self.hud.winfo_x()) + dx
        cur_y = getattr(self, "hud_y", self.hud.winfo_y()) + dy
        self._hud_custom_pos = (cur_x, cur_y)
        self._position_hud()

    def _on_hud_resize_press(self, event) -> None:
        cur_x = getattr(self, "hud_x", self.hud.winfo_x())
        cur_y = getattr(self, "hud_y", self.hud.winfo_y())
        self._hud_custom_pos = (cur_x, cur_y)
        self._hud_resize_origin = (event.x_root, event.y_root, self.hud_w, self.hud_h)

    def _on_hud_resize_drag(self, event) -> None:
        ox, oy, ow, oh = self._hud_resize_origin
        self.resize_hud(ow + (event.x_root - ox), oh + (event.y_root - oy))

    def resize_hud(self, width: int, height: int) -> Tuple[int, int]:
        """Resize the expandable HUD freely within screen bounds and persist dimensions."""
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        cur_x = getattr(self, "hud_x", 12)
        cur_y = getattr(self, "hud_y", 12)
        max_w = max(MIN_HUD_W, screen_w - cur_x - 12) if self._hud_custom_pos else screen_w - 24
        max_h = max(MIN_HUD_H, screen_h - cur_y - 12) if self._hud_custom_pos else screen_h - 32
        self.hud_w = max(MIN_HUD_W, min(max_w, int(width)))
        self.hud_h = max(MIN_HUD_H, min(max_h, int(height)))
        self._position_hud()
        self._schedule_save_prefs()
        return self.hud_w, self.hud_h

    def _schedule_save_prefs(self) -> None:
        if self._closed:
            return
        if self._prefs_save_after_id is not None:
            try:
                self.root.after_cancel(self._prefs_save_after_id)
            except Exception:
                pass
        self._prefs_save_after_id = self.root.after(300, self._flush_prefs)

    def _flush_prefs(self) -> None:
        self._prefs_save_after_id = None
        if self.is_minimized:
            return
        _save_widget_prefs({
            "scale": round(self.scale, 3),
            "hud_w": int(self.hud_w),
            "hud_h": int(self.hud_h),
            "hud_chat_bar_visible": bool(self.hud_chat_bar_visible),
        }, self._prefs_path)

    def _refresh_sprites_if_needed(self) -> None:
        self._sprite_refresh_after_id = None
        if not self._closed and self._rendered_sprite_scale != round(self.scale, 3):
            self._load_avatar_assets(force_raw=False)
            self._last_sprite_rebuild_ts = time.monotonic()

    def set_scale(self, new_scale: float, anchor: str = "bottom_right") -> float:
        """Resize the floating avatar widget cleanly to any supported scale and re-render crisp assets."""
        if self.is_minimized:
            self.restore_widget()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        clamped = clamp_widget_scale(new_scale, screen_w, screen_h)
        old_w, old_h = self.stage_w, self.stage_h
        new_w = max(76, round(BASE_STAGE_W * clamped))
        new_h = max(84, round(BASE_STAGE_H * clamped))

        if anchor == "bottom_right":
            self.orb_x += old_w - new_w
            self.orb_y += old_h - new_h

        self.orb_x = max(12, min(screen_w - new_w - 12, self.orb_x))
        self.orb_y = max(12, min(screen_h - new_h - 12, self.orb_y))
        self.scale = clamped
        self.stage_w = new_w
        self.stage_h = new_h
        self.orb_size = new_w
        self._update_badge_positions()

        self.canvas.configure(width=self.stage_w, height=self.stage_h)
        self.root.geometry(f"{self.stage_w}x{self.stage_h}+{self.orb_x}+{self.orb_y}")

        now = time.monotonic()
        if now - self._last_sprite_rebuild_ts >= 0.045:
            self._load_avatar_assets(force_raw=False)
            self._last_sprite_rebuild_ts = now
        else:
            if self._sprite_refresh_after_id is not None:
                try:
                    self.root.after_cancel(self._sprite_refresh_after_id)
                except Exception:
                    pass
            self._sprite_refresh_after_id = self.root.after(55, self._refresh_sprites_if_needed)

        if hasattr(self, "size_pct_btn"):
            self.size_pct_btn.configure(text=f"{round(self.scale * 100)}%")
        if self.hud_visible:
            self._position_hud()
        if self.bubble.state() == "normal":
            self._position_bubble()
        self._schedule_save_prefs()
        return self.scale

    def adjust_scale(self, factor: float) -> float:
        """Multiply current avatar scale by factor cleanly."""
        return self.set_scale(self.scale * factor, anchor="bottom_right")

    def reset_sizes(self) -> None:
        """Restore default avatar scale (100%) and default HUD size cleanly."""
        self._hud_custom_pos = None
        self.hud_w = BASE_HUD_W
        self.hud_h = BASE_HUD_H
        self.set_scale(1.0, anchor="bottom_right")
        self._flush_prefs()

    def toggle_hud(self) -> None:
        if self.is_minimized:
            self.restore_widget()
        if self.hud_visible:
            self.hud.withdraw()
            self.hud_visible = False
        else:
            self._position_hud()
            self.hud.deiconify()
            self.hud.lift()
            if getattr(self, "hud_chat_bar_visible", True):
                try:
                    self.entry.focus_set()
                except Exception:
                    pass
            self.hud_visible = True

    def toggle_hud_chat_bar(self, visible: Optional[bool] = None) -> bool:
        """Toggle or set visibility of the bottom HUD chat input bar and persist preference."""
        if visible is None:
            self.hud_chat_bar_visible = not getattr(self, "hud_chat_bar_visible", True)
        else:
            self.hud_chat_bar_visible = bool(visible)

        if not hasattr(self, "hud_chat_bar") or self._closed:
            return self.hud_chat_bar_visible

        if self.hud_chat_bar_visible:
            try:
                self.hud_chat_bar.pack(side="left", fill="x", expand=True, before=self.hud_resize_grip)
            except Exception:
                self.hud_chat_bar.pack(side="left", fill="x", expand=True)
            if hasattr(self, "hud_footer"):
                self.hud_footer.configure(pady=6)
            if hasattr(self, "chat_bar_btn"):
                self.chat_bar_btn.configure(text="💬", fg="#38bdf8", bg="#1e293b")
            if hasattr(self, "_chat_bar_var"):
                self._chat_bar_var.set(True)
            if self.hud_visible:
                try:
                    self.entry.focus_set()
                except Exception:
                    pass
        else:
            try:
                self.hud_chat_bar.pack_forget()
            except Exception:
                pass
            if hasattr(self, "hud_footer"):
                self.hud_footer.configure(pady=2)
            if hasattr(self, "chat_bar_btn"):
                self.chat_bar_btn.configure(text="💬", fg="#64748b", bg="#1e293b")
            if hasattr(self, "_chat_bar_var"):
                self._chat_bar_var.set(False)

        self._schedule_save_prefs()
        return self.hud_chat_bar_visible

    def set_hud_chat_bar_visible(self, visible: bool) -> bool:
        """Explicitly set HUD chat input bar visibility."""
        return self.toggle_hud_chat_bar(visible=visible)

    def clear_chat_history(self) -> None:
        """Clear local HUD chat transcript cleanly."""
        def _apply():
            if self._closed:
                return
            self.chat_box.configure(state="normal")
            self.chat_box.delete("1.0", "end")
            self.chat_box.configure(state="disabled")
        self._ui_queue.put(_apply)

    def _on_hud_right_click(self, event) -> None:
        if hasattr(self, "hud_menu"):
            try:
                self.hud_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.hud_menu.grab_release()

    def minimize_widget(self) -> None:
        """Collapse the floating avatar stage into a compact cyberpunk corner dock pill."""
        if self._closed or self.is_minimized:
            return
        self._saved_geometry_before_min = (self.stage_w, self.stage_h, self.orb_x, self.orb_y)
        self.is_minimized = True
        if self.hud_visible:
            self.hud.withdraw()
        self._hide_bubble()
        min_w = 142
        min_h = 38
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        new_x = max(12, min(screen_w - min_w - 12, self.orb_x + self.stage_w - min_w))
        new_y = max(12, min(screen_h - min_h - 12, self.orb_y + self.stage_h - min_h))
        self.stage_w = min_w
        self.stage_h = min_h
        self.orb_x = new_x
        self.orb_y = new_y
        self.canvas.configure(width=min_w, height=min_h)
        self.root.geometry(f"{min_w}x{min_h}+{new_x}+{new_y}")

    def restore_widget(self) -> None:
        """Restore the floating avatar widget from minimized dock pill to full interactive stage."""
        if self._closed or not self.is_minimized:
            return
        self.is_minimized = False
        if self._saved_geometry_before_min is not None:
            w, h, x, y = self._saved_geometry_before_min
        else:
            w = max(76, round(BASE_STAGE_W * self.scale))
            h = max(84, round(BASE_STAGE_H * self.scale))
            x, y = self.orb_x, self.orb_y
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.stage_w = w
        self.stage_h = h
        self.orb_x = max(12, min(screen_w - self.stage_w - 12, x))
        self.orb_y = max(12, min(screen_h - self.stage_h - 12, y))
        self._update_badge_positions()
        self.canvas.configure(width=self.stage_w, height=self.stage_h)
        self.root.geometry(f"{self.stage_w}x{self.stage_h}+{self.orb_x}+{self.orb_y}")
        if self.hud_visible:
            self._position_hud()
            self.hud.deiconify()
            self.hud.lift()

    def toggle_minimize(self) -> None:
        if self.is_minimized:
            self.restore_widget()
        else:
            self.minimize_widget()

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

    def start_continuous_mode(self, duration_seconds: float = 60.0, speak_announcement: bool = True) -> None:
        """Activate 1-minute continuous hands-free chat mode (no need to repeat 'Anureet')."""
        self.continuous_mode_active = True
        self.continuous_mode_until = time.time() + duration_seconds
        if hasattr(self, "continuous_btn"):
            self.continuous_btn.configure(bg="#78350f", fg="#fef3c7", text="♾️ 1M")
        self._set_state("listening", "Continuous (60s)")
        self._show_bubble("♾️ CONTINUOUS CHAT", user_text="1-Minute hands-free chat active", auto_hide_ms=4500)
        self._append_chat("System", "1-Minute Continuous Chat Mode enabled (speak freely without saying 'Anureet').", tag_hint="meta")
        if not self.wake_mode_enabled:
            self.toggle_wake_mode()
        if speak_announcement and self.tts_enabled:
            eng = self._get_voice_engine()
            if eng:
                eng.speak("Continuous listening mode activated for 1 minute.")
        self._schedule_continuous_ticker()

    def stop_continuous_mode(self, speak_announcement: bool = True) -> None:
        """Deactivate continuous chat mode and revert to strict command mode ('Anureet <command>')."""
        self.continuous_mode_active = False
        self.continuous_mode_until = 0.0
        if self._continuous_ticker_after_id is not None:
            try:
                self.root.after_cancel(self._continuous_ticker_after_id)
            except Exception:
                pass
            self._continuous_ticker_after_id = None
        if hasattr(self, "continuous_btn"):
            self.continuous_btn.configure(bg="#1e293b", fg="#cbd5e1", text="♾️")
        self._set_state("idle", "ONLINE")
        self._show_bubble("🔒 STRICT MODE", user_text="Say 'Anureet' + command", auto_hide_ms=3500)
        self._append_chat("System", "Strict Command Mode active (requires 'Anureet ...').", tag_hint="meta")
        if speak_announcement and self.tts_enabled:
            eng = self._get_voice_engine()
            if eng:
                eng.speak("Continuous chat mode ended. Back to strict wake word mode.")

    def toggle_continuous_mode(self) -> bool:
        """Toggle between strict command mode and 1-minute continuous chat mode."""
        if self.is_continuous_mode_active():
            self.stop_continuous_mode()
            return False
        else:
            self.start_continuous_mode(60.0)
            return True

    def is_continuous_mode_active(self) -> bool:
        """Check whether 1-minute continuous chat mode is actively running."""
        if not self.continuous_mode_active:
            return False
        if time.time() >= self.continuous_mode_until:
            self.stop_continuous_mode(speak_announcement=True)
            return False
        return True

    def _schedule_continuous_ticker(self) -> None:
        """Update countdown timer on continuous mode badge every second."""
        if self._closed or not self.continuous_mode_active:
            return
        if self._continuous_ticker_after_id is not None:
            try:
                self.root.after_cancel(self._continuous_ticker_after_id)
            except Exception:
                pass
            self._continuous_ticker_after_id = None
        rem = max(0, int(self.continuous_mode_until - time.time()))
        if rem <= 0:
            self.stop_continuous_mode(speak_announcement=True)
            return
        if hasattr(self, "continuous_btn"):
            self.continuous_btn.configure(text=f"♾️ {rem}s")
        if self.state in ("listening", "idle"):
            self.status_lbl.configure(text=f"Chatting ({rem}s)...")
        self._continuous_ticker_after_id = self.root.after(1000, self._schedule_continuous_ticker)

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
        try:
            publish_widget_event("thinking", user_text=query, source="widget")
        except Exception:
            pass

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
                try:
                    publish_widget_event("idle", user_text=query, agent_text=reply, source="widget")
                except Exception:
                    pass
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
                try:
                    publish_widget_event("idle", user_text=query, agent_text=str(e), source="widget")
                except Exception:
                    pass
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
        while self.wake_mode_enabled and not self._wake_stop_event.is_set() and not self._closed:
            try:
                # Never listen while an instruction is being processed, spoken aloud, or active SAPI audio playback
                while (
                    (self._busy_event.is_set() or self._mic_active_event.is_set() or engine.is_speaking_active())
                    and self.wake_mode_enabled
                    and not self._wake_stop_event.is_set()
                    and not self._closed
                ):
                    time.sleep(0.12)

                if not self.wake_mode_enabled or self._wake_stop_event.is_set() or self._closed:
                    break

                is_continuous = self.is_continuous_mode_active()
                if is_continuous and not self._busy_event.is_set():
                    rem = max(0, int(self.continuous_mode_until - time.time()))
                    self._set_state("listening", f"Continuous ({rem}s)")

                text = engine.listen_once(timeout=5, phrase_time_limit=10)
                if (
                    not text
                    or self._wake_stop_event.is_set()
                    or self._busy_event.is_set()
                    or self._mic_active_event.is_set()
                    or engine.is_speaking_active()
                ):
                    continue

                from voice_assistant import CONTINUOUS_START_PATTERN, CONTINUOUS_STOP_PATTERN

                # 1. Continuous mode voice triggers
                if CONTINUOUS_START_PATTERN.search(text):
                    self.start_continuous_mode(60.0)
                    continue

                if CONTINUOUS_STOP_PATTERN.search(text):
                    self.stop_continuous_mode()
                    continue

                # 2. Extract command according to current mode (strict vs continuous)
                wake_found, cmd = engine.extract_wake_command(text, continuous_mode=is_continuous)
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
                        if is_continuous and not self._closed:
                            self._set_state("listening", "Continuous...")
                    else:
                        done = threading.Event()
                        self._busy_event.set()
                        self._ui_queue.put(lambda c=cmd, d=done: self.submit_query(c, done_event=d))
                        while not done.wait(0.15):
                            if self._wake_stop_event.is_set() or self._closed:
                                break
                        time.sleep(0.30)
            except Exception as e:
                logger.debug(f"Wake loop cycle skipped: {e}")
                time.sleep(0.5)

    def _start_event_listener(self) -> None:
        """Poll external database events asynchronously off the GUI thread to eliminate OneDrive/SQLite UI stalls."""
        def _poller_worker():
            while not self._closed:
                try:
                    events = get_latest_widget_events(after_id=self._last_event_id)
                    if events and not self._closed:
                        self._ui_queue.put(lambda evs=events: self._dispatch_external_events(evs))
                except Exception:
                    pass
                time.sleep(0.35)

        t = threading.Thread(target=_poller_worker, name="AnureetEventPoller", daemon=True)
        t.start()

    def _dispatch_external_events(self, events: List[Dict[str, Any]]) -> None:
        """Process external database events safely on Tkinter's main thread."""
        if self._closed:
            return
        for ev in events:
            self._last_event_id = max(self._last_event_id, int(ev.get("id", 0)))
            ev_state = ev.get("state", "idle")
            user_text = ev.get("user_text", "")
            agent_text = ev.get("agent_text", "")
            source = ev.get("source", "voice")
            if ev_state == "shutdown":
                self.root.after(200, self.quit_all)
                return
            if ev_state == "continuous_chat":
                action = (user_text or "").strip().lower()
                if "start" in action or action in ("1", "true", "on"):
                    if not self.continuous_mode_active:
                        self.start_continuous_mode(60.0, speak_announcement=False)
                elif "stop" in action or action in ("0", "false", "off"):
                    if self.continuous_mode_active:
                        self.stop_continuous_mode(speak_announcement=False)
                continue
            if ev_state == "hud_chat_bar":
                action = (user_text or "").strip().lower()
                if "hide" in action or action in ("0", "false", "off"):
                    self.toggle_hud_chat_bar(visible=False)
                elif "show" in action or action in ("1", "true", "on"):
                    self.toggle_hud_chat_bar(visible=True)
                else:
                    self.toggle_hud_chat_bar()
                continue
            if ev_state == "hud_visibility":
                action = (user_text or "").strip().lower()
                if "hide" in action or "close" in action or action in ("0", "false", "off"):
                    if self.hud_visible:
                        self.toggle_hud()
                elif "show" in action or "open" in action or action in ("1", "true", "on"):
                    if not self.hud_visible:
                        self.toggle_hud()
                else:
                    self.toggle_hud()
                continue
            if source == "widget":
                continue

            source_label = "Web" if source == "web" else ("Voice" if source == "voice" else "")
            user_prefix = f"You ({source_label})" if source_label else "You"

            if ev_state == "thinking":
                self._set_state("thinking", "Thinking...")
                if user_text and self._last_instruction != user_text:
                    self._last_instruction = user_text
                    self._append_chat(user_prefix, user_text)
                    self._show_bubble("⚡ THINKING", user_text=user_text)
            elif ev_state == "speaking":
                self._set_state("speaking", "Speaking...")
                if agent_text and self._last_response != agent_text:
                    self._last_response = agent_text
                    self._append_chat("Anureet", agent_text)
                    self._show_bubble("🗣️ SPEAKING", user_text=user_text, agent_text=agent_text)
            elif ev_state == "listening":
                self._set_state("listening", "Listening...")
                self._show_bubble("🎙️ LISTENING...", user_text=user_text or "Listening for follow-up...")
            else:
                self._set_state("idle", "ONLINE")
                if user_text and self._last_instruction != user_text:
                    self._last_instruction = user_text
                    self._append_chat(user_prefix, user_text)
                if agent_text and self._last_response != agent_text:
                    self._last_response = agent_text
                    self._append_chat("Anureet", agent_text)
                    self._show_bubble("✅ COMPLETED", user_text=user_text, agent_text=agent_text, auto_hide_ms=7000)

    def _poll_external_events(self) -> None:
        """Manual poll hook for testing/fallback."""
        try:
            events = get_latest_widget_events(after_id=self._last_event_id)
            if events:
                self._dispatch_external_events(events)
        except Exception:
            pass

    def _hit_close_badge(self, x: float, y: float) -> bool:
        if self.is_minimized:
            return False
        bx, by = self._close_badge_pos
        return (x - bx) ** 2 + (y - by) ** 2 <= (self._close_badge_r + 4) ** 2

    def _hit_min_badge(self, x: float, y: float) -> bool:
        if self.is_minimized:
            return False
        bx, by = self._min_badge_pos
        return (x - bx) ** 2 + (y - by) ** 2 <= (self._min_badge_r + 4) ** 2

    def _hit_resize_grip(self, x: float, y: float) -> bool:
        if self.is_minimized:
            return False
        gx, gy = self._resize_grip_pos
        return (x - gx) ** 2 + (y - gy) ** 2 <= (self._resize_grip_r + 6) ** 2

    def _on_orb_motion(self, event) -> None:
        if self.is_minimized:
            self.canvas.configure(cursor="hand2")
            return
        on_grip = self._hit_resize_grip(event.x, event.y)
        if on_grip != self._hover_resize_grip:
            self._hover_resize_grip = on_grip
            self.canvas.configure(cursor="size_nw_se" if on_grip else "hand2")

    def _on_orb_wheel(self, event) -> None:
        if self.is_minimized:
            return
        delta = getattr(event, "delta", 0)
        if not delta:
            return
        factor = 1.08 if delta > 0 else 0.92
        steps = max(1, min(4, int(round(abs(delta) / 120.0))))
        self.adjust_scale(factor ** steps)

    def _on_orb_double_click(self, event) -> None:
        if self.is_minimized:
            self.restore_widget()
            return
        if self._hit_close_badge(event.x, event.y) or self._hit_min_badge(event.x, event.y) or self._hit_resize_grip(event.x, event.y):
            return
        self.trigger_voice_command()

    def _on_orb_press(self, event) -> None:
        self._drag_origin = (event.x_root, event.y_root)
        self._dragged = False
        if self.is_minimized:
            self._interaction_mode = "move"
        else:
            self._interaction_mode = "resize" if self._hit_resize_grip(event.x, event.y) else "move"

    def _on_orb_drag(self, event) -> None:
        if self.is_minimized:
            dx = event.x_root - self._drag_origin[0]
            dy = event.y_root - self._drag_origin[1]
            if abs(dx) >= 3 or abs(dy) >= 3:
                self._dragged = True
            self.orb_x += dx
            self.orb_y += dy
            self._drag_origin = (event.x_root, event.y_root)
            self.root.geometry(f"142x38+{self.orb_x}+{self.orb_y}")
            return
        if self._interaction_mode == "resize":
            self._dragged = True
            target_scale = max(
                (event.x_root - self.orb_x + 12) / float(BASE_STAGE_W),
                (event.y_root - self.orb_y + 12) / float(BASE_STAGE_H),
            )
            self.set_scale(target_scale, anchor="top_left")
            return
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
        if self.is_minimized:
            if not self._dragged:
                self.restore_widget()
            return
        if self._interaction_mode == "resize":
            self._interaction_mode = "move"
            self._refresh_sprites_if_needed()
            return
        if not self._dragged:
            if self._hit_close_badge(event.x, event.y):
                self.quit_all()
                return
            if self._hit_min_badge(event.x, event.y):
                self.minimize_widget()
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

        try:
            state = self.state
            pal = STATE_PALETTES.get(state, STATE_PALETTES["idle"])

            # If minimized, render sleek compact cyberpunk dock pill
            if self.is_minimized:
                self.phase += 0.12
                self.canvas.delete("all")
                # Glass container
                self.canvas.create_rectangle(
                    2, 2, 140, 36,
                    fill="#071324", outline=pal["mid"], width=1,
                )
                # Mini avatar thumb or icon
                if self._hud_thumb is not None:
                    self.canvas.create_image(19, 19, image=self._hud_thumb, anchor="center")
                else:
                    self.canvas.create_oval(10, 10, 28, 28, fill=pal["badge_bg"], outline=pal["outer"], width=1)
                # Pulsing status dot
                dot_r = 3.0 + 1.0 * math.sin(self.phase * 2.0)
                self.canvas.create_oval(39 - dot_r, 19 - dot_r, 39 + dot_r, 19 + dot_r, fill=pal["core"], outline=pal["mid"], width=1)
                # Status label
                state_short = self.state_note[:8]
                self.canvas.create_text(
                    47, 19,
                    text=f"{state_short}",
                    anchor="w",
                    fill="#e2e8f0",
                    font=("Segoe UI", 8, "bold"),
                )
                # Restore chevron
                self.canvas.create_text(
                    127, 19,
                    text="▲",
                    anchor="center",
                    fill=pal["accent"],
                    font=("Segoe UI", 9, "bold"),
                )
                return

            self.phase += 0.14 if state == "idle" else 0.36
            s = self.scale
            cx = self.stage_w / 2.0
            cy = 88.0 * s
            stroke_2 = max(1, round(2 * s))
            stroke_3 = max(2, round(3 * s))

            self.canvas.delete("all")

            # 1. Cosmic base glow disc behind Anureet so silhouette pops cleanly over any wallpaper
            base_r = (66.0 + 2.0 * math.sin(self.phase)) * s
            self.canvas.create_oval(
                cx - base_r, cy - base_r, cx + base_r, cy + base_r,
                fill="#071324", outline=pal["outer"], width=stroke_2,
            )

            # 2. State-specific holographic background effects
            if state == "listening":
                for k in range(3):
                    prog = ((self.phase * 0.28 + k / 3.0) % 1.0)
                    rr = (42.0 + prog * 44.0) * s
                    ring_color = pal["core"] if prog < 0.35 else (pal["accent"] if prog < 0.7 else pal["outer"])
                    self.canvas.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, outline=ring_color, width=stroke_2)
            elif state == "thinking":
                deg1 = (self.phase * 72) % 360
                deg2 = (-self.phase * 96) % 360
                arc_r = 76.0 * s
                dot_r = max(2.0, 4.0 * s)
                self.canvas.create_arc(
                    cx - arc_r, cy - arc_r, cx + arc_r, cy + arc_r,
                    start=deg1, extent=95, style="arc", outline=pal["mid"], width=stroke_3,
                )
                self.canvas.create_arc(
                    cx - arc_r, cy - arc_r, cx + arc_r, cy + arc_r,
                    start=(deg1 + 180) % 360, extent=95, style="arc", outline=pal["accent"], width=stroke_3,
                )
                for idx in range(4):
                    ang = math.radians(deg2 + idx * 90)
                    sx = cx + arc_r * math.cos(ang)
                    sy = cy + arc_r * math.sin(ang)
                    self.canvas.create_oval(sx - dot_r, sy - dot_r, sx + dot_r, sy + dot_r, fill=pal["core"], outline="")
            elif state == "speaking":
                for k in range(2):
                    prog = ((self.phase * 0.45 + k * 0.5) % 1.0)
                    rr = (54.0 + prog * 28.0) * s
                    self.canvas.create_oval(
                        cx - rr, cy - rr, cx + rr, cy + rr,
                        outline=pal["accent"] if prog < 0.5 else pal["outer"], width=stroke_2,
                    )
            else:
                orb_r = 70.0 * s
                dot_r = max(1.8, 2.5 * s)
                for idx in range(2):
                    ang = self.phase * 0.6 + idx * math.pi
                    sx = cx + orb_r * math.cos(ang)
                    sy = cy + orb_r * math.sin(ang)
                    self.canvas.create_oval(sx - dot_r, sy - dot_r, sx + dot_r, sy + dot_r, fill=pal["core"], outline="")

            # 3. Render Chibi Anureet 3D Model Sprite with smooth breathing/speaking bounce
            if state == "speaking":
                bob_y = -abs(math.sin(self.phase * 1.6)) * 4.5 * s
                sprite = (
                    self._sprite_frames.get("speak_pulse")
                    if math.sin(self.phase * 2.2) > 0.15
                    else self._sprite_frames.get("active")
                )
            elif state == "listening":
                bob_y = math.sin(self.phase * 1.1) * 2.5 * s
                sprite = self._sprite_frames.get("active")
            elif state == "thinking":
                bob_y = math.sin(self.phase * 0.9) * 3.0 * s
                sprite = self._sprite_frames.get("active")
            else:
                bob_y = math.sin(self.phase) * 3.0 * s
                sprite = self._sprite_frames.get("base")

            if sprite is not None:
                self.canvas.create_image(cx, cy - 2.0 * s + bob_y, image=sprite, anchor="center")
            else:
                r_core = (26.0 + 3.0 * math.sin(self.phase)) * s
                self.canvas.create_oval(cx - r_core, cy - r_core, cx + r_core, cy + r_core, fill=pal["mid"], outline=pal["core"], width=stroke_2)

            # 4. Live animated equalizer / waveform bars across pedestal bottom
            eq_y = 166.0 * s
            bar_w = max(2.0, 5.0 * s)
            gap = max(1.5, 4.0 * s)
            n_bars = len(self._eq_levels)
            start_x = cx - ((n_bars * bar_w + (n_bars - 1) * gap) / 2.0)
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
                bar_h = max(2.0, self._eq_levels[i] * 18.0 * s)
                x0 = start_x + i * (bar_w + gap)
                bar_color = pal["core"] if (state == "speaking" and i % 2 == 0) else pal["mid"]
                self.canvas.create_rectangle(
                    x0, eq_y - bar_h / 2.0, x0 + bar_w, eq_y + bar_h / 2.0,
                    fill=bar_color, outline="",
                )

            # 5. State status badge pill
            pill_w = min(self.stage_w - 12.0, 132.0 * s)
            pill_top = 180.0 * s
            pill_bot = 200.0 * s
            pill_font = max(6, min(18, round(8 * s)))
            self.canvas.create_rectangle(
                cx - pill_w / 2.0, pill_top, cx + pill_w / 2.0, pill_bot,
                fill=pal["badge_bg"], outline=pal["mid"], width=1,
            )
            self.canvas.create_text(
                cx, (pill_top + pill_bot) / 2.0,
                text=f"● {self.state_note}",
                fill=pal["core"],
                font=("Segoe UI", pill_font, "bold"),
            )

            # 6. Top-right quick Close (✕) and Minimize (–) badges
            bx, by = self._close_badge_pos
            br = self._close_badge_r
            badge_font = max(6, min(13, round(8 * s)))
            self.canvas.create_oval(bx - br, by - br, bx + br, by + br, fill="#7f1d1d", outline="#fca5a5", width=1)
            self.canvas.create_text(bx, by, text="✕", fill="#ffffff", font=("Segoe UI", badge_font, "bold"))

            mbx, mby = self._min_badge_pos
            mbr = self._min_badge_r
            self.canvas.create_oval(mbx - mbr, mby - mbr, mbx + mbr, mby + mbr, fill="#1e293b", outline="#64748b", width=1)
            self.canvas.create_text(mbx, mby, text="–", fill="#cbd5e1", font=("Segoe UI", badge_font, "bold"))

            # 7. Bottom-right drag-to-resize grip handle (↘)
            gx, gy = self._resize_grip_pos
            gr = self._resize_grip_r
            grip_fill = "#0c4a6e" if self._hover_resize_grip else "#0f172a"
            grip_outline = "#7dd3fc" if self._hover_resize_grip else "#38bdf8"
            self.canvas.create_oval(gx - gr, gy - gr, gx + gr, gy + gr, fill=grip_fill, outline=grip_outline, width=1)
            self.canvas.create_text(gx, gy, text="↘", fill="#e0f2fe", font=("Segoe UI", badge_font, "bold"))

        except Exception as e:
            logger.debug(f"Stage animation frame error: {e}")
        finally:
            if not self._closed:
                self._animate_after_id = self.root.after(40, self._animate_stage)

    def _drain_ui_queue(self) -> None:
        while True:
            try:
                fn = self._ui_queue.get_nowait()
                fn()
            except queue.Empty:
                break
        if not self._closed:
            self._drain_after_id = self.root.after(50, self._drain_ui_queue)

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
                    if self.is_minimized:
                        self.restore_widget()
                    self.toggle_hud()
        if not self._closed:
            self._hotkey_after_id = self.root.after(60, self._poll_hotkey_messages)

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
        for attr in (
            "_prefs_save_after_id",
            "_animate_after_id",
            "_drain_after_id",
            "_hotkey_after_id",
            "_bubble_hide_after_id",
            "_sprite_refresh_after_id",
            "_auto_wake_after_id",
            "_continuous_ticker_after_id",
        ):
            val = getattr(self, attr, None)
            if val is not None:
                try:
                    self.root.after_cancel(val)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            self._flush_prefs()
        except Exception:
            pass
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
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass
        try:
            self.root.update()
        except Exception:
            pass
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
