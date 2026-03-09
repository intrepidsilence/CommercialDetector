# Device Discovery & MQTT Configuration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add capture device auto-detection (V4L2 + ALSA) to the web config page, and update the install script for remote-only MQTT broker configuration.

**Architecture:** New `device_discovery.py` module with pure functions for enumerating V4L2 video and ALSA audio devices. A `/api/devices` REST endpoint exposes discovery results. The config page gets dropdowns with a "Detect Devices" button. The install script drops Mosquitto and prompts for remote MQTT broker details.

**Tech Stack:** Python 3.11+, Flask, subprocess (for `arecord -l`), pathlib (for `/sys/class/video4linux/`), Jinja2, vanilla JS

---

### Task 1: Device Discovery Module — V4L2 Enumeration

**Files:**
- Create: `commercial_detector/device_discovery.py`
- Create: `tests/test_device_discovery.py`

**Step 1: Write the failing test for V4L2 enumeration**

```python
# tests/test_device_discovery.py
"""Tests for device discovery module."""

import pytest
from unittest.mock import patch, mock_open
from commercial_detector.device_discovery import list_video_devices


class TestListVideoDevices:
    """V4L2 video device enumeration."""

    def test_single_device(self, tmp_path):
        """A single video device is found."""
        video0 = tmp_path / "video4linux" / "video0"
        video0.mkdir(parents=True)
        (video0 / "name").write_text("USB Video Capture\n")

        devices = list_video_devices(sysfs_root=tmp_path)
        assert len(devices) == 1
        assert devices[0]["path"] == "/dev/video0"
        assert devices[0]["name"] == "USB Video Capture"

    def test_multiple_devices(self, tmp_path):
        """Multiple video devices are listed."""
        for i in range(3):
            d = tmp_path / "video4linux" / f"video{i}"
            d.mkdir(parents=True)
            (d / "name").write_text(f"Device {i}\n")

        devices = list_video_devices(sysfs_root=tmp_path)
        assert len(devices) == 3
        paths = [d["path"] for d in devices]
        assert "/dev/video0" in paths
        assert "/dev/video1" in paths
        assert "/dev/video2" in paths

    def test_no_devices(self, tmp_path):
        """Empty list when no video devices exist."""
        (tmp_path / "video4linux").mkdir(parents=True)
        devices = list_video_devices(sysfs_root=tmp_path)
        assert devices == []

    def test_missing_sysfs_dir(self, tmp_path):
        """Empty list when sysfs directory doesn't exist."""
        devices = list_video_devices(sysfs_root=tmp_path)
        assert devices == []

    def test_missing_name_file(self, tmp_path):
        """Device with no name file uses path as name."""
        video0 = tmp_path / "video4linux" / "video0"
        video0.mkdir(parents=True)
        # no "name" file

        devices = list_video_devices(sysfs_root=tmp_path)
        assert len(devices) == 1
        assert devices[0]["name"] == "video0"

    def test_sorted_by_path(self, tmp_path):
        """Devices are sorted by path."""
        for i in [2, 0, 1]:
            d = tmp_path / "video4linux" / f"video{i}"
            d.mkdir(parents=True)
            (d / "name").write_text(f"Dev {i}\n")

        devices = list_video_devices(sysfs_root=tmp_path)
        paths = [d["path"] for d in devices]
        assert paths == ["/dev/video0", "/dev/video1", "/dev/video2"]
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_device_discovery.py::TestListVideoDevices -v`
Expected: FAIL with "ModuleNotFoundError" or "cannot import name 'list_video_devices'"

**Step 3: Write minimal implementation**

```python
# commercial_detector/device_discovery.py
"""Capture device discovery for V4L2 video and ALSA audio devices.

Provides pure functions to enumerate available capture devices on Linux.
Used by the web dashboard config page to populate device dropdowns.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_SYSFS = Path("/sys/class")


def list_video_devices(sysfs_root: Path | None = None) -> list[dict]:
    """Enumerate V4L2 video devices from sysfs.

    Returns a list of dicts: [{"path": "/dev/video0", "name": "USB Video"}]
    """
    root = (sysfs_root or _DEFAULT_SYSFS) / "video4linux"
    if not root.is_dir():
        return []

    devices = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        dev_path = f"/dev/{entry.name}"
        name_file = entry / "name"
        if name_file.exists():
            name = name_file.read_text().strip()
        else:
            name = entry.name
        devices.append({"path": dev_path, "name": name})

    return sorted(devices, key=lambda d: d["path"])
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_device_discovery.py::TestListVideoDevices -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add commercial_detector/device_discovery.py tests/test_device_discovery.py
git commit -m "feat: add V4L2 video device enumeration"
```

