"""
Core Local AI Agent implementation for Anureet (Jarvis-Class Local System Agent).
Handles Ollama API communication, Jarvis Directives, App Control Modes,
Deep Web Research Protocols, Self-Healing Code Execution, Fast Router, and JSON Tool Parsing.
"""

import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Match
import requests
from pydantic import BaseModel

from config import config
from tools.os_tools import (
    open_application, close_application, close_session_apps, save_notepad_file_as,
    write_to_notepad, open_website_or_search, system_control,
    list_installed_apps, media_control, open_user_folder,
    _match_installed_app, URI_APP_ALIASES,
)
from tools.gui_vision_tools import (
    capture_screen, type_text,
    chrome_select_profile_and_search, spotify_search_and_play,
    click_first_link_or_element, list_open_windows, focus_window,
    browser_tab_action, click_screen_element, type_in_search_bar,
)
from tools.live_screen_vision import scroll_screen, play_video_number_on_screen
from tools.deep_research_tools import deep_research_topic, self_healing_python_exec
from tools.app_modes import app_mode_manager, sanitize_and_press_key
from tools.utility_tools import get_weather, get_exchange_rate, get_system_status, get_morning_briefing, get_world_time


def shutdown_anureet(close_launched_apps: bool = False) -> Dict[str, Any]:
    """Shut down Anureet's widget, voice listener, server, and launcher-owned Ollama cleanly."""
    from launch_anureet import request_full_shutdown
    return request_full_shutdown(close_launched_apps=close_launched_apps)
from tools.premiere_tools import premiere_import_and_setup
from tools.fl_studio_tools import fl_studio_action
from tools.file_tools import list_files, search_files, read_file
from tools.memory_store import (
    save_memory, recall_memory, forget_memory,
    auto_extract_memories, append_conversation_turn, get_recent_conversation,
    publish_widget_event
)
from tools.productivity_tools import (
    copy_to_clipboard, get_clipboard_content,
    set_timer, list_timers, cancel_timer,
    add_todo, list_todos, complete_todo, delete_todo,
    add_reminder, list_reminders
)

logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("LocalAIAgent")


class ToolDeclaration(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]
    func: Any


class ExecutionResult(BaseModel):
    query: str
    routed_fast: bool
    response: str
    execution_logs: List[str]
    tool_calls_made: List[Dict[str, Any]]
    execution_time_ms: float


JARVIS_SYSTEM_PROMPT = (
    "You are Anureet, a warm, smart, helpful desktop AI assistant running locally on the user's Windows laptop. "
    "You can control desktop apps, folders, media, files, browser, screen, reminders, todos, and web research.\n\n"
    "Language & ChatGPT-Style Hinglish Tone:\n"
    "* Understand English, Devanagari Hindi, Romanized Hinglish (Hindi + English mix), and Punjabi effortlessly.\n"
    "* ALWAYS match the user's language and vibe from their LATEST message:\n"
    "  - If the user asks in Hinglish, reply like a friendly, articulate Indian friend/ChatGPT in natural Romanized Hinglish "
    "(e.g., 'Haan bilkul! Maine aapke liye Notepad khol diya hai. Aur kuch karna ho toh batayein.'). "
    "NEVER reply in stiff shuddh Hindi unless they wrote in Devanagari Hindi or asked for pure Hindi.\n"
    "  - If the user writes in Devanagari Hindi, reply in clear, natural conversational Hindi.\n"
    "  - If the user writes in English, reply in crisp, natural conversational English.\n\n"
    "Core Directives:\n"
    "1. Action First: Do not just talk about an action—call the right tool (`open_application`, `close_application`, `focus_window`, `browser_tab_action`, `click_screen_element`, `type_in_search_bar`, `get_world_time`, `shutdown_anureet`, `deep_research_topic`, etc.) when the user wants something done.\n"
    "2. 100% Truthful Reporting: NEVER claim an app was opened/closed, a window/tab was switched, text was typed, or an element was clicked if the tool returned `[TOOL_FAILED...]` or any error/not_found message. NEVER output template placeholders like `[Current Date and Time]` or `[Current Weather]`.\n"
    "3. Voice-Friendly Clarity: Lead with the main answer right away in 1-3 short sentences (or tidy bullets when listing multiple items). Avoid robotic sci-fi jargon.\n\n"
    "Error Handling:\n"
    "* If something fails or isn't found, explain it honestly and politely in the user's language with the exact next step to fix it."
)

_HINGLISH_MARKERS = re.compile(
    r"\b(?:kya|hai|hain|ka|ki|ke|kaisa|kaisi|kaise|mausam|kholo|khol|band|chalao|batao|bata|bataiye|"
    r"likho|likh|karo|kar|kijiye|samjhao|samjha|aaj|kal|kitna|kitne|yaar|bhai|dost|mera|meri|mere|"
    r"mein|me|pe|par|se|chahiye|nahi|haan|theek|thik|achha|accha|zaroor|jaldi|thoda|kaun|kaunse|"
    r"kahan|kab|kyu|kyunki|hoga|hua|raha|rahi|rha|gaana|awaaz|roko|badhao|kam)\b",
    re.IGNORECASE,
)


def _detect_query_style(query: str) -> str:
    """Detect whether the user query is Devanagari Hindi, Romanized Hinglish, or English."""
    if re.search(r"[\u0900-\u097F]", query):
        return "hindi"
    if _HINGLISH_MARKERS.search(query):
        return "hinglish"
    return "english"


