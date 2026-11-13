"""
Phonetic and Speech-To-Text (STT) Query Normalizer for Anureet.
Maps common ASR misrecognitions, accent artifacts, and brand phonetics
to canonical application names and desktop command intents.
"""

import re
import difflib
from typing import Dict, List, Optional, Tuple, Set

# Non-app words that must NEVER be resolved to an app name
NON_APP_WORDS: Set[str] = {
    "audio", "file", "folder", "desktop", "tab", "window", "windows",
    "screen", "link", "button", "project", "note", "notes", "text",
    "image", "video", "sound", "volume", "music", "song", "media",
    "search", "bar", "box", "menu", "page", "web", "website",
    "chat", "hud",
}

# Canonical App Name -> List of common phonetic/STT misrecognitions (lowercased)
PHONETIC_APP_ALIASES: Dict[str, List[str]] = {
    "CapCut": [
        "capcut", "cap cut", "capgut", "cap gut", "capecut", "cape cut",
        "capquette", "capquet", "capquete", "cabcut", "cab cut", "cupcut",
        "cup cut", "kapcut", "kapkut", "grab cut", "grabcut", "cut cut",
        "capcu", "capcur",
    ],
    "Google Chrome": [
        "google chrome", "chrome", "crome", "crom", "chorme", "chrom",
    ],
    "YouTube": [
        "youtube", "you tube", "u tube", "jutabh", "yutub", "youtub",
        "u-tube", "you-tube", "yootube",
    ],
    "Spotify": [
        "spotify", "spotifi", "spotifay", "sportify",
    ],
    "Notepad": [
        "notepad", "notpad", "nodepad", "node pad", "note pad",
        "नोटपैड", "नोटपेड", "नोद पेर",
    ],
    "VS Code": [
        "visual studio code", "vscode", "vs code", "vs-code", "vencode",
    ],
    "FL Studio": [
        "fl studio", "flstudio", "fl 64", "fl-studio",
    ],
    "Premiere Pro": [
        "premiere pro", "adobe premiere", "premiere", "premier pro", "premier",
    ],
    "After Effects": [
        "after effects", "after effect", "afterfx", "after-effects",
    ],
    "Task Manager": [
        "task manager", "taskmgr", "task-manager",
    ],
    "File Explorer": [
        "file explorer", "explorer", "my computer", "this pc",
    ],
    "Calculator": [
        "calculator", "calc",
    ],
    "Word": [
        "ms word", "winword", "microsoft word",
    ],
}

# Reverse lookup: variant string -> canonical name
_VARIANT_TO_CANONICAL: Dict[str, str] = {}
for canonical, variants in PHONETIC_APP_ALIASES.items():
    for var in variants:
        _VARIANT_TO_CANONICAL[var.lower().replace(" ", "")] = canonical
        _VARIANT_TO_CANONICAL[var.lower()] = canonical

# Phonetic action regex replacements: (compiled_regex, replacement_text)
_ACTION_REPLACEMENTS: List[Tuple[re.Pattern, str]] = [
    # "ilika ona audio" -> "click on audio"
    (re.compile(r"\b(?:ilika|lik|clik)\s+(?:ona|on)\b", re.IGNORECASE), "click on"),
    (re.compile(r"\b(?:ilika|lik)\b", re.IGNORECASE), "click"),
    # "switch again" -> "switch tab"
    (re.compile(r"^\s*(?:anureet[,\s]+)?(?:switch|tab)\s+again\s*[.!?]*$", re.IGNORECASE), "switch tab"),
    (re.compile(r"^\s*(?:anureet[,\s]+)?jutabh\s+again\s*[.!?]*$", re.IGNORECASE), "switch tab"),
    # "close, grab, cut" -> "close CapCut"
    (re.compile(r"\b(?:close|quit|exit)\s*[,]?\s*grab\s*[,]?\s*cut\b", re.IGNORECASE), "close CapCut"),
    (re.compile(r"\b(?:close|quit|exit)\s+(?:capgut|capecut|capquette|grab cut)\b", re.IGNORECASE), "close CapCut"),
    # "minimize all app" -> "minimize all windows"
    (re.compile(r"\b(?:minimize\s+all\s+app(?:s|lication|lications)?|saare\s+app\s+minimize)\b", re.IGNORECASE), "minimize all windows"),
]


