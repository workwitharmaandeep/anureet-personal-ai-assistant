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
from contextlib import asynccontextmanager
import json
import logging
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent_core import agent
from config import config
from tools.memory_store import (
    get_synced_history,
    get_latest_widget_events,
    get_widget_head_id,
)
from voice_assistant import AnureetVoiceEngine

logger = logging.getLogger("GatewayServer")

BASE_DIR = config.base_dir.resolve()
STATIC_DIR = BASE_DIR / "static"
UPLOADS_DIR = config.uploads_dir.resolve()
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MB streaming chunks


async def _reminder_watchdog_loop():
    """Periodically check due reminders and notify desktop + WebSocket clients."""
    from tools.productivity_tools import get_due_reminders, show_desktop_notification

    while True:
        try:
            await asyncio.sleep(30)
            due = await asyncio.to_thread(get_due_reminders, True)
            reminders = due.get("reminders", [])
            for rem in reminders:
                title = rem.get("title", "Reminder")
                await asyncio.to_thread(show_desktop_notification, "Anureet Reminder", title)
                await manager.broadcast({
                    "type": "reminder",
                    "message": f"Reminder due: {title}",
                    "reminder": rem,
                })
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Reminder watchdog iteration skipped: {e}")


async def _widget_event_broadcast_loop():
    """Broadcast cross-process widget/voice turns to connected Web UI WebSocket clients."""
    last_id = await asyncio.to_thread(get_widget_head_id)
    while True:
        try:
            await asyncio.sleep(0.35)
            if not manager.active_connections:
                # Still advance head so reconnects rely on /api/history
                events = await asyncio.to_thread(get_latest_widget_events, last_id)
                for ev in events:
                    last_id = max(last_id, int(ev.get("id", 0)))
                continue

            events = await asyncio.to_thread(get_latest_widget_events, last_id)
            for ev in events:
                last_id = max(last_id, int(ev.get("id", 0)))
                await manager.broadcast({
                    "type": "external_event",
                    "event": ev,
                })
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Widget event broadcast skipped: {e}")


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    task_rem = asyncio.create_task(_reminder_watchdog_loop())
    task_ev = asyncio.create_task(_widget_event_broadcast_loop())
    try:
        yield
    finally:
        task_rem.cancel()
        task_ev.cancel()
        for t in (task_rem, task_ev):
            try:
                await t
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Local AI Agent Gateway", version="1.2.0", lifespan=lifespan)