class LocalAIAgent:
    def __init__(self, ollama_url: Optional[str] = None, model_name: Optional[str] = None):
        self.ollama_url = (ollama_url or config.ollama_base_url).rstrip("/")
        self.model_name = model_name or config.default_model
        self.tools: Dict[str, ToolDeclaration] = {}
        self.fast_routes: List[Dict[str, Any]] = []
        self._session = requests.Session()
        self._tool_schemas_cache: Optional[List[Dict[str, Any]]] = None
        self._register_default_tools()
        self._register_default_fast_routes()

    def check_ollama_status(self) -> bool:
        """Check whether the local Ollama server is reachable."""
        try:
            resp = self._session.get(f"{self.ollama_url}/api/tags", timeout=1)
            return resp.status_code == 200
        except Exception:
            return False

    def _fallback_to_installed_model(self) -> Optional[str]:
        """Pick an installed Ollama model if the configured model returned 404."""
        try:
            resp = self._session.get(f"{self.ollama_url}/api/tags", timeout=2)
            if resp.status_code != 200:
                return None
            available = [
                m.get("name", "")
                for m in resp.json().get("models", [])
                if isinstance(m, dict) and m.get("name")
            ]
        except Exception:
            return None

        if not available:
            return None

        preferred_order = ("llama3.1-gpu", "llama3.1", "llama3.2", "llama3", "qwen2.5", "mistral")
        for prefix in preferred_order:
            for candidate in available:
                if candidate.startswith(prefix) and "coder" not in candidate.lower():
                    return candidate

        non_coder = [c for c in available if "coder" not in c.lower()]
        return non_coder[0] if non_coder else available[0]

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str,
        parameters: Dict[str, Any]
    ):
        """Register a tool function for Ollama function calling."""
        self.tools[name] = ToolDeclaration(
            name=name,
            description=description,
            parameters=parameters,
            func=func
        )
        self._tool_schemas_cache = None
        logger.info(f"Registered tool: {name}")

    def _register_default_tools(self):
        """Register all GUI, application, research, and screen tools directly for LLM tool calling."""
        self.register_tool(
            "deep_research_topic", deep_research_topic,
            "Execute Protocol 1: Deep Web Research Engine with query fan-out, multi-source scraping, and intelligence briefing.",
            {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}
        )
        self.register_tool(
            "self_healing_python_exec", self_healing_python_exec,
            "Execute Protocol 2: Local System Access & Control with self-healing Python code execution.",
            {"type": "object", "properties": {"script_code": {"type": "string"}}, "required": ["script_code"]}
        )
        self.register_tool(
            "open_application", open_application,
            "Launch any installed Win32/Store application or built-in Windows app on the laptop (also supports 'app1 and app2').",
            {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}
        )
        self.register_tool(
            "list_installed_apps", list_installed_apps,
            "List installed desktop and Microsoft Store applications available on this laptop, optionally filtered by keyword.",
            {"type": "object", "properties": {"query": {"type": "string"}}}
        )
        self.register_tool(
            "media_control", media_control,
            "Control system media playback and audio volume ('play', 'pause', 'next', 'previous', 'mute', 'volume_up', 'volume_down').",
            {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}
        )
        self.register_tool(
            "open_user_folder", open_user_folder,
            "Open standard user folders in File Explorer ('downloads', 'documents', 'desktop', 'pictures', 'videos', 'music').",
            {"type": "object", "properties": {"folder_name": {"type": "string"}}, "required": ["folder_name"]}
        )
        self.register_tool(
            "close_application", close_application,
            "Close an application on screen (e.g. Notepad, Chrome, Spotify, FL Studio).",
            {"type": "object", "properties": {"app_name": {"type": "string"}}, "required": ["app_name"]}
        )
        self.register_tool(
            "scroll_screen", scroll_screen,
            "Scroll the active webpage or window screen up or down.",
            {"type": "object", "properties": {"direction": {"type": "string"}}, "required": ["direction"]}
        )
        self.register_tool(
            "play_video_number_on_screen", play_video_number_on_screen,
            "Click and play the Nth video thumbnail showing on screen (e.g. 1st, 2nd, 3rd video).",
            {"type": "object", "properties": {"video_number": {"type": "integer"}}, "required": ["video_number"]}
        )
        self.register_tool(
            "chrome_select_profile_and_search", chrome_select_profile_and_search,
            "Navigate Google Chrome, select profile, focus address bar, and search or open a website.",
            {"type": "object", "properties": {"query_or_url": {"type": "string"}}, "required": ["query_or_url"]}
        )
        self.register_tool(
            "spotify_search_and_play", spotify_search_and_play,
            "Search for a specific song, artist, or playlist on Spotify and start playback.",
            {"type": "object", "properties": {"search_term": {"type": "string"}}, "required": ["search_term"]}
        )
        self.register_tool(
            "click_first_link_or_element", click_first_link_or_element,
            "Click the first search result link or active primary button on screen.",
            {"type": "object", "properties": {}}
        )
        self.register_tool(
            "type_text", type_text,
            "Type arbitrary text directly into the active window or search bar.",
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
        )
        self.register_tool(
            "press_key", sanitize_and_press_key,
            "Press a shortcut key or button (e.g. 'enter', 'tab', 'space', 'ctrl+l', 'ctrl+s', 'alt+f4', 'f5').",
            {"type": "object", "properties": {"key_name": {"type": "string"}}, "required": ["key_name"]}
        )
        self.register_tool(
            "capture_screen", capture_screen,
            "Take a screenshot of the laptop screen to inspect open windows and content.",
            {"type": "object", "properties": {}}
        )
        self.register_tool(
            "get_weather", get_weather,
            "Get real-time weather and daily forecast for a given city.",
            {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
        )
        self.register_tool(
            "get_exchange_rate", get_exchange_rate,
            "Fetch real-time currency exchange rate between two currency codes (e.g., USD to INR).",
            {
                "type": "object",
                "properties": {
                    "source_currency": {"type": "string"},
                    "target_currency": {"type": "string"}
                }
            }
        )
        self.register_tool(
            "premiere_import_and_setup", premiere_import_and_setup,
            "Generate and run an Adobe Premiere Pro ExtendScript (.jsx) to import video, create a white matte, and add cut markers.",
            {
                "type": "object",
                "properties": {
                    "video_path": {"type": "string"},
                    "add_white_matte": {"type": "boolean"},
                    "cut_points": {"type": "array", "items": {"type": "number"}}
                },
                "required": ["video_path"]
            }
        )
        self.register_tool(
            "fl_studio_action", fl_studio_action,
            "Launch FL Studio or trigger FL Studio macro shortcuts (play, record, piano roll, playlist, channel rack, mixer).",
            {"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]}
        )
        self.register_tool(
            "write_to_notepad", write_to_notepad,
            "Write text to a file in uploads and open it in Notepad.",
            {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "filename": {"type": "string"}
                },
                "required": ["text"]
            }
        )
        self.register_tool(
            "save_notepad_file_as", save_notepad_file_as,
            "Save the active Notepad file under a specified filename.",
            {"type": "object", "properties": {"filename": {"type": "string"}}, "required": ["filename"]}
        )
        self.register_tool(
            "open_website_or_search", open_website_or_search,
            "Open a URL or Google search in the default web browser.",
            {"type": "object", "properties": {"query_or_url": {"type": "string"}}, "required": ["query_or_url"]}
        )
        self.register_tool(
            "list_files", list_files,
            "List files and folders inside a directory path.",
            {"type": "object", "properties": {"directory_path": {"type": "string"}}}
        )
        self.register_tool(
            "search_files", search_files,
            "Search recursively for files or directories matching a query string.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "search_path": {"type": "string"}
                },
                "required": ["query"]
            }
        )
        self.register_tool(
            "read_file", read_file,
            "Read content from a local text or binary file.",
            {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}
        )
        self.register_tool(
            "system_control", system_control,
            "Perform Windows system actions like lock screen, shutdown timer, or cancel shutdown.",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "value": {"type": "integer"}
                },
                "required": ["action"]
            }
        )
        self.register_tool(
            "save_memory", save_memory,
            "Save a durable fact, preference, or contact note to local long-term memory.",
            {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "category": {"type": "string"}
                },
                "required": ["fact"]
            }
        )
        self.register_tool(
            "recall_memory", recall_memory,
            "Search or list stored long-term memories by keyword.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"}
                }
            }
        )
        self.register_tool(
            "forget_memory", forget_memory,
            "Delete saved memories matching a keyword.",
            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
        )
        self.register_tool(
            "copy_to_clipboard", copy_to_clipboard,
            "Copy text or code directly into the Windows system clipboard.",
            {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
        )
        self.register_tool(
            "get_clipboard_content", get_clipboard_content,
            "Read the current text stored in the Windows clipboard.",
            {"type": "object", "properties": {}}
        )
        self.register_tool(
            "set_timer", set_timer,
            "Start a background countdown timer (e.g. duration_text='5 minutes', label='Tea break').",
            {
                "type": "object",
                "properties": {
                    "duration_text": {"type": "string"},
                    "label": {"type": "string"}
                },
                "required": ["duration_text"]
            }
        )
        self.register_tool(
            "list_timers", list_timers,
            "List active countdown timers and remaining time.",
            {"type": "object", "properties": {}}
        )
        self.register_tool(
            "cancel_timer", cancel_timer,
            "Cancel an active timer by label or ID (empty cancels all).",
            {"type": "object", "properties": {"label_or_id": {"type": "string"}}}
        )
        self.register_tool(
            "add_todo", add_todo,
            "Add a new task to the persistent todo list.",
            {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}
        )
        self.register_tool(
            "list_todos", list_todos,
            "List pending (or all) todo items.",
            {"type": "object", "properties": {"include_completed": {"type": "boolean"}}}
        )
        self.register_tool(
            "complete_todo", complete_todo,
            "Mark a todo item completed by ID or keyword.",
            {"type": "object", "properties": {"task_query": {"type": "string"}}, "required": ["task_query"]}
        )
        self.register_tool(
            "add_reminder", add_reminder,
            "Schedule a persistent reminder (e.g. title='Call Mom', when_text='in 15 minutes' or 'tomorrow 9:00 am').",
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "when_text": {"type": "string"}
                },
                "required": ["title", "when_text"]
            }
        )
        self.register_tool(
            "list_reminders", list_reminders,
            "List scheduled upcoming reminders.",
            {"type": "object", "properties": {"include_triggered": {"type": "boolean"}}}
        )
        self.register_tool(
            "list_open_windows", list_open_windows,
            "List titles of currently open desktop windows.",
            {"type": "object", "properties": {}}
        )
        self.register_tool(
            "focus_window", focus_window,
            "Bring an open desktop window to the foreground by partial title.",
            {"type": "object", "properties": {"title_query": {"type": "string"}}, "required": ["title_query"]}
        )
        self.register_tool(
            "get_system_status", get_system_status,
            "Get current date, time, battery percentage, CPU, and RAM usage.",
            {"type": "object", "properties": {}}
        )
        self.register_tool(
            "get_morning_briefing", get_morning_briefing,
            "Compile a briefing with current date/time, weather, pending todos, and upcoming reminders.",
            {"type": "object", "properties": {"city": {"type": "string"}}}
        )
        self.register_tool(
            "get_world_time", get_world_time,
            "Get exact current local date, time, and UTC offset for any city or country worldwide.",
            {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]}
        )
        self.register_tool(
            "browser_tab_action", browser_tab_action,
            "Switch, search, open, close, or reopen browser tabs in Chrome/Edge (action: next|previous|new|close|reopen|switch_to).",
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "target": {"type": "string"}
                },
                "required": ["action"]
            }
        )
        self.register_tool(
            "click_screen_element", click_screen_element,
            "Find and click a real visible button, tab, menu item, or text label in the active app or specified app using Windows UI Automation and OCR.",
            {
                "type": "object",
                "properties": {
                    "target_text": {"type": "string"},
                    "app_name": {"type": "string"}
                },
                "required": ["target_text"]
            }
        )
        self.register_tool(
            "type_in_search_bar", type_in_search_bar,
            "Focus the search bar inside an active browser/app (or specified app), type the query text, and submit.",
            {
                "type": "object",
                "properties": {
                    "query_text": {"type": "string"},
                    "app_hint": {"type": "string"}
                },
                "required": ["query_text"]
            }
        )
        self.register_tool(
            "close_session_apps", close_session_apps,
            "Close user applications that were launched by Anureet during the current session without touching unrelated apps or VS Code.",
            {"type": "object", "properties": {}}
        )
        self.register_tool(
            "shutdown_anureet", shutdown_anureet,
            "Shut down Anureet's widget, voice listener, backend server, and launcher-owned Ollama cleanly. Use close_launched_apps=true for 'stop and close everything'.",
            {"type": "object", "properties": {"close_launched_apps": {"type": "boolean"}}}
        )

    def register_fast_route(self, pattern: str, handler: Callable[[str, Match], str], name: str = "fast_route"):
        """Register a regex pattern for sub-50ms intent routing."""
        compiled = re.compile(pattern, re.IGNORECASE)
        self.fast_routes.append({"name": name, "pattern": compiled, "handler": handler})

    def _register_default_fast_routes(self):
        """Built-in default fast routes for instant human-like physical desktop execution."""

        # 0. Self-Shutdown Routes (runs first; distinct from laptop/PC shutdown)
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:stop\s+and\s+close\s+everything|close\s+and\s+stop\s+everything|stop\s+everything\s+and\s+close|shut\s*down\s+everything|sab\s+kuch\s+band\s+karo)\s*[.!?]*$",
            lambda q, m: shutdown_anureet(close_launched_apps=True)["message"],
            name="shutdown_anureet",
        )
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:(?:switch\s+off|turn\s+off|shut\s*down|power\s+off|close|stop|exit|quit)\s+(?:yourself|anureet)|(?:do|shut)\s+yourself\s+down|yourself\s+(?:off|down)|(?:khud|apne\s+aap)\s+ko\s+band\s+karo|band\s+ho\s+jao)\s*[.!?]*$",
            lambda q, m: shutdown_anureet(close_launched_apps=False)["message"],
            name="shutdown_anureet",
        )

        # 1. Deep Research & Real-Time Utility Routes
        self.register_fast_route(r".*deep.*research\s+(.*)", lambda q, m: deep_research_topic(m.group(1)), name="deep_research_topic")
        self.register_fast_route(r".*\bresearch\s+(.*)", lambda q, m: deep_research_topic(m.group(1)), name="deep_research_topic")

        def _fast_weather(query: str, match: Match) -> str:
            city = (match.group(1) or "Kurukshetra").strip(" ?.!,'\"")
            res = get_weather(city or "Kurukshetra")
            style = _detect_query_style(query)
            if res.get("status") == "success":
                if style == "hinglish":
                    return (
                        f"{res['city']} ka abhi ka mausam: {res['temperature_celsius']}°C, {res['condition']}. "
                        f"Hawa ki speed {res['wind_speed_kmh']} km/h hai, aur aaj ka high {res['max_temperature_today']}°C / low {res['min_temperature_today']}°C rahega."
                    )
                if style == "hindi":
                    return (
                        f"{res['city']} का वर्तमान मौसम: {res['temperature_celsius']}°C, {res['condition']}। "
                        f"हवा की गति {res['wind_speed_kmh']} km/h है और आज का अधिकतम तापमान {res['max_temperature_today']}°C व न्यूनतम {res['min_temperature_today']}°C है।"
                    )
            return res.get("summary") or res.get("message", "Unable to fetch weather.")

        def _fast_world_time(query: str, match: Match) -> Optional[str]:
            loc = (match.group(1) or "").strip(" \t\r\n.,!?'\"")
            low = loc.lower()
            if not loc or low in ("abhi", "kya", "aaj", "kitna", "laptop", "system", "computer", "india here") or re.match(r"^\d", low):
                return None
            res = get_world_time(loc)
            style = _detect_query_style(query)
            if res.get("status") == "success":
                if style == "hinglish":
                    return f"Abhi {res['location']} mein {res['time']} ({res['date']}, {res['timezone_abbr']}) ho raha hai."
                if style == "hindi":
                    return f"अभी {res['location']} में {res['time']} ({res['date']}, {res['timezone_abbr']}) हो रहे हैं।"
            return res.get("summary") or res.get("message", "Unable to fetch world time.")

        def _fast_exchange_rate(query: str, match: Match) -> str:
            res = get_exchange_rate("USD", "INR")
            style = _detect_query_style(query)
            if res.get("status") == "success":
                if style == "hinglish":
                    return f"Abhi 1 {res['source_currency']} ki keemat {res['exchange_rate']:.4f} {res['target_currency']} hai."
                if style == "hindi":
                    return f"अभी 1 {res['source_currency']} का विनिमय दर {res['exchange_rate']:.4f} {res['target_currency']} है।"
            return res.get("summary") or res.get("message", "Unable to fetch exchange rate.")

        self.register_fast_route(r".*\b(?:weather|mausam|मौसम)\b(?:\s+(?:in|for|at|of|mein|me|ka|ki)\s+([a-zA-Z\s,]+?))?[?.!]*$", _fast_weather, name="get_weather")
        self.register_fast_route(r".*\b([a-zA-Z]{3,})\s+(?:ka|ki|ke|mein|me)\s+(?:weather|mausam)\b.*", _fast_weather, name="get_weather")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:what(?:'s|\s+is)\s+(?:the\s+)?)?(?:current\s+)?(?:time|date\s+and\s+time)\s+(?:in|of|at|for)\s+([a-zA-Z\s,.-]+?)\s*[?.!]*$", _fast_world_time, name="get_world_time")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:can\s+you\s+|please\s+)?(?:tell\s+me\s+(?:the\s+)?)?(?:current\s+)?time\s+(?:in|of|at)\s+([a-zA-Z\s,.-]+?)\s*[?.!]*$", _fast_world_time, name="get_world_time")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?([a-zA-Z\s,.-]+?)\s+(?:mein|me|ka|ki)\s+(?:kya\s+)?time(?:\s+(?:kya\s+)?(?:hai|ho\s+raha\s+hai|batao|batayein))?\s*[?.!]*$", _fast_world_time, name="get_world_time")
        self.register_fast_route(r".*\b(?:exchange\s+rate|usd\s+to\s+inr|dollar\s+(?:ka\s+)?rate)\b.*", _fast_exchange_rate, name="get_exchange_rate")

        # 2. Mode Activation Routes
        def _activate_chrome_mode(query: str, match: Match) -> str:
            open_application("chrome")
            app_mode_manager.set_mode("CHROME_MODE")
            return "Activated Chrome Control Mode, Sir! Commands online: 'new tab', 'close tab', 'search <query>', 'scroll down', or 'click first link'."

        def _activate_spotify_mode(query: str, match: Match) -> str:
            open_application("spotify")
            app_mode_manager.set_mode("SPOTIFY_MODE")
            return "Activated Spotify Control Mode, Sir! Commands online: 'play', 'pause', 'next', 'previous', or 'search <song>'."

        def _activate_fl_mode(query: str, match: Match) -> str:
            open_application("fl studio")
            app_mode_manager.set_mode("FL_STUDIO_MODE")
            return "Activated FL Studio Control Mode, Sir! Commands online: 'piano roll', 'playlist', 'channel rack', 'mixer', 'play', or 'record'."

        def _activate_notepad_mode(query: str, match: Match) -> str:
            open_application("notepad")
            app_mode_manager.set_mode("NOTEPAD_MODE")
            return "Activated Notepad Control Mode, Sir! Commands online: 'type <text>', 'new file', or 'save'."

        self.register_fast_route(r".*activate.*chrome.*", _activate_chrome_mode)
        self.register_fast_route(r".*activate.*spotify.*", _activate_spotify_mode)
        self.register_fast_route(r".*activate.*fl.*", _activate_fl_mode)
        self.register_fast_route(r".*activate.*notepad.*", _activate_notepad_mode)

        # 3. Screen Capture & Perception
        self.register_fast_route(r".*(take|capture).*screenshot.*", lambda q, m: capture_screen("laptop_screen.png"))
        self.register_fast_route(r".*what.*on.*(my\s+)?screen.*", lambda q, m: capture_screen("laptop_screen.png"))

        # 4. Live Screen Vision & Navigation
        self.register_fast_route(r".*scroll\s+down.*", lambda q, m: scroll_screen("down"))
        self.register_fast_route(r".*scroll\s+up.*", lambda q, m: scroll_screen("up"))
        self.register_fast_route(r".*(play|click).*3(rd)?.*video.*", lambda q, m: play_video_number_on_screen(3))
        self.register_fast_route(r".*(play|click).*2(nd)?.*video.*", lambda q, m: play_video_number_on_screen(2))
        self.register_fast_route(r".*(play|click).*1(st)?.*video.*", lambda q, m: play_video_number_on_screen(1))
        self.register_fast_route(r".*(play|click).*4(th)?.*video.*", lambda q, m: play_video_number_on_screen(4))

        # 5. Chrome Navigation & Link Clicking
        def _fast_click_link(query: str, match: Match) -> str:
            return click_first_link_or_element()

        def _fast_chrome_search(query: str, match: Match) -> str:
            target = match.group(1).strip() if match.lastindex and match.lastindex >= 1 else query
            open_application("chrome")
            return chrome_select_profile_and_search(target)

        self.register_fast_route(r".*refresh.*(page|browser)?.*", lambda q, m: sanitize_and_press_key("f5"), name="press_key")
        self.register_fast_route(r".*click.*(first|1st)?.*link.*", _fast_click_link, name="click_first_link")
        self.register_fast_route(r".*open.*link.*", _fast_click_link, name="click_first_link")

        def _fast_click_ui(query: str, match: Match) -> Optional[str]:
            target = (match.group(1) or "").strip(" \t\r\n.,!?'\"")
            app_name = (match.group(2) or "").strip(" \t\r\n.,!?'\"") if match.lastindex and match.lastindex >= 2 and match.group(2) else None
            # Preserve 'video 1'/'first video'/'link' routes if somehow reached
            if not target or re.search(r"\b(?:first|1st|2nd|3rd|4th)\s+video\b", query, re.IGNORECASE):
                return None
            target = re.sub(r"\s+(?:button|icon|option)\s*$", "", target, flags=re.IGNORECASE).strip()
            if not app_name:
                m_in = re.match(r"^(?:the\s+)?(.+?)\s+in\s+(?:the\s+)?([a-zA-Z0-9\s._-]{2,})$", target, re.IGNORECASE)
                if m_in:
                    target = re.sub(r"\s+(?:button|icon|option)\s*$", "", m_in.group(1), flags=re.IGNORECASE).strip()
                    app_name = m_in.group(2).strip(" \t\r\n.,!?'\"")
            return click_screen_element(target, app_name=app_name)["message"]

        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:please\s+)?click(?:\s+on)?\s+(?:the\s+)?(.+?)(?:\s+(?:in|inside|on)\s+([a-zA-Z0-9\s._-]+?))?\s*[.!?]*$",
            _fast_click_ui,
            name="click_screen_element",
        )
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(.+?)\s+(?:pe|par|pr)\s+click\s+karo\s*[.!?]*$",
            lambda q, m: click_screen_element(m.group(1).strip(" \t\r\n.,!?'\""))["message"],
            name="click_screen_element",
        )

        # Browser Tab Switching & Management
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:switch|next)\s+tab\s*[.!?]*$", lambda q, m: browser_tab_action("next"), name="browser_tab_action")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:previous|prev|last|back)\s+tab\s*[.!?]*$", lambda q, m: browser_tab_action("previous"), name="browser_tab_action")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:switch|go)\s+to\s+(.+?)\s+tab\s*[.!?]*$", lambda q, m: browser_tab_action("switch_to", m.group(1)), name="browser_tab_action")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:open\s+)?new\s+tab\s*[.!?]*$", lambda q, m: browser_tab_action("new"), name="browser_tab_action")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?close\s+(?:this\s+|current\s+)?tab\s*[.!?]*$", lambda q, m: browser_tab_action("close"), name="browser_tab_action")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?reopen\s+(?:closed\s+)?tab\s*[.!?]*$", lambda q, m: browser_tab_action("reopen"), name="browser_tab_action")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:tab\s+badlo|agla\s+tab)\s*[.!?]*$", lambda q, m: browser_tab_action("next"), name="browser_tab_action")

        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:open|launch|start)\s+youtube\s*[.!?]*$", lambda q, m: open_application("youtube"), name="open_application")
        self.register_fast_route(r".*search\s+(.*)\s+in.*(chrome|browser).*", _fast_chrome_search, name="chrome_select_profile_and_search")
        self.register_fast_route(r".*open.*chrome.*and.*search\s+(.*)", _fast_chrome_search, name="chrome_select_profile_and_search")
        self.register_fast_route(r".*select.*profile.*", lambda q, m: sanitize_and_press_key("enter"), name="press_key")
        self.register_fast_route(r".*open.*chrome.*", lambda q, m: open_application("chrome"), name="open_application")
        self.register_fast_route(r".*uiet.*kuk.*", lambda q, m: chrome_select_profile_and_search("https://www.uietkuk.ac.in"), name="chrome_select_profile_and_search")

        # Hinglish open/close & universal installed app / media / folder quick routes
        def _hinglish_open(app: str, query: str) -> str:
            res = open_application(app)
            if _detect_query_style(query) == "hinglish":
                if "Opened " in res:
                    return f"Haan bilkul, {app.title()} khol diya hai."
                return res
            return res

        def _hinglish_close(app: str, query: str) -> str:
            res = close_application(app)
            if _detect_query_style(query) == "hinglish":
                return f"{app.title()} band kar diya hai."
            return res

        _KNOWN_BUILTIN_APPS = {
            "notepad", "calculator", "calc", "paint", "cmd", "powershell",
            "chrome", "google chrome", "edge", "spotify", "youtube",
            "word", "excel", "powerpoint", "explorer", "file explorer",
            "task manager", "vscode", "vs code", "vlc", "settings",
            "camera", "clock", "microsoft store", "store",
        } | set(URI_APP_ALIASES.keys())

        def _is_known_openable_app(target: str) -> bool:
            clean = re.sub(r"^(?:the|my|app|application)\s+", "", target.strip().lower()).strip()
            if not clean:
                return False
            # Reject explicit files, URLs, folders, or generic phrases
            if any(tok in clean for tok in (".", "\\", "/", ":", "folder", "file", "link", "website", "url", "tab", "window", "all app", "sabhi app")):
                return False
            if clean in _KNOWN_BUILTIN_APPS:
                return True
            return _match_installed_app(clean) is not None

        def _fast_universal_open(query: str, match: Match) -> Optional[str]:
            raw_target = (match.group(1) or "").strip(" .?!\"'")
            if not raw_target:
                return None
            parts = [p.strip() for p in re.split(r"\s*(?:,|(?:\band\b)|(?:\baur\b))\s*", raw_target, flags=re.IGNORECASE) if p.strip()]
            if not parts or not all(_is_known_openable_app(p) for p in parts):
                return None
            res = open_application(raw_target)
            if _detect_query_style(query) == "hinglish" and "Opened " in res:
                return f"Haan ji, {', '.join(parts)} khol diya hai!"
            return res

        def _fast_list_apps(query: str, match: Match) -> str:
            filt = (match.group(1) or "").strip(" ?.!") if match.lastindex and match.lastindex >= 1 else ""
            res = list_installed_apps(filt)
            if _detect_query_style(query) == "hinglish":
                count = res.get("matched_count", len(res.get("apps", [])))
                return f"Aapke laptop par {count} apps mil gaye hain:\n{res['summary']}"
            return res["summary"]

        def _fast_open_folder(query: str, match: Match) -> str:
            folder = match.group(1).strip().lower()
            res = open_user_folder(folder)
            if _detect_query_style(query) == "hinglish" and res.startswith("Opened "):
                return f"Done! {folder.title()} folder khol diya hai."
            return res

        def _fast_media(action: str, query: str) -> str:
            res = media_control(action)
            if _detect_query_style(query) == "hinglish" and not res.startswith(("Unsupported", "Media hardware")):
                labels = {
                    "pause": "Media pause kar diya hai.",
                    "play": "Media play kar diya hai.",
                    "next": "Agla gaana laga diya hai.",
                    "previous": "Pichla gaana laga diya hai.",
                    "mute": "Audio mute/unmute toggle kar diya hai.",
                    "volume_up": "Awaaz thodi badha di hai.",
                    "volume_down": "Awaaz thodi kam kar di hai.",
                }
                return labels.get(action, res)
            return res

        self.register_fast_route(
            r".*\b(?:list|show|what(?:'s|\s+are)?)\s+(?:the\s+|all\s+)?installed\s+apps?(?:\s+(?:like|matching|for)\s+(.+))?$",
            _fast_list_apps,
            name="list_installed_apps",
        )
        self.register_fast_route(
            r".*\b(?:laptop|pc|system)\s+(?:mein|me)\s+(?:kaun\s*kaun\s*se|kaunse|kya)\s+apps?\s+hain\b.*",
            _fast_list_apps,
            name="list_installed_apps",
        )
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:open|launch|show)\s+(downloads|documents|desktop|pictures|videos|music)(?:\s+folder)?\s*[.!?]*$",
            _fast_open_folder,
            name="open_user_folder",
        )
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(downloads|documents|desktop|pictures|videos|music)(?:\s+folder)?\s+(?:kholo|khol\s+do|dikhao)\s*[.!?]*$",
            _fast_open_folder,
            name="open_user_folder",
        )
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:pause|play|resume)\s+(?:the\s+)?(?:music|song|media|video)?\s*[.!?]*$", lambda q, m: _fast_media("pause" if "pause" in q.lower() else "play", q), name="media_control")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:gaana|music)\s+(?:roko|pause\s+karo|band\s+karo)\s*[.!?]*$", lambda q, m: _fast_media("pause", q), name="media_control")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:gaana|music)\s+(?:chalao|play\s+karo)\s*[.!?]*$", lambda q, m: _fast_media("play", q), name="media_control")
        self.register_fast_route(r".*\b(?:next\s+(?:song|track)|agla\s+gaana)\b.*", lambda q, m: _fast_media("next", q), name="media_control")
        self.register_fast_route(r".*\b(?:previous\s+(?:song|track)|pichla\s+gaana)\b.*", lambda q, m: _fast_media("previous", q), name="media_control")
        self.register_fast_route(r".*\b(?:volume\s+up|increase\s+volume|awaaz\s+badhao)\b.*", lambda q, m: _fast_media("volume_up", q), name="media_control")
        self.register_fast_route(r".*\b(?:volume\s+down|decrease\s+volume|awaaz\s+kam\s+karo)\b.*", lambda q, m: _fast_media("volume_down", q), name="media_control")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:mute|unmute|awaaz\s+band\s+karo|mute\s+karo)\b.*", lambda q, m: _fast_media("mute", q), name="media_control")

        self.register_fast_route(r".*\bnotepad\b.*\b(?:kholo|khol|chalao)\b.*", lambda q, m: _hinglish_open("notepad", q), name="open_application")
        self.register_fast_route(r".*(?:नोटपैड|नोद\s*पेर|नोटपेड).*", lambda q, m: _hinglish_open("notepad", q), name="open_application")
        self.register_fast_route(r".*\bchrome\b.*\b(?:kholo|khol|chalao)\b.*", lambda q, m: _hinglish_open("chrome", q), name="open_application")
        self.register_fast_route(r".*\byoutube\b.*\b(?:kholo|khol|chalao)\b.*", lambda q, m: _hinglish_open("youtube", q), name="open_application")
        self.register_fast_route(r".*\bnotepad\b.*\bband\s+karo\b.*", lambda q, m: _hinglish_close("notepad", q), name="close_application")
        self.register_fast_route(r".*\bchrome\b.*\bband\s+karo\b.*", lambda q, m: _hinglish_close("chrome", q), name="close_application")
        self.register_fast_route(r".*\bspotify\b.*\bband\s+karo\b.*", lambda q, m: _hinglish_close("spotify", q), name="close_application")

        # 6. Notepad, File Save & Typing
        def _fast_type(query: str, match: Match) -> str:
            text = match.group(2).strip()
            return type_text(text, press_enter=True)

        def _fast_search_bar_type(query: str, match: Match) -> str:
            query_text = (match.group(1) or "").strip(" \t\r\n'\"")
            app_hint = (match.group(2) or "").strip(" \t\r\n'\"") if match.lastindex and match.lastindex >= 2 and match.group(2) else None
            return type_in_search_bar(query_text, app_hint=app_hint)

        def _fast_notepad_write(query: str, match: Match) -> str:
            note_text = match.group(1).strip() or f"Note created via Local AI Agent: {query}"
            return write_to_notepad(note_text, "meeting_notes.txt")

        def _fast_save_as(query: str, match: Match) -> str:
            filename = match.group(2).strip() if match.lastindex and match.lastindex >= 2 else "my_file.txt"
            return save_notepad_file_as(filename)

        self.register_fast_route(r".*save.*(file|notepad).*as\s+(.*)", _fast_save_as, name="save_notepad_file_as")
        self.register_fast_route(r".*save.*(file|notepad).*with.*name\s+(.*)", _fast_save_as, name="save_notepad_file_as")
        self.register_fast_route(r".*save.*file.*", lambda q, m: sanitize_and_press_key("ctrl+s"), name="press_key")
        self.register_fast_route(r".*open.*notepad.*?(?:and\s+)?(?:write|type|note)\s+(.*)", _fast_notepad_write, name="write_to_notepad")
        self.register_fast_route(r".*open.*notepad.*", lambda q, m: open_application("notepad"), name="open_application")
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:type|write|search|enter)\s+['\"]?(.+?)['\"]?\s+(?:in|into|on)\s+(?:the\s+)?(?:([a-zA-Z0-9\s._-]+?)\s+)?search\s*(?:bar|box)\s*[.!?]*$",
            _fast_search_bar_type,
            name="type_in_search_bar",
        )
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:([a-zA-Z0-9\s._-]+?)\s+)?search\s*(?:bar|box)\s+(?:mein|me|pe|par)\s+['\"]?(.+?)['\"]?\s+(?:likho|type\s+karo|search\s+karo)\s*[.!?]*$",
            lambda q, m: type_in_search_bar(m.group(2).strip(" \t\r\n'\""), app_hint=m.group(1).strip() if m.group(1) else None),
            name="type_in_search_bar",
        )
        self.register_fast_route(r"^\s*(type|write)\s+(.*)$", _fast_type, name="type_text")

        # 7. App Closing Routes
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:close|quit|exit)\s+all\s+(?:opened\s+|open\s+|launched\s+)?apps?\s*[.!?]*$", lambda q, m: close_session_apps()["message"], name="close_session_apps")
        self.register_fast_route(r"^\s*(?:anureet[,\s]+)?(?:saare|sabhi|all)\s+apps?\s+band\s+karo\s*[.!?]*$", lambda q, m: close_session_apps()["message"], name="close_session_apps")
        self.register_fast_route(r".*close.*notepad.*", lambda q, m: close_application("notepad"), name="close_application")
        self.register_fast_route(r".*close.*chrome.*", lambda q, m: close_application("chrome"), name="close_application")
        self.register_fast_route(r".*close.*spotify.*", lambda q, m: close_application("spotify"), name="close_application")
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:close|quit|exit)\s+(?!all\b|everything\b|yourself\b|anureet\b|tab\b|(?:active|current)?\s*window\b)(.+?)\s*[.!?]*$",
            lambda q, m: close_application(m.group(1).strip(" \t\r\n.,!?'\"")),
            name="close_application",
        )
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?!khud\b|apne\s+aap\b|sab\s+kuch\b|window\b|tab\b)(.+?)\s+(?:ko\s+)?(?:band|close)\s+karo\s*[.!?]*$",
            lambda q, m: close_application(m.group(1).strip(" \t\r\n.,!?'\"")),
            name="close_application",
        )
        self.register_fast_route(r".*close.*(active|current)?.*window.*", lambda q, m: sanitize_and_press_key("alt+f4"), name="press_key")

        # 8. Spotify Search & Playback
        def _fast_spotify_song(query: str, match: Match) -> str:
            song_term = match.group(1).strip()
            open_application("spotify")
            return spotify_search_and_play(song_term)

        self.register_fast_route(r".*open.*spotify.*search\s+(.*)", _fast_spotify_song, name="spotify_search_and_play")
        self.register_fast_route(r".*play\s+(.*)\s+by\s+(.*)", lambda q, m: spotify_search_and_play(f"{m.group(1)} {m.group(2)}"), name="spotify_search_and_play")
        self.register_fast_route(r".*play\s+(.*)\s+(song|music|playlist).*", _fast_spotify_song, name="spotify_search_and_play")
        self.register_fast_route(r".*play\s+song\s+(.*)", _fast_spotify_song, name="spotify_search_and_play")
        self.register_fast_route(r".*play.*(punjabi|motivational).*music.*", lambda q, m: spotify_search_and_play("Punjabi Motivational Hits"), name="spotify_search_and_play")
        self.register_fast_route(r".*open.*spotify.*", lambda q, m: spotify_search_and_play("Punjabi Hits"), name="spotify_search_and_play")

        # 9. Creative & System Apps
        def _fast_premiere_setup(query: str, match: Match) -> str:
            res = premiere_import_and_setup(video_path="project_video.mp4", add_white_matte=True)
            return res.get("message", "Generated Premiere Pro ExtendScript.")

        self.register_fast_route(r".*(?:white\s+(?:color\s+)?matte|white\s+layer).*premiere.*|.*premiere.*(?:white\s+(?:color\s+)?matte|white\s+layer|import\s+video).*", _fast_premiere_setup, name="premiere_import_and_setup")
        self.register_fast_route(r".*\b(open|launch|start)\b.*\bfl(\s+studio)?\b.*", lambda q, m: open_application("fl studio"), name="open_application")
        self.register_fast_route(r".*\b(open|launch|start)\b.*after.*effect.*", lambda q, m: open_application("after effects"), name="open_application")
        self.register_fast_route(r".*\b(open|launch|start)\b.*premiere.*", lambda q, m: open_application("premiere"), name="open_application")
        self.register_fast_route(r".*\b(open|launch|start)\b.*(file\s+)?explorer.*", lambda q, m: open_application("file explorer"), name="open_application")
        self.register_fast_route(r".*\b(open|launch|start)\b.*task\s+manager.*", lambda q, m: open_application("task manager"), name="open_application")
        self.register_fast_route(r".*\b(open|launch|start)\b.*(ms\s+)?word.*", lambda q, m: open_application("word"), name="open_application")

        # 10. Key Press & Hotkeys
        self.register_fast_route(r"^\s*press\s+(.*)$", lambda q, m: sanitize_and_press_key(m.group(1)), name="press_key")

        # 11. Memory, Productivity, Window & Briefing Routes (English + Hinglish)
        def _fast_remember(query: str, match: Match) -> str:
            fact = match.group(1).strip(" .")
            res = save_memory(fact, category="explicit")
            if _detect_query_style(query) in {"hindi", "hinglish"}:
                return f"Thik hai, yaad rakh liya: {res.get('content', fact)}"
            return res.get("message", "Saved to memory.")

        def _fast_recall(query: str, match: Match) -> str:
            term = (match.group(1) if match.lastindex else "") or ""
            res = recall_memory(term.strip(" ?."))
            return res.get("summary", "No matching memories found.")

        self.register_fast_route(
            r"^\s*(?:please\s+)?(?:remember\s+that|remember|yaad\s+rakhna|yaad\s+rakh)\s+(?:ki\s+)?(.+)$",
            _fast_remember,
            name="save_memory",
        )
        self.register_fast_route(
            r".*\b(?:what\s+do\s+you\s+remember|recall\s+memory|mujhe\s+kya\s+yaad\s+hai)\b(?:\s+about\s+(.*))?",
            _fast_recall,
            name="recall_memory",
        )
        self.register_fast_route(
            r"^\s*copy\s+(.+?)\s+to\s+clipboard\s*$",
            lambda q, m: copy_to_clipboard(m.group(1))["message"],
            name="copy_to_clipboard",
        )
        self.register_fast_route(
            r".*\b(?:read\s+clipboard|what(?:'s|\s+is)\s+in\s+(?:my\s+)?clipboard|clipboard\s+(?:padho|mein\s+kya\s+hai))\b.*",
            lambda q, m: get_clipboard_content()["message"],
            name="get_clipboard_content",
        )
        self.register_fast_route(
            r".*\b(?:set|start)\s+(?:a\s+)?timer\s+(?:for\s+)?(.+?)(?:\s+(?:called|named|for)\s+(.+))?$",
            lambda q, m: set_timer(m.group(1), m.group(2) or "Timer")["message"],
            name="set_timer",
        )
        self.register_fast_route(
            r".*\b(\d+\s*(?:sec|secs|second|seconds|min|mins|minute|minutes|mint|hour|hours|hr|ghanta|ghante))\s+ka\s+timer\s+(?:lagao|set\s+karo|chalu\s+karo)\b.*",
            lambda q, m: set_timer(m.group(1), "Timer")["message"],
            name="set_timer",
        )
        self.register_fast_route(r".*\b(?:list|show|active)\s+timers?\b.*", lambda q, m: list_timers()["message"], name="list_timers")
        self.register_fast_route(
            r".*\bcancel\s+timer(?:\s+(.*))?$",
            lambda q, m: cancel_timer(m.group(1) or "")["message"],
            name="cancel_timer",
        )
        self.register_fast_route(
            r"^\s*(?:add\s+todo|todo\s+add)\s+(.+)$",
            lambda q, m: add_todo(m.group(1))["message"],
            name="add_todo",
        )
        self.register_fast_route(
            r".*\b(?:list|show|check)\s+(?:my\s+)?todos?\b.*",
            lambda q, m: list_todos()["message"],
            name="list_todos",
        )
        self.register_fast_route(
            r"^\s*(?:complete|finish|done)\s+todo\s+(.+)$",
            lambda q, m: complete_todo(m.group(1))["message"],
            name="complete_todo",
        )
        self.register_fast_route(
            r"^\s*remind\s+me\s+(?:to\s+)?(.+?)\s+(?:at|in)\s+(.+)$",
            lambda q, m: add_reminder(m.group(1), m.group(2))["message"],
            name="add_reminder",
        )
        self.register_fast_route(
            r".*\b(?:list|show)\s+(?:my\s+)?reminders?\b.*",
            lambda q, m: list_reminders()["message"],
            name="list_reminders",
        )
        self.register_fast_route(
            r".*\b(?:list|show)\s+(?:open\s+)?windows\b.*",
            lambda q, m: list_open_windows()["message"],
            name="list_open_windows",
        )

        def _fast_focus_or_switch(query: str, match: Match) -> str:
            target = match.group(1).strip(" \t\r\n.,!?'\"")
            res = focus_window(target)
            if res.get("status") != "success":
                low = target.lower()
                if any(k in low for k in ("youtube", "google", "github", "gmail", "whatsapp", "tab")):
                    return browser_tab_action("switch_to", target)
            return res["message"]

        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:switch(?:\s+window)?|next\s+window)\s*[.!?]*$",
            lambda q, m: sanitize_and_press_key("alt+tab"),
            name="press_key",
        )
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:switch\s+to|focus(?:\s+(?:on|window))?)\s+(.+?)\s*[.!?]*$",
            _fast_focus_or_switch,
            name="focus_window",
        )
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(.+?)\s+(?:window|app)?\s*(?:pe|pr|par|mein|me)\s+(?:switch|jao|aao)\s*(?:karo)?\s*[.!?]*$",
            _fast_focus_or_switch,
            name="focus_window",
        )
        self.register_fast_route(
            r".*\b(?:morning\s+briefing|daily\s+briefing|aaj\s+ka\s+briefing)\b.*",
            lambda q, m: get_morning_briefing()["summary"],
            name="get_morning_briefing",
        )
        self.register_fast_route(
            r".*\b(?:system\s+status|battery\s+status|kitni\s+battery|abhi\s+kya\s+time|what\s+time\s+is\s+it)\b.*",
            lambda q, m: get_system_status()["summary"],
            name="get_system_status",
        )
        # 12. Universal Installed App & Multi-App Catch-All (runs last; returns None if not an installed/known app)
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:please\s+)?(?:open|launch|start)\s+(.+?)\s*[.!?]*$",
            _fast_universal_open,
            name="open_application",
        )
        self.register_fast_route(
            r"^\s*(?:anureet[,\s]+)?(?:please\s+)?(.+?)\s+(?:kholo|khol\s+do|chalao|start\s+karo)\s*[.!?]*$",
            _fast_universal_open,
            name="open_application",
        )

    def _get_ollama_tool_schemas(self) -> List[Dict[str, Any]]:
        """Format registered tools into Ollama compatible function tool schema (cached)."""
        if self._tool_schemas_cache is not None:
            return self._tool_schemas_cache
        self._tool_schemas_cache = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters
                }
            }
            for tool in self.tools.values()
        ]
        return self._tool_schemas_cache

    def _match_fast_route(self, query: str) -> Optional[Dict[str, Any]]:
        """Match query against fast routes and return route name + output."""
        for route in self.fast_routes:
            match = route["pattern"].search(query)
            if match:
                try:
                    t0 = time.perf_counter()
                    output = route["handler"](query, match)
                    if output is None:
                        continue
                    elapsed_ms = int((time.perf_counter() - t0) * 1000)
                    return {"name": route["name"], "output": output, "elapsed_ms": elapsed_ms}
                except Exception as e:
                    logger.warning(f"Fast route handler error: {e}")
                    return None
        return None

    def check_fast_route(self, query: str) -> Optional[str]:
        """Check if query matches any fast route pattern."""
        matched = self._match_fast_route(query)
        return matched["output"] if matched else None

    def _parse_embedded_json_tool_call(self, text: str) -> Optional[Dict[str, Any]]:
        """Intercept raw JSON tool call objects emitted in text by LLM (supports nested dicts)."""
        if not text or "{" not in text:
            return None
        decoder = json.JSONDecoder()
        idx = 0
        while True:
            start = text.find("{", idx)
            if start == -1:
                break
            try:
                obj, _ = decoder.raw_decode(text, start)
                if isinstance(obj, dict) and isinstance(obj.get("name"), str):
                    params = obj.get("parameters", obj.get("arguments", {}))
                    if isinstance(params, dict):
                        return {"name": obj["name"], "args": params}
            except json.JSONDecodeError:
                pass
            idx = start + 1
        return None

    @staticmethod
    def _format_tool_result(result: Any) -> str:
        """Convert a tool return value into a concise user-facing summary string."""
        if isinstance(result, dict):
            return str(result.get("summary") or result.get("message") or json.dumps(result))
        return str(result)

    def _invoke_tool(self, tool_name: str, tool_args: Any) -> Dict[str, Any]:
        """Execute a registered tool safely and return standardized telemetry."""
        t0 = time.perf_counter()
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args) if tool_args.strip() else {}
            except json.JSONDecodeError:
                tool_args = {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        if tool_name not in self.tools:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            msg = f"Tool '{tool_name}' is not registered."
            return {
                "tool": tool_name,
                "name": tool_name,
                "arguments": tool_args,
                "details": msg,
                "result_str": msg,
                "execution_time_ms": elapsed_ms,
                "ok": False,
            }

        try:
            raw_res = self.tools[tool_name].func(**tool_args)
            result_str = self._format_tool_result(raw_res)
            ok = True
            if isinstance(raw_res, dict) and raw_res.get("status") in ("error", "not_found", "failed"):
                ok = False
            elif isinstance(raw_res, str) and raw_res.lstrip().startswith((
                "Could not", "No open window", "Failed", "Error", "Specify",
                "Cannot ", "Unable to", "Not found"
            )):
                ok = False
        except Exception as e:
            result_str = f"Error executing tool '{tool_name}': {e}"
            ok = False

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "tool": tool_name,
            "name": tool_name,
            "arguments": tool_args,
            "details": result_str,
            "result_str": result_str,
            "execution_time_ms": elapsed_ms,
            "ok": ok,
        }

    def execute(
        self,
        user_query: str,
        system_prompt: Optional[str] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        session_id: str = "default",
    ) -> ExecutionResult:
        """Main execution pipeline with Mode Controller, Fast Router, Memory Injection, and Ollama Loop."""
        start_time = time.perf_counter()
        logs: List[str] = []
        tool_calls_made: List[Dict[str, Any]] = []

        def emit_log(msg: str):
            logs.append(msg)
            if log_callback:
                try:
                    log_callback(msg)
                except Exception:
                    pass

        def finalize(response_text: str, routed_fast: bool, persist_turn: bool = True) -> ExecutionResult:
            exec_time = (time.perf_counter() - start_time) * 1000
            if persist_turn:
                try:
                    append_conversation_turn(session_id, "user", user_query)
                    append_conversation_turn(session_id, "assistant", response_text)
                    if session_id not in {"widget", "voice"}:
                        publish_widget_event("idle", user_text=user_query, agent_text=response_text, source=session_id)
                except Exception as mem_err:
                    logger.debug(f"Conversation save skipped: {mem_err}")
            return ExecutionResult(
                query=user_query,
                routed_fast=routed_fast,
                response=response_text,
                execution_logs=logs,
                tool_calls_made=tool_calls_made,
                execution_time_ms=exec_time,
            )

        emit_log("Analyzing query intent...")
        if session_id not in {"widget", "voice"}:
            try:
                publish_widget_event("thinking", user_text=user_query, source=session_id)
            except Exception:
                pass
        try:
            extracted_facts = auto_extract_memories(user_query)
            if extracted_facts:
                emit_log(f"Auto-remembered: {extracted_facts[0]}")
        except Exception as mem_err:
            logger.debug(f"Auto-memory extraction skipped: {mem_err}")

        # 0. Check Active App Control Mode First
        mode_res = app_mode_manager.process_mode_command(user_query)
        if mode_res is not None:
            emit_log(f"Executed by Mode Controller ({app_mode_manager.get_mode()})")
            tool_calls_made.append({
                "tool": f"mode:{app_mode_manager.get_mode().lower()}",
                "name": "app_mode_manager",
                "arguments": {"query": user_query},
                "details": mode_res,
                "execution_time_ms": int((time.perf_counter() - start_time) * 1000)
            })
            return finalize(mode_res, routed_fast=True)

        # 1. Fast Router Interception (Sub-50ms Physical Desktop Action)
        fast_match = self._match_fast_route(user_query)
        if fast_match is not None:
            emit_log(f"Fast Router matched: [{fast_match['name']}]")
            tool_calls_made.append({
                "tool": fast_match["name"],
                "name": fast_match["name"],
                "arguments": {"query": user_query},
                "details": fast_match["output"],
                "execution_time_ms": fast_match["elapsed_ms"]
            })
            return finalize(fast_match["output"], routed_fast=True)

        # 2. Ollama Reasoning Loop with Jarvis Directives, Long-Term Memory & Conversation Context
        system_content = system_prompt or JARVIS_SYSTEM_PROMPT
        query_style = _detect_query_style(user_query)
        if query_style == "hinglish":
            system_content = (
                f"{system_content}\n\n"
                "[Current Turn Style]: User is speaking in Hinglish. Reply naturally like ChatGPT in friendly Romanized Hinglish "
                "(e.g., 'Haan bilkul! ...', 'Dekho simple words mein...'), keeping it crisp and easy to speak aloud."
            )
        elif query_style == "hindi":
            system_content = (
                f"{system_content}\n\n"
                "[Current Turn Style]: User wrote in Devanagari Hindi. Reply in warm, clear, everyday conversational Hindi."
            )
        try:
            recalled = recall_memory(user_query, limit=3).get("memories", [])
            if recalled:
                facts_block = "\n".join(f"- {m['content']}" for m in recalled)
                system_content = f"{system_content}\n\nKnown User Memory Context:\n{facts_block}"
        except Exception as mem_err:
            logger.debug(f"Memory recall skipped: {mem_err}")

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
        try:
            for turn_msg in get_recent_conversation(session_id, limit=6):
                if not str(turn_msg.get("content", "")).startswith("Local LLM request failed:"):
                    messages.append(turn_msg)
        except Exception as hist_err:
            logger.debug(f"History load skipped: {hist_err}")
        messages.append({"role": "user", "content": user_query})

        tools_schema = self._get_ollama_tool_schemas()
        max_turns = 5
        turn = 0

        while turn < max_turns:
            turn += 1
            emit_log(f"Turn {turn}: Sending payload to Ollama model '{self.model_name}'...")

            payload: Dict[str, Any] = {
                "model": self.model_name,
                "messages": messages,
                "stream": False,
                "keep_alive": "15m",
                "options": {"num_ctx": 4096, "num_predict": 320, "temperature": 0.2},
            }
            if tools_schema:
                payload["tools"] = tools_schema

            try:
                resp = self._session.post(
                    f"{self.ollama_url}/api/chat",
                    json=payload,
                    timeout=config.ollama_timeout_seconds
                )
                if resp.status_code == 404:
                    fallback_model = self._fallback_to_installed_model()
                    if fallback_model and fallback_model != self.model_name:
                        old_model = self.model_name
                        self.model_name = fallback_model
                        payload["model"] = fallback_model
                        logger.warning(
                            f"Ollama model '{old_model}' not found (404); switching to installed model '{fallback_model}'."
                        )
                        emit_log(f"Model '{old_model}' not found; switched to '{fallback_model}'.")
                        resp = self._session.post(
                            f"{self.ollama_url}/api/chat",
                            json=payload,
                            timeout=config.ollama_timeout_seconds
                        )
                resp.raise_for_status()
                data = resp.json()
            except requests.ConnectionError as e:
                style = _detect_query_style(user_query)
                err_msg = (
                    f"Local Ollama server abhi start nahi hai ({self.ollama_url}). Pehle Ollama chalayein aur dobara try karein."
                    if style in {"hindi", "hinglish"}
                    else f"Local Ollama server is offline at {self.ollama_url}. Please start Ollama and try again."
                )
                logger.error(f"Ollama connection error: {e}")
                emit_log(f"Ollama connection error: {e}")
                return finalize(err_msg, routed_fast=False, persist_turn=False)
            except Exception as e:
                err_msg = f"Local LLM request failed: {e}"
                logger.error(f"Ollama request error: {e}")
                emit_log(f"Ollama error: {e}")
                return finalize(err_msg, routed_fast=False, persist_turn=False)

            msg = data.get("message", {})
            messages.append(msg)

            tool_calls = msg.get("tool_calls", [])
            content = (msg.get("content") or "").strip()

            # Check if LLM emitted raw JSON tool call inside text content
            if not tool_calls:
                embedded_call = self._parse_embedded_json_tool_call(content)
                if embedded_call:
                    t_name = embedded_call["name"]
                    t_args = embedded_call["args"]
                    emit_log(f"Parsed embedded JSON tool call: {t_name}")
                    exec_info = self._invoke_tool(t_name, t_args)
                    tool_calls_made.append({
                        "tool": exec_info["tool"],
                        "name": exec_info["name"],
                        "arguments": exec_info["arguments"],
                        "details": exec_info["details"],
                        "execution_time_ms": exec_info["execution_time_ms"]
                    })
                    return finalize(exec_info["result_str"], routed_fast=False)

                emit_log("Completed execution.")
                return finalize(content, routed_fast=False)

            for call in tool_calls:
                func_obj = call.get("function", {})
                tool_name = func_obj.get("name", "")
                tool_args = func_obj.get("arguments", {})

                emit_log(f"Ollama invoked tool: {tool_name}")
                exec_info = self._invoke_tool(tool_name, tool_args)
                tool_calls_made.append({
                    "tool": exec_info["tool"],
                    "name": exec_info["name"],
                    "arguments": exec_info["arguments"],
                    "details": exec_info["details"],
                    "execution_time_ms": exec_info["execution_time_ms"]
                })
                emit_log(f"Tool '{tool_name}' output: {exec_info['result_str'][:200]}")

                tool_msg = exec_info["result_str"]
                if not exec_info.get("ok", True):
                    tool_msg = f"[TOOL_FAILED - REPORT THIS HONESTLY, DO NOT CLAIM IT SUCCEEDED]: {tool_msg}"
                messages.append({
                    "role": "tool",
                    "content": tool_msg,
                    "name": tool_name
                })

        return finalize("Reached max tool iteration limit.", routed_fast=False, persist_turn=False)

    def process_query(
        self,
        query: str,
        log_callback: Optional[Callable[[str], None]] = None,
        session_id: str = "default",
    ) -> Dict[str, Any]:
        """Wrapper method providing unified dict output for voice and server interfaces."""
        exec_res = self.execute(query, log_callback=log_callback, session_id=session_id)
        return {
            "query": query,
            "response_text": exec_res.response,
            "executed_tools": exec_res.tool_calls_made,
            "execution_logs": exec_res.execution_logs,
            "execution_time_ms": exec_res.execution_time_ms,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }


# Global singleton agent instance
agent = LocalAIAgent()