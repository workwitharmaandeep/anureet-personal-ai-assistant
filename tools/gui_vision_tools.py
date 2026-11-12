"""
Human-like GUI Automation, Screen Perception, and Mouse/Keyboard Control Module.
Provides robust window-focused typing, screen vision, link clicking, button clicking, profile picking, and key press tools.
"""

import base64
import ctypes
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config import config

logger = logging.getLogger("GUIVisionTools")
_ANUREET_WINDOW_TITLES = {"anureet widget", "anureet hud"}

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    pyautogui = None

try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = None
    ImageGrab = None


def _activate_hwnd(hwnd: int) -> bool:
    """Bring hwnd visibly to the foreground even from background pythonw processes."""
    if os.name != "nt" or not hasattr(ctypes, "windll") or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        cur_tid = kernel32.GetCurrentThreadId()
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        try:
            # Brief Alt tap relaxes Windows foreground lock when background-triggered
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            user32.BringWindowToTop(hwnd)
            ok = bool(user32.SetForegroundWindow(hwnd))
        finally:
            if attached:
                user32.AttachThreadInput(cur_tid, fg_tid, False)
        return ok
    except Exception as e:
        logger.debug(f"_activate_hwnd failed: {e}")
        return False


def ensure_window_focused(app_keyword: str) -> bool:
    """Ensure the target application window is focused on screen before typing."""
    res = focus_window(app_keyword)
    if res.get("status") == "success":
        time.sleep(0.25)
        return True
    return False


def capture_screen(output_filename: str = "current_screen.png") -> str:
    """Capture a screenshot of the laptop desktop into the configured screenshots directory."""
    try:
        safe_name = Path(output_filename).name or "current_screen.png"
        config.screenshots_dir.mkdir(parents=True, exist_ok=True)
        save_path = config.screenshots_dir / safe_name

        if ImageGrab:
            screenshot = ImageGrab.grab()
            screenshot.save(save_path)
            return f"Captured desktop screen (saved to 'uploads/screenshots/{save_path.name}')."
        elif pyautogui:
            pyautogui.screenshot(str(save_path))
            return f"Captured desktop screen (saved to 'uploads/screenshots/{save_path.name}')."
        else:
            return "Screen capture library not available."
    except Exception as e:
        return f"Screen capture error: {e}"


def click_first_link_or_element() -> str:
    """Simulate human action of clicking the first search result link or primary button in active browser/window."""
    if not pyautogui:
        return "PyAutoGUI not available."
    try:
        ensure_window_focused("Chrome")
        time.sleep(0.5)
        pyautogui.press("tab")
        pyautogui.press("enter")
        return "Clicked the first result link on screen."
    except Exception as e:
        return f"Click link error: {e}"


def click_screen(x: int = None, y: int = None, button: str = "left") -> str:
    """Click at screen coordinates (x, y) or at current cursor location."""
    if not pyautogui:
        return "PyAutoGUI not installed."
    try:
        if x is not None and y is not None:
            pyautogui.click(x, y, button=button)
            return f"Clicked {button} mouse button at ({x}, {y})."
        else:
            pyautogui.click(button=button)
            return f"Clicked {button} mouse button at current position."
    except Exception as e:
        return f"Mouse click error: {e}"


def type_text(text: str, press_enter: bool = True) -> str:
    """Type arbitrary text into the currently active window."""
    if not pyautogui:
        return "PyAutoGUI not installed."
    try:
        time.sleep(0.5)  # Brief delay to allow window focus
        pyautogui.write(text, interval=0.01)
        if press_enter:
            pyautogui.press("enter")
        return f"Typed '{text}' into the active window."
    except Exception as e:
        return f"Typing error: {e}"


def press_key(key_name: str) -> str:
    """Press a single key or shortcut (e.g. 'enter', 'tab', 'space', 'esc', 'f5', 'ctrl+s')."""
    if not pyautogui:
        return "PyAutoGUI not installed."
    try:
        key_clean = key_name.lower().strip()
        if "+" in key_clean or "," in key_clean:
            keys = [k.strip() for k in key_clean.replace("+", ",").split(",")]
            pyautogui.hotkey(*keys)
            return f"Pressed hotkey combination: {' + '.join(keys)}"
        else:
            pyautogui.press(key_clean)
            return f"Pressed '{key_clean}' key."
    except Exception as e:
        return f"Key press error: {e}"


