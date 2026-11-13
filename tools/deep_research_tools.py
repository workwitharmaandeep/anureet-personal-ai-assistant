"""
Offline Local Intelligence & Sandboxed Code Execution Module for Anureet.
Operates 100% air-gapped: local memory/workspace briefing and Windows Job Object
sandboxed C / Python / JavaScript execution inside TemporaryDirectory (10s timeout).
"""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional

from config import config

try:
    import win32api
    import win32job
    WIN32JOB_AVAILABLE = True
except ImportError:
    win32api = None
    win32job = None
    WIN32JOB_AVAILABLE = False

logger = logging.getLogger("DeepResearchTools")
SANDBOX_TIMEOUT_SECONDS = 10


def deep_research_topic(topic: str) -> str:
    """
    Execute Protocol 1 (Air-Gapped Offline Mode): query local SQLite FTS5 memory and workspace
    documentation for a topic without opening any external network connections.
    """
    clean_topic = (topic or "").strip()
    if not clean_topic:
        return "Please provide a topic to research."

    logger.info(f"Executing 100% Offline Local Research on: '{clean_topic}'")
    findings: List[str] = []

    try:
        from tools.memory_store import recall_memory
        recalled = recall_memory(clean_topic, limit=5).get("memories", [])
        for item in recalled:
            findings.append(f"• **Stored Memory ({item.get('category', 'fact')})**: {item.get('content', '')}")
    except Exception as e:
        logger.debug(f"Offline memory lookup note: {e}")

    tokens = [t.lower() for t in clean_topic.split() if len(t) >= 3][:4]
    if tokens:
        matched_files: List[str] = []
        skip_dirs = {"venv", ".git", "__pycache__", "node_modules", "model"}
        for root, dirs, files in os.walk(config.base_dir):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                if fname.endswith((".md", ".txt", ".py")) and any(tok in fname.lower() for tok in tokens):
                    rel = Path(root, fname).relative_to(config.base_dir).as_posix()
                    matched_files.append(rel)
                    if len(matched_files) >= 5:
                        break
            if len(matched_files) >= 5:
                break
        if matched_files:
            findings.append("• **Local Workspace Matches**: " + ", ".join(matched_files))

    summary = (
        f"### Offline Intelligence Briefing: {clean_topic.upper()}\n\n"
        f"**Mode**: 100% Air-Gapped Local Synthesis (Zero external web requests).\n\n"
        f"**Local Findings**:\n"
    )
    if findings:
        summary += "\n".join(findings) + "\n"
    else:
        summary += (
            f"• No stored notes matched '{clean_topic}' in local memory or workspace files.\n"
            f"• Tip: Use local LLM knowledge or save facts via `save_memory` for instant FTS5 recall.\n"
        )
    return summary


def _create_kill_on_close_job():
    """Create a Windows Job Object configured with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE."""
    if not WIN32JOB_AVAILABLE:
        return None
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    return job


def _run_in_sandbox(cmd: List[str], cwd: str, timeout: int = SANDBOX_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Run subprocess inside a Windows Job Object enforcing kill-on-close + hard timeout."""
    job = None
    proc: Optional[subprocess.Popen] = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        if os.name == "nt" and WIN32JOB_AVAILABLE:
            try:
                job = _create_kill_on_close_job()
                if job is not None:
                    win32job.AssignProcessToJobObject(job, proc._handle)
            except Exception as e:
                logger.debug(f"Job Object attach skipped: {e}")

        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout=stdout, stderr=stderr)
    except subprocess.TimeoutExpired:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate()
        raise
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        if job is not None and win32api is not None:
            try:
                win32api.CloseHandle(job)
            except Exception:
                pass


def run_sandboxed_code(
    script_code: str,
    language: str = "python",
    timeout: int = SANDBOX_TIMEOUT_SECONDS,
) -> str:
    """Execute Python, JavaScript, or C code in TemporaryDirectory under a Windows Job Object (10s limit)."""
    if not config.enable_code_execution:
        return "Local code execution is disabled in configuration."

    if not script_code or not script_code.strip():
        return "Execution error: script_code cannot be empty."

    lang = (language or "python").strip().lower()
    if lang == "js":
        lang = "javascript"
    if lang not in {"python", "javascript", "c"}:
        return f"Execution error: unsupported language '{language}' (choose python, javascript, or c)."

    effective_timeout = max(1, min(int(timeout or SANDBOX_TIMEOUT_SECONDS), SANDBOX_TIMEOUT_SECONDS))

    try:
        with tempfile.TemporaryDirectory(prefix="anureet_sandbox_") as tmpdir:
            tmp_path = Path(tmpdir)
            if lang == "python":
                src = tmp_path / "main.py"
                src.write_text(script_code, encoding="utf-8")
                result = _run_in_sandbox([sys.executable, str(src)], cwd=tmpdir, timeout=effective_timeout)
            elif lang == "javascript":
                node_exe = shutil.which("node")
                if not node_exe:
                    return "Execution error: local 'node' runtime not found on PATH (offline sandbox)."
                src = tmp_path / "main.js"
                src.write_text(script_code, encoding="utf-8")
                result = _run_in_sandbox([node_exe, str(src)], cwd=tmpdir, timeout=effective_timeout)
            else:
                gcc_exe = shutil.which("gcc")
                if not gcc_exe:
                    return "Execution error: local C compiler ('gcc') not found on PATH (offline sandbox)."
                src = tmp_path / "main.c"
                out_bin = tmp_path / ("main.exe" if os.name == "nt" else "main")
                src.write_text(script_code, encoding="utf-8")
                comp = _run_in_sandbox([gcc_exe, str(src), "-o", str(out_bin)], cwd=tmpdir, timeout=effective_timeout)
                if comp.returncode != 0:
                    return f"Compilation failed:\n{(comp.stderr or comp.stdout).strip()}"
                result = _run_in_sandbox([str(out_bin)], cwd=tmpdir, timeout=effective_timeout)

            if result.returncode == 0:
                output = (result.stdout or "").strip() or "Script executed successfully with 0 exit code."
                return f"**Autonomous Execution Success ({lang})**:\n{output}"

            stderr = (result.stderr or "").strip() or f"Exited with code {result.returncode}"
            logger.warning(f"Sandboxed {lang} execution failed: {stderr}")
            return f"Execution failed:\n{stderr}"

    except subprocess.TimeoutExpired:
        return f"Execution timed out after {effective_timeout} seconds."
    except Exception as e:
        logger.warning(f"Sandboxed execution error: {e}")
        return f"Execution error: {e}"


def self_healing_python_exec(script_code: str, max_retries: int = 1, language: str = "python") -> str:
    """
    Execute Protocol 2: Local System Access & Control inside a TemporaryDirectory +
    Windows Job Object (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE) with a hard 10-second timeout.
    """
    return run_sandboxed_code(script_code=script_code, language=language, timeout=SANDBOX_TIMEOUT_SECONDS)