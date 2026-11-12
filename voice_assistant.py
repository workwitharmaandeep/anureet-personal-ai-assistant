"""
Anureet - Offline Multilingual Local Voice AI Assistant powered by Faster-Whisper
(multilingual 'small' model with CUDA auto-detection), Ollama, PyAutoGUI, and TTS.
"""

import asyncio
import base64
import ctypes
import difflib
import io
import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import wave
from typing import Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] AnureetVoice: %(message)s")
logger = logging.getLogger("AnureetVoice")

# 1. High-Accuracy Offline Speech Recognition (Faster-Whisper / Vosk / SpeechRecognition)
try:
    import ctranslate2
except ImportError:
    ctranslate2 = None

try:
    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    WHISPER_AVAILABLE = True
except ImportError:
    np = None
    WhisperModel = None
    VadOptions = None
    get_speech_timestamps = None
    WHISPER_AVAILABLE = False

try:
    from vosk import Model as VoskModel, KaldiRecognizer
    VOSK_AVAILABLE = True
except ImportError:
    VOSK_AVAILABLE = False

try:
    import pyaudio
except ImportError:
    pyaudio = None

try:
    import speech_recognition as sr
except ImportError:
    sr = None

# 2. Audio Playback (Pygame)
try:
    import pygame
    pygame.mixer.init()
except Exception:
    pygame = None

# 3. Offline TTS Engines
try:
    import comtypes
    import comtypes.client
except ImportError:
    comtypes = None

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None
pyttsx3_engine = None  # Never reuse a cross-thread COM STA pyttsx3 instance

try:
    import edge_tts
except ImportError:
    edge_tts = None

from agent_core import agent
from config import config
from tools.memory_store import publish_widget_event

ASSISTANT_NAME = "Anureet"
SESSION_TIMEOUT_SECONDS = 60

WHISPER_INITIAL_PROMPT = (
    "Anureet, Hey Anureet. Open notepad, open chrome, open youtube, open spotify, "
    "weather, morning briefing, Bharat ki rajdhani kya hai, notepad kholo, "
    "haan bhai, kya haal hai, samjhao, kaise ho."
)

LEADING_GREETINGS = {"hey", "hi", "hello", "ok", "okay", "sun", "suno", "oye"}
DEVANAGARI_PATTERN = re.compile(r"[\u0900-\u097F]")
_EDGE_TTS_COOLDOWN_UNTIL = 0.0

_DEVANAGARI_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "क़": "q", "ख़": "kh",
    "ग़": "gh", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
}
_DEVANAGARI_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
}
_DEVANAGARI_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo",
    "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
    "ं": "n", "ँ": "n", "ः": "h",
}


def _devanagari_to_hinglish(text: str) -> str:
    """Best-effort Devanagari->Roman phonetic mapper so offline English SAPI voices speak Hindi aloud."""
    if not DEVANAGARI_PATTERN.search(text or ""):
        return text
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _DEVANAGARI_CONSONANTS:
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt == "्":
                out.append(_DEVANAGARI_CONSONANTS[ch])
                i += 2
                continue
            if nxt in _DEVANAGARI_MATRAS:
                out.append(_DEVANAGARI_CONSONANTS[ch] + _DEVANAGARI_MATRAS[nxt])
                i += 2
                continue
            out.append(_DEVANAGARI_CONSONANTS[ch] + "a")
        elif ch in _DEVANAGARI_VOWELS:
            out.append(_DEVANAGARI_VOWELS[ch])
        elif ch in _DEVANAGARI_MATRAS:
            out.append(_DEVANAGARI_MATRAS[ch])
        elif ch == "।":
            out.append(".")
        elif ch == "्":
            pass
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _build_wake_word_pattern() -> re.Pattern:
    """Compile wake-word regex covering Latin splits/variants, Devanagari, and config aliases."""
    variants = [
        r"anuree?t",
        r"anuri[td]",
        r"anree?t",
        r"anureat",
        r"anureed",
        r"annuree?t",
        r"anu\s+ree?[td]",
        r"anu\s+ri[td]",
        r"a\s+new\s+ree?[td]",
        r"अनुरित",
        r"अनुरीत",
        r"अनुरीट",
        r"अन्नुरीत",
    ]
    for extra in config.extra_wake_words:
        cleaned = extra.strip()
        if cleaned:
            variants.append(re.escape(cleaned))

    greeting_prefix = r"(?:(?:hey|hi|hello|ok|okay|sun|suno|oye|हे|हाय|हेलो)\s+)?"
    latin_or_indic = "|".join(variants)
    return re.compile(rf"(?<!\w){greeting_prefix}(?:{latin_or_indic})(?!\w)", re.IGNORECASE)


