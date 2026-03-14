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

check_not_root() {
    if [[ $EUID -eq 0 ]]; then
        fail "Do not run this script as root or with sudo. Run as your normal user:\n       bash install.sh [--repo URL] [--sudoers]\n       The script will prompt for sudo only when needed."
    fi
}

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
  host: "localhost"          # use remote IP/hostname for remote access
  port: 5432
  user: "postgres"
  password: ""               # leave empty for local peer auth
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
# nginx
$current_user ALL=(ALL) NOPASSWD: /usr/sbin/nginx, /usr/bin/nginx
$current_user ALL=(ALL) NOPASSWD: /usr/bin/systemctl reload nginx, /usr/bin/systemctl restart nginx, /usr/bin/systemctl status nginx
# postgres (peer auth runs as postgres user)
$current_user ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart postgresql, /usr/bin/systemctl status postgresql
$current_user ALL=(postgres) NOPASSWD: /usr/bin/psql, /usr/bin/pg_dump, /usr/bin/createdb, /usr/bin/dropdb, /usr/bin/pg_isready
# firewall & fail2ban
$current_user ALL=(ALL) NOPASSWD: /usr/sbin/ufw
$current_user ALL=(ALL) NOPASSWD: /usr/bin/fail2ban-client
# nginx site management (symlinks in sites-enabled only)
$current_user ALL=(ALL) NOPASSWD: /usr/bin/ln -sf /etc/nginx/sites-available/* /etc/nginx/sites-enabled/*
$current_user ALL=(ALL) NOPASSWD: /usr/bin/rm /etc/nginx/sites-enabled/*
# file operations (restricted to config paths)
$current_user ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/nginx/*, /usr/bin/tee /etc/cron.d/bastion-*, /usr/bin/tee /etc/fail2ban/*, /usr/bin/tee /etc/sysctl.d/99-bastion.conf, /usr/bin/tee /etc/security/limits.d/99-bastion.conf, /usr/bin/tee /usr/local/maldetect/conf.maldet, /usr/bin/tee /etc/postgresql/*/*
$current_user ALL=(ALL) NOPASSWD: /usr/bin/cat /etc/nginx/*, /usr/bin/cat /etc/postgresql/*/*, /usr/bin/cat /etc/cron.d/bastion-*, /usr/bin/cat /usr/local/maldetect/conf.maldet
$current_user ALL=(ALL) NOPASSWD: /usr/bin/rm /etc/cron.d/bastion-*, /usr/bin/rm -f /etc/cron.d/bastion-*, /usr/bin/rm -f /etc/fail2ban/jail.d/*, /usr/bin/rm -f /etc/fail2ban/filter.d/*
$current_user ALL=(ALL) NOPASSWD: /usr/bin/mkdir -p /etc/nginx/snippets
$current_user ALL=(ALL) NOPASSWD: /usr/bin/mkdir -p /var/log/bastion
$current_user ALL=(ALL) NOPASSWD: /usr/bin/chown $current_user /var/log/bastion
$current_user ALL=(ALL) NOPASSWD: /usr/bin/chmod 0644 /etc/cron.d/bastion-*, /usr/bin/chmod +x /usr/local/bin/bastion-*
# sysctl
$current_user ALL=(ALL) NOPASSWD: /usr/sbin/sysctl
# crontab (for cloudflare refresh)
$current_user ALL=(ALL) NOPASSWD: /usr/bin/crontab
# package management (restricted to specific packages)
$current_user ALL=(ALL) NOPASSWD: /usr/bin/apt-get update -qq
$current_user ALL=(ALL) NOPASSWD: /usr/bin/apt-get install -y -qq clamav clamav-daemon
$current_user ALL=(ALL) NOPASSWD: /usr/bin/apt-get remove --purge -y -qq clamav clamav-daemon
$current_user ALL=(ALL) NOPASSWD: /usr/bin/apt-get autoremove -y -qq
$current_user ALL=(ALL) NOPASSWD: /usr/bin/dpkg -s clamav, /usr/bin/dpkg -s clamav-daemon
# audit & health checks
$current_user ALL=(ALL) NOPASSWD: /usr/bin/openssl x509 -enddate -noout -in *
$current_user ALL=(ALL) NOPASSWD: /usr/bin/openssl x509 -subject -noout -in *
$current_user ALL=(ALL) NOPASSWD: /usr/bin/openssl x509 -issuer -noout -in *
$current_user ALL=(ALL) NOPASSWD: /usr/bin/dpkg -l unattended-upgrades
# ssl certificate management
$current_user ALL=(ALL) NOPASSWD: /usr/bin/mkdir -p /etc/ssl/bastion/*
$current_user ALL=(ALL) NOPASSWD: /usr/bin/tee /etc/ssl/bastion/*
$current_user ALL=(ALL) NOPASSWD: /usr/bin/cat /etc/ssl/bastion/*
$current_user ALL=(ALL) NOPASSWD: /usr/bin/chmod 600 /etc/ssl/bastion/*, /usr/bin/chmod 644 /etc/ssl/bastion/*
$current_user ALL=(ALL) NOPASSWD: /usr/bin/rm -rf /etc/ssl/bastion/*
$current_user ALL=(ALL) NOPASSWD: /usr/bin/certbot
# clamav & malware detect
$current_user ALL=(ALL) NOPASSWD: /usr/bin/systemctl start clamav-daemon, /usr/bin/systemctl stop clamav-daemon, /usr/bin/systemctl enable clamav-daemon, /usr/bin/systemctl restart clamav-daemon, /usr/bin/systemctl is-active clamav-daemon
$current_user ALL=(ALL) NOPASSWD: /usr/bin/systemctl start clamav-freshclam, /usr/bin/systemctl stop clamav-freshclam, /usr/bin/systemctl enable clamav-freshclam, /usr/bin/systemctl is-active clamav-freshclam
$current_user ALL=(ALL) NOPASSWD: /usr/bin/freshclam, /usr/bin/clamscan
$current_user ALL=(ALL) NOPASSWD: /usr/local/sbin/maldet, /usr/local/sbin/lmd
# resource limiting for scans
$current_user ALL=(ALL) NOPASSWD: /usr/bin/systemd-run --scope --quiet --property=CPUQuota=*% nice -n 19 ionice -c3 /usr/local/sbin/maldet *
$current_user ALL=(ALL) NOPASSWD: /usr/bin/systemd-run --scope --quiet --property=CPUQuota=*% nice -n 19 ionice -c3 /usr/bin/clamscan *
$current_user ALL=(ALL) NOPASSWD: /usr/bin/nice, /usr/bin/ionice
# LMD installation (one-time setup)
$current_user ALL=(ALL) NOPASSWD: /usr/bin/rm -rf /tmp/maldetect-install
$current_user ALL=(ALL) NOPASSWD: /usr/bin/mkdir -p /tmp/maldetect-install
$current_user ALL=(ALL) NOPASSWD: /usr/bin/curl -sSL -o /tmp/maldetect-install/maldetect.tar.gz *
$current_user ALL=(ALL) NOPASSWD: /usr/bin/tar xzf /tmp/maldetect-install/maldetect.tar.gz -C /tmp/maldetect-install --strip-components=1
$current_user ALL=(ALL) NOPASSWD: /usr/bin/bash /tmp/maldetect-install/install.sh
# LMD uninstall paths
$current_user ALL=(ALL) NOPASSWD: /usr/bin/rm -rf /usr/local/maldetect
$current_user ALL=(ALL) NOPASSWD: /usr/bin/rm -rf /usr/local/sbin/maldet
$current_user ALL=(ALL) NOPASSWD: /usr/bin/rm -rf /usr/local/sbin/lmd
$current_user ALL=(ALL) NOPASSWD: /usr/bin/rm -rf /etc/cron.daily/maldet
$current_user ALL=(ALL) NOPASSWD: /usr/bin/rm -rf /etc/cron.d/maldet"

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

    # Add to shell profile so future shells find bastion
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

    check_not_root
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
