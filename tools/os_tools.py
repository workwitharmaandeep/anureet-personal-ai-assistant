"""
OS and Application automation tools.
Handles physically launching/closing applications (Notepad, Chrome, FL Studio, Premiere Pro, After Effects, Spotify, YouTube, Explorer, Task Manager, Word),
window activation, typing text into active applications, searching web, and OS control commands.
"""

import ctypes
import difflib
import json
import os
import re
import shutil
import threading
import time
import subprocess
import urllib.parse
import webbrowser
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("OSTools")

# System32 CMD Path to prevent "cmd.exe not recognized" errors
CMD_PATH = os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")

_APPS_LOCK = threading.Lock()
_APPS_CACHE: Optional[tuple[float, List[Dict[str, str]]]] = None
_APPS_CACHE_TTL = 300.0

URI_APP_ALIASES = {
    "settings": ("ms-settings:", "Windows Settings"),
    "windows settings": ("ms-settings:", "Windows Settings"),
    "calculator": ("calc.exe", "Calculator"),
    "calc": ("calc.exe", "Calculator"),
    "camera": ("microsoft.windows.camera:", "Camera"),
    "paint": ("mspaint.exe", "Paint"),
    "clock": ("ms-clock:", "Clock"),
    "store": ("ms-windows-store:", "Microsoft Store"),
    "microsoft store": ("ms-windows-store:", "Microsoft Store"),
    "control panel": ("control.exe", "Control Panel"),
    "snipping tool": ("snippingtool.exe", "Snipping Tool"),
    "terminal": ("wt.exe", "Windows Terminal"),
    "cmd": ("cmd.exe", "Command Prompt"),
    "command prompt": ("cmd.exe", "Command Prompt"),
}

APP_PATHS = {
    "notepad": ["notepad.exe"],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "chrome.exe"
    ],
    "file explorer": ["explorer.exe"],
    "explorer": ["explorer.exe"],
    "task manager": ["taskmgr.exe"],
    "word": ["winword.exe"],
    "ms word": ["winword.exe"],
    "excel": ["excel.exe"],
    "powerpoint": ["powerpnt.exe"],
    "spotify": [
        os.path.expanduser(r"~\AppData\Roaming\Spotify\Spotify.exe"),
        r"C:\Program Files\Spotify\Spotify.exe",
        "spotify.exe"
    ],
    "fl studio": [
        r"C:\Program Files\Image-Line\FL Studio 2024\FL64.exe",
        r"C:\Program Files\Image-Line\FL Studio 21\FL64.exe",
        r"C:\Program Files\Image-Line\FL Studio 20\FL64.exe",
        r"C:\Program Files (x86)\Image-Line\FL Studio 21\FL64.exe",
        r"C:\Program Files (x86)\Image-Line\FL Studio 20\FL64.exe",
        "FL64.exe"
    ],
    "premiere": [
        r"C:\Program Files\Adobe\Adobe Premiere Pro 2024\Adobe Premiere Pro.exe",
        r"C:\Program Files\Adobe\Adobe Premiere Pro 2023\Adobe Premiere Pro.exe",
        r"C:\Program Files\Adobe\Adobe Premiere Pro CC\Adobe Premiere Pro.exe"
    ],
    "after effects": [
        r"C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\AfterFX.exe",
        r"C:\Program Files\Adobe\Adobe After Effects 2023\Support Files\AfterFX.exe",
        "AfterFX.exe"
    ],
    "vscode": [
        os.path.expanduser(r"~\AppData\Local\Programs\Microsoft VS Code\Code.exe"),
        r"C:\Program Files\Microsoft VS Code\Code.exe"
    ]
}

APP_PROCESS_NAMES = {
    "notepad": "notepad.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "spotify": "spotify.exe",
    "fl studio": "FL64.exe",
    "after effects": "AfterFX.exe",
    "premiere": "Adobe Premiere Pro.exe",
    "explorer": "explorer.exe",
    "task manager": "taskmgr.exe",
    "capcut": "CapCut.exe",
    "cap cut": "CapCut.exe",
    "word": "WINWORD.EXE",
    "ms word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE",
    "vscode": "Code.exe",
    "vs code": "Code.exe",
    "edge": "msedge.exe",
    "vlc": "vlc.exe",
    "whatsapp": "WhatsApp.exe",
    "discord": "Discord.exe",
}