WAKE_WORD_PATTERN = _build_wake_word_pattern()

_INTERNET_CACHE: Tuple[float, bool] = (0.0, False)


def check_internet_connection(ttl_seconds: float = 60.0) -> bool:
    """Check if internet is reachable, caching the result to avoid per-utterance stalls."""
    global _INTERNET_CACHE
    now = time.monotonic()
    cached_until, cached_val = _INTERNET_CACHE
    if now < cached_until:
        return cached_val

    try:
        with socket.create_connection(("8.8.8.8", 53), timeout=1):
            _INTERNET_CACHE = (now + ttl_seconds, True)
            return True
    except OSError:
        _INTERNET_CACHE = (now + ttl_seconds, False)
        return False


def _poll_cli_interrupt() -> bool:
    """Return True immediately if the user hits Esc, q, or Ctrl+D on a Windows console."""
    if os.name != "nt" or not (sys.stdin and sys.stdin.isatty()):
        return False
    try:
        import msvcrt
        interrupted = False
        while msvcrt.kbhit():
            ch = msvcrt.getch()
            if ch in (b"\x1b", b"\x04", b"q", b"Q"):
                interrupted = True
        return interrupted
    except Exception:
        return False


def _ensure_pygame_mixer() -> bool:
    """Initialize pygame.mixer lazily if pygame is available."""
    if not pygame:
        return False
    if pygame.mixer.get_init():
        return True
    try:
        pygame.mixer.init()
        return bool(pygame.mixer.get_init())
    except Exception:
        return False


