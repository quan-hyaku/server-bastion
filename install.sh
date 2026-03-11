#!/bin/bash
# bastion installer
# Installs Python toolchain (uv, pip) and bastion as a system-wide CLI.
# Does NOT install or configure server services (nginx, postgres, etc.).
#
# Usage:
#   sudo bash install.sh              # run from the project directory
#   sudo bash install.sh --repo URL   # clone from a git repo first

set -euo pipefail

INSTALL_DIR="/opt/bastion"
CONFIG_DIR="/etc/bastion"
REPO_URL=""

# ── Parse args ──────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            REPO_URL="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: sudo bash install.sh [--repo URL]"
            exit 1
            ;;
    esac
done

# ── Helpers ─────────────────────────────────────────────────────────

info()    { echo -e "\033[1;34m[INFO]\033[0m $*"; }
success() { echo -e "\033[1;32m[OK]\033[0m   $*"; }
warn()    { echo -e "\033[1;33m[WARN]\033[0m $*"; }
fail()    { echo -e "\033[1;31m[FAIL]\033[0m $*"; exit 1; }

check_root() {
    if [[ $EUID -ne 0 ]]; then
        fail "This script must be run as root (use: sudo bash install.sh)"
    fi
}

# ── Step 1: Minimal system deps (toolchain only) ───────────────────

install_prerequisites() {
    info "Installing prerequisites..."

    # Detect package manager
    if command -v apt-get &>/dev/null; then
        apt-get update -qq
        apt-get install -y -qq curl git python3 python3-venv
    elif command -v dnf &>/dev/null; then
        dnf install -y -q curl git python3
    elif command -v yum &>/dev/null; then
        yum install -y -q curl git python3
    else
        warn "Unknown package manager. Ensure curl, git, and python3 are installed."
    fi

    # Verify python3
    if ! command -v python3 &>/dev/null; then
        fail "python3 not found. Install Python 3.11+ and re-run."
    fi

    success "Prerequisites installed (curl, git, python3)"
}

# ── Step 2: Install uv ─────────────────────────────────────────────

install_uv() {
    if command -v uv &>/dev/null; then
        success "uv already installed: $(uv --version)"
        return
    fi

    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Make uv available in current session
    export PATH="$HOME/.local/bin:$PATH"

    if command -v uv &>/dev/null; then
        success "uv installed: $(uv --version)"
    else
        fail "uv installation failed. Install manually: https://docs.astral.sh/uv/getting-started/installation/"
    fi
}

# ── Step 3: Get bastion source ────────────────────────────────────

install_source() {
    if [[ -n "$REPO_URL" ]]; then
        info "Cloning from $REPO_URL..."
        if [[ -d "$INSTALL_DIR" ]]; then
            warn "$INSTALL_DIR exists, pulling latest..."
            cd "$INSTALL_DIR"
            git pull
        else
            git clone "$REPO_URL" "$INSTALL_DIR"
        fi
        success "Source cloned to $INSTALL_DIR"
    else
        local script_dir
        script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

        if [[ ! -f "$script_dir/pyproject.toml" ]]; then
            fail "No --repo provided and pyproject.toml not found in $script_dir. Run from the project directory or pass --repo URL."
        fi

        if [[ "$script_dir" != "$INSTALL_DIR" ]]; then
            info "Copying project to $INSTALL_DIR..."
            mkdir -p "$INSTALL_DIR"
            rsync -a --exclude '.venv' --exclude '__pycache__' --exclude '.git' \
                "$script_dir/" "$INSTALL_DIR/"
            success "Source copied to $INSTALL_DIR"
        else
            success "Already at $INSTALL_DIR"
        fi
    fi
}

# ── Step 4: Install bastion ───────────────────────────────────────

install_bastion() {
    info "Installing bastion..."
    cd "$INSTALL_DIR"

    export PATH="$HOME/.local/bin:$PATH"

    uv sync --no-dev
    uv tool install "$INSTALL_DIR" --force

    # Symlink to /usr/local/bin so all users can access it
    local uv_bin
    uv_bin="$(uv tool dir)/../bin"

    if [[ -f "$uv_bin/bastion" ]]; then
        ln -sf "$uv_bin/bastion" /usr/local/bin/bastion
        success "bastion linked to /usr/local/bin/bastion"
    elif command -v bastion &>/dev/null; then
        success "bastion is already on PATH"
    else
        warn "bastion installed but may not be on PATH. Add $uv_bin to your PATH."
    fi

    # Verify
    if command -v bastion &>/dev/null; then
        success "bastion $(bastion --version) installed"
    fi
}

# ── Step 5: Create default config ───────────────────────────────────

setup_config() {
    if [[ -f "$CONFIG_DIR/profile.yaml" ]]; then
        success "Config already exists at $CONFIG_DIR/profile.yaml"
        return
    fi

    info "Creating default config..."
    mkdir -p "$CONFIG_DIR"

    cat > "$CONFIG_DIR/profile.yaml" << 'EOF'
# bastion server profile
# Customize paths and settings for your server.
# Usage: bastion --profile /etc/bastion/profile.yaml <command>

name: "default"
description: "Default server profile"

nginx:
  sites_available: "/etc/nginx/sites-available"
  sites_enabled: "/etc/nginx/sites-enabled"
  snippets_dir: "/etc/nginx/snippets"
  config_test_cmd: "nginx -t"
  reload_cmd: "systemctl reload nginx"
  cloudflare_refresh_script: "/usr/local/bin/bastion-cloudflare-refresh"

postgres:
  host: "localhost"
  port: 5432
  user: "postgres"
  backup_dir: "/var/backups/postgresql"

firewall:
  backend: "ufw"

fail2ban:
  client_cmd: "fail2ban-client"
  jail_dir: "/etc/fail2ban/jail.d"
  filter_dir: "/etc/fail2ban/filter.d"

tune:
  sysctl_conf: "/etc/sysctl.d/99-bastion.conf"
  limits_conf: "/etc/security/limits.d/99-bastion.conf"
EOF

    success "Config created at $CONFIG_DIR/profile.yaml"
}

# ── Main ────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "=================================="
    echo "  bastion installer"
    echo "=================================="
    echo ""

    check_root
    install_prerequisites
    install_uv
    install_source
    install_bastion
    setup_config

    echo ""
    echo "=================================="
    echo "  Installation complete!"
    echo "=================================="
    echo ""
    echo "  Run:"
    echo "    bastion --help"
    echo ""
    echo "  Config: $CONFIG_DIR/profile.yaml"
    echo "  Source: $INSTALL_DIR"
    echo ""
}

main
