#!/usr/bin/env bash
#
# CommercialDetector Installer
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/intrepidsilence/CommercialDetector/main/install.sh | bash
#
# Options (via environment variables):
#   INSTALL_DIR=/opt/commercial-detector   # Installation directory
#   ENABLE_WHISPER=1                       # Install faster-whisper for transcript analysis
#   SETUP_SERVICE=1                        # Create and enable systemd service
#   SERVICE_USER=pi                        # User to run the service as
#
set -euo pipefail

# --- Configuration -----------------------------------------------------------

INSTALL_DIR="${INSTALL_DIR:-/opt/commercial-detector}"
ENABLE_WHISPER="${ENABLE_WHISPER:-0}"
SETUP_SERVICE="${SETUP_SERVICE:-0}"
SERVICE_USER="${SERVICE_USER:-$(whoami)}"
REPO_URL="https://github.com/intrepidsilence/CommercialDetector.git"

# --- Helpers -----------------------------------------------------------------

info()  { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
ok()    { printf "\033[1;32m  ✓\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m  !\033[0m %s\n" "$*"; }
err()   { printf "\033[1;31m  ✗\033[0m %s\n" "$*" >&2; }

check_cmd() {
    command -v "$1" &>/dev/null
}

# --- Pre-flight checks -------------------------------------------------------

info "CommercialDetector Installer"
echo ""

# Detect OS
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    OS_ID="${ID:-unknown}"
    OS_NAME="${PRETTY_NAME:-$OS_ID}"
elif [[ "$(uname)" == "Darwin" ]]; then
    OS_ID="macos"
    OS_NAME="macOS $(sw_vers -productVersion)"
else
    OS_ID="unknown"
    OS_NAME="Unknown"
fi

info "Detected OS: $OS_NAME"

# Check for root/sudo when installing to system paths
SUDO=""
if [[ "$INSTALL_DIR" == /opt/* || "$INSTALL_DIR" == /usr/* ]]; then
    if [[ $EUID -ne 0 ]]; then
        if check_cmd sudo; then
            SUDO="sudo"
            info "Will use sudo for system-level operations"
        else
            err "Installation to $INSTALL_DIR requires root. Run with sudo or set INSTALL_DIR."
            exit 1
        fi
    fi
fi

# --- Install system dependencies ---------------------------------------------

info "Installing system dependencies..."

case "$OS_ID" in
    raspbian|debian|ubuntu|linuxmint|pop)
        $SUDO apt-get update -qq
        $SUDO apt-get install -y -qq ffmpeg mosquitto mosquitto-clients \
            python3 python3-pip python3-venv git >/dev/null 2>&1
        ok "Installed ffmpeg, mosquitto, python3, git"
        ;;
    fedora|rhel|centos|rocky|alma)
        $SUDO dnf install -y -q ffmpeg mosquitto python3 python3-pip git >/dev/null 2>&1
        ok "Installed ffmpeg, mosquitto, python3, git"
        ;;
    arch|manjaro)
        $SUDO pacman -Sy --noconfirm --quiet ffmpeg mosquitto python python-pip git >/dev/null 2>&1
        ok "Installed ffmpeg, mosquitto, python3, git"
        ;;
    macos)
        if ! check_cmd brew; then
            err "Homebrew required on macOS. Install from https://brew.sh"
            exit 1
        fi
        brew install ffmpeg mosquitto python@3.11 git 2>/dev/null || true
        ok "Installed ffmpeg, mosquitto, python3, git"
        ;;
    *)
        warn "Unknown OS ($OS_ID). Please install manually: ffmpeg, mosquitto, python3, git"
        ;;
esac

# Verify critical dependencies
for cmd in ffmpeg python3 git; do
    if ! check_cmd "$cmd"; then
        err "$cmd not found after installation. Please install it manually."
        exit 1
    fi
done
ok "All required commands available"

# --- Clone repository ---------------------------------------------------------

info "Installing to $INSTALL_DIR..."

if [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Existing installation found, pulling latest..."
    $SUDO git -C "$INSTALL_DIR" pull --ff-only
    ok "Updated to latest"
else
    if [[ -d "$INSTALL_DIR" ]]; then
        warn "$INSTALL_DIR exists but is not a git repo. Backing up..."
        $SUDO mv "$INSTALL_DIR" "${INSTALL_DIR}.bak.$(date +%s)"
    fi
    $SUDO git clone "$REPO_URL" "$INSTALL_DIR"
    ok "Cloned repository"
fi

# Fix ownership if we used sudo to clone
if [[ -n "$SUDO" && "$SERVICE_USER" != "root" ]]; then
    $SUDO chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR" 2>/dev/null || true
fi

# --- Python virtual environment -----------------------------------------------

info "Setting up Python virtual environment..."

cd "$INSTALL_DIR"

if [[ ! -d .venv ]]; then
    python3 -m venv .venv
    ok "Created virtual environment"
else
    ok "Virtual environment already exists"
fi

source .venv/bin/activate

pip install --upgrade pip -q
pip install -r requirements.txt -q
ok "Installed Python dependencies (paho-mqtt, pyyaml)"

# --- Optional: Whisper --------------------------------------------------------

if [[ "$ENABLE_WHISPER" == "1" ]]; then
    info "Installing faster-whisper for transcript analysis..."
    pip install faster-whisper numpy -q
    ok "Installed faster-whisper"

    # Enable transcript in config if not already
    if grep -q "enabled: false" config.yaml 2>/dev/null; then
        sed -i.bak 's/enabled: false/enabled: true/' config.yaml
        rm -f config.yaml.bak
        ok "Enabled transcript analysis in config.yaml"
    fi
else
    info "Skipping Whisper (set ENABLE_WHISPER=1 to include)"
fi

# --- Verify installation ------------------------------------------------------

info "Verifying installation..."

.venv/bin/python -c "from commercial_detector import __version__; print(f'CommercialDetector v{__version__}')"
ok "Python package loads successfully"

.venv/bin/python -m pytest tests/ -q --tb=no 2>&1 | tail -1
ok "Tests passed"

# --- Optional: systemd service ------------------------------------------------

if [[ "$SETUP_SERVICE" == "1" ]]; then
    if ! check_cmd systemctl; then
        warn "systemctl not found. Skipping service setup."
    else
        info "Setting up systemd service..."

        # Create log directory
        LOG_DIR="/var/log/commercial-detector"
        $SUDO mkdir -p "$LOG_DIR"
        $SUDO chown "$SERVICE_USER":"$SERVICE_USER" "$LOG_DIR"
        ok "Created log directory: $LOG_DIR"

        # Set up logrotate
        $SUDO tee /etc/logrotate.d/commercial-detector > /dev/null << ROTATEEOF
$LOG_DIR/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
ROTATEEOF
        ok "Configured logrotate (daily, 7 days retained)"

        # Create systemd service
        $SUDO tee /etc/systemd/system/commercial-detector.service > /dev/null << SERVICEEOF
[Unit]
Description=CommercialDetector - TV commercial break detection
After=network.target mosquitto.service
Wants=mosquitto.service

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python -m commercial_detector
Restart=on-failure
RestartSec=10
StandardOutput=append:$LOG_DIR/output.log
StandardError=append:$LOG_DIR/error.log

[Install]
WantedBy=multi-user.target
SERVICEEOF

        $SUDO systemctl daemon-reload
        $SUDO systemctl enable commercial-detector
        ok "Service installed and enabled"
        echo ""
        info "Start with:   sudo systemctl start commercial-detector"
        info "Tail logs:    tail -f $LOG_DIR/output.log"
        info "Error logs:   tail -f $LOG_DIR/error.log"
        info "Status:       sudo systemctl status commercial-detector"
    fi
else
    info "Skipping service setup (set SETUP_SERVICE=1 to include)"
fi

# --- Done ---------------------------------------------------------------------

echo ""
info "Installation complete!"
echo ""
echo "  Install dir:  $INSTALL_DIR"
echo "  Python venv:  $INSTALL_DIR/.venv"
echo "  Config file:  $INSTALL_DIR/config.yaml"
echo ""
echo "  Quick start:"
echo "    cd $INSTALL_DIR"
echo "    source .venv/bin/activate"
echo ""
echo "    # Test with a video file"
echo "    python -m commercial_detector --input video.mp4 --dry-run"
echo ""
echo "    # Run with live capture"
echo "    python -m commercial_detector"
echo ""
echo "    # Monitor MQTT output"
echo "    mosquitto_sub -t 'commercial-detector/#' -v"
echo ""
echo "  Full install (with Whisper + systemd service):"
echo "    curl -sSL https://raw.githubusercontent.com/intrepidsilence/CommercialDetector/main/install.sh | ENABLE_WHISPER=1 SETUP_SERVICE=1 bash"
echo ""