# Enable CORS for local network and mobile access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _format_bytes(size: int) -> str:
    """Format byte size into a compact human-readable string."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size} B"


def _resolve_workspace_path(raw_path: str) -> Path:
    """Resolve a relative path and enforce containment within BASE_DIR."""
    clean = (raw_path or ".").replace("\\", "/").lstrip("/")
    candidate = (BASE_DIR / (clean or ".")).resolve()
    if not candidate.is_relative_to(BASE_DIR):
        raise HTTPException(status_code=403, detail="Access outside workspace is denied.")
    return candidate


# Models for REST API
class ChatRequest(BaseModel):
    query: str


@app.get("/api/health")
async def health_check():
    ollama_online = await asyncio.to_thread(agent.check_ollama_status)
    return {
        "status": "ok",
        "model": agent.model_name,
        "ollama_online": ollama_online,
    }


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")
    return await asyncio.to_thread(agent.process_query, query, None, "web")


@app.get("/api/history")
async def api_get_history(limit: int = Query(40, ge=1, le=100)):
    history = await asyncio.to_thread(get_synced_history, limit)
    return {"status": "success", "history": history}


@app.get("/api/events")
async def api_get_events(after_id: int = Query(0, ge=0)):
    events = await asyncio.to_thread(get_latest_widget_events, after_id)
    head_id = await asyncio.to_thread(get_widget_head_id)
    return {"status": "success", "events": events, "head_id": head_id}


class TTSRequest(BaseModel):
    text: str


@app.post("/api/tts")
async def api_tts_post(req: TTSRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
    wav_bytes = await asyncio.to_thread(AnureetVoiceEngine.synthesize_offline_female_wav, text)
    if not wav_bytes:
        raise HTTPException(status_code=500, detail="TTS synthesis failed.")
    return Response(content=wav_bytes, media_type="audio/wav")


@app.get("/api/tts")
async def api_tts_get(text: str = Query(...)):
    cleaned = (text or "").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Text parameter cannot be empty.")
    wav_bytes = await asyncio.to_thread(AnureetVoiceEngine.synthesize_offline_female_wav, cleaned)
    if not wav_bytes:
        raise HTTPException(status_code=500, detail="TTS synthesis failed.")
    return Response(content=wav_bytes, media_type="audio/wav")


# File Manager REST APIs
@app.get("/api/files/browse")
async def browse_files(path: str = Query(".")):
    target_path = _resolve_workspace_path(path)

    if not target_path.exists():
        raise HTTPException(status_code=404, detail="Directory not found.")

    if target_path.is_file():
        target_path = target_path.parent

    try:
        items = []
        for p in target_path.iterdir():
            try:
                stat = p.stat()
            except OSError:
                continue
            is_dir = p.is_dir()
            size = 0 if is_dir else stat.st_size
            items.append({
                "name": p.name,
                "path": p.relative_to(BASE_DIR).as_posix(),
                "is_dir": is_dir,
                "size": size,
                "size_formatted": "Folder" if is_dir else _format_bytes(size),
                "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            })

        # Sort: directories first, then alphabetical
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

        current_rel = "." if target_path == BASE_DIR else target_path.relative_to(BASE_DIR).as_posix()
        parent_rel = (
            "."
            if target_path == BASE_DIR or target_path.parent == BASE_DIR
            else target_path.parent.relative_to(BASE_DIR).as_posix()
        )

        return {
            "current_path": current_rel,
            "parent_path": parent_rel,
            "items": items,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error browsing '{target_path}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/files/download")
async def download_file(
    path: Optional[str] = Query(None),
    file_path: Optional[str] = Query(None),
):
    requested = path if path is not None else file_path
    if not requested or not requested.strip():
        raise HTTPException(status_code=400, detail="File path parameter is required.")

    resolved = _resolve_workspace_path(requested)
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path=resolved, filename=resolved.name)


@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    target_dir: Optional[str] = Form(None),
    destination_dir: Optional[str] = Query(None),
):
    raw_dir = target_dir if target_dir is not None else (destination_dir or ".")
    dest_dir = _resolve_workspace_path(raw_dir)
    if dest_dir.exists() and not dest_dir.is_dir():
        raise HTTPException(status_code=400, detail="Target path is not a directory.")
    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_filename = Path(file.filename or "").name
    if not safe_filename or safe_filename in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid upload filename.")

    save_path = (dest_dir / safe_filename).resolve()
    if not save_path.is_relative_to(BASE_DIR):
        raise HTTPException(status_code=403, detail="Invalid destination path.")

    try:
        with open(save_path, "wb") as buffer:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                buffer.write(chunk)

        return {
            "status": "success",
            "filename": safe_filename,
            "saved_path": save_path.relative_to(BASE_DIR).as_posix(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed for '{safe_filename}': {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


# WebSocket Connection Manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_json(self, websocket: WebSocket, data: Dict[str, Any]):
        await websocket.send_text(json.dumps(data))

    async def broadcast(self, data: Dict[str, Any]):
        for ws_conn in list(self.active_connections):
            try:
                await self.send_json(ws_conn, data)
            except Exception:
                self.disconnect(ws_conn)


manager = ConnectionManager()


@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await manager.connect(websocket)
    loop = asyncio.get_running_loop()
    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                query = str(payload.get("query", "")).strip()
            except Exception:
                query = data.strip()

            if not query:
                continue

            def thread_log_callback(msg: str):
                asyncio.run_coroutine_threadsafe(
                    manager.send_json(websocket, {"type": "log", "message": msg}),
                    loop,
                )

            await manager.send_json(websocket, {
                "type": "status",
                "status": "processing",
                "message": f"Processing query: '{query}'",
            })

            res = await asyncio.to_thread(agent.process_query, query, thread_log_callback, "web")

            await manager.send_json(websocket, {
                "type": "response",
                "data": res,
            })

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"WebSocket session ended with error: {e}")
        manager.disconnect(websocket)


def _can_bind_port(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def find_available_port(host: str, preferred_port: int, max_offset: int = 50) -> int:
    """Return preferred_port if bindable exclusively, or the next available port nearby."""
    check_hosts = [host] + (["127.0.0.1"] if host in {"0.0.0.0", ""} else [])
    for offset in range(max_offset + 1):
        candidate = preferred_port + offset
        if all(_can_bind_port(h, candidate) for h in check_hosts):
            return candidate
    return preferred_port


# Mount static files at root
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    port = find_available_port(config.server_host, config.server_port)
    if port != config.server_port:
        logger.warning(
            f"Port {config.server_port} is unavailable or already in use. "
            f"Starting on http://{config.server_host}:{port} instead "
            f"(stop the existing process on {config.server_port} or set SERVER_PORT to override)."
        )
    print(f"Starting Local AI Agent Gateway on http://{config.server_host}:{port} ...")
    uvicorn.run(
        "server:app" if config.server_reload else app,
        host=config.server_host,
        port=port,
        reload=config.server_reload,
    )