---

### Task 2: Device Discovery Module — ALSA Enumeration

**Files:**
- Modify: `commercial_detector/device_discovery.py`
- Modify: `tests/test_device_discovery.py`

**Step 1: Write the failing test for ALSA enumeration**

Add to `tests/test_device_discovery.py`:

```python
from commercial_detector.device_discovery import list_audio_devices


class TestListAudioDevices:
    """ALSA audio device enumeration via arecord -l."""

    def test_single_card(self):
        """Parse arecord output with one capture device."""
        output = (
            "**** List of CAPTURE Hardware Devices ****\n"
            "card 1: Device [USB Audio Device], device 0: USB Audio [USB Audio]\n"
            "  Subdevices: 1/1\n"
            "  Subdevice #0: subdevice #0\n"
        )
        with patch("commercial_detector.device_discovery.subprocess.run") as mock_run:
            mock_run.return_value.stdout = output
            mock_run.return_value.returncode = 0

            devices = list_audio_devices()
            assert len(devices) == 1
            assert devices[0]["path"] == "hw:1,0"
            assert devices[0]["name"] == "USB Audio Device"

    def test_multiple_cards(self):
        """Parse arecord output with multiple capture devices."""
        output = (
            "**** List of CAPTURE Hardware Devices ****\n"
            "card 0: PCH [HDA Intel PCH], device 0: ALC269 Analog [ALC269 Analog]\n"
            "  Subdevices: 1/1\n"
            "  Subdevice #0: subdevice #0\n"
            "card 2: Capture [USB Capture], device 0: USB Audio [USB Audio]\n"
            "  Subdevices: 1/1\n"
            "  Subdevice #0: subdevice #0\n"
        )
        with patch("commercial_detector.device_discovery.subprocess.run") as mock_run:
            mock_run.return_value.stdout = output
            mock_run.return_value.returncode = 0

            devices = list_audio_devices()
            assert len(devices) == 2
            assert devices[0]["path"] == "hw:0,0"
            assert devices[1]["path"] == "hw:2,0"

    def test_no_devices(self):
        """Empty list when arecord reports no capture devices."""
        output = "**** List of CAPTURE Hardware Devices ****\n"
        with patch("commercial_detector.device_discovery.subprocess.run") as mock_run:
            mock_run.return_value.stdout = output
            mock_run.return_value.returncode = 0

            devices = list_audio_devices()
            assert devices == []

    def test_arecord_not_found(self):
        """Empty list when arecord is not installed."""
        with patch("commercial_detector.device_discovery.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("arecord not found")

            devices = list_audio_devices()
            assert devices == []

    def test_arecord_failure(self):
        """Empty list when arecord returns non-zero."""
        with patch("commercial_detector.device_discovery.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            mock_run.return_value.returncode = 1

            devices = list_audio_devices()
            assert devices == []

    def test_device_with_multiple_subdevices(self):
        """Card number and device number are parsed correctly."""
        output = (
            "**** List of CAPTURE Hardware Devices ****\n"
            "card 3: HDMI [USB HDMI Capture], device 2: Capture [HDMI Capture]\n"
            "  Subdevices: 1/1\n"
            "  Subdevice #0: subdevice #0\n"
        )
        with patch("commercial_detector.device_discovery.subprocess.run") as mock_run:
            mock_run.return_value.stdout = output
            mock_run.return_value.returncode = 0

            devices = list_audio_devices()
            assert len(devices) == 1
            assert devices[0]["path"] == "hw:3,2"
            assert devices[0]["name"] == "USB HDMI Capture"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_device_discovery.py::TestListAudioDevices -v`
Expected: FAIL with "cannot import name 'list_audio_devices'"

**Step 3: Write minimal implementation**

Add to `commercial_detector/device_discovery.py`:

```python
import re

_RE_ARECORD_CARD = re.compile(
    r"^card\s+(\d+):\s+(\w+)\s+\[([^\]]+)\],\s+device\s+(\d+):"
)


def list_audio_devices() -> list[dict]:
    """Enumerate ALSA audio capture devices via arecord -l.

    Returns a list of dicts: [{"path": "hw:1,0", "name": "USB Audio Device"}]
    """
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    devices = []
    for line in result.stdout.splitlines():
        m = _RE_ARECORD_CARD.match(line)
        if m:
            card_num, _card_id, card_name, dev_num = m.groups()
            devices.append({
                "path": f"hw:{card_num},{dev_num}",
                "name": card_name,
            })

    return devices
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_device_discovery.py -v`
Expected: PASS (all 12 tests)

