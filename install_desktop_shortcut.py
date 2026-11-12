"""
Generate static/assets/anureet_icon.ico from the Chibi 3D Anureet avatar and install
the one-click 'Anureet AI.lnk' shortcut on the user's Windows Desktop.
"""

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PIL import Image

from config import config

PROJECT_ROOT = Path(__file__).resolve().parent
ICON_PATH = PROJECT_ROOT / "static" / "assets" / "anureet_icon.ico"


def build_ico_from_avatar(
    avatar_path: Optional[Path] = None,
    output_ico: Optional[Path] = None,
) -> Path:
    """Convert the transparent Chibi Anureet PNG into a multi-size Windows .ico file."""
    src = Path(avatar_path or config.widget_avatar_path)
    dst = Path(output_ico or ICON_PATH)
    if not src.is_file():
        raise FileNotFoundError(f"Avatar image not found: {src}")

    os.makedirs(dst.parent, exist_ok=True)
    img = Image.open(src).convert("RGBA")
    side = max(img.width, img.height)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    square.save(
        dst,
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return dst


def resolve_desktop_dir() -> Path:
    """Resolve the real Windows Desktop path (handles OneDrive folder redirection)."""
    if os.name == "nt":
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            desktop = res.stdout.strip()
            if desktop and Path(desktop).exists():
                return Path(desktop)
        except Exception:
            pass
    return Path.home() / "Desktop"


def resolve_pythonw_executable() -> Path:
    """Prefer venv pythonw.exe so double-clicking the Desktop icon opens no console window."""
    candidates = [
        PROJECT_ROOT / "venv" / "Scripts" / "pythonw.exe",
        Path(sys.executable).with_name("pythonw.exe"),
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def create_desktop_shortcut(shortcut_name: str = "Anureet AI.lnk", desktop_dir: Optional[Path] = None) -> Path:
    """Create the Windows .lnk Desktop shortcut pointing to launch_anureet.py via pythonw.exe."""
    ico_path = build_ico_from_avatar()
    target_desktop = desktop_dir or resolve_desktop_dir()
    os.makedirs(target_desktop, exist_ok=True)
    lnk_path = target_desktop / shortcut_name

    pythonw = resolve_pythonw_executable()
    launcher = PROJECT_ROOT / "launch_anureet.py"

    def _ps_escape(val: Path) -> str:
        return str(val).replace("'", "''")

    ps_script = (
        "$WshShell = New-Object -ComObject WScript.Shell; "
        f"$Shortcut = $WshShell.CreateShortcut('{_ps_escape(lnk_path)}'); "
        f"$Shortcut.TargetPath = '{_ps_escape(pythonw)}'; "
        f"$Shortcut.Arguments = '\"{_ps_escape(launcher)}\"'; "
        f"$Shortcut.WorkingDirectory = '{_ps_escape(PROJECT_ROOT)}'; "
        f"$Shortcut.IconLocation = '{_ps_escape(ico_path)},0'; "
        "$Shortcut.Description = 'Launch Anureet AI (Ollama + Gateway + Animated Widget)'; "
        "$Shortcut.WindowStyle = 7; "
        "$Shortcut.Save()"
    )
    encoded = base64.b64encode(ps_script.encode("utf-16le")).decode("ascii")
    res = subprocess.run(
        ["powershell", "-NoProfile", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if res.returncode != 0:
        raise RuntimeError(f"Failed to create desktop shortcut: {res.stderr or res.stdout}")
    return lnk_path


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Install the one-click Anureet AI Desktop shortcut.")
    parser.add_argument("--name", default="Anureet AI.lnk", help="Shortcut filename on Desktop")
    args = parser.parse_args(argv)
    lnk_path = create_desktop_shortcut(shortcut_name=args.name)
    print(f"Created icon: {ICON_PATH}")
    print(f"Created Desktop shortcut: {lnk_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

