# Anureet — Local AI Agent System

Anureet is a privacy-first, local AI desktop and voice assistant powered by **Ollama**. It combines a sub-50ms Fast Intent Router, application-specific control modes, GUI/screen automation, deep web research, real-time utilities, a mobile-friendly web dashboard, a remote file manager, and an optional Telegram bot gateway.

---

## Key Features

- **Core Agent Engine (`agent_core.py`)**:
  - **App Control Modes (`tools/app_modes.py`)**: Stateful control modes for **Chrome**, **Spotify**, **Notepad**, and **FL Studio** (`CHROME_MODE`, `SPOTIFY_MODE`, `NOTEPAD_MODE`, `FL_STUDIO_MODE`).
  - **Fast Intent Router**: Regex-based instant routing for common desktop commands, memory, clipboard, timers, todos, reminders, window switching, morning briefing, weather, currency rates, research, and app launching without waiting on an LLM round-trip.
  - **Persistent Local Memory (`tools/memory_store.py`)**: Zero-bloat SQLite + FTS5 long-term memory, deterministic English/Hinglish auto-extraction (`"remember..."`, `"yaad rakhna..."`), and per-session multi-turn conversation history.
  - **Ollama Function Calling**: Multi-turn tool-calling loop with persistent HTTP session pooling, fallback parsing for embedded JSON tool calls, and automatic 404 fallback to installed local Ollama models.
- **Floating Corner Orb & HUD Widget (`anureet_widget.py`)**:
  - Lightweight built-in `tkinter` always-on-top neural orb (`Idle`, `Listening`, `Thinking`, `Speaking`) with a click-to-expand dark HUD chat panel, offline mic button, `Esc` speech cutoff, and global Windows hotkey (`Ctrl+Space` / `Ctrl+Alt+Space`).
- **Hands-Free Multilingual Voice Assistant (`voice_assistant.py`)**:
  - Wake-word activation (`"Anureet"`, `"Hey Anureet"`, Hinglish/Devanagari & phonetic variants) with a **60-second conversational follow-up window** and `<30ms` speech interruption (`Esc` / `q` / `Ctrl+D`).
  - **Offline Multilingual STT Priority**: Faster-Whisper (`small` multilingual model with CUDA auto-detection / `int8` CPU fallback, Silero VAD, and Hinglish wake-word biasing), Vosk (`model/`), or `SpeechRecognition` (`en-IN`) fallback.
  - **Language-Matched Responses & TTS**: Automatically matches the user's language (English, Hindi, Hinglish, etc.) and selects `hi-IN-SwaraNeural` or `en-IN-NeerjaNeural` in `edge-tts`, with Piper -> `pyttsx3` -> PowerShell SAPI5 offline fallbacks.
- **Desktop, Creative & Research Tools (`tools/`)**:
  - **OS & Apps (`os_tools.py`)**: Launch/close Windows apps (Notepad, Chrome, Spotify, FL Studio, Premiere Pro, After Effects, Word, Explorer, Task Manager), write/save Notepad notes, open URLs, and lock/shutdown controls.
  - **GUI & Screen Control (`gui_vision_tools.py`, `live_screen_vision.py`)**: Screenshots (saved to `uploads/screenshots/`), window focusing, mouse clicks, keyboard hotkeys, scrolling, Chrome search, and Spotify playback.
  - **Adobe Premiere Pro (`premiere_tools.py`)**: Generates ExtendScript (`scripts/generated_premiere_task.jsx`) to import video, add a white color matte, and place timeline cut markers.
  - **FL Studio (`fl_studio_tools.py`)**: Launch FL Studio and trigger Play/Record/Piano Roll/Playlist/Channel Rack/Mixer shortcuts.
  - **Deep Research & Local Python (`deep_research_tools.py`)**: Concurrent 3-vector DuckDuckGo web research briefing (`deep_research_topic`) and isolated temp-file Python execution (`self_healing_python_exec`, gated by `config.enable_code_execution`).
  - **Real-Time Utilities (`utility_tools.py`)**: Open-Meteo geocoded weather forecasts and live currency exchange rates with in-memory TTL caching.
  - **File Tools (`file_tools.py`)**: Directory listing, fast recursive search (skipping `venv`, `.git`, `node_modules`, `__pycache__`), text/binary/PDF (`pypdf`) reading, and file saving.
  - **Productivity & Window Tools (`productivity_tools.py`, `gui_vision_tools.py`, `utility_tools.py`)**: Clipboard read/write, background countdown timers with Windows chimes/toasts, persistent todos & due reminders, open window listing/focusing, system/battery telemetry, and daily morning briefing.
