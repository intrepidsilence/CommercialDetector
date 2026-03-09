#!/usr/bin/env bash
#
# CommercialDetector Installer
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/intrepidsilence/CommercialDetector/main/install.sh | bash
#
# For non-interactive installs, set environment variables to skip prompts:
#   INSTALL_DIR=/opt/commercial-detector
#   ENABLE_WHISPER=1
#   SETUP_SERVICE=1
#   SERVICE_USER=pi
#   MQTT_HOST=broker.example.com
#   MQTT_PORT=1883
#   MQTT_USERNAME=user
#   MQTT_PASSWORD=pass
#
set -euo pipefail

REPO_URL="https://github.com/intrepidsilence/CommercialDetector.git"

# --- Helpers -----------------------------------------------------------------

info()  { printf "\033[1;34m==>\033[0m %s\n" "$*"; }
ok()    { printf "\033[1;32m  ✓\033[0m %s\n" "$*"; }
warn()  { printf "\033[1;33m  !\033[0m %s\n" "$*"; }
err()   { printf "\033[1;31m  ✗\033[0m %s\n" "$*" >&2; }

check_cmd() {
    command -v "$1" &>/dev/null
}

ask() {
    # ask VAR_NAME "prompt" "default"
    # Skips prompt if env var is already set
    local var_name="$1" prompt="$2" default="${3:-}"
    local current="${!var_name:-}"

    if [[ -n "$current" ]]; then
        return
    fi

    if [[ -n "$default" ]]; then
        read -rp "  $prompt [$default]: " input
        eval "$var_name=\"\${input:-$default}\""
    else
        read -rp "  $prompt: " input
        eval "$var_name=\"\$input\""
    fi
}

ask_password() {
    local var_name="$1" prompt="$2"
    local current="${!var_name:-}"

    if [[ -n "$current" ]]; then
        return
    fi

    read -rsp "  $prompt: " input
    echo ""
    eval "$var_name=\"\$input\""
}

ask_yesno() {
    # ask_yesno VAR_NAME "prompt" "default_yn"
    # Sets VAR_NAME to "1" or "0"
    local var_name="$1" prompt="$2" default_yn="${3:-n}"
    local current="${!var_name:-}"

    if [[ -n "$current" ]]; then
        return
    fi

    local hint
    if [[ "$default_yn" == "y" ]]; then
        hint="Y/n"
    else
        hint="y/N"
    fi

    read -rp "  $prompt ($hint): " input
    input="${input:-$default_yn}"

    if [[ "$input" =~ ^[Yy] ]]; then
        eval "$var_name=1"
    else
        eval "$var_name=0"
    fi
}

# --- Banner ------------------------------------------------------------------

echo ""
printf "\033[1;36m"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║        CommercialDetector Installer          ║"
echo "  ║   Real-time TV commercial break detection    ║"
echo "  ╚══════════════════════════════════════════════╝"
printf "\033[0m"
echo ""

# --- Detect OS ---------------------------------------------------------------

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
echo ""

# --- Interactive configuration -----------------------------------------------

info "Configuration"
echo ""
echo "  Press Enter to accept defaults shown in [brackets]."
echo ""

# Installation directory
INSTALL_DIR="${INSTALL_DIR:-}"
ask INSTALL_DIR "Installation directory" "/opt/commercial-detector"

# MQTT broker
echo ""
info "MQTT Broker"
echo "  CommercialDetector publishes state changes to a remote MQTT broker."
echo ""

MQTT_HOST="${MQTT_HOST:-}"
ask MQTT_HOST "MQTT broker hostname or IP" ""

if [[ -z "$MQTT_HOST" ]]; then
    warn "No MQTT host provided. You can configure this later in config.yaml."
    MQTT_HOST="localhost"
fi

MQTT_PORT="${MQTT_PORT:-}"
ask MQTT_PORT "MQTT broker port" "1883"

MQTT_USERNAME="${MQTT_USERNAME:-}"
ask MQTT_USERNAME "MQTT username (blank for none)" ""

MQTT_PASSWORD="${MQTT_PASSWORD:-}"
if [[ -n "$MQTT_USERNAME" ]]; then
    ask_password MQTT_PASSWORD "MQTT password"
fi

# Whisper
echo ""
info "Optional Features"
echo ""

ENABLE_WHISPER="${ENABLE_WHISPER:-}"
ask_yesno ENABLE_WHISPER "Install faster-whisper for transcript analysis?" "n"

# systemd service
SETUP_SERVICE="${SETUP_SERVICE:-}"
if check_cmd systemctl; then
    ask_yesno SETUP_SERVICE "Create systemd service (auto-start on boot)?" "y"
else
    SETUP_SERVICE="0"
fi

