"""
File Management and Remote Sync Tools for Local AI Agent.
Handles listing, searching, reading, and saving/uploading files across laptop and mobile bridge.
"""

import os
import sys
import datetime
import base64
import logging
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger("file_tools")

SKIP_SEARCH_DIRS = {"venv", ".venv", ".git", "__pycache__", "node_modules"}


def list_files(directory_path: str = ".") -> Dict[str, Any]:
    """
    List files and directories in a given path with file sizes and modification timestamps.

    Args:
        directory_path (str): Path to list (default current directory).

    Returns:
        dict: List of file and directory metadata items.
    """
    try:
        target_path = os.path.abspath(directory_path)
        if not os.path.exists(target_path):
            return {
                "status": "error",
                "message": f"Directory path '{directory_path}' does not exist."
            }

        items = []
        for entry in os.scandir(target_path):
            stat = entry.stat()
            mod_time = datetime.datetime.fromtimestamp(stat.st_mtime).isoformat()
            items.append({
                "name": entry.name,
                "is_directory": entry.is_dir(),
                "size_bytes": stat.st_size if not entry.is_dir() else 0,
                "modified_at": mod_time,
                "path": os.path.join(target_path, entry.name)
            })

        return {
            "status": "success",
            "directory_path": target_path,
            "total_items": len(items),
            "items": items
        }

    except Exception as e:
        logger.error(f"Error listing files in '{directory_path}': {e}")
        return {
            "status": "error",
            "message": f"Failed to list directory: {str(e)}"
        }


def search_files(query: str, search_path: str = ".", max_results: int = 50) -> Dict[str, Any]:
    """
    Search for files or directories matching a query string recursively within search_path.

    Args:
        query (str): Keyword or extension to match in filename.
        search_path (str): Directory path to search in.
        max_results (int): Maximum number of matching items to return.

    Returns:
        dict: Matching file paths and metadata.
    """
    try:
        base_path = os.path.abspath(search_path)
        clean_query = query.strip().lower()
        matches = []

        for root, dirs, files in os.walk(base_path):
            # Prune dependency/VCS directories to keep file search fast
            dirs[:] = [d for d in dirs if d not in SKIP_SEARCH_DIRS]
            for filename in files + dirs:
                if clean_query in filename.lower():
                    full_path = os.path.join(root, filename)
                    try:
                        stat = os.stat(full_path)
                        matches.append({
                            "name": filename,
                            "path": full_path,
                            "is_directory": os.path.isdir(full_path),
                            "size_bytes": stat.st_size if not os.path.isdir(full_path) else 0,
                            "modified_at": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat()
                        })
                    except Exception:
                        matches.append({"name": filename, "path": full_path})

                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break

        return {
            "status": "success",
            "query": query,
            "search_path": base_path,
            "match_count": len(matches),
            "matches": matches
        }

    except Exception as e:
        logger.error(f"Error searching files for query '{query}': {e}")
        return {
            "status": "error",
            "message": f"Failed to search files: {str(e)}"
        }


def read_file(file_path: str, max_bytes: int = 100000) -> Dict[str, Any]:
    """
    Read text or code file content safely up to max_bytes limit.

    Args:
        file_path (str): Path to file.
        max_bytes (int): Maximum bytes to read.

    Returns:
        dict: Content string or binary note.
    """
    try:
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return {
                "status": "error",
                "message": f"File '{file_path}' does not exist."
            }
        if not os.path.isfile(abs_path):
            return {
                "status": "error",
                "message": f"Path '{file_path}' is a directory, not a file."
            }

        size = os.path.getsize(abs_path)
        if abs_path.lower().endswith(".pdf"):
            try:
                from pypdf import PdfReader
            except ImportError:
                return {
                    "status": "error",
                    "message": "PDF reading requires the lightweight 'pypdf' package (pip install pypdf).",
                }
            reader = PdfReader(abs_path)
            total_pages = len(reader.pages)
            pages_to_read = min(total_pages, 10)
            extracted = []
            for idx in range(pages_to_read):
                page_text = (reader.pages[idx].extract_text() or "").strip()
                if page_text:
                    extracted.append(f"[Page {idx + 1}]\n{page_text}")
            joined = "\n\n".join(extracted)
            truncated = total_pages > pages_to_read or len(joined) > max_bytes
            return {
                "status": "success",
                "file_path": abs_path,
                "file_size_bytes": size,
                "total_pages": total_pages,
                "pages_read": pages_to_read,
                "is_truncated": truncated,
                "content": joined[:max_bytes],
            }

        with open(abs_path, "rb") as f:
            raw_bytes = f.read(max_bytes)

        # Attempt UTF-8 decoding
        try:
            content = raw_bytes.decode("utf-8")
            truncated = size > max_bytes
            return {
                "status": "success",
                "file_path": abs_path,
                "file_size_bytes": size,
                "read_bytes": len(raw_bytes),
                "is_truncated": truncated,
                "content": content
            }
        except UnicodeDecodeError:
            # Binary file fallback
            encoded = base64.b64encode(raw_bytes).decode("ascii")
            return {
                "status": "success",
                "file_path": abs_path,
                "file_size_bytes": size,
                "is_binary": True,
                "content_base64": encoded,
                "message": "Binary file read and encoded as Base64."
            }

    except Exception as e:
        logger.error(f"Error reading file '{file_path}': {e}")
        return {
            "status": "error",
            "message": f"Failed to read file: {str(e)}"
        }


