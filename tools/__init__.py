"""
Local AI Agent Tools Package.
Exposes OS, Adobe Premiere, FL Studio, Utility, and File Management tools.
"""

from .os_tools import (
    open_application,
    close_application,
    close_session_apps,
    list_installed_apps,
    media_control,
    open_user_folder,
    write_to_notepad,
    save_notepad_file_as,
    open_website_or_search,
    type_text_into_active_window,
    system_control,
)
from .premiere_tools import premiere_import_and_setup
from .fl_studio_tools import fl_studio_action
from .utility_tools import get_weather, get_exchange_rate, get_system_status, get_morning_briefing, get_world_time
from .gui_vision_tools import browser_tab_action, click_screen_element, type_in_search_bar
from .file_tools import list_files, search_files, read_file, upload_save_file
from .deep_research_tools import deep_research_topic, self_healing_python_exec
from .memory_store import save_memory, recall_memory, forget_memory
from .productivity_tools import (
    copy_to_clipboard,
    get_clipboard_content,
    set_timer,
    list_timers,
    cancel_timer,
    add_todo,
    list_todos,
    complete_todo,
    add_reminder,
    list_reminders,
)

__all__ = [
    "open_application",
    "close_application",
    "close_session_apps",
    "list_installed_apps",
    "media_control",
    "open_user_folder",
    "write_to_notepad",
    "save_notepad_file_as",
    "open_website_or_search",
    "type_text_into_active_window",
    "system_control",
    "premiere_import_and_setup",
    "fl_studio_action",
    "get_weather",
    "get_world_time",
    "get_exchange_rate",
    "get_system_status",
    "get_morning_briefing",
    "browser_tab_action",
    "click_screen_element",
    "type_in_search_bar",
    "list_files",
    "search_files",
    "read_file",
    "upload_save_file",
    "deep_research_topic",
    "self_healing_python_exec",
    "save_memory",
    "recall_memory",
    "forget_memory",
    "copy_to_clipboard",
    "get_clipboard_content",
    "set_timer",
    "list_timers",
    "cancel_timer",
    "add_todo",
    "list_todos",
    "complete_todo",
    "add_reminder",
    "list_reminders",
]
