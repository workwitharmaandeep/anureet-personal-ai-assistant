"""
Anureet - Offline Multilingual Local Voice AI Assistant powered by Faster-Whisper
(multilingual 'small' model with CUDA auto-detection), Ollama, PyAutoGUI, and TTS.
"""

import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["OLLAMA_HOST"] = "127.0.0.1:11434"
os.environ["OLLAMA_FLASH_ATTENTION"] = "1"
os.environ["OLLAMA_KV_CACHE_TYPE"] = "q8_0"
os.environ["OLLAMA_MAX_LOADED_MODELS"] = "1"
os.environ["OLLAMA_NUM_PARALLEL"] = "1"
os.environ["OLLAMA_NOPRUNE"] = "1"

import asyncio
import base64
import ctypes
import difflib
import io
import json
import logging
import queue
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import wave
from typing import Optional, Tuple
from xml.sax.saxutils import escape as xml_escape

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")
if sys.stdin is None:
    try:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")
    except Exception:
        pass

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

# 2. Offline Native Windows SAPI5 TTS (pywin32 COM STA Worker Thread, Zero Cloud Calls)
try:
    import pythoncom
    import win32com.client
    SAPI5_AVAILABLE = True
except ImportError:
    pythoncom = None
    win32com = None
    SAPI5_AVAILABLE = False

try:
    import comtypes
    import comtypes.client
except ImportError:
    comtypes = None

SVSFlagsAsync = 1
SVSFPurgeBeforeSpeak = 2
SVSFIsXML = 8
SRSE_IS_SPEAKING = 2
_ONECORE_VOICES_CATEGORY = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"

from agent_core import agent
from config import config
from tools.memory_store import publish_widget_event

ASSISTANT_NAME = "Anureet"
SESSION_TIMEOUT_SECONDS = 60

WHISPER_INITIAL_PROMPT = (
    "Anureet, Hey Anureet. CapCut, Google Chrome, Spotify, Notepad, YouTube, VS Code, "
    "FL Studio, Premiere Pro, After Effects, Task Manager, File Explorer, Word, Excel, PowerPoint. "
    "Click on, audio, create project, switch tab, next tab, new folder, minimize all, close window, "
    "weather in Kurukshetra, Bharat ki rajdhani kya hai, notepad kholo, haan bhai, kya haal hai, samjhao, kaise ho."
)

LEADING_GREETINGS = {"hey", "hi", "hello", "ok", "okay", "sun", "suno", "oye"}
DEVANAGARI_PATTERN = re.compile(r"[\u0900-\u097F]")

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

CONTINUOUS_START_PATTERN = re.compile(
    r"^(?:anureet[,\s]+)?(?:please\s+)?(?:"
    r"listen\s+continuously|continuous(?:ly)?\s+listen|"
    r"start\s+continuous(?:\s+chat|\s+mode|\s+listening)?|"
    r"continuous\s+(?:chat|mode|listening)|"
    r"continuous\s+chat\s+mode|1\s*min(?:ute)?\s+continuous\s+chat|"
    r"baat\s+karte\s+raho|continuous\s+suno"
    r")\s*[.!?]*$",
    re.IGNORECASE,
)

CONTINUOUS_STOP_PATTERN = re.compile(
    r"^(?:anureet[,\s]+)?(?:please\s+)?(?:"
    r"stop\s+continuous(?:\s+chat|\s+mode|\s+listening)?|"
    r"exit\s+continuous(?:\s+chat|\s+mode)?|"
    r"stop\s+listening\s+continuously|"
    r"normal\s+mode|wake\s*word\s+mode|strict\s+mode|"
    r"continuous\s+band\s+karo|bas\s+anureet"
    r")\s*[.!?]*$",
    re.IGNORECASE,
)

_INTERNET_CACHE: Tuple[float, bool] = (0.0, False)