_PROTECTED_PROCESSES = {
    "explorer.exe",
    "antigravity.exe",
    "code.exe",
    "windowsterminal.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "python.exe",
    "pythonw.exe",
    "ollama.exe",
    "dwm.exe",
    "csrss.exe",
    "svchost.exe",
    "winlogon.exe",
    "system",
}

_SESSION_APPS_LOCK = threading.Lock()
_SESSION_OPENED_APPS: set[str] = set()


def _record_opened_app(name_or_hint: str) -> None:
    clean = (name_or_hint or "").strip().lower()
    if clean and clean not in {"explorer", "file explorer"}:
        with _SESSION_APPS_LOCK:
            _SESSION_OPENED_APPS.add(clean)


def _get_installed_apps_index(force_refresh: bool = False) -> List[Dict[str, str]]:
    """Return cached list of installed Windows Start Menu / Store apps via Get-StartApps."""
    global _APPS_CACHE
    now = time.monotonic()
    with _APPS_LOCK:
        if not force_refresh and _APPS_CACHE is not None and (now - _APPS_CACHE[0]) < _APPS_CACHE_TTL:
            return list(_APPS_CACHE[1])

    apps: List[Dict[str, str]] = []
    if os.name == "nt":
        try:
            res = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=8,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                parsed = json.loads(res.stdout.strip())
                if isinstance(parsed, dict):
                    parsed = [parsed]
                if isinstance(parsed, list):
                    for item in parsed:
                        if not isinstance(item, dict):
                            continue
                        name = str(item.get("Name") or "").strip()
                        app_id = str(item.get("AppID") or "").strip()
                        if name and app_id:
                            apps.append({"name": name, "app_id": app_id})
        except Exception as e:
            logger.debug(f"Get-StartApps query skipped: {e}")

    with _APPS_LOCK:
        _APPS_CACHE = (now, apps)
    return list(apps)


def _match_installed_app(query: str) -> Optional[Dict[str, str]]:
    """Find best matching installed Windows app using exact, token, and fuzzy matching."""
    cleaned = re.sub(r"[^a-z0-9\s.+_-]", " ", (query or "").lower()).strip()
    cleaned = re.sub(r"\b(?:app|application|software|program)\b", "", cleaned).strip()
    if not cleaned or len(cleaned) < 2:
        return None

    apps = _get_installed_apps_index()
    if not apps:
        return None

    # 1. Exact lowercase or compact (space-stripped) match
    compact_q = cleaned.replace(" ", "")
    for item in apps:
        low_name = item["name"].lower()
        if low_name == cleaned or (len(compact_q) >= 3 and low_name.replace(" ", "") == compact_q):
            return item

    # 2. All query tokens appear inside app name (prefer shortest name)
    tokens = [t for t in cleaned.split() if len(t) >= 2]
    if tokens:
        token_hits = [
            item for item in apps
            if all(re.search(rf"(?<!\w){re.escape(tok)}", item["name"].lower()) for tok in tokens)
        ]
        if token_hits:
            return min(token_hits, key=lambda it: len(it["name"]))

    # 3. Fuzzy closest match over full names, compact names, and individual words
    lower_names = {it["name"].lower(): it for it in apps}
    close = difflib.get_close_matches(cleaned, list(lower_names.keys()), n=1, cutoff=0.72)
    if close:
        return lower_names[close[0]]

    if len(compact_q) >= 4:
        compact_map = {it["name"].lower().replace(" ", ""): it for it in apps}
        close_compact = difflib.get_close_matches(compact_q, list(compact_map.keys()), n=1, cutoff=0.72)
        if close_compact:
            return compact_map[close_compact[0]]

        word_map: Dict[str, Dict[str, str]] = {}
        for it in apps:
            for w in re.findall(r"[a-z0-9]{4,}", it["name"].lower()):
                word_map.setdefault(w, it)
        close_word = difflib.get_close_matches(compact_q, list(word_map.keys()), n=1, cutoff=0.78)
        if close_word:
            return word_map[close_word[0]]

    # 4. Partial substring match on single keywords >= 4 chars
    if len(cleaned) >= 4:
        for n_low, item in lower_names.items():
            if cleaned in n_low and not n_low.endswith("help"):
                return item
    return None