def upload_save_file(
    file_path: str,
    content_base64_or_text: str,
    is_base64: bool = False
) -> Dict[str, Any]:
    """
    Save or write a file (text or binary Base64) to the laptop filesystem.
    Facilitates mobile upload to laptop.

    Args:
        file_path (str): Destination file path.
        content_base64_or_text (str): Content string or Base64 encoded payload.
        is_base64 (bool): True if content is Base64 binary.

    Returns:
        dict: Status of file save operation.
    """
    try:
        abs_path = os.path.abspath(file_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        if is_base64:
            binary_data = base64.b64decode(content_base64_or_text)
            with open(abs_path, "wb") as f:
                f.write(binary_data)
            written_bytes = len(binary_data)
        else:
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content_base64_or_text)
            written_bytes = len(content_base64_or_text.encode("utf-8"))

        return {
            "status": "success",
            "message": f"Successfully saved file to '{abs_path}'.",
            "file_path": abs_path,
            "bytes_written": written_bytes
        }

    except Exception as e:
        logger.error(f"Error saving file to '{file_path}': {e}")
        return {
            "status": "error",
            "message": f"Failed to save file: {str(e)}"
        }


def create_folder(folder_name: str = "", parent_dir: str = "desktop", **kwargs) -> Dict[str, Any]:
    """Create a new folder on the local filesystem (defaults to Desktop).

    Args:
        folder_name: Name or relative path of the folder to create (e.g. 'AI', 'index.ai', 'Notes').
        parent_dir: Target directory location: 'desktop', 'documents', 'downloads', or an absolute path.
    """
    name = (folder_name or kwargs.get("name") or kwargs.get("folder") or kwargs.get("path") or "").strip()
    if not name:
        return {"status": "error", "message": "No folder name specified to create."}

    user_home = Path.home()
    onedrive_dir = user_home / "OneDrive"
    
    target_parent = parent_dir.lower().strip()
    if target_parent in ("desktop", "desktop/"):
        # Match open_user_folder desktop resolution
        candidate = onedrive_dir / "Desktop"
        base_dir = candidate if candidate.is_dir() else (user_home / "Desktop")
    elif target_parent in ("documents", "my documents", "documents/"):
        candidate = onedrive_dir / "Documents"
        base_dir = candidate if candidate.is_dir() else (user_home / "Documents")
    elif target_parent in ("downloads", "downloads/"):
        base_dir = user_home / "Downloads"
    elif target_parent in ("home", "user", "~"):
        base_dir = user_home
    else:
        # Check if parent_dir is a valid absolute/relative path
        custom_path = Path(parent_dir).expanduser()
        base_dir = custom_path if custom_path.is_absolute() else (user_home / custom_path)

    try:
        new_path = Path(name).expanduser()
        if not new_path.is_absolute():
            new_path = (base_dir / name).resolve()
        
        new_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created folder at '{new_path}'")
        return {
            "status": "success",
            "message": f"Created folder '{new_path.name}' at '{new_path}'.",
            "folder_path": str(new_path)
        }
    except Exception as e:
        logger.error(f"Failed to create folder '{name}' in '{base_dir}': {e}")
        return {
            "status": "error",
            "message": f"Could not create folder '{name}': {str(e)}"
        }


def download_file(url: str, destination_path: str = "desktop", filename: str = "") -> Dict[str, Any]:
    """Download a file from a URL and save it to the specified path/folder.

    Args:
        url: Direct URL to the file to download (must start with http:// or https://).
        destination_path: Destination folder. Use 'desktop', 'downloads', 'documents',
                          or an absolute path. Defaults to Desktop.
        filename: Optional filename to save as. If empty, inferred from URL.

    Returns:
        dict with status, file_path, size_bytes, message.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"status": "error", "message": f"Invalid URL '{url}'. Must start with http:// or https://."}

    # Resolve destination folder (mirrors create_folder logic)
    user_home = Path.home()
    onedrive_dir = user_home / "OneDrive"
    dest_key = (destination_path or "desktop").lower().strip()

    if dest_key in ("desktop", "desktop/"):
        candidate = onedrive_dir / "Desktop"
        base_dir = candidate if candidate.is_dir() else (user_home / "Desktop")
    elif dest_key in ("documents", "my documents", "documents/"):
        candidate = onedrive_dir / "Documents"
        base_dir = candidate if candidate.is_dir() else (user_home / "Documents")
    elif dest_key in ("downloads", "downloads/"):
        base_dir = user_home / "Downloads"
    elif dest_key in ("home", "user", "~"):
        base_dir = user_home
    else:
        custom = Path(destination_path).expanduser()
        base_dir = custom if custom.is_absolute() else (user_home / custom)

    base_dir.mkdir(parents=True, exist_ok=True)

    # Infer filename from URL if not provided
    if not filename:
        url_path = url.split("?")[0].rstrip("/")
        filename = url_path.split("/")[-1] or "downloaded_file"
    # Sanitize filename
    filename = "".join(c for c in filename if c not in r'\/:*?"<>|').strip()
    if not filename:
        filename = "downloaded_file"

    save_path = base_dir / filename

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read()
        with open(save_path, "wb") as f:
            f.write(data)
        size_bytes = len(data)
        logger.info(f"Downloaded {url} → {save_path} ({size_bytes} bytes)")
        return {
            "status": "success",
            "message": f"Downloaded and saved '{filename}' to '{save_path}' ({size_bytes:,} bytes).",
            "file_path": str(save_path),
            "size_bytes": size_bytes,
        }
    except urllib.error.HTTPError as e:
        logger.error(f"HTTP error downloading '{url}': {e.code} {e.reason}")
        return {"status": "error", "message": f"HTTP {e.code} error downloading file: {e.reason}"}
    except urllib.error.URLError as e:
        logger.error(f"URL error downloading '{url}': {e.reason}")
        return {"status": "error", "message": f"Network error (are you offline?): {e.reason}"}
    except Exception as e:
        logger.error(f"Failed to download '{url}': {e}")
        return {"status": "error", "message": f"Download failed: {str(e)}"}