def resolve_app_name(raw_name: str, open_window_titles: Optional[List[str]] = None) -> str:
    """Resolve a raw or phonetically misrecognized application name to its canonical form.

    Args:
        raw_name: Extracted app name (e.g. 'capquette', 'capgut', 'jutabh').
        open_window_titles: Optional list of currently visible open window titles to prioritize.

    Returns:
        Canonical app name if resolved, otherwise stripped raw_name.
    """
    clean = re.sub(r'[?!,;:"\']+|[.]$', "", (raw_name or "").strip()).strip()
    if not clean or len(clean) < 2:
        return clean
    clean_low = clean.lower()
    clean_compact = clean_low.replace(" ", "")

    if clean_low in NON_APP_WORDS or clean_compact in NON_APP_WORDS:
        return clean

    # 1. Direct dictionary match
    if clean_low in _VARIANT_TO_CANONICAL:
        return _VARIANT_TO_CANONICAL[clean_low]
    if clean_compact in _VARIANT_TO_CANONICAL:
        return _VARIANT_TO_CANONICAL[clean_compact]

    # 2. Check open window titles if provided
    if open_window_titles:
        for title in open_window_titles:
            t_clean = (title or "").strip()
            t_low = t_clean.lower()
            t_compact = t_low.replace(" ", "")
            # Direct containment
            if (len(clean_compact) >= 3 and clean_compact in t_compact) or clean_low in t_low:
                for canon, variants in PHONETIC_APP_ALIASES.items():
                    if any(v in t_low for v in variants):
                        return canon
                return t_clean

            # Fuzzy match against open window title
            if len(clean_compact) >= 4:
                ratio = difflib.SequenceMatcher(None, clean_compact, t_compact).ratio()
                if ratio >= 0.58:
                    for canon, variants in PHONETIC_APP_ALIASES.items():
                        if any(v in t_low for v in variants):
                            return canon
                    return t_clean

    # 3. Fuzzy match against all known variants
    if len(clean_compact) >= 4:
        all_variants = [v for v in _VARIANT_TO_CANONICAL.keys() if len(v) >= 4]
        close_matches = difflib.get_close_matches(clean_compact, all_variants, n=1, cutoff=0.62)
        if close_matches:
            return _VARIANT_TO_CANONICAL[close_matches[0]]

    return clean


def normalize_voice_query(query: str, open_window_titles: Optional[List[str]] = None) -> str:
    """Pre-process and phonetically correct spoken user commands before routing.

    Args:
        query: Raw transcribed query text from Whisper or user input.
        open_window_titles: Optional list of open window titles for context.

    Returns:
        Normalized query string with phonetic errors repaired.
    """
    if not query or not query.strip():
        return ""

    text = query.strip()

    # Step 1: Action phrase replacements
    for pattern, replacement in _ACTION_REPLACEMENTS:
        text = pattern.sub(replacement, text)

    # Step 2: Replace known app misspellings in prepositional context: "in/inside <app>" or "open/close <app>"
    def _replace_app_match(match: re.Match) -> str:
        prefix = match.group(1)
        raw_app = match.group(2).strip()
        resolved = resolve_app_name(raw_app, open_window_titles=open_window_titles)
        return f"{prefix} {resolved}"

    app_context_pattern = re.compile(
        r"\b(in|inside|open|launch|start|close|quit|exit|focus|switch\s+to)\s+([a-zA-Z0-9\s._-]+?)(?=[.,!?;]|$)",
        re.IGNORECASE,
    )
    text = app_context_pattern.sub(_replace_app_match, text)

    # Step 3: Replace standalone variant names (sorting longest first to avoid partial replacements)
    sorted_variants = sorted(_VARIANT_TO_CANONICAL.items(), key=lambda x: len(x[0]), reverse=True)
    for variant, canonical in sorted_variants:
        if len(variant) < 4:
            continue
        pattern = rf"(?<!\w){re.escape(variant)}(?!\w)"
        if re.search(pattern, text, re.IGNORECASE):
            text = re.sub(pattern, canonical, text, flags=re.IGNORECASE)

    return text