def list_installed_apps(query: str = "", limit: int = 25) -> Dict[str, Any]:
    """List or search installed applications on the Windows laptop."""
    apps = _get_installed_apps_index()
    q = (query or "").strip().lower()
    if q:
        filtered = [a for a in apps if q in a["name"].lower()]
    else:
        filtered = apps
    names = sorted({a["name"] for a in filtered})[: max(1, min(limit, 60))]
    return {
        "status": "success",
        "total_installed": len(apps),
        "matched_count": len(filtered),
        "apps": names,
        "summary": (
            f"Found {len(filtered)} installed app(s)"
            + (f" matching '{query}': " if q else " (showing sample): ")
            + ", ".join(names[:12])
        )
        if names
        else f"No installed apps matched '{query}'.",
    }


def _launch_single_application(app_name: str) -> str:
    """Launch a single application, website, UWP/Store app, or Start Menu app on Windows."""
    app_key = (app_name or "").lower().strip()
    if not app_key:
        return "Specify which application to open."
    logger.info(f"Physically launching application: '{app_name}'")

    if "youtube" in app_key:
        webbrowser.open("https://www.youtube.com")
        _record_opened_app("chrome")
        return "Opened YouTube in Google Chrome."
    elif "uiet" in app_key:
        webbrowser.open("https://www.uietkuk.ac.in")
        _record_opened_app("chrome")
        return "Opened UIET Kurukshetra website in Chrome."

    # 1. Built-in Windows URI schemes & utilities
    for alias, (target, label) in URI_APP_ALIASES.items():
        if alias == app_key or re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", app_key):
            try:
                if target.endswith(".exe"):
                    resolved = shutil.which(target) or target
                    subprocess.Popen([resolved], shell=False)
                else:
                    subprocess.Popen([CMD_PATH, "/c", "start", "", target], shell=False)
                _record_opened_app(label)
                return f"Opened {label}."
            except Exception as e:
                logger.warning(f"Alias launch failed for '{alias}': {e}")

    # 2. Curated executable paths
    matched_paths = None
    for key, paths in APP_PATHS.items():
        if key in app_key or app_key in key:
            matched_paths = paths
            break

    if matched_paths:
        for path in matched_paths:
            resolved = path if (os.path.isabs(path) and os.path.exists(path)) else shutil.which(path)
            if resolved:
                try:
                    subprocess.Popen([resolved], shell=False)
                    _record_opened_app(app_key)
                    return f"Opened {app_name} on your screen."
                except Exception as e:
                    logger.warning(f"Failed opening path '{resolved}': {e}")

    # 3. Dynamic Windows Start Menu / Microsoft Store AppID discovery (164+ apps)
    start_app = _match_installed_app(app_key)
    if start_app:
        try:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{start_app['app_id']}"], shell=False)
            _record_opened_app(start_app["name"])
            return f"Opened {start_app['name']} on your laptop."
        except Exception as e:
            logger.warning(f"AppsFolder launch failed for {start_app['name']}: {e}")

    # 4. Known Windows shell protocols / PATH executables
    shell_targets = [
        (("spotify", "music"), "spotify:", "Opening Spotify on your laptop..."),
        (("fl studio",), "FL64.exe", "Opening FL Studio..."),
        (("chrome", "browser"), "chrome", "Opening Google Chrome..."),
        (("after effect",), "afterfx", "Opening Adobe After Effects..."),
        (("explorer",), "explorer.exe", "Opening File Explorer..."),
        (("task manager",), "taskmgr.exe", "Opening Task Manager..."),
        (("word",), "winword.exe", "Opening Microsoft Word..."),
    ]
    try:
        for keywords, target, msg in shell_targets:
            if any(k in app_key for k in keywords):
                subprocess.Popen([CMD_PATH, "/c", "start", "", target], shell=False)
                _record_opened_app(keywords[0])
                return msg

        safe_name = re.sub(r'[&|><^%\r\n"`$;]', "", app_name).strip()
        if not safe_name:
            return f"Invalid application name: '{app_name}'."
        exe_resolved = shutil.which(safe_name) or shutil.which(f"{safe_name}.exe")
        if exe_resolved:
            subprocess.Popen([exe_resolved], shell=False)
            _record_opened_app(safe_name)
            return f"Opened {safe_name} on your desktop."
        return f"Could not find installed app '{safe_name}'. Say 'list installed apps' to see available apps."
    except Exception as e:
        return f"Could not open {app_name}: {e}"


