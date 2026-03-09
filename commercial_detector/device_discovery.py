"""Enumerate V4L2 video capture and ALSA audio capture devices.

Used by the web dashboard's /api/devices endpoint to let users pick
which capture card to use. Pure functions, no state.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Default sysfs location on Linux
_DEFAULT_SYSFS_ROOT = Path("/sys/class")


def list_video_devices(
    sysfs_root: Path | None = None,
) -> list[dict[str, str]]:
    """Enumerate V4L2 video devices via sysfs.

    Args:
        sysfs_root: Base sysfs path (default ``/sys/class``).
                    Override with a tmp dir for testing.

    Returns:
        Sorted list of ``{"path": "/dev/videoN", "name": "..."}`` dicts.
    """
    if sysfs_root is None:
        sysfs_root = _DEFAULT_SYSFS_ROOT
    v4l2_dir = sysfs_root / "video4linux"
    if not v4l2_dir.is_dir():
        return []

    devices: list[dict[str, str]] = []
    for entry in v4l2_dir.iterdir():
        if not entry.is_dir():
            continue
        dir_name = entry.name  # e.g. "video0"
        name_file = entry / "name"
        if name_file.is_file():
            name = name_file.read_text().strip()
        else:
            name = dir_name
        devices.append({"path": f"/dev/{dir_name}", "name": name})

    devices.sort(key=lambda d: _natural_sort_key(d["path"]))
    return devices


def _natural_sort_key(s: str) -> list[int | str]:
    """Sort strings with embedded numbers naturally (video2 < video10)."""
    parts: list[int | str] = []
    for segment in re.split(r"(\d+)", s):
        if segment.isdigit():
            parts.append(int(segment))
        else:
            parts.append(segment)
    return parts


# Regex for arecord -l output lines like:
#   card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]
_ARECORD_LINE_RE = re.compile(
    r"^card\s+(\d+):\s+\S+\s+\[(.+?)\],\s+device\s+(\d+):"
)


def list_audio_devices() -> list[dict[str, str]]:
    """Enumerate ALSA capture devices by running ``arecord -l``.

    Returns:
        List of ``{"path": "hw:N,M", "name": "..."}`` dicts.
        Empty list on any error (missing binary, timeout, non-zero exit).
    """
    try:
        proc = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if proc.returncode != 0:
        return []

    devices: list[dict[str, str]] = []
    for line in proc.stdout.splitlines():
        m = _ARECORD_LINE_RE.match(line)
        if m:
            card, name, device = m.group(1), m.group(2), m.group(3)
            devices.append({"path": f"hw:{card},{device}", "name": name})

    return devices
