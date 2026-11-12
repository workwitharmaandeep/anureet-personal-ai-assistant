"""
Configuration settings for Local AI Agent System.
"""

import os
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, Field


class Config(BaseModel):
    # Ollama settings
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    default_model: str = os.getenv("OLLAMA_MODEL", "llama3.1-gpu:latest")
    ollama_timeout_seconds: int = int(os.getenv("OLLAMA_TIMEOUT", "120"))

    # Voice & Multilingual STT settings
    whisper_model_name: str = os.getenv("WHISPER_MODEL", "small")
    whisper_device: str = os.getenv("WHISPER_DEVICE", "auto")
    whisper_compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "auto")
    whisper_language: Optional[str] = os.getenv("WHISPER_LANGUAGE") or None
    voice_sample_rate: int = 16000
    extra_wake_words: List[str] = Field(default_factory=list)

    # Gateway Server settings
    server_host: str = os.getenv("SERVER_HOST", "0.0.0.0")
    server_port: int = int(os.getenv("SERVER_PORT", "8000"))
    server_reload: bool = os.getenv("SERVER_RELOAD", "false").lower() in {"1", "true", "yes"}

    # Workspace paths
    base_dir: Path = Path(__file__).resolve().parent
    uploads_dir: Path = Path(__file__).resolve().parent / "uploads"
    scripts_dir: Path = Path(__file__).resolve().parent / "scripts"
    screenshots_dir: Path = Path(__file__).resolve().parent / "uploads" / "screenshots"
    memory_db_path: Path = Path(
        os.getenv("MEMORY_DB_PATH", str(Path(__file__).resolve().parent / "uploads" / "anureet_memory.db"))
    )

    # Desktop Widget
    widget_hotkey: str = os.getenv("WIDGET_HOTKEY", "ctrl+space")
    widget_avatar_path: Path = Path(
        os.getenv(
            "WIDGET_AVATAR_PATH",
            str(Path(__file__).resolve().parent / "static" / "assets" / "anureet_avatar.png"),
        )
    )

    # Safety & Execution
    enable_code_execution: bool = True

    # Logging
    log_level: str = "INFO"


config = Config()
config.uploads_dir.mkdir(parents=True, exist_ok=True)
config.scripts_dir.mkdir(parents=True, exist_ok=True)
config.screenshots_dir.mkdir(parents=True, exist_ok=True)