def open_application(app_name: str) -> str:
    """Launch one or more installed applications or websites on Windows."""
    raw = (app_name or "").strip()
    if not raw:
        return "Specify an application name to open."

    low = raw.lower()
    if any(tok in low for tok in ("all app", "all application", "every app", "sabhi app", "saare app")):
        apps = _get_installed_apps_index()
        sample = ", ".join(a["name"] for a in apps[:12])
        return (
            f"Safety check: opening all {len(apps)} installed apps at once would freeze Windows. "
            f"Tell me the specific apps (e.g. 'open chrome and spotify'). Sample apps: {sample}"
        )

    parts = [p.strip() for p in re.split(r"\s*(?:,|&|\band\b|\baur\b)\s*", raw) if p.strip()]
    if len(parts) > 1:
        results = [_launch_single_application(p) for p in parts[:6]]
        return " | ".join(results)
    return _launch_single_application(raw)


def _find_running_app_pids(app_query: str) -> List[tuple[int, str, str]]:
    """Enumerate visible top-level windows matching app_query -> list of (pid, proc_exe, title)."""
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        return []
    clean_q = re.sub(r'[.?!,;:"\']+', "", (app_query or "").lower()).strip()
    compact_q = clean_q.replace(" ", "")
    if not clean_q or len(clean_q) < 2:
        return []

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    found: List[tuple[int, str, str]] = []
    seen_pids: set[int] = set()

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
        pid_val = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_val))
        pid = int(pid_val.value)
        if not pid or pid in seen_pids:
            return True

        proc_exe = ""
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            try:
                pbuf = ctypes.create_unicode_buffer(512)
                psize = ctypes.c_ulong(512)
                if kernel32.QueryFullProcessImageNameW(handle, 0, pbuf, ctypes.byref(psize)):
                    proc_exe = os.path.basename(pbuf.value)
            finally:
                kernel32.CloseHandle(handle)

        proc_low = proc_exe.lower()
        if proc_low in _PROTECTED_PROCESSES:
            return True
        proc_stem = proc_low[:-4] if proc_low.endswith(".exe") else proc_low
        title_low = title.lower()
        title_compact = title_low.replace(" ", "")
        if (
            clean_q in title_low
            or (len(compact_q) >= 3 and compact_q in title_compact)
            or clean_q in proc_stem
            or (len(compact_q) >= 3 and compact_q in proc_stem.replace(" ", ""))
        ):
            seen_pids.add(pid)
            found.append((pid, proc_exe or clean_q, title))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return found


