"""
App-Specific Control Modes and Shortcut Mapping Module.
Provides specialized control states for Chrome, Spotify, Notepad, FL Studio, After Effects, and Premiere Pro.
"""

import re
import time
import logging
from typing import Optional

logger = logging.getLogger("AppModes")

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None


KEY_ALIASES = {
    "space bar": "space",
    "spacebar": "space",
    "space bar.": "space",
    "space.": "space",
    "enter.": "enter",
    "tab.": "tab",
    "esc.": "escape",
    "escape.": "escape",
    "alt f4": "alt,f4",
    "alt+f4": "alt,f4",
    "ctrl l": "ctrl,l",
    "ctrl+l": "ctrl,l",
    "ctrl t": "ctrl,t",
    "ctrl w": "ctrl,w",
    "ctrl n": "ctrl,n",
    "ctrl s": "ctrl,s",
    "ctrl a": "ctrl,a",
}


def sanitize_and_press_key(raw_key: str) -> str:
    """Clean key strings transcribed from speech and press key/hotkey combination."""
    if not pyautogui:
        return "PyAutoGUI not installed."
    try:
        clean = raw_key.lower().strip().rstrip(".")
        mapped = KEY_ALIASES.get(clean, clean)
        
        if "," in mapped or "+" in mapped:
            keys = [k.strip() for k in mapped.replace("+", ",").split(",")]
            pyautogui.hotkey(*keys)
            return f"Pressed hotkey combination: {' + '.join(keys)}"
        else:
            pyautogui.press(mapped)
            return f"Pressed '{mapped}' key."
    except Exception as e:
        return f"Key press error: {e}"


class AppModeManager:
    def __init__(self):
        self.current_mode = "DEFAULT"

    def set_mode(self, mode_name: str) -> str:
        mode_clean = mode_name.upper().strip()
        self.current_mode = mode_clean
        return f"Activated {mode_clean} Control Mode."

    def get_mode(self) -> str:
        return self.current_mode

    def process_mode_command(self, query: str) -> Optional[str]:
        """Process commands specific to the currently active application control mode."""
        q = query.lower().strip().rstrip(".")
        mode = self.current_mode

        if "exit mode" in q or "deactivate mode" in q or "exit control" in q or "default mode" in q:
            self.current_mode = "DEFAULT"
            return "Deactivated app control mode. Reverted to Default Mode."

        if mode in {"CHROME_MODE", "SPOTIFY_MODE", "NOTEPAD_MODE", "FL_STUDIO_MODE"} and not pyautogui:
            return "PyAutoGUI not installed."

        # 1. CHROME MODE
        if mode == "CHROME_MODE":
            if "new tab" in q or "open new tab" in q:
                return sanitize_and_press_key("ctrl,t")
            elif "close tab" in q or "close current tab" in q:
                return sanitize_and_press_key("ctrl,w")
            elif "reopen tab" in q or "restore tab" in q:
                return sanitize_and_press_key("ctrl,shift,t")
            elif "address bar" in q or "search bar" in q or "focus url" in q:
                return sanitize_and_press_key("ctrl,l")
            elif "go back" in q or "previous page" in q:
                return sanitize_and_press_key("alt,left")
            elif "go forward" in q or "next page" in q:
                return sanitize_and_press_key("alt,right")
            elif "refresh" in q or "reload" in q:
                return sanitize_and_press_key("f5")
            elif "downloads" in q:
                return sanitize_and_press_key("ctrl,j")
            elif "history" in q:
                return sanitize_and_press_key("ctrl,h")
            elif "scroll down" in q:
                pyautogui.scroll(-600)
                return "Scrolled Chrome down."
            elif "scroll up" in q:
                pyautogui.scroll(600)
                return "Scrolled Chrome up."
            elif "click first link" in q or "click 1st link" in q or "click link" in q:
                pyautogui.press("tab")
                pyautogui.press("enter")
                return "Clicked first link in Chrome."

        # 2. SPOTIFY MODE
        elif mode == "SPOTIFY_MODE":
            if "play" in q or "pause" in q or "space" in q or "stop" in q:
                return sanitize_and_press_key("space")
            elif "next" in q or "skip" in q:
                return sanitize_and_press_key("ctrl,right")
            elif "previous" in q or "back" in q:
                return sanitize_and_press_key("ctrl,left")
            elif "volume up" in q:
                return sanitize_and_press_key("ctrl,up")
            elif "volume down" in q:
                return sanitize_and_press_key("ctrl,down")
            elif "shuffle" in q:
                return sanitize_and_press_key("ctrl,s")
            elif "search" in q:
                target = re.sub(r"\bsearch\b", "", q, count=1).strip()
                if not target:
                    return "Please specify what to search on Spotify."
                sanitize_and_press_key("ctrl,l")
                time.sleep(0.3)
                pyautogui.write(target, interval=0.01)
                pyautogui.press("enter")
                return f"Searched '{target}' on Spotify."

        # 3. NOTEPAD MODE
        elif mode == "NOTEPAD_MODE":
            if "new file" in q or "new document" in q:
                return sanitize_and_press_key("ctrl,n")
            elif "save" in q:
                return sanitize_and_press_key("ctrl,s")
            elif "select all" in q:
                return sanitize_and_press_key("ctrl,a")
            elif q.startswith("type ") or q.startswith("write "):
                text_to_type = re.sub(r"^(type|write)\s+", "", q)
                pyautogui.write(text_to_type, interval=0.01)
                return f"Typed '{text_to_type}' into Notepad."

        # 4. FL STUDIO MODE
        elif mode == "FL_STUDIO_MODE":
            if "piano roll" in q:
                return sanitize_and_press_key("f7")
            elif "playlist" in q:
                return sanitize_and_press_key("f5")
            elif "channel rack" in q:
                return sanitize_and_press_key("f6")
            elif "mixer" in q:
                return sanitize_and_press_key("f9")
            elif "play" in q or "pause" in q:
                return sanitize_and_press_key("space")
            elif "record" in q:
                return sanitize_and_press_key("r")

        return None


# Singleton instance
app_mode_manager = AppModeManager()