def _play_audio_file(file_path: str, stop_event: Optional[threading.Event] = None) -> bool:
    """Play an audio file synchronously via pygame mixer with <30ms speech interruption."""
    if not _ensure_pygame_mixer():
        return False
    interrupted = False
    try:
        pygame.mixer.music.load(file_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if (stop_event and stop_event.is_set()) or _poll_cli_interrupt():
                pygame.mixer.music.stop()
                if stop_event:
                    stop_event.set()
                interrupted = True
                break
            time.sleep(0.025)
    finally:
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass
    return interrupted


def _fuzzy_leading_wake_match(text: str, threshold: float = 0.75) -> Optional[str]:
    """
    Check only the leading 1-2 tokens (after optional greeting) for a close phonetic match
    to 'anureet', returning the remaining command string if matched.
    """
    tokens = list(re.finditer(r"[A-Za-z]+", text))
    if not tokens:
        return None

    idx = 0
    if tokens[0].group(0).lower() in LEADING_GREETINGS:
        idx = 1
    if idx >= len(tokens):
        return None

    # Only trigger fuzzy match at the start of the utterance
    if idx == 0 and tokens[0].start() > 2:
        return None
    if idx == 1 and tokens[0].start() > 2:
        return None

    candidates = [(tokens[idx].group(0).lower(), tokens[idx].end())]
    if idx + 1 < len(tokens):
        joined = (tokens[idx].group(0) + tokens[idx + 1].group(0)).lower()
        candidates.append((joined, tokens[idx + 1].end()))

    for cand, end_pos in candidates:
        if len(cand) < 5:
            continue
        best_ratio = max(
            difflib.SequenceMatcher(None, cand, target).ratio()
            for target in ("anureet", "anurit")
        )
        if best_ratio >= threshold:
            return text[end_pos:].lstrip(" ,.:;!?-").strip()

    return None


class AnureetVoiceEngine:
    def __init__(self, piper_model_path: str = "piper_voices/en_US-lessac-medium.onnx", init_stt: bool = True):
        self.piper_model_path = os.path.abspath(piper_model_path)
        self.recognizer = self._create_recognizer() if sr else None
        self.whisper_model = None
        self.whisper_device = "cpu"
        self.whisper_compute_type = "int8"
        self._stop_speech_event = threading.Event()
        self._stt_lock = threading.Lock()
        self._stt_initialized = False

        if init_stt and WHISPER_AVAILABLE:
            self.ensure_stt_loaded()

    def ensure_stt_loaded(self) -> None:
        """Load Faster-Whisper lazily once without blocking TTS startup."""
        if self._stt_initialized or not WHISPER_AVAILABLE:
            self._stt_initialized = True
            return
        with self._stt_lock:
            if self._stt_initialized:
                return
            self._init_whisper_model()
            self._stt_initialized = True

    def stop_speaking(self) -> None:
        """Immediately halt any active TTS playback (<30ms cutoff)."""
        self._stop_speech_event.set()
        if pygame and pygame.mixer.get_init():
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        if pyttsx3_engine:
            try:
                pyttsx3_engine.stop()
            except Exception:
                pass

    @staticmethod
    def _create_recognizer():
        rec = sr.Recognizer()
        rec.phrase_threshold = 0.2
        rec.non_speaking_duration = 0.4
        rec.pause_threshold = 0.7
        rec.dynamic_energy_threshold = True
        return rec

    @staticmethod
    def _silent_wav_bytes(duration_ms: int = 200, sample_rate: int = 16000) -> bytes:
        buf = io.BytesIO()
        frames = b"\x00\x00" * int(sample_rate * duration_ms / 1000)
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(frames)
        return buf.getvalue()

    @staticmethod
    def _is_cuda_usable() -> bool:
        if not ctranslate2 or ctranslate2.get_cuda_device_count() <= 0:
            return False
        if os.name == "nt":
            try:
                ctypes.WinDLL("cublas64_12.dll")
            except OSError as e:
                logger.info(f"CUDA GPU detected but CUDA 12 runtime DLL unavailable ({e}); using CPU.")
                return False
        return True

    @staticmethod
    def _has_speech(wav_bytes: bytes) -> bool:
        """Fast Silero VAD pre-check so pure silence skips Whisper decoding in milliseconds."""
        if np is None or get_speech_timestamps is None or VadOptions is None:
            return True
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                    return True
                sample_rate = wf.getframerate()
                pcm = wf.readframes(wf.getnframes())
        except wave.Error:
            return True

        if not pcm:
            return False

        audio_f32 = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        speech_chunks = get_speech_timestamps(
            audio_f32,
            vad_options=VadOptions(min_silence_duration_ms=300),
            sampling_rate=sample_rate,
        )
        return bool(speech_chunks)

    @staticmethod
    def _cpu_threads() -> int:
        return min(8, max(4, os.cpu_count() or 4))

    def _init_whisper_model(self) -> None:
        model_name = config.whisper_model_name
        device = config.whisper_device
        compute_type = config.whisper_compute_type

        if device == "auto":
            attempts = (
                [("cuda", "int8_float16" if compute_type == "auto" else compute_type), ("cpu", "int8")]
                if self._is_cuda_usable()
                else [("cpu", "int8" if compute_type == "auto" else compute_type)]
            )
        else:
            resolved_ct = compute_type if compute_type != "auto" else ("int8_float16" if device == "cuda" else "int8")
            attempts = [(device, resolved_ct)]

        for dev, ct in attempts:
            try:
                logger.info(f"Initializing Faster-Whisper '{model_name}' (device={dev}, compute_type={ct})...")
                init_kwargs = {"device": dev, "compute_type": ct}
                if dev == "cpu":
                    init_kwargs["cpu_threads"] = self._cpu_threads()
                candidate = WhisperModel(model_name, **init_kwargs)
                if dev == "cuda":
                    # Probe CUDA runtime DLLs (cuBLAS/cuDNN) early before accepting CUDA.
                    segs, _ = candidate.transcribe(
                        io.BytesIO(self._silent_wav_bytes()),
                        beam_size=1,
                        vad_filter=False,
                    )
                    list(segs)
                self.whisper_model = candidate
                self.whisper_device = dev
                self.whisper_compute_type = ct
                logger.info(f"Faster-Whisper '{model_name}' loaded successfully on {dev}!")
                return
            except Exception as e:
                logger.warning(f"Faster-Whisper init/probe failed on {dev} ({ct}): {e}")

    def _run_whisper_decode(self, wav_bytes: bytes, kwargs: dict) -> str:
        segments, info = self.whisper_model.transcribe(io.BytesIO(wav_bytes), **kwargs)
        seg_list = list(segments)
        fallback_lang = self._select_fallback_language(info)
        if fallback_lang:
            logger.info(
                f"Low-confidence '{getattr(info, 'language', '')}' detection "
                f"({getattr(info, 'language_probability', 0.0):.2f}); retrying as '{fallback_lang}'..."
            )
            segments, _ = self.whisper_model.transcribe(
                io.BytesIO(wav_bytes),
                **dict(kwargs, language=fallback_lang),
            )
            seg_list = list(segments)
        return " ".join(segment.text for segment in seg_list).strip()

    def _transcribe_whisper(self, wav_bytes: bytes) -> str:
        """Transcribe WAV audio bytes using Faster-Whisper with fast silence skip and wake-word biasing."""
        if not self._has_speech(wav_bytes):
            return ""

        kwargs = {
            "language": config.whisper_language,
            "multilingual": False,
            "beam_size": 1,
            "best_of": 1,
            "temperature": 0.0,
            "vad_filter": True,
            "vad_parameters": {"min_silence_duration_ms": 300},
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.6,
            "initial_prompt": WHISPER_INITIAL_PROMPT,
            "hotwords": "Anureet, Hey Anureet",
        }
        try:
            return self._run_whisper_decode(wav_bytes, kwargs)
        except Exception as e:
            err_msg = str(e).lower()
            is_cuda_runtime_err = any(tok in err_msg for tok in ("cublas", "cudnn", "cuda"))
            if (
                WhisperModel is not None
                and getattr(self, "whisper_device", "cpu") == "cuda"
                and config.whisper_device == "auto"
                and is_cuda_runtime_err
            ):
                logger.warning(f"CUDA transcription failed ({e}); falling back to CPU (int8)...")
                self.whisper_model = WhisperModel(
                    config.whisper_model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=self._cpu_threads(),
                )
                self.whisper_device = "cpu"
                self.whisper_compute_type = "int8"
                return self._run_whisper_decode(wav_bytes, kwargs)
            raise

    @staticmethod
    def _select_fallback_language(info) -> Optional[str]:
        """Rescue low-confidence non-en/hi misdetections (e.g. 'ro' 0.08) toward 'hi' or 'en'."""
        if info is None or config.whisper_language is not None:
            return None
        detected = getattr(info, "language", None)
        prob = float(getattr(info, "language_probability", 1.0) or 0.0)
        if not detected or detected in {"en", "hi"} or prob >= 0.70:
            return None
        all_probs = dict(getattr(info, "all_language_probs", None) or [])
        hi_prob = float(all_probs.get("hi", 0.0))
        en_prob = float(all_probs.get("en", 0.0))
        return "hi" if hi_prob > en_prob else "en"

    @staticmethod
    def extract_wake_command(text: str, active_session_until: float, now: Optional[float] = None) -> Tuple[bool, str]:
        """Determine whether a transcript triggers Anureet and extract the command string."""
        cleaned = (text or "").strip()
        if not cleaned:
            return False, ""

        match = WAKE_WORD_PATTERN.search(cleaned)
        if match:
            command_part = cleaned[match.end():].lstrip(" ,.:;!?-").strip()
            return True, command_part

        fuzzy_cmd = _fuzzy_leading_wake_match(cleaned)
        if fuzzy_cmd is not None:
            return True, fuzzy_cmd

        current_time = time.time() if now is None else now
        if current_time < active_session_until:
            return True, cleaned

        return False, ""

    @staticmethod
    def _emit_widget_event(state: str, user_text: str = "", agent_text: str = "") -> None:
        try:
            publish_widget_event(state, user_text=user_text, agent_text=agent_text, source="voice")
        except Exception as e:
            logger.debug(f"Widget event sync skipped: {e}")

    def _handle_command(self, command_part: str) -> float:
        """Execute an extracted voice command (or prompt for follow-up) and update session deadline."""
        if (
            not command_part
            or len(command_part) < 2
            or command_part.lower() in {"i am listening", "yes"}
        ):
            prompt = "Yes, I am listening! Aap kya karna chahte hain?"
            self._emit_widget_event("speaking", user_text="Anureet", agent_text=prompt)
            self.speak(prompt)
            self._emit_widget_event("idle", user_text="Anureet", agent_text=prompt)
            return time.time() + SESSION_TIMEOUT_SECONDS

        print(f"\n[Anureet Active 🎙️] Executing: '{command_part}'")
        self._emit_widget_event("thinking", user_text=command_part)
        res_dict = agent.process_query(command_part, session_id="voice")
        response = res_dict.get("response_text", "")

        print(f"[Anureet Response 🗣️]: {response}\n")
        if response:
            self._emit_widget_event("speaking", user_text=command_part, agent_text=response)
            self.speak(response)
        self._emit_widget_event("idle", user_text=command_part, agent_text=response)
        return time.time() + SESSION_TIMEOUT_SECONDS

    def speak(self, text: str):
        """Synthesize and speak text aloud with <30ms Esc/q/Ctrl+D interruption support."""
        global _EDGE_TTS_COOLDOWN_UNTIL
        logger.info(f"Anureet Speaking: {text}")
        clean_text = str(text).replace("*", "").strip()
        if not clean_text:
            return
        self._stop_speech_event.clear()

        # 1. Try Piper Neural TTS (100% Offline Human Neural Voice)
        if os.path.exists(self.piper_model_path):
            try:
                self._speak_piper_tts(clean_text)
                return
            except Exception as e:
                logger.warning(f"Piper TTS playback failed: {e}. Trying fallback TTS...")

        # 2. Try Edge-TTS (Only if Internet is Connected & Not in Error Cooldown)
        now = time.monotonic()
        if (
            not self._stop_speech_event.is_set()
            and edge_tts
            and now >= _EDGE_TTS_COOLDOWN_UNTIL
            and check_internet_connection()
        ):
            try:
                asyncio.run(asyncio.wait_for(self._speak_edge_tts(clean_text), timeout=6.0))
                return
            except Exception as e:
                _EDGE_TTS_COOLDOWN_UNTIL = time.monotonic() + 600.0
                logger.warning(f"Edge-TTS unavailable ({e}); falling back to offline voice for 10m.")

        if self._stop_speech_event.is_set():
            return

        # 3. Offline Windows WinRT Neural-Quality Indian Voice (Microsoft Heera en-IN -> WAV -> Pygame)
        if os.name == "nt":
            try:
                self._speak_winrt_wav(clean_text)
                return
            except Exception as e:
                logger.warning(f"WinRT Heera WAV synthesis failed: {e}. Trying SAPI WAV...")

        if self._stop_speech_event.is_set():
            return

        # 4. Thread-Safe Offline Windows COM SAPI5 -> WAV -> Pygame Mixer (<30ms Esc Interrupt)
        if os.name == "nt" and comtypes is not None:
            try:
                self._speak_sapi_wav(clean_text)
                return
            except Exception as e:
                logger.warning(f"SAPI WAV synthesis failed: {e}. Trying PowerShell SAPI...")

        # 5. Windows Native PowerShell SAPI5 TTS Fallback (also thread-safe isolated process)
        if os.name == "nt":
            try:
                spoken_text = _devanagari_to_hinglish(clean_text)
                literal_text = spoken_text.replace("'", "''")
                ps_script = (
                    "Add-Type -AssemblyName System.Speech; "
                    "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                    "$v = $synth.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Gender -eq 'Female' } | Select-Object -First 1; "
                    "if ($v) { $synth.SelectVoice($v.VoiceInfo.Name) }; "
                    f"$synth.Speak('{literal_text}'); "
                    "$synth.Dispose()"
                )
                encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
                subprocess.run(
                    ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return
            except Exception as e:
                logger.error(f"PowerShell TTS failed: {e}")

        # 6. Non-Windows / generic thread-local pyttsx3 fallback
        if pyttsx3 is not None:
            try:
                engine = pyttsx3.init()
                engine.setProperty("rate", 170)
                engine.say(clean_text)
                engine.runAndWait()
            except Exception as e:
                logger.error(f"pyttsx3 speak failed: {e}")

    def _speak_winrt_wav(self, text: str) -> None:
        """Synthesize offline Windows WinRT SpeechSynthesizer (Microsoft Heera en-IN) into WAV."""
        if not _ensure_pygame_mixer():
            raise RuntimeError("pygame mixer unavailable")

        spoken_text = _devanagari_to_hinglish(text) if DEVANAGARI_PATTERN.search(text) else text
        literal_text = spoken_text.replace("'", "''")
        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                wav_path = tmp.name

            wav_escaped = wav_path.replace("'", "''")
            ps_script = (
                "$ErrorActionPreference = 'Stop'; "
                "Add-Type -AssemblyName System.Runtime.WindowsRuntime; "
                "[Windows.Media.SpeechSynthesis.SpeechSynthesizer, Windows.Media.SpeechSynthesis, ContentType=WindowsRuntime] | Out-Null; "
                "[Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null; "
                "$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | "
                "Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]; "
                "Function Await($op, $resType) { "
                "$netTask = $asTaskGeneric.MakeGenericMethod($resType).Invoke($null, @($op)); "
                "$netTask.Wait(-1) | Out-Null; "
                "$netTask.Result "
                "}; "
                "$synth = New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer; "
                "$voice = [Windows.Media.SpeechSynthesis.SpeechSynthesizer]::AllVoices | Where-Object { $_.DisplayName -like '*Heera*' -or $_.Language -like 'en-IN*' } | Select-Object -First 1; "
                "if ($voice) { $synth.Voice = $voice }; "
                "try { $synth.Options.SpeakingRate = 1.04 } catch {}; "
                f"$stream = Await ($synth.SynthesizeTextToStreamAsync('{literal_text}')) ([Windows.Media.SpeechSynthesis.SpeechSynthesisStream]); "
                "$size = [uint32]$stream.Size; "
                "$reader = New-Object Windows.Storage.Streams.DataReader($stream); "
                "Await ($reader.LoadAsync($size)) ([uint32]) | Out-Null; "
                "$bytes = New-Object byte[] $size; "
                "$reader.ReadBytes($bytes); "
                f"[System.IO.File]::WriteAllBytes('{wav_escaped}', $bytes); "
                "$reader.Dispose(); $stream.Dispose(); $synth.Dispose()"
            )
            encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
            res = subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
                capture_output=True,
                text=True,
                timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if res.returncode != 0:
                raise RuntimeError((res.stderr or res.stdout or f"exit {res.returncode}").strip())
            if self._stop_speech_event.is_set():
                return
            if not os.path.exists(wav_path) or os.path.getsize(wav_path) <= 44:
                raise RuntimeError("WinRT Heera produced empty WAV output")
            _play_audio_file(wav_path, stop_event=self._stop_speech_event)
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    def _speak_sapi_wav(self, text: str) -> None:
        """Synthesize offline Windows SAPI5 speech into WAV and play via pygame mixer."""
        if not _ensure_pygame_mixer():
            raise RuntimeError("pygame mixer unavailable")

        wav_path = None
        com_initialized = False
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                wav_path = tmp.name

            comtypes.CoInitialize()
            com_initialized = True
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            voices = voice.GetVoices()
            selected_desc = ""
            for i in range(voices.Count):
                desc = voices.Item(i).GetDescription()
                low = desc.lower()
                if any(name in low for name in ("heera", "neerja", "swara", "kalpana", "zira", "female")):
                    voice.Voice = voices.Item(i)
                    selected_desc = low
                    break

            speak_text = text
            if DEVANAGARI_PATTERN.search(text) and not any(h in selected_desc for h in ("kalpana", "hemant", "hindi", "hi-in")):
                speak_text = _devanagari_to_hinglish(text)

            stream = comtypes.client.CreateObject("SAPI.SpFileStream")
            stream.Open(wav_path, 3, False)
            voice.AudioOutputStream = stream
            voice.Rate = 1
            voice.Speak(speak_text, 0)
            stream.Close()

            if self._stop_speech_event.is_set():
                return
            if not os.path.exists(wav_path) or os.path.getsize(wav_path) <= 44:
                raise RuntimeError("SAPI produced empty WAV output")
            _play_audio_file(wav_path, stop_event=self._stop_speech_event)
        finally:
            if com_initialized:
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    def _speak_piper_tts(self, text: str):
        wav_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
                wav_path = tmp.name
            subprocess.run(
                ["piper", "--model", self.piper_model_path, "--output_file", wav_path],
                input=text,
                text=True,
                check=True,
            )
            _play_audio_file(wav_path, stop_event=self._stop_speech_event)
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError:
                    pass

    async def _speak_edge_tts(self, text: str):
        if pygame and pygame.mixer.get_init():
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass

        voice = "hi-IN-SwaraNeural" if DEVANAGARI_PATTERN.search(text) else "en-IN-NeerjaNeural"
        mp3_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                mp3_path = tmp.name
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(mp3_path)
            if os.path.getsize(mp3_path) > 0:
                _play_audio_file(mp3_path, stop_event=self._stop_speech_event)
        finally:
            if mp3_path and os.path.exists(mp3_path):
                try:
                    os.remove(mp3_path)
                except OSError:
                    pass

    @staticmethod
    def _open_microphone():
        """Open microphone at 16 kHz for Whisper, falling back to default rate if unsupported."""
        try:
            return sr.Microphone(sample_rate=config.voice_sample_rate)
        except Exception:
            return sr.Microphone()

    def listen_once(self, timeout: int = 6, phrase_time_limit: int = 10) -> str:
        """Capture and transcribe a single voice utterance using Faster-Whisper or SpeechRecognition."""
        if not self.recognizer:
            return ""
        self.ensure_stt_loaded()
        try:
            with self._open_microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.35)
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
            if WHISPER_AVAILABLE and self.whisper_model:
                return self._transcribe_whisper(audio.get_wav_data())
            raw_text = self.recognizer.recognize_google(audio, language="en-IN")
            return str(raw_text[0]) if isinstance(raw_text, list) and raw_text else str(raw_text)
        except Exception as e:
            logger.debug(f"Single-shot listen skipped/timed out: {e}")
            return ""

    def listen_and_process(self):
        """Continuous background listener with 1-Minute Active Follow-Up Window."""
        self.ensure_stt_loaded()
        print("\n" + "=" * 60)
        print(f"  🎙️  Anureet Voice Assistant (LLM: {config.default_model}, STT: {config.whisper_model_name}/{self.whisper_device})")
        print(f"  Say '{ASSISTANT_NAME}' to wake her up (English, Hindi, Hinglish & multilingual supported)!")
        print("  Once awake, you can talk continuously for 1 minute without repeating her name!")
        print("=" * 60 + "\n")

        active_session_until = 0.0

        # Option A: High-Accuracy Multilingual Faster-Whisper Offline Listener
        if self.whisper_model and sr:
            logger.info("Starting 100% Offline Multilingual Faster-Whisper Listener...")
            self._listen_whisper(active_session_until)
            return

        # Option B: Offline Vosk Listener
        vosk_model_path = os.path.abspath("model")
        if VOSK_AVAILABLE and pyaudio and os.path.exists(vosk_model_path):
            logger.info("Starting Offline Vosk Listener...")
            self._listen_vosk(vosk_model_path, active_session_until)
            return

        # Option C: SpeechRecognition Fallback
        if sr:
            logger.info("Starting SpeechRecognition Listener...")
            self._listen_speech_recognition(active_session_until)

    @staticmethod
    def _pause_mic_stream(source) -> None:
        """Stop microphone audio capture while processing & speaking to avoid self-echo/buffering."""
        try:
            py_stream = getattr(getattr(source, "stream", None), "pyaudio_stream", None)
            if py_stream is not None and not py_stream.is_stopped():
                py_stream.stop_stream()
        except Exception:
            pass

    @staticmethod
    def _resume_mic_stream(source) -> None:
        """Resume microphone audio capture after processing & TTS finish."""
        try:
            py_stream = getattr(getattr(source, "stream", None), "pyaudio_stream", None)
            if py_stream is not None and py_stream.is_stopped():
                py_stream.start_stream()
        except Exception:
            pass

    def _run_command_with_mic_paused(self, command_part: str, pause_fn, resume_fn) -> float:
        """Execute command and TTS with microphone paused cleanly in a finally block."""
        pause_fn()
        try:
            return self._handle_command(command_part)
        finally:
            time.sleep(0.25)
            resume_fn()

    def _listen_whisper(self, active_session_until: float):
        with self._open_microphone() as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            logger.info("Microphone calibrated for ambient noise.")

            while True:
                try:
                    now = time.time()
                    if now < active_session_until:
                        logger.info(f"Conversational Follow-Up Mode Active ({int(active_session_until - now)}s remaining)...")
                        self._emit_widget_event("listening")
                    else:
                        logger.info("Listening for wake word 'Anureet'...")

                    audio = self.recognizer.listen(source, timeout=8, phrase_time_limit=12)
                    text = self._transcribe_whisper(audio.get_wav_data())
                    if not text:
                        continue

                    logger.info(f"Heard (Faster-Whisper): '{text}'")
                    wake_found, command_part = self.extract_wake_command(text, active_session_until)
                    if wake_found:
                        active_session_until = self._run_command_with_mic_paused(
                            command_part,
                            lambda: self._pause_mic_stream(source),
                            lambda: self._resume_mic_stream(source),
                        )

                except sr.WaitTimeoutError:
                    if time.time() >= active_session_until > 0:
                        logger.info("Conversational session idle for 1 minute. Reverting to background wake-word listening.")
                        active_session_until = 0.0
                        self._emit_widget_event("idle")
                except KeyboardInterrupt:
                    print("\nAnureet Voice Assistant stopped.")
                    break
                except Exception as e:
                    logger.error(f"Whisper listener error: {e}")
                    time.sleep(1)

    def _listen_vosk(self, model_path: str, active_session_until: float):
        vosk_model = VoskModel(model_path)
        recognizer = KaldiRecognizer(vosk_model, 16000)
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=8000)
        stream.start_stream()

        try:
            while True:
                try:
                    data = stream.read(4000, exception_on_overflow=False)
                    if not data:
                        continue

                    if recognizer.AcceptWaveform(data):
                        res = json.loads(recognizer.Result())
                        text = res.get("text", "").strip()
                        if not text:
                            continue

                        logger.info(f"Heard (Offline Vosk): '{text}'")
                        wake_found, command_part = self.extract_wake_command(text, active_session_until)
                        if wake_found:
                            active_session_until = self._run_command_with_mic_paused(
                                command_part,
                                stream.stop_stream,
                                stream.start_stream,
                            )

                except KeyboardInterrupt:
                    print("\nVoice assistant stopped.")
                    break
                except Exception as e:
                    logger.error(f"Vosk listener error: {e}")
                    time.sleep(1)
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
            p.terminate()

    def _listen_speech_recognition(self, active_session_until: float):
        with self._open_microphone() as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            logger.info("Microphone calibrated for ambient noise.")

            while True:
                try:
                    now = time.time()
                    if now < active_session_until:
                        logger.info(f"Conversational Follow-Up Mode Active ({int(active_session_until - now)}s remaining)...")
                        self._emit_widget_event("listening")
                    else:
                        logger.info("Listening for wake word 'Anureet'...")

                    audio = self.recognizer.listen(source, timeout=8, phrase_time_limit=12)
                    raw_text = self.recognizer.recognize_google(audio, language="en-IN")
                    text = str(raw_text[0]) if isinstance(raw_text, list) and raw_text else str(raw_text)

                    logger.info(f"Heard: '{text}'")
                    wake_found, command_part = self.extract_wake_command(text, active_session_until)
                    if wake_found:
                        active_session_until = self._run_command_with_mic_paused(
                            command_part,
                            lambda: self._pause_mic_stream(source),
                            lambda: self._resume_mic_stream(source),
                        )

                except sr.WaitTimeoutError:
                    if time.time() >= active_session_until > 0:
                        logger.info("Conversational session idle for 1 minute. Reverting to background wake-word listening.")
                        active_session_until = 0.0
                        self._emit_widget_event("idle")
                except sr.UnknownValueError:
                    continue
                except KeyboardInterrupt:
                    print("\nAnureet Voice Assistant stopped.")
                    break
                except Exception as e:
                    logger.error(f"Voice loop error: {e}")
                    time.sleep(1)


if __name__ == "__main__":
    assistant = AnureetVoiceEngine()
    if not agent.check_ollama_status():
        logger.warning(
            f"Ollama is not reachable at {config.ollama_base_url}. "
            "Start Ollama for conversational replies (fast desktop/weather commands still work)."
        )
    assistant.speak("Anureet voice assistant initialized. Ready for your commands.")
    assistant.listen_and_process()