def close_application(app_name: str) -> str:
    """Close a running application physically on Windows using known aliases or live window/process discovery."""
    raw_name = (app_name or "").strip(" .?!,;:\"'")
    app_key = raw_name.lower()
    if not app_key:
        return "Specify which application to close."

    target_proc = None
    for key, proc in APP_PROCESS_NAMES.items():
        if key == app_key or key in app_key or app_key in key or (
            len(app_key.replace(" ", "")) >= 3 and key.replace(" ", "") == app_key.replace(" ", "")
        ):
            target_proc = proc
            break

    hide_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    # 1. Try known process image name first
    if target_proc and target_proc.lower() not in _PROTECTED_PROCESSES:
        try:
            res = subprocess.run(
                ["taskkill", "/f", "/t", "/im", target_proc],
                capture_output=True,
                text=True,
                check=False,
                creationflags=hide_flags,
            )
            if res.returncode == 0:
                return f"Closed {raw_name}."
        except Exception as e:
            logger.debug(f"taskkill /im failed for {target_proc}: {e}")

    # 2. Fallback: match active visible windows / running executable names by PID
    pid_matches = _find_running_app_pids(raw_name)
    closed_labels: List[str] = []
    for pid, proc_exe, _title in pid_matches:
        try:
            res = subprocess.run(
                ["taskkill", "/f", "/t", "/pid", str(pid)],
                capture_output=True,
                text=True,
                check=False,
                creationflags=hide_flags,
            )
            if res.returncode == 0:
                closed_labels.append(proc_exe)
        except Exception:
            pass

    if closed_labels:
        return f"Closed {raw_name} ({', '.join(dict.fromkeys(closed_labels))})."

    # 3. Last resort direct executable name guess (strip invalid chars/spaces)
    clean_key = re.sub(r'[&|><^%\r\n"`$;]', "", app_key).strip()
    if clean_key:
        guess_proc = clean_key if clean_key.endswith(".exe") else f"{clean_key.replace(' ', '')}.exe"
        if guess_proc.lower() not in _PROTECTED_PROCESSES:
            try:
                res = subprocess.run(
                    ["taskkill", "/f", "/t", "/im", guess_proc],
                    capture_output=True,
                    text=True,
                    check=False,
                    creationflags=hide_flags,
                )
                if res.returncode == 0:
                    return f"Closed {raw_name}."
            except Exception:
                pass

    return f"Could not find any running application matching '{raw_name}'."


def close_session_apps() -> Dict[str, Any]:
    """Close only user applications that Anureet opened during the current session."""
    with _SESSION_APPS_LOCK:
        targets = list(_SESSION_OPENED_APPS)

    closed: List[str] = []
    not_running: List[str] = []
    for app_hint in targets:
        msg = close_application(app_hint)
        if msg.startswith("Closed "):
            closed.append(app_hint)
        else:
            not_running.append(app_hint)

    with _SESSION_APPS_LOCK:
        _SESSION_OPENED_APPS.clear()

    if closed:
        summary = f"Closed session apps: {', '.join(closed)}."
    elif targets:
        summary = "No session-opened apps are still running."
    else:
        summary = "No apps were opened by Anureet in this session."
    return {
        "status": "success",
        "closed": closed,
        "already_closed": not_running,
        "message": summary,
    }


def save_notepad_file_as(filename: str) -> str:
    """Save current open Notepad document with a specific filename."""
    try:
        import pyautogui
        time.sleep(0.5)
        pyautogui.hotkey("ctrl", "s")
        time.sleep(0.5)
        clean_name = filename.strip()
        if not clean_name.endswith(".txt"):
            clean_name += ".txt"
        pyautogui.write(clean_name, interval=0.01)
        pyautogui.press("enter")
        return f"Saved file as '{clean_name}'."
    except Exception as e:
        return f"Save file error: {e}"


def open_website_or_search(query_or_url: str) -> str:
    """Open a URL or perform a search in Google Chrome."""
    query = query_or_url.strip()
    if query.startswith("http://") or query.startswith("https://") or query.startswith("www."):
        url = query if query.startswith("http") else "https://" + query
    else:
        url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"

    try:
        webbrowser.open(url)
        return f"Opened {url} in Google Chrome."
    except Exception as e:
        return f"Failed to open browser: {e}"


def write_to_notepad(text: str, filename: str = "meeting_notes.txt") -> str:
    """Write text to a file and open Notepad to view it."""
    safe_name = Path(filename).name or "meeting_notes.txt"
    if not safe_name.endswith(".txt"):
        safe_name += ".txt"
    try:
        from config import config
        target_path = config.uploads_dir / safe_name
    except Exception:
        target_path = Path.cwd() / safe_name

    try:
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(text)
        
        subprocess.Popen(["notepad.exe", str(target_path)])
        return f"Written text to Notepad and opened it on your screen."
    except Exception as e:
        return f"Error opening notepad: {e}"