- **Web Gateway & Mobile Dashboard (`server.py`, `static/`)**:
  - FastAPI REST + WebSocket backend (`/api/chat`, `/ws/chat`, `/api/health`) wired asynchronously to `agent_core.agent` via `asyncio.to_thread`.
  - Accessible mobile-responsive web UI (`static/index.html`, `static/app.js`, `static/style.css`) featuring WebSocket streaming with REST fallback, Web Speech API voice input/output with a voice mute toggle, and a **File Manager** (workspace-sandboxed browse, chunked upload, and download).
- **Telegram Remote Gateway (`scripts/telegram_bot.py`)**:
  - Long-polling Telegram bot forwarding commands to the FastAPI gateway with HTML escaping and 4000-character message chunking.

---

## Project Structure

```text
local_ai_agent/
├── config.py                     # Central configuration (Ollama, server, directories, flags)
├── agent_core.py                 # LocalAIAgent, Fast Router, and Ollama tool-calling loop
├── anureet_widget.py             # Floating corner Arc-Reactor orb & HUD desktop widget
├── server.py                     # FastAPI REST, WebSocket & File Manager gateway
├── voice_assistant.py            # AnureetVoiceEngine (Whisper/Vosk STT + Piper/Edge/SAPI5 TTS)
├── requirements.txt              # Python dependencies
├── tools/
│   ├── __init__.py
│   ├── app_modes.py              # App-specific control modes & hotkey sanitization
│   ├── deep_research_tools.py    # Concurrent DuckDuckGo research & isolated Python exec
│   ├── file_tools.py             # Local filesystem list/search/read/save tools (+ PDF)
│   ├── fl_studio_tools.py        # FL Studio launcher & shortcut macros
│   ├── gui_vision_tools.py       # Screen capture, window focus/list, Chrome & Spotify automation
│   ├── live_screen_vision.py     # Scroll and video selection helpers
│   ├── memory_store.py           # Local SQLite FTS5 memory, history, todos & reminders store
│   ├── os_tools.py               # Windows app launch/close, Notepad & system control
│   ├── premiere_tools.py         # ExtendScript (.jsx) generator & runner
│   ├── productivity_tools.py     # Clipboard, countdown timers, todos, reminders & toasts
│   └── utility_tools.py          # Cached weather, FX, system telemetry & morning briefing
├── scripts/
│   ├── premiere_automation.jsx   # ExtendScript template
│   ├── generated_premiere_task.jsx
│   └── telegram_bot.py           # Optional Telegram remote gateway
├── static/
│   ├── index.html                # Accessible tabbed web UI
│   ├── app.js                    # WebSocket/REST chat, Web Speech API, File Manager client
│   └── style.css                 # Responsive dark theme
├── tests/
│   └── test_suite.py             # Unit & FastAPI integration tests
├── model/                        # Offline Vosk speech model files
└── uploads/                      # Uploaded files, notes, and screenshots/
```

---

## Setup & Installation

### 1. Prerequisites
- **Python 3.10+** (tested on Python 3.12 on Windows)
- **Ollama** installed and running locally (`http://localhost:11434`):
  ```powershell
  ollama pull custom-llama
  # Or use another local tool-capable model such as qwen2.5:7b-instruct-q4_K_M
  ```

### 2. Install Dependencies
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> **Optional offline voice packages**: `faster-whisper`, `vosk`, `pygame`, `pillow`, and `PyGetWindow` enable offline Faster-Whisper/Vosk STT, pygame audio playback, PIL screen capture, and window-title focusing.

---

## Configuration

Settings in `config.py` can be customized directly or overridden via environment variables:

| Setting / Env Var | Default | Description |
| :--- | :--- | :--- |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama API endpoint |
| `OLLAMA_MODEL` | `custom-llama` | Default Ollama model name |
| `WHISPER_MODEL` | `small` | Multilingual Faster-Whisper model (`tiny`, `base`, `small`, `medium`, etc.) |
| `WHISPER_DEVICE` | `auto` | Compute device (`auto`, `cuda`, or `cpu`) |
| `WHISPER_LANGUAGE` | *(unset / auto)* | Optional ISO language code (`en`, `hi`, etc.); unset enables multilingual auto-detect |
| `WIDGET_HOTKEY` | `ctrl+space` | Global Windows summon hotkey for `anureet_widget.py` |
| `MEMORY_DB_PATH` | `uploads/anureet_memory.db` | Local SQLite database for long-term memory, history, todos & reminders |
| `TELEGRAM_BOT_TOKEN` | *(unset)* | Required only when running `scripts/telegram_bot.py` |
| `GATEWAY_URL` | `http://localhost:8000/api/chat` | Gateway URL targeted by `scripts/telegram_bot.py` |

---

## Running the System

### 0. One-Click Desktop Icon (Recommended — Launches Everything Silently)
Create the Desktop shortcut (`Anureet AI.lnk` with the custom Chibi 3D icon):
```powershell
.\venv\Scripts\python.exe install_desktop_shortcut.py
```
Double-clicking **Anureet AI** on your Windows Desktop launches:
- Local `ollama serve` (if not already running) + `llama3.1-gpu:latest` warmup
- `server.py` FastAPI/WebSocket Gateway + reminder watchdog (headless)
- `anureet_widget.py` floating animated avatar with Always-Listen Wake Word (`"Anureet"`) active

Click the red **`✕`** button on the widget corner, HUD header, or right-click **`Close & Stop Everything`** (or run `.\venv\Scripts\python.exe launch_anureet.py --stop`) to cleanly terminate the widget, gateway, and launcher-started background services.

### 1. Web & API Gateway
```powershell
.\venv\Scripts\python.exe server.py
```
Or with Uvicorn directly:
```powershell
.\venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8000
```
Open `http://localhost:8000` in your browser (or `http://<your-tailscale-or-lan-ip>:8000` from mobile).

### 2. Hands-Free Laptop Voice Assistant
```powershell
.\venv\Scripts\python.exe voice_assistant.py
```
Say **"Anureet"** to wake the assistant and issue commands continuously within the 60-second active window.

### 3. Floating Animated Model Widget & HUD
```powershell
.\venv\Scripts\python.exe anureet_widget.py
```
- Displays the animated Chibi 3D Anureet model (`static/assets/anureet_avatar.png`, customizable via `WIDGET_AVATAR_PATH`) with live state effects (`idle`, `listening`, `thinking`, `speaking`) and a floating instruction caption bubble.
- Single-click the avatar (or press `Ctrl+Space` / `Ctrl+Alt+Space`) to open the dark HUD chat panel, double-click or click `🎤` for voice commands, click `👂` for Always-Listen wake-word mode (`"Anureet ..."`), and press `Esc` anytime to stop speech output.

### 4. Telegram Bot (Optional)
```powershell
$env:TELEGRAM_BOT_TOKEN = "your_bot_token_here"
.\venv\Scripts\python.exe scripts/telegram_bot.py
```

---

## API Endpoints (`server.py`)

| Method / Protocol | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/api/health` | Returns gateway status, active model, and Ollama connectivity |
| `POST` | `/api/chat` | Processes `{"query": "..."}` via `agent_core.agent` |
| `WS` | `/ws/chat` | Real-time status, execution logs, and agent responses |
| `GET` | `/api/files/browse?path=.` | Lists files/folders safely within the project workspace |
| `GET` | `/api/files/download?path=...` | Downloads a workspace file |
| `POST` | `/api/files/upload` | Streams multipart file uploads into `target_dir` inside workspace |

---

## Running Tests

Run bytecode compilation and the unit/API test suite:
```powershell
.\venv\Scripts\python.exe -m compileall -q . -x "venv|\.git|__pycache__"
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```
