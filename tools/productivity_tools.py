"""
Productivity Tools for Anureet: Clipboard control, background countdown timers
with Windows chimes/toasts, and persistent SQLite Todos & Reminders.
"""

import base64
import datetime
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from tools.memory_store import (
    add_reminder_item,
    add_todo_item,
    complete_todo_item,
    delete_todo_item,
    fetch_due_reminders,
    list_reminder_items,
    list_todo_items,
)

try:
    import pyperclip
except ImportError:
    pyperclip = None

try:
    import winsound
except ImportError:
    winsound = None

logger = logging.getLogger("ProductivityTools")

_TIMER_LOCK = threading.Lock()
_ACTIVE_TIMERS: Dict[str, Dict[str, Any]] = {}
_TIMER_COUNTER = 0


def _play_notification_chime() -> None:
    if winsound is None:
        return
    try:
        for _ in range(3):
            winsound.Beep(1100, 180)
            time.sleep(0.06)
    except Exception:
        pass


def show_desktop_notification(title: str, message: str) -> bool:
    """Display a non-blocking Windows notification balloon cleanly using EncodedCommand."""
    if not hasattr(subprocess, "CREATE_NO_WINDOW"):
        return False
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        f"$n.BalloonTipTitle = {repr(str(title)[:64])}; "
        f"$n.BalloonTipText = {repr(str(message)[:200])}; "
        "$n.Visible = $true; "
        "$n.ShowBalloonTip(5000); "
        "Start-Sleep -Milliseconds 600; "
        "$n.Dispose()"
    )
    try:
        encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        logger.debug(f"Desktop notification skipped: {e}")
        return False