**Step 5: Commit**

```bash
git add commercial_detector/device_discovery.py tests/test_device_discovery.py
git commit -m "feat: add ALSA audio device enumeration"
```

---

### Task 3: REST API endpoint — `/api/devices`

**Files:**
- Modify: `commercial_detector/web/server.py:76-93`
- Modify: `tests/test_web_server.py`

**Step 1: Write the failing test**

Add to `tests/test_web_server.py`:

```python
from unittest.mock import patch


class TestDeviceDiscovery:
    """GET /api/devices endpoint."""

    def test_api_devices_returns_video_and_audio(self, client):
        """Endpoint returns video and audio device lists."""
        mock_video = [{"path": "/dev/video0", "name": "USB Capture"}]
        mock_audio = [{"path": "hw:1,0", "name": "USB Audio"}]

        with patch("commercial_detector.web.server.list_video_devices", return_value=mock_video), \
             patch("commercial_detector.web.server.list_audio_devices", return_value=mock_audio):
            resp = client.get("/api/devices")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["video"] == mock_video
            assert data["audio"] == mock_audio

    def test_api_devices_empty(self, client):
        """Endpoint returns empty lists when no devices found."""
        with patch("commercial_detector.web.server.list_video_devices", return_value=[]), \
             patch("commercial_detector.web.server.list_audio_devices", return_value=[]):
            resp = client.get("/api/devices")
            assert resp.status_code == 200
            data = resp.get_json()
            assert data["video"] == []
            assert data["audio"] == []
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web_server.py::TestDeviceDiscovery -v`
Expected: FAIL with 404 (route doesn't exist yet)

**Step 3: Write minimal implementation**

In `commercial_detector/web/server.py`, add the import at the top (after the existing imports):

```python
from commercial_detector.device_discovery import list_audio_devices, list_video_devices
```

Add the route inside `create_app()`, after the existing `/api/system` route (around line 112):

```python
    @app.route("/api/devices")
    def api_devices():
        return jsonify({
            "video": list_video_devices(),
            "audio": list_audio_devices(),
        })
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_web_server.py -v`
Expected: PASS (all 14 tests)

**Step 5: Commit**

```bash
git add commercial_detector/web/server.py tests/test_web_server.py
git commit -m "feat: add /api/devices endpoint for capture device discovery"
```

---

### Task 4: Config Page — Device Dropdowns & Detect Button

**Files:**
- Modify: `commercial_detector/web/templates/config.html:17-46`
- Modify: `commercial_detector/web/static/app.js` (add device detection function at end)

**Step 1: Update the Capture section in config.html**

Replace the Capture `<details>` section (lines 18-46) in `commercial_detector/web/templates/config.html` with:

```html
    {# --- Capture --- #}
    <details class="config-section" open>
      <summary class="config-section-title">Capture</summary>
      <div class="config-fields">
        <div class="config-field">
          <label for="capture-video_device">Video Device</label>
          <div class="device-select-group">
            <select id="capture-video_device-select" class="device-dropdown">
              <option value="">-- Select or type below --</option>
            </select>
            <input type="text" id="capture-video_device" name="capture.video_device"
                   value="{{ config.capture.video_device }}" data-restart>
          </div>
          <span class="field-desc">V4L2 video device path <span class="restart-badge">restart</span></span>
        </div>
        <div class="config-field">
          <label for="capture-audio_device">Audio Device</label>
          <div class="device-select-group">
            <select id="capture-audio_device-select" class="device-dropdown">
              <option value="">-- Select or type below --</option>
            </select>
            <input type="text" id="capture-audio_device" name="capture.audio_device"
                   value="{{ config.capture.audio_device }}" data-restart>
          </div>
          <span class="field-desc">ALSA audio device <span class="restart-badge">restart</span></span>
        </div>
        <div class="config-field" style="grid-column: 1 / -1;">
          <button type="button" class="btn btn-primary" id="detect-devices-btn">Detect Devices</button>
          <span class="config-feedback" id="detect-feedback"></span>
        </div>
        <div class="config-field">
          <label for="capture-input_format">Input Format</label>
          <input type="text" id="capture-input_format" name="capture.input_format"
                 value="{{ config.capture.input_format }}" data-restart>
          <span class="field-desc">V4L2 pixel format <span class="restart-badge">restart</span></span>
        </div>
        <div class="config-field">
          <label for="capture-input_file">Input File</label>
          <input type="text" id="capture-input_file" name="capture.input_file"
                 value="{{ config.capture.input_file or '' }}" data-restart>
          <span class="field-desc">File path for testing (blank = live capture) <span class="restart-badge">restart</span></span>
        </div>
      </div>
    </details>
```

**Step 2: Add device detection JavaScript**

Add to the end of `commercial_detector/web/static/app.js` (inside the DOMContentLoaded listener, before the closing `});`):

```javascript
  // ----------------------------------------------------------------
  // Device Detection
  // ----------------------------------------------------------------

  const detectBtn = document.getElementById('detect-devices-btn');
  const detectFeedback = document.getElementById('detect-feedback');

  if (detectBtn) {
    detectBtn.addEventListener('click', () => {
      detectBtn.disabled = true;
      detectBtn.textContent = 'Detecting\u2026';
      if (detectFeedback) {
        detectFeedback.textContent = '';
        detectFeedback.className = 'config-feedback';
      }

      fetch('/api/devices')
        .then(r => {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(data => {
          populateDeviceDropdown('capture-video_device', data.video);
          populateDeviceDropdown('capture-audio_device', data.audio);

          const total = (data.video || []).length + (data.audio || []).length;
          if (detectFeedback) {
            detectFeedback.textContent = total > 0
              ? 'Found ' + (data.video || []).length + ' video, ' + (data.audio || []).length + ' audio device(s)'
              : 'No devices found';
            detectFeedback.className = 'config-feedback ' + (total > 0 ? 'success' : 'error');
          }
        })
        .catch(err => {
          if (detectFeedback) {
            detectFeedback.textContent = 'Detection failed: ' + err.message;
            detectFeedback.className = 'config-feedback error';
          }
        })
        .finally(() => {
          detectBtn.disabled = false;
          detectBtn.textContent = 'Detect Devices';
        });
    });
  }

  function populateDeviceDropdown(inputId, devices) {
    const select = document.getElementById(inputId + '-select');
    const input = document.getElementById(inputId);
    if (!select || !input) return;

    // Clear existing options except placeholder
    while (select.options.length > 1) {
      select.remove(1);
    }

    (devices || []).forEach(dev => {
      const opt = document.createElement('option');
      opt.value = dev.path;
      opt.textContent = dev.path + ' \u2014 ' + dev.name;
      select.appendChild(opt);
    });

    // Auto-select if current value matches a discovered device
    const currentVal = input.value;
    for (let i = 0; i < select.options.length; i++) {
      if (select.options[i].value === currentVal) {
        select.selectedIndex = i;
        break;
      }
    }

    // When dropdown changes, update the text input
    select.onchange = () => {
      if (select.value) {
        input.value = select.value;
      }
    };
  }
```

**Step 3: Add CSS for device select group**

Add to `commercial_detector/web/static/style.css`:

```css
/* Device discovery dropdown + text input combo */
.device-select-group {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.device-dropdown {
  background: var(--bg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 0.85rem;
}
.device-dropdown:focus {
  border-color: var(--blue);
  outline: none;
}
```

**Step 4: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: PASS (all tests)

**Step 5: Commit**

```bash
git add commercial_detector/web/templates/config.html commercial_detector/web/static/app.js commercial_detector/web/static/style.css
git commit -m "feat: add device detection UI to config page"
```

---

### Task 5: Update Install Script — Remote MQTT Only

**Files:**
- Modify: `install.sh`

**Step 1: Update the install script**

Make these changes to `install.sh`:

1. **Add MQTT environment variables** to the configuration section (after line 21):

```bash
MQTT_HOST="${MQTT_HOST:-}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USERNAME="${MQTT_USERNAME:-}"
MQTT_PASSWORD="${MQTT_PASSWORD:-}"
```

2. **Remove mosquitto from all package manager install commands** (lines 73-99). Remove `mosquitto mosquitto-clients` from the apt-get, dnf, pacman, and brew install lines. Also remove `mosquitto` from the ok messages.

3. **Add MQTT configuration section** after the Whisper section (after line 166) and before the verify section:

```bash
# --- MQTT broker configuration -----------------------------------------------

info "Configuring MQTT broker connection..."

# Interactive prompts if not set via environment
if [[ -z "$MQTT_HOST" ]]; then
    echo ""
    read -rp "  MQTT broker hostname or IP: " MQTT_HOST
fi

if [[ -z "$MQTT_HOST" ]]; then
    warn "No MQTT host provided. Using localhost (edit config.yaml later)."
    MQTT_HOST="localhost"
fi

if [[ -z "$MQTT_PORT" || "$MQTT_PORT" == "1883" ]]; then
    read -rp "  MQTT broker port [1883]: " input_port
    MQTT_PORT="${input_port:-1883}"
fi

if [[ -z "$MQTT_USERNAME" ]]; then
    read -rp "  MQTT username (blank for none): " MQTT_USERNAME
fi

if [[ -n "$MQTT_USERNAME" && -z "$MQTT_PASSWORD" ]]; then
    read -rsp "  MQTT password: " MQTT_PASSWORD
    echo ""
fi

# Write MQTT settings to config.yaml
python3 - "$INSTALL_DIR/config.yaml" "$MQTT_HOST" "$MQTT_PORT" "$MQTT_USERNAME" "$MQTT_PASSWORD" << 'PYEOF'
import sys, yaml
from pathlib import Path

config_path, host, port, username, password = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
p = Path(config_path)
config = {}
if p.exists():
    with open(p) as f:
        config = yaml.safe_load(f) or {}

config.setdefault("mqtt", {})
config["mqtt"]["broker_host"] = host
config["mqtt"]["broker_port"] = int(port)
if username:
    config["mqtt"]["username"] = username
    config["mqtt"]["password"] = password
else:
    config["mqtt"].pop("username", None)
    config["mqtt"].pop("password", None)

with open(p, "w") as f:
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)
PYEOF

ok "MQTT broker: $MQTT_HOST:$MQTT_PORT"
```

4. **Update systemd service** — remove `mosquitto.service` from `After=` and `Wants=` lines (lines 210-211). Replace with:

```
After=network.target
```

5. **Update the quick start section** at the end — remove the `mosquitto_sub` line and add MQTT info:

```bash
echo "    # Monitor MQTT output (install mosquitto-clients on any machine)"
echo "    mosquitto_sub -h <MQTT_HOST> -t 'commercial-detector/#' -v"
```

6. **Add MQTT env vars to the usage comment** at top of script:

```bash
#   MQTT_HOST=broker.example.com          # Remote MQTT broker hostname
#   MQTT_PORT=1883                         # Remote MQTT broker port
#   MQTT_USERNAME=user                     # MQTT username (optional)
#   MQTT_PASSWORD=pass                     # MQTT password (optional)
```

**Step 2: Run the full test suite** (install script changes don't have unit tests, but verify nothing else broke)

Run: `python -m pytest tests/ -v`
Expected: PASS

**Step 3: Commit**

```bash
git add install.sh
git commit -m "feat: install script prompts for remote MQTT broker, removes Mosquitto"
```

---

### Task 6: Update README — Remote MQTT & Web Dashboard Device Config

**Files:**
- Modify: `README.md`

**Step 1: Update README**

1. **Remove Mosquitto from prerequisites** — delete "Mosquitto MQTT broker" from the prerequisites list. Add instead: "Access to an MQTT broker (remote)"

2. **Remove `mosquitto` and `mosquitto-clients` from apt/brew install commands** in the Manual Installation section.

3. **Update the install script env vars section** — add the MQTT variables:

```
| `MQTT_HOST` | Remote MQTT broker hostname |
| `MQTT_PORT` | MQTT broker port (default: 1883) |
| `MQTT_USERNAME` | MQTT username (optional) |
| `MQTT_PASSWORD` | MQTT password (optional) |
```

4. **Update the "Monitor MQTT output" command** to show `-h <broker-host>`.

5. **Update the curl examples** — add `MQTT_HOST=broker.example.com` to the full install command.

**Step 2: Run tests**

Run: `python -m pytest tests/ -v`
Expected: PASS

**Step 3: Commit and push**

```bash
git add README.md
git commit -m "docs: update README for remote MQTT and device discovery"
git push
```

---

## Execution Order

Tasks are mostly independent, but have a natural dependency chain:

```
Task 1 (V4L2 discovery) ─┐
                          ├──→ Task 3 (API endpoint) ──→ Task 4 (Config UI)
Task 2 (ALSA discovery) ─┘
                                                         Task 5 (Install script)
                                                         Task 6 (README + push)
```

- Tasks 1 & 2 can run in parallel (both add to same files but different functions/classes)
- Task 3 depends on Tasks 1 & 2
- Task 4 depends on Task 3
- Tasks 5 & 6 are independent of 1-4 but should run after for a clean final push