SERVICE_USER="${SERVICE_USER:-}"
if [[ "$SETUP_SERVICE" == "1" ]]; then
    ask SERVICE_USER "User to run the service as" "$(whoami)"
else
    SERVICE_USER="${SERVICE_USER:-$(whoami)}"
fi

# --- Confirm -----------------------------------------------------------------

echo ""
info "Configuration Summary"
echo ""
echo "  Install directory:  $INSTALL_DIR"
echo "  MQTT broker:        $MQTT_HOST:$MQTT_PORT"
if [[ -n "$MQTT_USERNAME" ]]; then
echo "  MQTT auth:          $MQTT_USERNAME / ********"
else
echo "  MQTT auth:          (none)"
fi
echo "  Whisper:            $([ "$ENABLE_WHISPER" == "1" ] && echo "yes" || echo "no")"
echo "  Systemd service:    $([ "$SETUP_SERVICE" == "1" ] && echo "yes (user: $SERVICE_USER)" || echo "no")"
echo ""

read -rp "  Proceed with installation? (Y/n): " confirm
confirm="${confirm:-y}"
if [[ ! "$confirm" =~ ^[Yy] ]]; then
    echo "  Aborted."
    exit 0
fi

echo ""

# --- Check for root/sudo when installing to system paths --------------------

SUDO=""
if [[ "$INSTALL_DIR" == /opt/* || "$INSTALL_DIR" == /usr/* ]]; then
    if [[ $EUID -ne 0 ]]; then
        if check_cmd sudo; then
            SUDO="sudo"
            info "Will use sudo for system-level operations"
        else
            err "Installation to $INSTALL_DIR requires root. Run with sudo or choose a different directory."
            exit 1
        fi
    fi
fi

# --- Install system dependencies ---------------------------------------------

info "Installing system dependencies..."

case "$OS_ID" in
    raspbian|debian|ubuntu|linuxmint|pop)
        $SUDO apt-get update -qq
        $SUDO apt-get install -y -qq ffmpeg \
            python3 python3-pip python3-venv git >/dev/null 2>&1
        ok "Installed ffmpeg, python3, git"
        ;;
    fedora|rhel|centos|rocky|alma)
        $SUDO dnf install -y -q ffmpeg python3 python3-pip git >/dev/null 2>&1
        ok "Installed ffmpeg, python3, git"
        ;;
    arch|manjaro)
        $SUDO pacman -Sy --noconfirm --quiet ffmpeg python python-pip git >/dev/null 2>&1
        ok "Installed ffmpeg, python3, git"
        ;;
    macos)
        if ! check_cmd brew; then
            err "Homebrew required on macOS. Install from https://brew.sh"
            exit 1
        fi
        brew install ffmpeg python@3.11 git 2>/dev/null || true
        ok "Installed ffmpeg, python3, git"
        ;;
    *)
        warn "Unknown OS ($OS_ID). Please install manually: ffmpeg, python3, git"
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
ok "Installed Python dependencies"

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
fi

# --- MQTT broker configuration -----------------------------------------------

info "Writing MQTT configuration to config.yaml..."

.venv/bin/python - "$INSTALL_DIR/config.yaml" "$MQTT_HOST" "$MQTT_PORT" "$MQTT_USERNAME" "$MQTT_PASSWORD" << 'PYEOF'
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
After=network.target

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
    fi
fi

# --- Done ---------------------------------------------------------------------

echo ""
printf "\033[1;32m"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║        Installation complete!                ║"
echo "  ╚══════════════════════════════════════════════╝"
printf "\033[0m"
echo ""
echo "  Install dir:  $INSTALL_DIR"
echo "  Config file:  $INSTALL_DIR/config.yaml"
echo "  MQTT broker:  $MQTT_HOST:$MQTT_PORT"
echo ""

echo "  Web dashboard:"
echo "    http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost"):8080"
echo ""

if [[ "$SETUP_SERVICE" == "1" ]]; then
    echo "  Service commands:"
    echo "    sudo systemctl start commercial-detector"
    echo "    sudo systemctl status commercial-detector"
    echo "    sudo journalctl -u commercial-detector -f"
    echo ""
    echo "  Logs:"
    echo "    tail -f /var/log/commercial-detector/output.log"
    echo "    tail -f /var/log/commercial-detector/error.log"
else
    echo "  Quick start:"
    echo "    cd $INSTALL_DIR"
    echo "    source .venv/bin/activate"
    echo ""
    echo "    # Test with a video file"
    echo "    python -m commercial_detector --input video.mp4 --dry-run"
    echo ""
    echo "    # Run with live capture"
    echo "    python -m commercial_detector"
fi

echo ""
echo "  Monitor MQTT output (from any machine with mosquitto-clients):"
echo "    mosquitto_sub -h $MQTT_HOST -t 'commercial-detector/#' -v"
echo ""