def copy_to_clipboard(text: str) -> Dict[str, Any]:
    """Copy text or code directly into the Windows system clipboard."""
    if pyperclip is None:
        return {"status": "error", "message": "pyperclip is not installed."}
    payload = "" if text is None else str(text)
    try:
        pyperclip.copy(payload)
        preview = payload[:80] + ("..." if len(payload) > 80 else "")
        return {
            "status": "success",
            "characters_copied": len(payload),
            "preview": preview,
            "message": f"Copied {len(payload)} character(s) to clipboard.",
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to copy to clipboard: {e}"}


def get_clipboard_content() -> Dict[str, Any]:
    """Read the current text content from the system clipboard."""
    if pyperclip is None:
        return {"status": "error", "message": "pyperclip is not installed."}
    try:
        content = pyperclip.paste() or ""
        if not content:
            return {"status": "success", "content": "", "message": "Clipboard is currently empty."}
        return {
            "status": "success",
            "content": content,
            "length": len(content),
            "message": f"Clipboard content ({len(content)} chars): {content[:400]}",
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to read clipboard: {e}"}


def parse_duration_seconds(duration_text: str) -> Optional[int]:
    """Parse English and Hinglish durations like '5 minutes', '30 sec', '1 hour 15 min', '2 ghante'."""
    text = (duration_text or "").lower().strip()
    if not text:
        return None
    if text.isdigit():
        return max(1, int(text) * 60)

    total = 0
    matched = False
    unit_patterns = [
        (r"(\d+)\s*(?:h|hr|hrs|hour|hours|ghanta|ghante)\b", 3600),
        (r"(\d+)\s*(?:m|min|mins|minute|minutes|mint)\b", 60),
        (r"(\d+)\s*(?:s|sec|secs|second|seconds)\b", 1),
    ]
    for pat, mult in unit_patterns:
        for val in re.findall(pat, text):
            total += int(val) * mult
            matched = True

    return max(1, total) if matched else None


def _timer_fired(timer_id: str, label: str) -> None:
    with _TIMER_LOCK:
        entry = _ACTIVE_TIMERS.pop(timer_id, None)
    if entry is None:
        return
    _play_notification_chime()
    show_desktop_notification("Anureet Timer Complete", f"Timer '{label}' finished!")
    logger.info(f"Timer completed: '{label}' ({timer_id})")


def set_timer(duration_text: str, label: str = "Timer") -> Dict[str, Any]:
    """Start a non-blocking background countdown timer with chime and desktop alert."""
    global _TIMER_COUNTER
    seconds = parse_duration_seconds(duration_text)
    if seconds is None:
        return {
            "status": "error",
            "message": f"Could not understand duration '{duration_text}'. Try e.g. '5 minutes' or '30 seconds'.",
        }

    clean_label = (label or "Timer").strip() or "Timer"
    ends_at = (datetime.datetime.now() + datetime.timedelta(seconds=seconds)).isoformat(timespec="seconds")

    with _TIMER_LOCK:
        _TIMER_COUNTER += 1
        timer_id = f"timer-{_TIMER_COUNTER}"
        timer = threading.Timer(seconds, _timer_fired, args=(timer_id, clean_label))
        timer.daemon = True
        _ACTIVE_TIMERS[timer_id] = {
            "id": timer_id,
            "label": clean_label,
            "seconds": seconds,
            "ends_at": ends_at,
            "timer_obj": timer,
        }
        timer.start()

    return {
        "status": "success",
        "timer_id": timer_id,
        "label": clean_label,
        "seconds": seconds,
        "ends_at": ends_at,
        "message": f"Timer '{clean_label}' set for {seconds} second(s) (ends at {ends_at}).",
    }


def list_timers() -> Dict[str, Any]:
    """List currently active background timers."""
    now = datetime.datetime.now()
    items = []
    with _TIMER_LOCK:
        for entry in _ACTIVE_TIMERS.values():
            ends = datetime.datetime.fromisoformat(entry["ends_at"])
            remaining = max(0, int((ends - now).total_seconds()))
            items.append({
                "id": entry["id"],
                "label": entry["label"],
                "remaining_seconds": remaining,
                "ends_at": entry["ends_at"],
            })
    if not items:
        return {"status": "success", "count": 0, "timers": [], "message": "No active timers running."}
    summary = ", ".join(f"{t['label']} ({t['remaining_seconds']}s left)" for t in items)
    return {"status": "success", "count": len(items), "timers": items, "message": f"Active timers: {summary}"}


def cancel_timer(label_or_id: str = "") -> Dict[str, Any]:
    """Cancel an active timer by ID or label (or cancel all if empty)."""
    target = (label_or_id or "").strip().lower()
    cancelled = []
    with _TIMER_LOCK:
        keys = list(_ACTIVE_TIMERS.keys())
        for k in keys:
            entry = _ACTIVE_TIMERS[k]
            if not target or k.lower() == target or target in entry["label"].lower():
                entry["timer_obj"].cancel()
                cancelled.append(entry["label"])
                del _ACTIVE_TIMERS[k]
    if not cancelled:
        return {"status": "error", "message": f"No active timer matched '{label_or_id}'."}
    return {"status": "success", "cancelled": cancelled, "message": f"Cancelled timer(s): {', '.join(cancelled)}"}


def add_todo(task: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    return add_todo_item(task, db_path=db_path)


def list_todos(include_completed: bool = False, db_path: Optional[Path] = None) -> Dict[str, Any]:
    res = list_todo_items(include_completed=include_completed, db_path=db_path)
    items = res["todos"]
    if not items:
        res["message"] = "Your todo list is empty."
    else:
        formatted = "; ".join(f"#{t['id']} {t['task']}" for t in items)
        res["message"] = f"Todos ({len(items)}): {formatted}"
    return res


def complete_todo(task_query: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    return complete_todo_item(task_query, db_path=db_path)


def delete_todo(task_query: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    return delete_todo_item(task_query, db_path=db_path)


def _parse_reminder_time(when_text: str) -> Optional[datetime.datetime]:
    raw = (when_text or "").strip().lower()
    now = datetime.datetime.now()
    if not raw:
        return None

    # ISO format attempt
    try:
        return datetime.datetime.fromisoformat(when_text.strip())
    except ValueError:
        pass

    # Relative duration ('in 10 minutes', '5 min baad')
    dur_sec = parse_duration_seconds(raw)
    if dur_sec is not None and any(k in raw for k in ("in ", "baad", "after", "min", "sec", "hour", "ghante")):
        if not re.search(r"\d{1,2}:\d{2}", raw):
            return now + datetime.timedelta(seconds=dur_sec)

    # Time of day ('9:30 am', '18:00', '9 baje')
    time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", raw)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or 0)
        ampm = time_match.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif "shaam" in raw or "evening" in raw:
            if hour < 12:
                hour += 12
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if "tomorrow" in raw or "kal" in raw:
                target += datetime.timedelta(days=1)
            elif target <= now:
                target += datetime.timedelta(days=1)
            return target

    return None


def add_reminder(title: str, when_text: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    due = _parse_reminder_time(when_text)
    if due is None:
        return {
            "status": "error",
            "message": f"Could not parse reminder time '{when_text}'. Try e.g. 'in 15 minutes' or 'tomorrow 9:00 am'.",
        }
    return add_reminder_item(title, due.isoformat(timespec="seconds"), db_path=db_path)


def list_reminders(include_triggered: bool = False, db_path: Optional[Path] = None) -> Dict[str, Any]:
    res = list_reminder_items(include_triggered=include_triggered, db_path=db_path)
    items = res["reminders"]
    if not items:
        res["message"] = "No upcoming reminders."
    else:
        formatted = "; ".join(f"#{r['id']} {r['title']} ({r['due_at']})" for r in items)
        res["message"] = f"Reminders ({len(items)}): {formatted}"
    return res


def get_due_reminders(mark_triggered: bool = True, db_path: Optional[Path] = None) -> Dict[str, Any]:
    items = fetch_due_reminders(mark_triggered=mark_triggered, db_path=db_path)
    return {"status": "success", "count": len(items), "reminders": items}