def chrome_select_profile_and_search(query_or_url: str) -> str:
    """
    Simulates human action in Google Chrome:
    Focuses Chrome window, selects profile, focuses address bar (Ctrl+L),
    types the query or website, and presses Enter.
    """
    target = query_or_url.strip()
    if not pyautogui:
        webbrowser.open(f"https://www.google.com/search?q={urllib.parse.quote(target)}")
        return f"Navigated to search results for '{target}'."

    try:
        # Focus Chrome immediately if open; wait briefly if just launched
        if not ensure_window_focused("Chrome"):
            time.sleep(1.0)
            ensure_window_focused("Chrome")
        
        # 1. Press Enter in case Chrome profile picker dialog is shown
        pyautogui.press("enter")
        time.sleep(0.5)

        # 2. Focus Chrome Address/Search Bar (Ctrl + L)
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.25)

        # 3. Format URL or Google Search
        if target.startswith("http://") or target.startswith("https://") or target.startswith("www."):
            url = target if target.startswith("http") else "https://" + target
        else:
            url = f"https://www.google.com/search?q={urllib.parse.quote(target)}"

        # 4. Type URL/Search into Chrome address bar and press Enter
        pyautogui.write(url, interval=0.01)
        pyautogui.press("enter")

        return f"Selected Chrome profile, focused address bar, and navigated to '{target}'."
    except Exception as e:
        return f"Chrome navigation error: {e}"


def spotify_search_and_play(search_term: str) -> str:
    """
    Simulates human action in Spotify:
    Focuses Spotify window, presses Ctrl+L (search shortcut), types search term,
    presses Enter, and presses Space to start playback. Falls back to YouTube if unavailable.
    """
    clean_term = search_term.strip()
    yt_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(clean_term)}"

    if not pyautogui:
        webbrowser.open(yt_url)
        return f"Opened YouTube search for '{clean_term}'."

    try:
        if not ensure_window_focused("Spotify"):
            time.sleep(1.2)
            ensure_window_focused("Spotify")
        
        # 1. Focus Spotify Search Bar (Ctrl + L)
        pyautogui.hotkey("ctrl", "l")
        time.sleep(0.25)

        # 2. Type song/playlist name and press Enter
        pyautogui.write(clean_term, interval=0.01)
        pyautogui.press("enter")
        time.sleep(0.7)

        # 3. Press Enter / Space to start playback
        pyautogui.press("enter")
        pyautogui.press("space")

        return f"Searched '{clean_term}' on Spotify and started playback."
    except Exception as e:
        logger.warning(f"Spotify playback failed ({e}); opening YouTube fallback.")
        try:
            webbrowser.open(yt_url)
        except Exception:
            pass
        return f"Spotify playback error ({e}); opened YouTube search fallback for '{clean_term}'."


