"""Tests for V4L2 video and ALSA audio device enumeration."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from commercial_detector.device_discovery import list_video_devices, list_audio_devices


# ---------------------------------------------------------------------------
# V4L2 video device tests
# ---------------------------------------------------------------------------

class TestListVideoDevices:
    """Tests for list_video_devices() using tmp_path sysfs fakes."""

    def _make_v4l2_device(self, sysfs_root: Path, index: int, name: str | None = None):
        """Helper: create a fake /sys/class/video4linux/videoN directory."""
        dev_dir = sysfs_root / "video4linux" / f"video{index}"
        dev_dir.mkdir(parents=True, exist_ok=True)
        if name is not None:
            (dev_dir / "name").write_text(name + "\n")
        return dev_dir

    def test_single_device(self, tmp_path):
        self._make_v4l2_device(tmp_path, 0, "USB Video Capture")
        result = list_video_devices(sysfs_root=tmp_path)
        assert result == [{"path": "/dev/video0", "name": "USB Video Capture"}]

    def test_multiple_devices(self, tmp_path):
        self._make_v4l2_device(tmp_path, 0, "Built-in Webcam")
        self._make_v4l2_device(tmp_path, 1, "USB Capture Card")
        result = list_video_devices(sysfs_root=tmp_path)
        assert len(result) == 2
        assert result[0]["name"] == "Built-in Webcam"
        assert result[1]["name"] == "USB Capture Card"

    def test_no_devices(self, tmp_path):
        (tmp_path / "video4linux").mkdir(parents=True)
        result = list_video_devices(sysfs_root=tmp_path)
        assert result == []

    def test_missing_sysfs_dir(self, tmp_path):
        # sysfs_root exists but video4linux subdir does not
        result = list_video_devices(sysfs_root=tmp_path)
        assert result == []

    def test_missing_name_file(self, tmp_path):
        self._make_v4l2_device(tmp_path, 2, name=None)
        result = list_video_devices(sysfs_root=tmp_path)
        assert result == [{"path": "/dev/video2", "name": "video2"}]

    def test_sorted_by_path(self, tmp_path):
        # Create in reverse order
        self._make_v4l2_device(tmp_path, 10, "Device Ten")
        self._make_v4l2_device(tmp_path, 2, "Device Two")
        self._make_v4l2_device(tmp_path, 0, "Device Zero")
        result = list_video_devices(sysfs_root=tmp_path)
        paths = [d["path"] for d in result]
        assert paths == ["/dev/video0", "/dev/video2", "/dev/video10"]


# ---------------------------------------------------------------------------
# ALSA audio device tests
# ---------------------------------------------------------------------------

ARECORD_SINGLE = """\
**** List of CAPTURE Hardware Devices ****
card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""

ARECORD_MULTIPLE = """\
**** List of CAPTURE Hardware Devices ****
card 0: PCH [HDA Intel PCH], device 0: ALC892 Analog [ALC892 Analog]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""

ARECORD_NO_DEVICES = """\
**** List of CAPTURE Hardware Devices ****
"""


class TestListAudioDevices:
    """Tests for list_audio_devices() using mocked subprocess.run."""

    def _mock_run(self, stdout: str, returncode: int = 0):
        result = MagicMock()
        result.stdout = stdout
        result.returncode = returncode
        return result

    @patch("commercial_detector.device_discovery.subprocess.run")
    def test_single_card(self, mock_run):
        mock_run.return_value = self._mock_run(ARECORD_SINGLE)
        result = list_audio_devices()
        assert result == [{"path": "hw:1,0", "name": "USB Audio Device"}]

    @patch("commercial_detector.device_discovery.subprocess.run")
    def test_multiple_cards(self, mock_run):
        mock_run.return_value = self._mock_run(ARECORD_MULTIPLE)
        result = list_audio_devices()
        assert len(result) == 2
        assert result[0] == {"path": "hw:0,0", "name": "HDA Intel PCH"}
        assert result[1] == {"path": "hw:1,0", "name": "USB Audio Device"}

    @patch("commercial_detector.device_discovery.subprocess.run")
    def test_no_devices(self, mock_run):
        mock_run.return_value = self._mock_run(ARECORD_NO_DEVICES)
        result = list_audio_devices()
        assert result == []

    @patch("commercial_detector.device_discovery.subprocess.run")
    def test_arecord_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("arecord not found")
        result = list_audio_devices()
        assert result == []

    @patch("commercial_detector.device_discovery.subprocess.run")
    def test_arecord_failure(self, mock_run):
        mock_run.return_value = self._mock_run("", returncode=1)
        result = list_audio_devices()
        assert result == []

    @patch("commercial_detector.device_discovery.subprocess.run")
    def test_device_with_different_card_device_numbers(self, mock_run):
        output = """\
**** List of CAPTURE Hardware Devices ****
card 3: Dongle [USB Dongle], device 2: Capture [Capture]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""
        mock_run.return_value = self._mock_run(output)
        result = list_audio_devices()
        assert result == [{"path": "hw:3,2", "name": "USB Dongle"}]