def check_internet_connection(ttl_seconds: float = 60.0) -> bool:
    """Air-gapped mode: always returns False without opening any external socket."""
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
        self.is_speaking = threading.Event()
        self._stt_lock = threading.Lock()
        self._stt_initialized = False

        # Dedicated COM STA SAPI5 worker thread + queue for non-blocking pipelined clauses & <5ms barge-in
        self._tts_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._tts_done_event = threading.Event()
        self._tts_done_event.set()
        self._tts_voice = None
        self._tts_selected_desc = ""
        self._tts_worker_started = False
        self._tts_worker_lock = threading.Lock()
        self.continuous_chat_mode = False
        self.continuous_chat_until = 0.0
        self._start_tts_worker()

    def start_continuous_mode(self, duration_seconds: float = 60.0) -> None:
        """Activate 1-minute continuous chat mode where user can speak without repeating 'Anureet'."""
        self.continuous_chat_mode = True
        self.continuous_chat_until = time.time() + duration_seconds
        logger.info(f"Continuous Chat Mode ACTIVATED for {int(duration_seconds)}s.")
        self._emit_widget_event("continuous_chat", user_text="start", agent_text="Continuous chat mode activated.")

    def stop_continuous_mode(self) -> None:
        """Deactivate continuous chat mode and revert to strict wake-word mode ('Anureet ...')."""
        self.continuous_chat_mode = False
        self.continuous_chat_until = 0.0
        logger.info("Continuous Chat Mode DEACTIVATED. Reverting to strict wake-word mode.")
        self._emit_widget_event("continuous_chat", user_text="stop", agent_text="Continuous chat mode stopped.")

    def is_continuous_mode_active(self) -> bool:
        """Return True if 1-minute continuous chat mode is currently active and within time limit."""
        if not self.continuous_chat_mode:
            return False
        if time.time() >= self.continuous_chat_until:
            self.stop_continuous_mode()
            return False
        return True

        if init_stt and WHISPER_AVAILABLE:
            self.ensure_stt_loaded()

    def _start_tts_worker(self) -> None:
        """Start dedicated background COM STA SAPI5 speech worker thread once."""
        if not (os.name == "nt" and SAPI5_AVAILABLE):
            return
        with self._tts_worker_lock:
            if self._tts_worker_started:
                return
            worker = threading.Thread(target=self._sapi5_worker_loop, name="AnureetSAPI5Worker", daemon=True)
            worker.start()
            self._tts_worker_started = True

    @staticmethod
    def _collect_sapi5_tokens(voice) -> list:
        """Collect voice tokens from Windows OneCore (includes Microsoft Heera en-IN) and classic SAPI5."""
        tokens = []
        cat = None
        if win32com is not None:
            try:
                cat = win32com.client.Dispatch("SAPI.SpObjectTokenCategory")
            except Exception:
                cat = None
        if cat is None and comtypes is not None:
            try:
                cat = comtypes.client.CreateObject("SAPI.SpObjectTokenCategory")
            except Exception:
                cat = None

        if cat is not None:
            try:
                cat.SetId(_ONECORE_VOICES_CATEGORY, False)
                oc_tokens = cat.EnumerateTokens()
                for i in range(oc_tokens.Count):
                    tokens.append(oc_tokens.Item(i))
            except Exception as e:
                logger.debug(f"OneCore SAPI5 voice scan skipped: {e}")

        try:
            std_tokens = voice.GetVoices()
            for i in range(std_tokens.Count):
                tokens.append(std_tokens.Item(i))
        except Exception as e:
            logger.debug(f"Classic SAPI5 voice scan skipped: {e}")
        return tokens

    @staticmethod
    def _score_indian_female_token(desc: str, gender: str, lang: str) -> int:
        low_desc = (desc or "").lower()
        low_gender = (gender or "").lower()
        low_lang = (lang or "").lower()

        indian_female_names = ("heera", "kalpana", "neerja", "swara", "aditi", "raveena", "priya")
        if any(n in low_desc for n in indian_female_names):
            return 200
        if low_gender == "female" and (
            "india" in low_desc or "4009" in low_lang or "439" in low_lang or "en-in" in low_lang or "hi-in" in low_lang
        ):
            return 180
        if low_gender == "female" or any(n in low_desc for n in ("zira", "female", "hazel", "susan")):
            return 100
        if low_gender == "male":
            return -100
        return 0

    @classmethod
    def _select_sapi5_female_voice(cls, voice) -> str:
        """Select Indian Female voice (Microsoft Heera en-IN preferred) over OneCore/classic SAPI5."""
        best_token = None
        best_desc = ""
        best_score = -1

        try:
            for tok in cls._collect_sapi5_tokens(voice):
                try:
                    desc = str(tok.GetDescription() or "")
                except Exception:
                    continue
                try:
                    gender = str(tok.GetAttribute("Gender") or "")
                except Exception:
                    gender = ""
                try:
                    lang = str(tok.GetAttribute("Language") or "")
                except Exception:
                    lang = ""
                score = cls._score_indian_female_token(desc, gender, lang)
                if score > best_score:
                    best_score = score
                    best_token = tok
                    best_desc = desc

            if best_token is not None:
                voice.Voice = best_token
                logger.info(f"Selected Offline Female Voice: {best_desc}")
                return best_desc.lower()
        except Exception as e:
            logger.debug(f"SAPI5 female voice selection skipped: {e}")
        return ""

    @staticmethod
    def _format_soft_indian_ssml(spoken_text: str) -> str:
        """Wrap spoken text in gentle SAPI5 XML prosody for a soft, clear delivery."""
        safe = xml_escape(spoken_text)
        return f'<pitch absmiddle="+1"><rate absspeed="0">{safe}</rate></pitch>'

    def _prepare_spoken_text(self, text: str) -> str:
        clean_text = str(text or "").replace("*", "").strip()
        if not clean_text:
            return ""
        if DEVANAGARI_PATTERN.search(clean_text) and not any(
            h in getattr(self, "_tts_selected_desc", "") for h in ("kalpana", "hemant", "hindi", "hi-in")
        ):
            return _devanagari_to_hinglish(clean_text)
        return clean_text


    @classmethod
    def synthesize_offline_female_wav(cls, text: str) -> bytes:
        """Synthesize text offline using Windows OneCore / SAPI5 Microsoft Heera into WAV audio bytes."""
        clean = str(text or "").replace("*", "").strip()
        if not clean:
            return b""
        if DEVANAGARI_PATTERN.search(clean):
            clean = _devanagari_to_hinglish(clean)

        com_init = False
        try:
            pythoncom.CoInitialize()
            com_init = True
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            cls._select_sapi5_female_voice(voice)
            voice.Rate = 0
            voice.Volume = 95
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                # 3 = SSFMCreateForWrite
                stream.Open(tmp_path, 3)
                voice.AudioOutputStream = stream
                safe_text = xml_escape(clean)
                ssml = f'<pitch absmiddle="+1"><rate absspeed="0">{safe_text}</rate></pitch>'
                voice.Speak(ssml, SVSFIsXML)
                stream.Close()
                with open(tmp_path, "rb") as f:
                    return f.read()
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"Offline female WAV synthesis error: {e}")
            return b""
        finally:
            if com_init:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def _sapi5_worker_loop(self) -> None:
        """Background COM STA thread running SAPI.SpVoice asynchronously with instant purge support."""
        com_init = False
        voice = None
        try:
            pythoncom.CoInitialize()
            com_init = True
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            self._tts_selected_desc = self._select_sapi5_female_voice(voice)
            voice.Rate = 0
            voice.Volume = 92
            self._tts_voice = voice

            while True:
                item = self._tts_queue.get()
                try:
                    if item is None:
                        break
                    if self._stop_speech_event.is_set():
                        continue
                    spoken = self._prepare_spoken_text(item)
                    if not spoken:
                        continue
                    self.is_speaking.set()
                    self._tts_done_event.clear()
                    voice.Speak(self._format_soft_indian_ssml(spoken), SVSFlagsAsync | SVSFIsXML)

                    # Poll WaitUntilDone in 35ms slices for <35ms Esc / barge-in cancellation
                    loop_start = time.monotonic()
                    max_duration = max(10.0, len(spoken) * 0.16 + 5.0)
                    while not self._stop_speech_event.is_set():
                        if _poll_cli_interrupt():
                            self._stop_speech_event.set()
                            break
                        try:
                            finished = voice.WaitUntilDone(35)
                        except Exception:
                            finished = True
                        if finished:
                            break
                        if time.monotonic() - loop_start >= max_duration:
                            logger.debug("TTS playback reached safety watchdog threshold.")
                            break

                    if self._stop_speech_event.is_set():
                        try:
                            voice.Speak("", SVSFPurgeBeforeSpeak)
                        except Exception:
                            pass
                finally:
                    if self._tts_queue.empty():
                        self.is_speaking.clear()
                        self._tts_done_event.set()
                    self._tts_queue.task_done()
        except Exception as e:
            logger.error(f"SAPI5 worker loop failed: {e}")
        finally:
            self.is_speaking.clear()
            self._tts_done_event.set()
            self._tts_voice = None
            if com_init:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def is_speaking_active(self) -> bool:
        """Echo guard check: True while SAPI5 is actively speaking or draining queued clauses."""
        if getattr(self, "is_speaking", None) and self.is_speaking.is_set():
            return True
        q = getattr(self, "_tts_queue", None)
        if q is not None and not q.empty():
            return True
        voice = getattr(self, "_tts_voice", None)
        if voice is not None:
            try:
                return not voice.WaitUntilDone(0)
            except Exception:
                try:
                    return getattr(voice.Status, "RunningState", 0) in (0, 2)
                except Exception:
                    return False
        return False

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
        """Immediately halt any active TTS playback (<5ms SVSFPurgeBeforeSpeak barge-in)."""
        self._stop_speech_event.set()
        q = getattr(self, "_tts_queue", None)
        if q is not None:
            while not q.empty():
                try:
                    q.get_nowait()
                    q.task_done()
                except Exception:
                    break
        voice = getattr(self, "_tts_voice", None)
        if voice is not None:
            try:
                voice.Speak("", SVSFPurgeBeforeSpeak)
            except Exception:
                pass
        if getattr(self, "is_speaking", None) is not None:
            self.is_speaking.clear()
        if getattr(self, "_tts_done_event", None) is not None:
            self._tts_done_event.set()

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
            vad_options=VadOptions(threshold=0.52, min_speech_duration_ms=180, min_silence_duration_ms=300),
            sampling_rate=sample_rate,
        )
        if not speech_chunks:
            return False
        voiced_samples = sum(max(0, int(c.get("end", 0)) - int(c.get("start", 0))) for c in speech_chunks if isinstance(c, dict))
        if speech_chunks and voiced_samples > 0 and voiced_samples < int(sample_rate * 0.15):
            return False
        return True

    @staticmethod
    def _cpu_threads() -> int:
        return 4

    def _init_whisper_model(self) -> None:
        """Load Faster-Whisper strictly offline on CPU int8 (0 MB VRAM, no HF network calls)."""
        model_name = config.whisper_model_name
        dev = "cpu"
        ct = "int8"
        try:
            logger.info(
                f"Initializing Offline Faster-Whisper '{model_name}' "
                f"(device={dev}, compute_type={ct}, cpu_threads=4, local_files_only=True)..."
            )
            candidate = WhisperModel(
                model_name,
                device=dev,
                compute_type=ct,
                cpu_threads=4,
                local_files_only=True,
            )
            self.whisper_model = candidate
            self.whisper_device = dev
            self.whisper_compute_type = ct
            logger.info(f"Faster-Whisper '{model_name}' loaded successfully on {dev}!")
        except Exception as e:
            logger.warning(f"Offline Faster-Whisper init failed on {dev} ({ct}): {e}; falling back to local Vosk model/.")

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
        kept_texts: list[str] = []
        for segment in seg_list:
            nsp = getattr(segment, "no_speech_prob", None)
            alp = getattr(segment, "avg_logprob", None)
            cr = getattr(segment, "compression_ratio", None)
            if isinstance(nsp, (int, float)) and nsp > 0.70:
                continue
            if isinstance(alp, (int, float)) and alp < -1.1:
                continue
            if isinstance(cr, (int, float)) and cr > 2.4:
                continue
            text_part = str(getattr(segment, "text", "") or "").strip()
            if text_part:
                kept_texts.append(text_part)
        return " ".join(kept_texts).strip()

    def _transcribe_whisper(self, wav_bytes: bytes) -> str:
        """Transcribe WAV audio bytes using Faster-Whisper with fast silence skip and wake-word biasing."""
        if not self._has_speech(wav_bytes):
            return ""

        hotwords_list = [
            "Anureet", "Hey Anureet", "CapCut", "Chrome", "Spotify", "Notepad",
            "YouTube", "VS Code", "FL Studio", "Premiere", "Audio", "Click",
            "Switch tab", "New folder", "Minimize", "Close",
        ]
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
            "hotwords": ", ".join(hotwords_list),
        }
        try:
            return self._run_whisper_decode(wav_bytes, kwargs)
        except Exception as e:
            err_msg = str(e).lower()
            is_cuda_runtime_err = any(tok in err_msg for tok in ("cublas", "cudnn", "cuda"))
            if (
                WhisperModel is not None
                and getattr(self, "whisper_device", "cpu") == "cuda"
                and is_cuda_runtime_err
            ):
                logger.warning(f"CUDA transcription failed ({e}); falling back to CPU (int8)...")
                self.whisper_model = WhisperModel(
                    config.whisper_model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=4,
                    local_files_only=True,
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

    @classmethod
    def extract_wake_command(
        cls,
        text: str,
        active_session_until: float = 0.0,
        now: Optional[float] = None,
        continuous_mode: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """
        Determine whether a transcript triggers Anureet and extract the command string.
        - Strict Command Mode (default): NO execution unless 'Anureet' is spoken at the start.
        - 1-Minute Continuous Chat Mode: Any utterance within the active window executes without saying 'Anureet'.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return False, ""

        # 1. Exact or variant wake word match: "Anureet <command>"
        match = WAKE_WORD_PATTERN.search(cleaned)
        if match:
            command_part = cleaned[match.end():].lstrip(" ,.:;!?-").strip()
            return True, command_part

        # 2. Leading phonetic fuzzy match: "Anurit <command>"
        fuzzy_cmd = _fuzzy_leading_wake_match(cleaned)
        if fuzzy_cmd is not None:
            return True, fuzzy_cmd

        # 3. 1-Minute Continuous Chat Mode check
        current_time = time.time() if now is None else now
        is_continuous = continuous_mode if continuous_mode is not None else (current_time < active_session_until)
        if is_continuous:
            return True, cleaned

        # Default strict mode: No execution without wake word
        return False, ""

    @staticmethod
    def _emit_widget_event(state: str, user_text: str = "", agent_text: str = "") -> None:
        try:
            publish_widget_event(state, user_text=user_text, agent_text=agent_text, source="voice")
        except Exception as e:
            logger.debug(f"Widget event sync skipped: {e}")

    def _handle_command(self, command_part: str) -> float:
        """Execute an extracted voice command with streaming clause-by-clause SAPI5 TTS."""
        if command_part == "__continuous_start__":
            reply = "Continuous listening mode activated for 1 minute. You can speak freely without saying Anureet."
            self._emit_widget_event("speaking", user_text="listen continuously", agent_text=reply)
            self.speak(reply)
            self._emit_widget_event("idle", user_text="listen continuously", agent_text=reply)
            return self.continuous_chat_until

        if command_part == "__continuous_stop__":
            reply = "Continuous chat mode ended. Back to strict wake word mode."
            self._emit_widget_event("speaking", user_text="stop continuous mode", agent_text=reply)
            self.speak(reply)
            self._emit_widget_event("idle", user_text="stop continuous mode", agent_text=reply)
            return 0.0

        if (
            not command_part
            or len(command_part) < 2
            or command_part.lower() in {"i am listening", "yes"}
        ):
            prompt = "Yes, I am listening! Aap kya karna chahte hain?"
            self._emit_widget_event("speaking", user_text="Anureet", agent_text=prompt)
            self.speak(prompt)
            self._emit_widget_event("idle", user_text="Anureet", agent_text=prompt)
            return self.continuous_chat_until if self.is_continuous_mode_active() else 0.0

        print(f"\n[Anureet Active 🎙️] Executing: '{command_part}'")
        self._emit_widget_event("thinking", user_text=command_part)
        self.begin_speech_turn()
        spoke_any = False

        def on_stream_clause(clause: str) -> None:
            nonlocal spoke_any
            if not clause or self._stop_speech_event.is_set():
                return
            if not spoke_any:
                spoke_any = True
                self._emit_widget_event("speaking", user_text=command_part, agent_text=clause)
            self.speak_clause(clause)

        res_dict = agent.stream_query(command_part, on_clause=on_stream_clause, session_id="voice")
        response = res_dict.get("response_text", "")
        print(f"[Anureet Response 🗣️]: {response}\n")

        if response and not self._stop_speech_event.is_set():
            self._emit_widget_event("speaking", user_text=command_part, agent_text=response)
            if not spoke_any:
                self.speak(response)
            else:
                self.wait_until_done_speaking()
        else:
            self.wait_until_done_speaking()

        self._emit_widget_event("idle", user_text=command_part, agent_text=response)
        if self.is_continuous_mode_active():
            return self.continuous_chat_until
        return 0.0

    def begin_speech_turn(self) -> None:
        """Reset barge-in stop flag at the start of a new response turn."""
        self._stop_speech_event.clear()

    def speak_clause(self, clause: str) -> None:
        """Enqueue a single spoken clause onto the background SAPI5 COM STA worker without blocking."""
        clean_clause = str(clause or "").replace("*", "").strip()
        if not clean_clause or self._stop_speech_event.is_set():
            return
        self._start_tts_worker()
        if getattr(self, "_tts_worker_started", False):
            self._tts_done_event.clear()
            self._tts_queue.put(clean_clause)
        elif os.name == "nt" and comtypes is not None:
            self._speak_sapi_comtypes_fallback(clean_clause)

    def wait_until_done_speaking(self, timeout: Optional[float] = None) -> None:
        """Wait until queued SAPI5 clauses finish speaking or barge-in cancels playback."""
        done_ev = getattr(self, "_tts_done_event", None)
        if done_ev is None:
            return
        deadline = (time.monotonic() + timeout) if timeout is not None else None
        while not done_ev.is_set():
            if self._stop_speech_event.is_set() or _poll_cli_interrupt():
                self.stop_speaking()
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            done_ev.wait(timeout=0.025)

    def speak(self, text: str, blocking: bool = True):
        """Speak text via offline Windows SAPI5 async worker with <5ms barge-in support."""
        logger.info(f"Anureet Speaking: {text}")
        clean_text = str(text or "").replace("*", "").strip()
        if not clean_text:
            return
        self.begin_speech_turn()
        self.speak_clause(clean_text)
        if blocking:
            self.wait_until_done_speaking()

    def _speak_sapi_comtypes_fallback(self, text: str) -> None:
        """Offline SAPI5 fallback if pywin32 worker is unavailable."""
        com_init = False
        try:
            comtypes.CoInitialize()
            com_init = True
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            selected_desc = self._select_sapi5_female_voice(voice)
            spoken = (
                _devanagari_to_hinglish(text)
                if DEVANAGARI_PATTERN.search(text) and not any(h in selected_desc for h in ("kalpana", "hemant", "hindi", "hi-in"))
                else text
            )
            voice.Rate = 0
            voice.Volume = 92
            voice.Speak(self._format_soft_indian_ssml(spoken), SVSFlagsAsync | SVSFIsXML)
            while not self._stop_speech_event.is_set():
                if _poll_cli_interrupt():
                    self._stop_speech_event.set()
                    break
                if voice.WaitUntilDone(25):
                    break
            if self._stop_speech_event.is_set():
                voice.Speak("", SVSFPurgeBeforeSpeak)
        except Exception as e:
            logger.error(f"Offline comtypes SAPI5 fallback failed: {e}")
        finally:
            if com_init:
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass

    @staticmethod
    def _open_microphone():
        """Open microphone at 16 kHz for Whisper, falling back to default rate if unsupported."""
        try:
            return sr.Microphone(sample_rate=config.voice_sample_rate)
        except Exception:
            return sr.Microphone()

    def _transcribe_vosk_wav(self, wav_bytes: bytes) -> str:
        """Offline transcription using local Kaldi/Vosk acoustic files inside model/."""
        vosk_model_path = os.path.abspath("model")
        if not (VOSK_AVAILABLE and os.path.exists(vosk_model_path)):
            return ""
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                pcm = wf.readframes(wf.getnframes())
                sample_rate = wf.getframerate() or 16000
            model = getattr(self, "_vosk_model", None)
            if model is None:
                model = VoskModel(vosk_model_path)
                self._vosk_model = model
            rec = KaldiRecognizer(model, sample_rate)
            rec.AcceptWaveform(pcm)
            res = json.loads(rec.FinalResult())
            return str(res.get("text", "")).strip()
        except Exception as e:
            logger.debug(f"Offline Vosk single-shot decode failed: {e}")
            return ""

    def listen_once(self, timeout: int = 6, phrase_time_limit: int = 10) -> str:
        """Capture and transcribe a single voice utterance strictly offline (Whisper or local Vosk)."""
        if not self.recognizer:
            return ""
        self.ensure_stt_loaded()
        try:
            with self._open_microphone() as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.35)
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
            wav_bytes = audio.get_wav_data(convert_rate=16000, convert_width=2)
            if WHISPER_AVAILABLE and self.whisper_model:
                return self._transcribe_whisper(wav_bytes)
            if hasattr(self.recognizer, "recognize_vosk"):
                try:
                    raw_vosk = self.recognizer.recognize_vosk(audio)
                    parsed = json.loads(raw_vosk) if raw_vosk else {}
                    return str(parsed.get("text", "")).strip()
                except Exception:
                    pass
            return self._transcribe_vosk_wav(wav_bytes)
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

        # Option C: Offline SpeechRecognition + local Vosk Fallback (never Google cloud API)
        if sr and VOSK_AVAILABLE and os.path.exists(vosk_model_path):
            logger.info("Starting Offline SpeechRecognition + local Vosk Listener...")
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
            self.wait_until_done_speaking()
            # 300ms acoustic grace period to absorb room reverberation & prevent audio clipping
            time.sleep(0.30)
            resume_fn()

    def _listen_whisper(self, active_session_until: float = 0.0):
        with self._open_microphone() as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            logger.info("Microphone calibrated for ambient noise.")

            while True:
                try:
                    is_continuous = self.is_continuous_mode_active()
                    if is_continuous:
                        rem = max(0, int(self.continuous_chat_until - time.time()))
                        logger.info(f"1-Minute Continuous Chat Mode Active ({rem}s remaining)...")
                        self._emit_widget_event("listening", user_text="Continuous Chat Active")
                    else:
                        logger.info("Strict Command Mode: Listening for 'Anureet <command>'...")

                    audio = self.recognizer.listen(source, timeout=8, phrase_time_limit=12)
                    text = self._transcribe_whisper(audio.get_wav_data())
                    if not text:
                        continue

                    logger.info(f"Heard (Faster-Whisper): '{text}'")

                    # Check for explicit continuous mode commands
                    if CONTINUOUS_START_PATTERN.search(text):
                        self.start_continuous_mode(60.0)
                        self._run_command_with_mic_paused(
                            "__continuous_start__",
                            lambda: self._pause_mic_stream(source),
                            lambda: self._resume_mic_stream(source),
                        )
                        continue

                    if CONTINUOUS_STOP_PATTERN.search(text):
                        self.stop_continuous_mode()
                        self._run_command_with_mic_paused(
                            "__continuous_stop__",
                            lambda: self._pause_mic_stream(source),
                            lambda: self._resume_mic_stream(source),
                        )
                        continue

                    wake_found, command_part = self.extract_wake_command(
                        text,
                        continuous_mode=is_continuous,
                    )
                    if wake_found:
                        self._run_command_with_mic_paused(
                            command_part,
                            lambda: self._pause_mic_stream(source),
                            lambda: self._resume_mic_stream(source),
                        )

                except sr.WaitTimeoutError:
                    if self.continuous_chat_mode and time.time() >= self.continuous_chat_until:
                        logger.info("1-Minute Continuous chat mode timed out. Reverting to strict wake-word mode.")
                        self.stop_continuous_mode()
                        self._emit_widget_event("idle")
                except KeyboardInterrupt:
                    print("\nAnureet Voice Assistant stopped.")
                    break
                except Exception as e:
                    logger.error(f"Whisper listener error: {e}")
                    time.sleep(1)

    def _listen_vosk(self, model_path: str, active_session_until: float = 0.0):
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

                        if CONTINUOUS_START_PATTERN.search(text):
                            self.start_continuous_mode(60.0)
                            self._run_command_with_mic_paused(
                                "__continuous_start__",
                                stream.stop_stream,
                                stream.start_stream,
                            )
                            continue

                        if CONTINUOUS_STOP_PATTERN.search(text):
                            self.stop_continuous_mode()
                            self._run_command_with_mic_paused(
                                "__continuous_stop__",
                                stream.stop_stream,
                                stream.start_stream,
                            )
                            continue

                        wake_found, command_part = self.extract_wake_command(
                            text,
                            continuous_mode=self.is_continuous_mode_active(),
                        )
                        if wake_found:
                            self._run_command_with_mic_paused(
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

    def _listen_speech_recognition(self, active_session_until: float = 0.0):
        with self._open_microphone() as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=1)
            logger.info("Microphone calibrated for ambient noise.")

            while True:
                try:
                    is_continuous = self.is_continuous_mode_active()
                    if is_continuous:
                        rem = max(0, int(self.continuous_chat_until - time.time()))
                        logger.info(f"1-Minute Continuous Chat Mode Active ({rem}s remaining)...")
                        self._emit_widget_event("listening", user_text="Continuous Chat Active")
                    else:
                        logger.info("Strict Command Mode: Listening for 'Anureet <command>'...")

                    audio = self.recognizer.listen(source, timeout=8, phrase_time_limit=12)
                    wav_bytes = audio.get_wav_data(convert_rate=16000, convert_width=2)
                    text = self._transcribe_vosk_wav(wav_bytes)
                    if not text:
                        continue

                    logger.info(f"Heard: '{text}'")

                    if CONTINUOUS_START_PATTERN.search(text):
                        self.start_continuous_mode(60.0)
                        self._run_command_with_mic_paused(
                            "__continuous_start__",
                            lambda: self._pause_mic_stream(source),
                            lambda: self._resume_mic_stream(source),
                        )
                        continue

                    if CONTINUOUS_STOP_PATTERN.search(text):
                        self.stop_continuous_mode()
                        self._run_command_with_mic_paused(
                            "__continuous_stop__",
                            lambda: self._pause_mic_stream(source),
                            lambda: self._resume_mic_stream(source),
                        )
                        continue

                    wake_found, command_part = self.extract_wake_command(
                        text,
                        continuous_mode=is_continuous,
                    )
                    if wake_found:
                        self._run_command_with_mic_paused(
                            command_part,
                            lambda: self._pause_mic_stream(source),
                            lambda: self._resume_mic_stream(source),
                        )

                except sr.WaitTimeoutError:
                    if self.continuous_chat_mode and time.time() >= self.continuous_chat_until:
                        logger.info("1-Minute Continuous chat mode timed out. Reverting to strict wake-word mode.")
                        self.stop_continuous_mode()
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