def list_open_windows() -> dict:
    """List titles of visible open desktop windows using PyGetWindow."""
    try:
        import pygetwindow as gw
        titles = [t.strip() for t in gw.getAllTitles() if t and t.strip()]
        seen = []
        for title in titles:
            if title not in seen:
                seen.append(title)
        return {
            "status": "success",
            "count": len(seen),
            "windows": seen[:25],
            "message": f"Open windows ({len(seen)}): {', '.join(seen[:12])}" if seen else "No titled open windows found.",
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to list open windows: {e}"}


def focus_window(title_query: str) -> dict:
    """Focus and restore an open desktop window whose title matches title_query."""
    query = re.sub(r'[.?!,;:"\']+$', "", (title_query or "").strip()).strip()
    if not query:
        return {"status": "error", "message": "Specify a window title to focus."}
    q_low = query.lower()
    q_compact = q_low.replace(" ", "")
    try:
        import pygetwindow as gw

        candidates = []
        for w in gw.getAllWindows():
            title = (w.title or "").strip()
            if not title or title.lower() in _ANUREET_WINDOW_TITLES:
                continue
            t_low = title.lower()
            if q_low in t_low or (len(q_compact) >= 3 and q_compact in t_low.replace(" ", "")):
                candidates.append(w)

        if not candidates:
            return {"status": "error", "message": f"No open window matching '{query}' found."}

        # Prefer non-minimized visible window, or first match
        win = next((w for w in candidates if not getattr(w, "isMinimized", False)), candidates[0])
        hwnd = getattr(win, "_hWnd", None)
        if getattr(win, "isMinimized", False):
            win.restore()
        activated = _activate_hwnd(int(hwnd)) if hwnd else False
        if not activated:
            try:
                win.activate()
            except Exception:
                pass
        return {
            "status": "success",
            "title": win.title,
            "hwnd": hwnd,
            "message": f"Switched focus to window: {win.title}",
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to focus window '{query}': {e}"}


def _get_target_hwnd(app_name: str = "") -> Tuple[Optional[int], str, str]:
    """Return (hwnd, title, error_message) for target app or top visible non-Anureet window."""
    clean_app = re.sub(r'[.?!,;:"\']+$', "", (app_name or "").strip()).strip()
    if clean_app:
        res = focus_window(clean_app)
        if res.get("status") != "success":
            return None, "", res.get("message", f"Could not focus '{clean_app}'.")
        time.sleep(0.25)
        hwnd = res.get("hwnd")
        if hwnd:
            return int(hwnd), str(res.get("title", clean_app)), ""

    if os.name == "nt" and hasattr(ctypes, "windll"):
        user32 = ctypes.windll.user32
        fg = user32.GetForegroundWindow()
        if fg:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(fg, buf, 256)
            fg_title = buf.value.strip()
            if fg_title and fg_title.lower() not in _ANUREET_WINDOW_TITLES:
                return int(fg), fg_title, ""

        # If HUD/widget was focused, pick topmost non-Anureet visible window
        try:
            import pygetwindow as gw
            for w in gw.getAllWindows():
                title = (w.title or "").strip()
                if title and title.lower() not in _ANUREET_WINDOW_TITLES and not getattr(w, "isMinimized", False):
                    if getattr(w, "width", 0) > 120 and getattr(w, "height", 0) > 120 and getattr(w, "_hWnd", None):
                        _activate_hwnd(int(w._hWnd))
                        time.sleep(0.2)
                        return int(w._hWnd), title, ""
        except Exception:
            pass
    return None, "", "No active application window found on screen."


def _get_window_rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    if os.name != "nt" or not hasattr(ctypes, "windll") or not hwnd:
        return None
    rect = (ctypes.c_long * 4)()
    if ctypes.windll.user32.GetWindowRect(int(hwnd), rect):
        return int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
    return None


def _run_hidden_ps(ps_script: str, timeout: int = 10) -> str:
    encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    res = subprocess.run(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if res.returncode != 0:
        raise RuntimeError((res.stderr or res.stdout or f"PowerShell exited {res.returncode}").strip())
    return res.stdout.strip()


def _find_uia_element_center(hwnd: int, target_text: str) -> Optional[Tuple[int, int]]:
    """Use Windows UI Automation to locate a button/link/input matching target_text inside hwnd."""
    if os.name != "nt":
        return None
    escaped = target_text.replace("'", "''")
    ps = (
        "$ErrorActionPreference = 'Stop'; "
        "Add-Type -AssemblyName UIAutomationClient; "
        "Add-Type -AssemblyName UIAutomationTypes; "
        f"$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]{int(hwnd)}); "
        "$cond = [System.Windows.Automation.Condition]::TrueCondition; "
        "$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond); "
        f"$needle = '{escaped}'.ToLower(); "
        "$tokens = $needle -split '\\s+' | Where-Object { $_.Length -ge 2 }; "
        "$exact = $null; $partial = $null; $tokHit = $null; "
        "foreach ($el in $all) { "
        "  try { "
        "    if ($el.Current.IsOffscreen -or -not $el.Current.IsEnabled) { continue }; "
        "    $name = ($el.Current.Name + '').Trim(); "
        "    if (-not $name) { continue }; "
        "    $rect = $el.Current.BoundingRectangle; "
        "    if ([double]::IsInfinity($rect.Width) -or $rect.Width -lt 4 -or $rect.Height -lt 4) { continue }; "
        "    $nLow = $name.ToLower(); "
        "    $cx = [int][math]::Round($rect.Left + $rect.Width / 2); "
        "    $cy = [int][math]::Round($rect.Top + $rect.Height / 2); "
        "    if ($nLow -eq $needle) { $exact = @{x=$cx; y=$cy}; break }; "
        "    if (-not $partial -and $nLow.Contains($needle)) { $partial = @{x=$cx; y=$cy} }; "
        "    if (-not $tokHit -and $tokens.Count -gt 0) { "
        "      $allOk = $true; foreach ($t in $tokens) { if (-not $nLow.Contains($t)) { $allOk = $false; break } }; "
        "      if ($allOk) { $tokHit = @{x=$cx; y=$cy} } "
        "    } "
        "  } catch {} "
        "}; "
        "$best = if ($exact) { $exact } elseif ($partial) { $partial } else { $tokHit }; "
        "if ($best) { $best | ConvertTo-Json -Compress }"
    )
    try:
        out = _run_hidden_ps(ps, timeout=8)
        if out:
            data = json.loads(out)
            if isinstance(data, dict) and "x" in data and "y" in data:
                return int(data["x"]), int(data["y"])
    except Exception as e:
        logger.debug(f"UIA element lookup skipped: {e}")
    return None


def _find_ocr_text_center(hwnd: int, target_text: str) -> Optional[Tuple[int, int]]:
    """Fallback offline Windows WinRT OCR lookup returning screen (x, y) of matching text in hwnd."""
    if os.name != "nt" or ImageGrab is None:
        return None
    rect = _get_window_rect(hwnd)
    if not rect:
        return None
    left, top, right, bottom = rect
    if right - left < 20 or bottom - top < 20:
        return None

    png_path = None
    try:
        img = ImageGrab.grab(bbox=(left, top, right, bottom))
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            png_path = tmp.name
        img.save(png_path)

        png_esc = png_path.replace("'", "''")
        needle_esc = target_text.replace("'", "''")
        ps = (
            "$ErrorActionPreference = 'Stop'; "
            "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
            "[Windows.Media.Ocr.OcrEngine, Windows.Media.Ocr, ContentType=WindowsRuntime] | Out-Null; "
            "[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null; "
            "[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null; "
            "[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null; "
            "[Windows.Storage.FileAccessMode, Windows.Storage, ContentType=WindowsRuntime] | Out-Null; "
            "$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | "
            "Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]; "
            "Function Await($op, $resType) { $t = $asTaskGeneric.MakeGenericMethod($resType).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }; "
            f"$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('{png_esc}')) ([Windows.Storage.StorageFile]); "
            "$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream]); "
            "$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder]); "
            "$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap]); "
            "$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages(); "
            "$ocrRes = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult]); "
            f"$needle = '{needle_esc}'.ToLower().Trim(); "
            "$tokens = @($needle -split '\\s+' | Where-Object { $_.Length -ge 2 }); "
            "$best = $null; "
            "foreach ($line in $ocrRes.Lines) { "
            "  $lText = ($line.Text + '').ToLower(); "
            "  $hit = $lText.Contains($needle); "
            "  if (-not $hit -and $tokens.Count -gt 0) { "
            "    $hit = $true; foreach ($tk in $tokens) { if (-not $lText.Contains($tk)) { $hit = $false; break } } "
            "  }; "
            "  if ($hit) { "
            "    $words = @($line.Words | Where-Object { $wLow = ($_.Text + '').ToLower(); $matched = $false; foreach ($tk in $tokens) { if ($wLow.Contains($tk) -or $tk.Contains($wLow)) { $matched = $true } }; $matched }); "
            "    if ($words.Count -eq 0) { $words = @($line.Words) }; "
            "    if ($words.Count -gt 0) { "
            "      $minX = ($words | ForEach-Object { $_.BoundingRect.Left } | Measure-Object -Minimum).Minimum; "
            "      $minY = ($words | ForEach-Object { $_.BoundingRect.Top } | Measure-Object -Minimum).Minimum; "
            "      $maxX = ($words | ForEach-Object { $_.BoundingRect.Left + $_.BoundingRect.Width } | Measure-Object -Maximum).Maximum; "
            "      $maxY = ($words | ForEach-Object { $_.BoundingRect.Top + $_.BoundingRect.Height } | Measure-Object -Maximum).Maximum; "
            "      $best = @{x=[int][math]::Round(($minX + $maxX) / 2); y=[int][math]::Round(($minY + $maxY) / 2)}; "
            "      break "
            "    } "
            "  } "
            "}; "
            "$stream.Dispose(); "
            "if ($best) { $best | ConvertTo-Json -Compress }"
        )
        out = _run_hidden_ps(ps, timeout=10)
        if out:
            data = json.loads(out)
            if isinstance(data, dict) and "x" in data and "y" in data:
                return left + int(data["x"]), top + int(data["y"])
    except Exception as e:
        logger.debug(f"WinRT OCR element lookup skipped: {e}")
    finally:
        if png_path and os.path.exists(png_path):
            try:
                os.remove(png_path)
            except OSError:
                pass
    return None


def click_screen_element(target_text: str, app_name: str = "") -> Dict[str, Any]:
    """Locate a named button, menu, tab, or text element via Windows UIA + WinRT OCR and click it."""
    if not pyautogui:
        return {"status": "error", "message": "PyAutoGUI not installed."}
    clean_target = re.sub(r'[.?!,;:"\']+$', "", (target_text or "").strip()).strip()
    if not clean_target:
        return {"status": "error", "message": "Specify which on-screen button or text to click."}

    hwnd, win_title, err = _get_target_hwnd(app_name)
    if hwnd is None:
        return {"status": "error", "message": err}

    coords = _find_uia_element_center(hwnd, clean_target)
    method = "uia"
    if coords is None:
        coords = _find_ocr_text_center(hwnd, clean_target)
        method = "ocr"

    if coords is None:
        return {
            "status": "error",
            "window": win_title,
            "message": f"Could not find '{clean_target}' visible on screen in '{win_title}'.",
        }

    x, y = coords
    pyautogui.click(x, y)
    return {
        "status": "success",
        "method": method,
        "coords": [x, y],
        "window": win_title,
        "message": f"Clicked '{clean_target}' in '{win_title}' at ({x}, {y}).",
    }


def browser_tab_action(action: str, target: str = "") -> str:
    """Switch, open, or close tabs in Google Chrome / Edge cleanly."""
    if not pyautogui:
        return "PyAutoGUI not installed."
    act = (action or "").lower().strip()
    clean_target = re.sub(r'[.?!,;:"\']+$', "", (target or "").strip()).strip()

    focus_res = focus_window("Chrome")
    if focus_res.get("status") != "success":
        focus_res = focus_window("Edge")
    if focus_res.get("status") != "success":
        return "No open Chrome or Edge browser window found."
    time.sleep(0.2)

    if act in {"next", "switch", "next_tab"}:
        pyautogui.hotkey("ctrl", "tab")
        return "Switched to the next browser tab."
    if act in {"prev", "previous", "prev_tab"}:
        pyautogui.hotkey("ctrl", "shift", "tab")
        return "Switched to the previous browser tab."
    if act in {"new", "new_tab"}:
        pyautogui.hotkey("ctrl", "t")
        return "Opened a new browser tab."
    if act in {"close", "close_tab"}:
        pyautogui.hotkey("ctrl", "w")
        return "Closed the active browser tab."
    if act in {"reopen", "reopen_tab"}:
        pyautogui.hotkey("ctrl", "shift", "t")
        return "Reopened the last closed tab."
    if act in {"switch_to", "find"} and clean_target:
        # Check if an open tab/window title already matches directly first
        direct = focus_window(clean_target)
        if direct.get("status") == "success":
            return str(direct.get("message"))
        focus_window("Chrome")
        time.sleep(0.15)
        pyautogui.hotkey("ctrl", "shift", "a")
        time.sleep(0.28)
        pyautogui.write(clean_target, interval=0.01)
        time.sleep(0.15)
        pyautogui.press("enter")
        return f"Switched browser tab matching '{clean_target}'."
    return f"Unsupported browser tab action '{action}'."


def type_in_search_bar(query_text: str, app_hint: str = "") -> str:
    """Focus a search box (e.g. YouTube/active app search bar), type query_text, and submit."""
    if not pyautogui:
        return "PyAutoGUI not installed."
    clean_query = re.sub(r'[.?!]+$', "", (query_text or "").strip()).strip()
    if not clean_query:
        return "Specify what text to search or type."

    hint = re.sub(r'[.?!,;:"\']+$', "", (app_hint or "").strip().lower()).strip()
    hwnd, win_title, err = _get_target_hwnd(hint if hint and hint != "youtube" else ("YouTube" if hint == "youtube" else ""))
    if hint == "youtube" and hwnd is None:
        # YouTube might be a background tab inside Chrome
        browser_tab_action("switch_to", "youtube")
        time.sleep(0.35)
        hwnd, win_title, err = _get_target_hwnd("Chrome")

    if hwnd is None:
        return err

    title_low = win_title.lower()
    focused_search = False

    if "youtube" in title_low or hint == "youtube":
        # Click inside viewport safely away from top tabs? Better: press Escape then '/' shortcut!
        pyautogui.press("escape")
        time.sleep(0.1)
        pyautogui.press("/")
        focused_search = True
    else:
        for candidate in ("Search", "Search YouTube", "Search Google", "Type to search"):
            hit = _find_uia_element_center(hwnd, candidate) or _find_ocr_text_center(hwnd, candidate)
            if hit:
                pyautogui.click(hit[0], hit[1])
                focused_search = True
                break
        if not focused_search and ("chrome" in title_low or "edge" in title_low):
            pyautogui.hotkey("ctrl", "e")
            focused_search = True

    if not focused_search:
        return f"Could not locate a search bar inside '{win_title}'."

    time.sleep(0.18)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.press("backspace")
    pyautogui.write(clean_query, interval=0.01)
    pyautogui.press("enter")
    return f"Typed '{clean_query}' into search bar in '{win_title}'."