# Device Discovery & MQTT Configuration Design

## Goal

Add capture device auto-detection (V4L2 video + ALSA audio) to the web dashboard config page, and update the install script for remote-only MQTT broker configuration.

## Device Discovery

### Backend

New `commercial_detector/device_discovery.py` module with pure functions:

- **V4L2 enumeration**: Read `/sys/class/video4linux/video*/name` to list video capture devices with paths and human-readable names.
- **ALSA enumeration**: Parse `arecord -l` output to list audio capture devices with card/device numbers and names.
- **Optional FFmpeg probe**: Quick validation that a selected device is accessible.

New REST endpoint: `GET /api/devices` returns:
```json
{
  "video": [
    {"path": "/dev/video0", "name": "USB Video Capture"}
  ],
  "audio": [
    {"path": "hw:1,0", "name": "USB Audio Device"}
  ]
}
```

### Frontend

Replace plain text inputs in the Capture section of config.html with:
- "Detect Devices" button that calls `/api/devices`
- Dropdown selectors populated with discovered devices (path + name)
- Fallback text input for manual entry if detection fails or returns empty

### Out of Scope

- Auto-selecting "best" device — user picks from list
- Hotplug monitoring — detect on demand only
- Format/resolution negotiation

## MQTT Configuration

### Current State

- install.sh installs Mosquitto locally — incorrect, broker is remote
- Config page already has full MQTT fields (host, port, topic prefix, credentials)
- config.yaml defaults to localhost:1883

### Changes

- **install.sh**: Remove Mosquitto from apt install. Add interactive prompts for remote MQTT broker host, port, username, password. Write to config.yaml.
- **README.md**: Remove Mosquitto installation references. Document remote MQTT setup.
- **config.yaml**: Keep localhost default for development; install script overrides for production.

## Files

| File | Change |
|------|--------|
| `commercial_detector/device_discovery.py` | New — V4L2 + ALSA enumeration functions |
| `commercial_detector/web/server.py` | Add `/api/devices` endpoint |
| `commercial_detector/web/templates/config.html` | Dropdowns + detect button for Capture section |
| `commercial_detector/web/static/app.js` | JS for detect API call and dropdown population |
| `install.sh` | Remove Mosquitto, add MQTT broker prompts |
| `README.md` | Update installation and MQTT sections |
| `tests/test_device_discovery.py` | Unit tests for discovery parsing |
