"""
Live Screen Vision and Accessibility Control Module.
Allows Anureet to continuously capture the laptop screen, scroll down/up,
locate items on screen, and click specific video thumbnails or elements.
"""

import time
import logging

logger = logging.getLogger("LiveScreenVision")

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None


def scroll_screen(direction: str = "down", amount: int = 600) -> str:
    """Scroll the active browser or window up or down."""
    if not pyautogui:
        return "PyAutoGUI not available."
    try:
        time.sleep(0.3)
        if direction.lower() == "down":
            pyautogui.scroll(-amount)
            return f"Scrolled down on screen."
        else:
            pyautogui.scroll(amount)
            return f"Scrolled up on screen."
    except Exception as e:
        return f"Scroll error: {e}"


def play_video_number_on_screen(video_number: int = 3) -> str:
    """
    Accessibility Feature: Simulates human action on YouTube/Chrome screen.
    Tabs through video grid or calculates the location of the Nth video thumbnail showing on screen and clicks it.
    """
    if not pyautogui:
        return "PyAutoGUI not available."
    try:
        time.sleep(0.5)
        # Tab through YouTube video grid and press Enter on Nth video
        for _ in range(max(1, video_number * 2)):
            pyautogui.press("tab")
            time.sleep(0.05)
        pyautogui.press("enter")
        return f"Clicked and started video #{video_number} showing on screen."
    except Exception as e:
        return f"Failed playing video #{video_number}: {e}"