"""
One-click silent launcher & lifecycle orchestrator for Anureet AI.
Starts Ollama (if offline), the FastAPI/WebSocket Gateway (server.py), and the
animated Chibi 3D Desktop Widget (with Always-Listen Wake Word enabled) without
opening any terminal console windows. Also manages runtime PID tracking so clicking
the widget's '✕' Close Everything button cleanly shuts down every component it owns.
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

import argparse
import json
import logging
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import config

_LOCAL_SESSION = requests.Session()
_LOCAL_SESSION.trust_env = False

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_FILE = config.uploads_dir / "anureet_runtime.json"
LOG_FILE = config.uploads_dir / "anureet_launcher.log"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000 if os.name == "nt" else 0)

os.makedirs(config.uploads_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] AnureetLauncher: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8")],
)
logger = logging.getLogger("AnureetLauncher")


def _hidden_subprocess_kwargs() -> Dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


def is_pid_running(pid: Optional[int]) -> bool:
    """Return True if a process with the given PID currently exists."""
    if not pid or int(pid) <= 0:
        return False
    pid_int = int(pid)
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid_int)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            STILL_ACTIVE = 259
            return exit_code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid_int, 0)
        return True
    except OSError:
        return False


def terminate_pid(pid: Optional[int]) -> bool:
    """Terminate a process tree cleanly (best-effort)."""
    if not pid or int(pid) <= 0:
        return False
    pid_int = int(pid)
    if os.name == "nt":
        res = subprocess.run(
            ["taskkill", "/PID", str(pid_int), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=8,
            **_hidden_subprocess_kwargs(),
        )
        return res.returncode == 0
    try:
        os.kill(pid_int, signal.SIGTERM)
        return True
    except OSError:
        return False


def find_ollama_executable() -> Optional[str]:
    """Locate the local Ollama CLI binary."""
    which_path = shutil.which("ollama")
    if which_path:
        return which_path
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.EXE",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
        Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def is_ollama_online(base_url: Optional[str] = None, timeout: float = 1.5) -> bool:
    url = (base_url or config.ollama_base_url).rstrip("/")
    try:
        resp = _LOCAL_SESSION.get(f"{url}/api/tags", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def warmup_ollama_model(model_name: Optional[str] = None, base_url: Optional[str] = None) -> None:
    """Ask Ollama in the background to load the active LLM into memory so first reply is snappy."""
    url = (base_url or config.ollama_base_url).rstrip("/")
    target_model = model_name or config.default_model
    try:
        _LOCAL_SESSION.post(
            f"{url}/api/generate",
            json={
                "model": target_model,
                "prompt": "",
                "keep_alive": "15m",
                "options": {"num_ctx": 2048, "num_batch": 256, "num_thread": 6},
            },
            timeout=25,
        )
    except Exception as e:
        logger.debug(f"Ollama warmup note: {e}")


def ensure_ollama_running(timeout: float = 20.0) -> Tuple[Optional[int], bool]:
    """Ensure Ollama is reachable; returns (pid_if_spawned, ollama_owned)."""
    if is_ollama_online():
        logger.info("Ollama is already online.")
        return None, False

    ollama_exe = find_ollama_executable()
    if not ollama_exe:
        logger.warning("Ollama executable not found; continuing without local LLM daemon.")
        return None, False

    logger.info(f"Starting headless Ollama server: {ollama_exe} serve")
    env = os.environ.copy()
    env.update(
        {
            "OLLAMA_HOST": "127.0.0.1:11434",
            "OLLAMA_FLASH_ATTENTION": "1",
            "OLLAMA_KV_CACHE_TYPE": "q8_0",
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_NOPRUNE": "1",
        }
    )
    env.setdefault("OLLAMA_KEEP_ALIVE", "15m")
    proc = subprocess.Popen(
        [ollama_exe, "serve"],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_hidden_subprocess_kwargs(),
    )

    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_ollama_online(timeout=1.0):
            logger.info(f"Ollama came online (PID={proc.pid}).")
            return proc.pid, True
        if proc.poll() is not None:
            logger.warning(f"Ollama serve exited early with code {proc.returncode}.")
            return None, False
        time.sleep(0.4)

    logger.warning("Timed out waiting for Ollama HTTP readiness.")
    return proc.pid, True


def _find_free_port(preferred_port: int = 8000, max_offset: int = 50) -> int:
    """Return preferred_port if bindable exclusively on 0.0.0.0 and 127.0.0.1, else next free port."""
    for offset in range(max_offset + 1):
        candidate = preferred_port + offset
        ok = True
        for host in ("0.0.0.0", "127.0.0.1"):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    sock.bind((host, candidate))
                except OSError:
                    ok = False
                    break
        if ok:
            return candidate
    return preferred_port


def is_gateway_online(port: int, timeout: float = 1.2) -> bool:
    try:
        resp = _LOCAL_SESSION.get(f"http://127.0.0.1:{port}/api/health", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def ensure_server_running(preferred_port: int = 8000, timeout: float = 12.0) -> Tuple[Optional[int], int]:
    """Ensure server.py Gateway is running cleanly without a console window."""
    if is_gateway_online(preferred_port):
        logger.info(f"Gateway server already listening on port {preferred_port}.")
        return None, preferred_port

    chosen_port = _find_free_port(preferred_port)
    python_exec = sys.executable
    if os.name == "nt" and python_exec.lower().endswith("pythonw.exe"):
        candidate_exe = Path(python_exec).with_name("python.exe")
        if candidate_exe.is_file():
            python_exec = str(candidate_exe)

    cmd = [
        python_exec,
        "-m",
        "uvicorn",
        "server:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(chosen_port),
        "--log-level",
        "warning",
    ]
    logger.info(f"Starting headless Gateway server on port {chosen_port}...")
    server_log_path = config.uploads_dir / "anureet_server.log"
    server_log = open(server_log_path, "ab")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=server_log,
            stderr=server_log,
            **_hidden_subprocess_kwargs(),
        )
    finally:
        server_log.close()

    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_gateway_online(chosen_port, timeout=0.8):
            logger.info(f"Gateway server ready on port {chosen_port} (PID={proc.pid}).")
            return proc.pid, chosen_port
        if proc.poll() is not None:
            logger.warning(f"Gateway server exited early with code {proc.returncode}.")
            return None, chosen_port
        time.sleep(0.35)

    return proc.pid, chosen_port


def save_runtime_state(state: Dict[str, Any]) -> None:
    os.makedirs(RUNTIME_FILE.parent, exist_ok=True)
    tmp_path = RUNTIME_FILE.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp_path.replace(RUNTIME_FILE)


def load_runtime_state() -> Dict[str, Any]:
    if not RUNTIME_FILE.is_file():
        return {}
    try:
        data = json.loads(RUNTIME_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def shutdown_runtime(kill_external_ollama: bool = False, exclude_self: bool = True) -> Dict[str, Any]:
    """Terminate tracked Anureet processes (server, launcher-owned Ollama, and optional widget PID)."""
    state = load_runtime_state()
    terminated: List[int] = []
    self_pid = os.getpid()

    pid_targets: List[Tuple[str, Optional[int]]] = [
        ("server_pid", state.get("server_pid")),
        ("widget_pid", state.get("widget_pid")),
    ]
    if state.get("ollama_owned") or kill_external_ollama:
        pid_targets.append(("ollama_pid", state.get("ollama_pid")))

    for label, pid in pid_targets:
        if not pid:
            continue
        pid_int = int(pid)
        if exclude_self and pid_int == self_pid:
            continue
        if is_pid_running(pid_int) and terminate_pid(pid_int):
            terminated.append(pid_int)
            logger.info(f"Terminated {label} (PID={pid_int}).")

    try:
        if RUNTIME_FILE.is_file():
            RUNTIME_FILE.unlink()
    except OSError:
        pass

    return {"status": "stopped", "terminated_pids": terminated}


def request_full_shutdown(close_launched_apps: bool = False, delay_seconds: float = 1.4) -> Dict[str, Any]:
    """Gracefully shut down Anureet (and optionally close session-opened apps)."""
    apps_note = ""
    if close_launched_apps:
        try:
            from tools.os_tools import close_session_apps
            app_res = close_session_apps()
            apps_note = f" {app_res.get('message', '')}"
        except Exception as e:
            logger.debug(f"close_session_apps during shutdown skipped: {e}")

    try:
        from tools.memory_store import publish_widget_event
        publish_widget_event("shutdown", user_text="", agent_text="Shutting down Anureet...", source="system")
    except Exception as e:
        logger.debug(f"publish shutdown event skipped: {e}")

    timer = threading.Timer(max(0.2, float(delay_seconds)), shutdown_runtime, kwargs={"exclude_self": False})
    timer.daemon = True
    timer.start()
    return {
        "status": "shutting_down",
        "close_launched_apps": close_launched_apps,
        "message": f"Anureet is shutting down cleanly.{apps_note}".strip(),
    }


def _ensure_null_stdio() -> None:
    """Guard pythonw.exe where sys.stdout / sys.stderr / sys.stdin default to None."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    if sys.stdin is None:
        try:
            sys.stdin = open(os.devnull, "r", encoding="utf-8")
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _ensure_null_stdio()
    parser = argparse.ArgumentParser(description="One-Click Silent Anureet AI Launcher")
    parser.add_argument("--stop", action="store_true", help="Stop all running Anureet runtime processes")
    parser.add_argument("--no-wake", action="store_true", help="Launch widget without Always-Listen Wake Word active")
    parser.add_argument("--no-server", action="store_true", help="Skip launching server.py Gateway")
    parser.add_argument("--port", type=int, default=8000, help="Preferred port for server.py Gateway")
    args = parser.parse_args(argv)

    if args.stop:
        result = shutdown_runtime(exclude_self=False)
        logger.info(f"Manual stop requested: {result}")
        return 0

    try:
        existing = load_runtime_state()
        existing_widget_pid = existing.get("widget_pid")
        if existing_widget_pid and int(existing_widget_pid) != os.getpid() and is_pid_running(existing_widget_pid):
            logger.info(f"Anureet widget is already running (PID={existing_widget_pid}). Exiting duplicate launcher.")
            return 0

        runtime_lock = threading.Lock()
        runtime_state: Dict[str, Any] = {
            "widget_pid": os.getpid(),
            "server_pid": None,
            "server_port": args.port,
            "ollama_pid": None,
            "ollama_owned": False,
            "started_at": time.time(),
        }
        save_runtime_state(runtime_state)

        from anureet_widget import AnureetWidgetApp

        def _on_quit_all() -> None:
            shutdown_runtime(exclude_self=True)

        app = AnureetWidgetApp(
            auto_wake=not args.no_wake,
            on_quit_all=_on_quit_all,
            server_port=args.port,
        )

        def _start_background_services() -> None:
            try:
                ollama_pid, ollama_owned = ensure_ollama_running()
                threading.Thread(target=warmup_ollama_model, daemon=True).start()

                server_pid: Optional[int] = None
                server_port = args.port
                if not args.no_server:
                    server_pid, server_port = ensure_server_running(preferred_port=args.port)

                with runtime_lock:
                    if app._closed:
                        if server_pid:
                            terminate_pid(server_pid)
                        if ollama_owned and ollama_pid:
                            terminate_pid(ollama_pid)
                        return
                    runtime_state["ollama_pid"] = ollama_pid
                    runtime_state["ollama_owned"] = ollama_owned
                    runtime_state["server_pid"] = server_pid
                    runtime_state["server_port"] = server_port
                    app._server_port = server_port
                    save_runtime_state(runtime_state)
            except Exception as svc_err:
                logger.exception(f"Background service startup warning: {svc_err}")

        threading.Thread(target=_start_background_services, daemon=True).start()
        try:
            app.run()
        finally:
            shutdown_runtime(exclude_self=True)
        return 0
    except Exception as exc:
        logger.exception(f"Anureet launcher failed: {exc}")
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None,
                    f"Anureet AI failed to start:\n{exc}\n\nSee uploads/anureet_launcher.log for details.",
                    "Anureet AI Launcher Error",
                    0x10,
                )
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

