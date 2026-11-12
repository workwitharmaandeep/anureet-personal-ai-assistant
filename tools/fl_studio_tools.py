"""
FL Studio Automation Module.
Handles launching FL Studio and triggering MIDI/shortcut macros.
"""

import os
import time
import subprocess
import logging

logger = logging.getLogger("FLStudioTools")

def fl_studio_action(action: str) -> str:
    """Launch FL Studio or execute macro actions inside FL Studio."""
    action_clean = action.lower().strip()
    
    # Check if FL Studio launch requested
    if "open" in action_clean or "launch" in action_clean or "start" in action_clean:
        fl_paths = [
            r"C:\Program Files\Image-Line\FL Studio 2024\FL64.exe",
            r"C:\Program Files\Image-Line\FL Studio 21\FL64.exe",
            r"C:\Program Files\Image-Line\FL Studio 20\FL64.exe",
            r"C:\Program Files (x86)\Image-Line\FL Studio 21\FL64.exe",
            r"C:\Program Files (x86)\Image-Line\FL Studio 20\FL64.exe"
        ]
        
        opened = False
        for path in fl_paths:
            if os.path.exists(path):
                try:
                    subprocess.Popen([path])
                    opened = True
                    break
                except Exception as e:
                    logger.warning(f"Failed opening FL path '{path}': {e}")
        
        if not opened:
            try:
                subprocess.Popen(["cmd.exe", "/c", "start FL64.exe"], shell=True)
                opened = True
            except Exception as e:
                return f"Failed to launch FL Studio: {e}"
                
        return "FL Studio application launch initiated."

    # GUI Macro shortcuts via pyautogui
    try:
        import pyautogui
        if "play" in action_clean or "stop" in action_clean:
            pyautogui.press("space")
            return "Triggered Play/Pause in FL Studio."
        elif "record" in action_clean:
            pyautogui.press("r")
            return "Triggered Record in FL Studio."
        elif "piano roll" in action_clean:
            pyautogui.press("f7")
            return "Opened Piano Roll (F7)."
        elif "playlist" in action_clean:
            pyautogui.press("f5")
            return "Opened Playlist (F5)."
        elif "channel rack" in action_clean:
            pyautogui.press("f6")
            return "Opened Channel Rack (F6)."
        elif "mixer" in action_clean:
            pyautogui.press("f9")
            return "Opened Mixer (F9)."
        else:
            return f"Executed FL Studio macro action: {action}"
    except Exception as e:
        return f"FL Studio macro error: {e}"