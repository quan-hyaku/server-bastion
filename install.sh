#!/bin/bash
# bastion installer
# Installs bastion as a user-local CLI tool (~/.local/bin).
# Uses sudo only for system prerequisites and optional sudoers setup.
#
# Usage:
#   bash install.sh                    # run from the project directory
#   bash install.sh --repo URL         # clone from a git repo first
#   bash install.sh --sudoers          # also set up passwordless sudo for bastion commands

set -euo pipefail

INSTALL_DIR="$HOME/.local/share/bastion"
CONFIG_DIR="$HOME/.config/bastion"
REPO_URL=""
SETUP_SUDOERS=false

# ── Parse args ──────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            REPO_URL="$2"
            shift 2
            ;;
        --sudoers)
            SETUP_SUDOERS=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: bash install.sh [--repo URL] [--sudoers]"
            exit 1
            ;;
    esac
done

# ── Helpers ─────────────────────────────────────────────────────────

info()    { echo -e "\033[1;34m[INFO]\033[0m $*"; }
success() { echo -e "\033[1;32m[OK]\033[0m   $*"; }
warn()    { echo -e "\033[1;33m[WARN]\033[0m $*"; }
fail()    { echo -e "\033[1;31m[FAIL]\033[0m $*"; exit 1; }

# ── Step 1: System prerequisites (needs sudo) ───────────────────────

install_prerequisites() {
    info "Installing prerequisites (may prompt for sudo)..."

    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq curl git python3 python3-venv
    elif command -v dnf &>/dev/null; then
        sudo dnf install -y -q curl git python3
    elif command -v yum &>/dev/null; then
        sudo yum install -y -q curl git python3
    else
        warn "Unknown package manager. Ensure curl, git, and python3 are installed."
    fi

    if ! command -v python3 &>/dev/null; then
        fail "python3 not found. Install Python 3.11+ and re-run."
    fi

    success "Prerequisites installed (curl, git, python3)"
}

# ── Step 2: Install uv (user-local) ─────────────────────────────────

install_uv() {
    if command -v uv &>/dev/null; then
        success "uv already installed: $(uv --version)"
        return
    fi

    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Make uv available in current session
    export PATH="$HOME/.local/bin:$PATH"
    if [[ -f "$HOME/.local/bin/env" ]]; then
        . "$HOME/.local/bin/env"
    fi

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
            mkdir -p "$(dirname "$INSTALL_DIR")"
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

# ── Step 4: Install bastion (user-local) ─────────────────────────

install_bastion() {
    info "Installing bastion..."
    cd "$INSTALL_DIR"

    export PATH="$HOME/.local/bin:$PATH"

    uv sync --no-dev
    uv tool install "$INSTALL_DIR" --force

    # Verify bastion is on PATH
    local tool_bin
    tool_bin="$(uv tool bin-dir 2>/dev/null || echo "$HOME/.local/bin")"
    export PATH="$tool_bin:$PATH"

    if command -v bastion &>/dev/null; then
        success "bastion installed: $(bastion --version)"
    else
        warn "bastion installed but not on PATH. Add this to your shell profile:"
        warn "  export PATH=\"$tool_bin:\$PATH\""
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
# Usage: bastion --profile ~/.config/bastion/profile.yaml <command>

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

# ── Step 6 (optional): Sudoers for bastion commands ──────────────────

setup_sudoers() {
    if [[ "$SETUP_SUDOERS" != true ]]; then
        return
    fi

    local current_user
    current_user="$(whoami)"

    info "Setting up passwordless sudo for bastion commands (user: $current_user)..."

    local sudoers_file="/etc/sudoers.d/bastion"
    local sudoers_content
    sudoers_content="# bastion — allow $current_user to run server admin commands without password
$current_user ALL=(ALL) NOPASSWD: /usr/sbin/nginx, /usr/bin/nginx
$current_user ALL=(ALL) NOPASSWD: /usr/bin/systemctl reload nginx, /usr/bin/systemctl restart nginx, /usr/bin/systemctl status nginx
$current_user ALL=(ALL) NOPASSWD: /usr/sbin/ufw
$current_user ALL=(ALL) NOPASSWD: /usr/bin/fail2ban-client
$current_user ALL=(ALL) NOPASSWD: /usr/bin/ln, /usr/bin/rm
$current_user ALL=(ALL) NOPASSWD: /usr/bin/tee
$current_user ALL=(ALL) NOPASSWD: /usr/bin/mkdir
$current_user ALL=(ALL) NOPASSWD: /usr/bin/chmod
$current_user ALL=(ALL) NOPASSWD: /usr/sbin/sysctl
$current_user ALL=(ALL) NOPASSWD: /usr/bin/crontab
$current_user ALL=(ALL) NOPASSWD: /usr/bin/pg_dump, /usr/bin/psql, /usr/bin/createdb, /usr/bin/dropdb
$current_user ALL=(ALL) NOPASSWD: /usr/bin/bash"

    echo "$sudoers_content" | sudo tee "$sudoers_file" > /dev/null
    sudo chmod 0440 "$sudoers_file"

    # Validate sudoers syntax
    if sudo visudo -cf "$sudoers_file" &>/dev/null; then
        success "Sudoers file created at $sudoers_file"
    else
        sudo rm -f "$sudoers_file"
        fail "Sudoers syntax error — file removed. Please check and retry."
    fi
}

# ── Step 7: Ensure PATH is persistent ─────────────────────────────

setup_path() {
    local tool_bin
    tool_bin="$(uv tool bin-dir 2>/dev/null || echo "$HOME/.local/bin")"

    # Check if bastion is already findable
    if command -v bastion &>/dev/null; then
        return
    fi

    # Add to shell profile
    local shell_rc=""
    if [[ -f "$HOME/.bashrc" ]]; then
        shell_rc="$HOME/.bashrc"
    elif [[ -f "$HOME/.zshrc" ]]; then
        shell_rc="$HOME/.zshrc"
    elif [[ -f "$HOME/.profile" ]]; then
        shell_rc="$HOME/.profile"
    fi

    if [[ -n "$shell_rc" ]]; then
        if ! grep -q 'uv tool bin-dir\|\.local/bin' "$shell_rc" 2>/dev/null; then
            echo "" >> "$shell_rc"
            echo "# bastion — added by installer" >> "$shell_rc"
            echo "export PATH=\"$tool_bin:\$PATH\"" >> "$shell_rc"
            success "Added $tool_bin to PATH in $shell_rc"
            warn "Run 'source $shell_rc' or open a new terminal to use bastion"
        fi
    else
        warn "Could not find shell profile. Add this to your shell config:"
        warn "  export PATH=\"$tool_bin:\$PATH\""
    fi
}

# ── Main ────────────────────────────────────────────────────────────

main() {
    echo ""
    echo "=================================="
    echo "  bastion installer"
    echo "=================================="
    echo ""

    install_prerequisites
    install_uv
    install_source
    install_bastion
    setup_config
    setup_sudoers
    setup_path

    echo ""
    echo "=================================="
    echo "  Installation complete!"
    echo "=================================="
    echo ""
    echo "  Run:"
    echo "    bastion --help"
    echo ""
    echo "  Config:  $CONFIG_DIR/profile.yaml"
    echo "  Source:  $INSTALL_DIR"
    if [[ "$SETUP_SUDOERS" == true ]]; then
        echo "  Sudoers: /etc/sudoers.d/bastion"
    fi
    echo ""
}

main