def type_text_into_active_window(text: str) -> str:
    """Type text directly into the currently focused window using PyAutoGUI."""
    try:
        import pyautogui
        time.sleep(1)
        pyautogui.write(text, interval=0.01)
        pyautogui.press("enter")
        return f"Typed '{text}' into the active application."
    except Exception as e:
        return f"PyAutoGUI typing error: {e}"


def system_control(action: str, value: int = 0) -> str:
    """Perform system actions like lock screen, shutdown timer, or volume adjustment."""
    action_clean = action.lower().strip()
    try:
        if "lock" in action_clean:
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], check=False)
            return "Workstation locked."
        elif "shutdown" in action_clean:
            secs = value if value > 0 else 60
            subprocess.run(["shutdown", "/s", "/t", str(secs)], check=False)
            return f"System shutdown scheduled in {secs} seconds."
        elif "cancel shutdown" in action_clean:
            subprocess.run(["shutdown", "/a"], check=False)
            return "Scheduled shutdown cancelled."
        else:
            return f"Unknown system action: {action}"
    except Exception as e:
        return f"System action failed: {e}"


_MEDIA_VK_MAP = {
    "play_pause": (0xB3, "Toggled play/pause."),
    "pause": (0xB3, "Paused/resumed media."),
    "play": (0xB3, "Playing/resumed media."),
    "next": (0xB0, "Skipped to next track."),
    "previous": (0xB1, "Returned to previous track."),
    "mute": (0xAD, "Toggled audio mute."),
    "volume_up": (0xAF, "Increased system volume."),
    "volume_down": (0xAE, "Decreased system volume."),
}


def media_control(action: str, steps: int = 3) -> str:
    """Trigger Windows hardware media & volume virtual keys (play/pause, next, prev, mute, volume up/down)."""
    key = (action or "").lower().strip().replace("-", "_").replace(" ", "_")
    aliases = {
        "toggle": "play_pause",
        "resume": "play_pause",
        "stop": "play_pause",
        "prev": "previous",
        "back": "previous",
        "up": "volume_up",
        "down": "volume_down",
    }
    resolved_key = aliases.get(key, key)
    if resolved_key not in _MEDIA_VK_MAP:
        return f"Unsupported media action '{action}'. Use play_pause, next, previous, mute, volume_up, or volume_down."

    vk, msg = _MEDIA_VK_MAP[resolved_key]
    count = max(1, min(int(steps or 1), 10)) if resolved_key in {"volume_up", "volume_down"} else 1
    if os.name == "nt" and hasattr(ctypes, "windll"):
        user32 = ctypes.windll.user32
        for _ in range(count):
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, 2, 0)
        return msg
    return "Media hardware keys are supported on Windows only."


def open_user_folder(folder_name: str = "downloads") -> str:
    """Open common user directories (Downloads, Documents, Desktop, Pictures, Music, Videos, workspace) cleanly."""
    clean = (folder_name or "downloads").lower().strip()
    home = Path.home()
    onedrive_desktop = home / "OneDrive" / "Desktop"
    desktop_path = onedrive_desktop if onedrive_desktop.exists() else home / "Desktop"

    try:
        from config import config
        project_path = Path(config.base_dir)
    except Exception:
        project_path = Path.cwd()

    folder_map = {
        "downloads": home / "Downloads",
        "download": home / "Downloads",
        "documents": home / "Documents",
        "docs": home / "Documents",
        "desktop": desktop_path,
        "pictures": home / "Pictures",
        "photos": home / "Pictures",
        "music": home / "Music",
        "videos": home / "Videos",
        "workspace": project_path,
        "project": project_path,
        "anureet": project_path,
    }
    target = folder_map.get(clean)
    if target is None:
        return f"Unknown folder '{folder_name}'. Supported: downloads, documents, desktop, pictures, music, videos, workspace."
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", str(target)], shell=False)
    return f"Opened {target.name} folder ({target})."