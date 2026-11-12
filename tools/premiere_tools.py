"""
Adobe Premiere Pro Automation Tool for Local AI Agent.
Generates ExtendScript (.jsx) and executes or queues project setups,
color matte creation, video imports, and timeline cut points.
"""

import os
import sys
import json
import shutil
import subprocess
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("premiere_tools")

TEMPLATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "premiere_automation.jsx"
)

PREMIERE_EXECUTABLES = [
    r"C:\Program Files\Adobe\Adobe Premiere Pro 2024\Adobe Premiere Pro.exe",
    r"C:\Program Files\Adobe\Adobe Premiere Pro 2023\Adobe Premiere Pro.exe",
    r"C:\Program Files\Adobe\Adobe Premiere Pro CC\Adobe Premiere Pro.exe",
    "Adobe Premiere Pro.exe"
]


def premiere_import_and_setup(
    video_path: str,
    add_white_matte: bool = True,
    cut_points: Optional[List[float]] = None
) -> Dict[str, Any]:
    """
    Generate an ExtendScript (.jsx) file for Adobe Premiere Pro automation
    and attempt to execute it via Adobe Premiere Pro / ExtendScript runner.

    Args:
        video_path (str): Full path to the video file to import.
        add_white_matte (bool): Whether to create a white color matte layer.
        cut_points (list): List of timestamps in seconds for timeline cut markers.

    Returns:
        dict: Result status including generated JSX file path and execution output.
    """
    if cut_points is None:
        cut_points = []

    video_path_json = json.dumps(os.path.abspath(video_path) if video_path else "")

    # Generate custom ExtendScript content
    jsx_content = f"""/* Dynamically Generated Premiere Automation Script */
function runPremiereAutomation(videoPath, addWhiteMatte, cutPointsJSON) {{
    try {{
        app.enableQE();
        var proj = app.project;
        if (!proj) {{
            $.writeln("No active Adobe Premiere Pro project found!");
            return false;
        }}

        var rootItem = proj.rootItem;

        // 1. Import Video
        if (videoPath && videoPath.length > 0) {{
            var importSuccess = proj.importFiles([videoPath], true, rootItem, false);
            $.writeln("Import status for " + videoPath + ": " + importSuccess);
        }}

        // 2. Add White Color Matte
        if (addWhiteMatte) {{
            var newMatte = proj.createNewColorMatte(255, 255, 255, "White Matte Layer");
            $.writeln("Created White Matte Layer");
        }}

        // 3. Process Sequence and Cut Points
        var activeSeq = proj.activeSequence;
        if (activeSeq && cutPointsJSON && cutPointsJSON.length > 0) {{
            for (var i = 0; i < cutPointsJSON.length; i++) {{
                var timestampSec = cutPointsJSON[i];
                var marker = activeSeq.markers.createMarker(timestampSec);
                marker.name = "AI Cut Point " + (i + 1);
                marker.comments = "Automated cut point at " + timestampSec + "s";
            }}
            $.writeln("Processed " + cutPointsJSON.length + " cut markers.");
        }}
        return true;
    }} catch(err) {{
        $.writeln("Error executing Premiere script: " + err.toString());
        return false;
    }}
}}

var CONFIG = {{
    videoPath: {video_path_json},
    addWhiteMatte: {str(add_white_matte).lower()},
    cutPoints: {json.dumps(cut_points)}
}};

runPremiereAutomation(CONFIG.videoPath, CONFIG.addWhiteMatte, CONFIG.cutPoints);
"""

    scripts_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    out_jsx_path = os.path.join(scripts_dir, "generated_premiere_task.jsx")

    try:
        with open(out_jsx_path, "w", encoding="utf-8") as f:
            f.write(jsx_content)

        logger.info(f"Saved ExtendScript to {out_jsx_path}")

        # Attempt execution on Windows if Adobe Premiere Pro is available
        premiere_exe = None
        for path in PREMIERE_EXECUTABLES:
            if os.path.isabs(path) and os.path.exists(path):
                premiere_exe = path
                break
            found = shutil.which(path)
            if found:
                premiere_exe = found
                break

        execution_triggered = False
        if premiere_exe and sys.platform == "win32":
            try:
                # Launch Adobe Premiere Pro passing script argument
                cmd = [premiere_exe, "-e", out_jsx_path]
                subprocess.Popen(cmd)
                execution_triggered = True
                exec_msg = f"Triggered Premiere Pro with generated script: {out_jsx_path}"
            except Exception as ex:
                exec_msg = f"Generated script successfully, but could not execute via command line: {ex}"
        else:
            exec_msg = f"Generated script successfully at {out_jsx_path}. Execute in Premiere via File -> Scripts or ExtendScript Toolkit."

        return {
            "status": "success",
            "message": exec_msg,
            "script_path": out_jsx_path,
            "execution_triggered": execution_triggered,
            "config": {
                "video_path": video_path,
                "add_white_matte": add_white_matte,
                "cut_points": cut_points
            }
        }

    except Exception as e:
        logger.error(f"Error in premiere_import_and_setup: {e}")
        return {
            "status": "error",
            "message": f"Failed to generate or run Premiere automation: {str(e)}"
        }
