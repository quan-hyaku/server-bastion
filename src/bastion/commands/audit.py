"""Security audit — scan for common misconfigurations and hardening gaps."""

from __future__ import annotations

import re
from pathlib import Path

import click

from bastion.output import console, print_error, print_success, print_table, print_warning
from bastion.runner import run


@click.group("audit")
def audit() -> None:
    """Security audit and hardening checks."""


# ── Check Infrastructure ────────────────────────────────────────────


class Check:
    """A single audit check with result."""

    def __init__(self, category: str, name: str, description: str) -> None:
        self.category = category
        self.name = name
        self.description = description
        self.passed: bool | None = None  # None = skipped
        self.detail = ""
        self.fix_hint = ""

    def pass_(self, detail: str = "") -> None:
        self.passed = True
        self.detail = detail

    def fail(self, detail: str, fix: str = "") -> None:
        self.passed = False
        self.detail = detail
        self.fix_hint = fix

    def skip(self, reason: str = "") -> None:
        self.passed = None
        self.detail = reason


# ── SSH Checks ──────────────────────────────────────────────────────


def _check_ssh_root_login(checks: list[Check]) -> None:
    c = Check("SSH", "Root login disabled", "PermitRootLogin should be 'no'")
    result = run(
        ["bash", "-c",
         "grep -i '^PermitRootLogin' /etc/ssh/sshd_config 2>/dev/null | tail -1"],
        check=False, timeout=10,
    )
    value = result.stdout.strip().split()[-1].lower() if result.stdout.strip() else ""
    if value == "no":
        c.pass_("PermitRootLogin no")
    elif value in ("yes", ""):
        setting = value if value else "not set (defaults to yes)"
        c.fail(
            f"PermitRootLogin is {setting}",
            "Add 'PermitRootLogin no' to /etc/ssh/sshd_config and restart sshd",
        )
    elif value in ("prohibit-password", "without-password"):
        c.pass_(f"PermitRootLogin {value} (key-only, acceptable)")
    else:
        c.fail(f"Unexpected value: {value}")
    checks.append(c)


def _check_ssh_password_auth(checks: list[Check]) -> None:
    c = Check("SSH", "Password auth disabled", "PasswordAuthentication should be 'no'")
    result = run(
        ["bash", "-c",
         "grep -i '^PasswordAuthentication' /etc/ssh/sshd_config 2>/dev/null | tail -1"],
        check=False, timeout=10,
    )
    value = result.stdout.strip().split()[-1].lower() if result.stdout.strip() else ""
    if value == "no":
        c.pass_("PasswordAuthentication no")
    elif value in ("yes", ""):
        setting = value if value else "not set (defaults to yes)"
        c.fail(
            f"PasswordAuthentication is {setting}",
            "Add 'PasswordAuthentication no' to /etc/ssh/sshd_config and restart sshd",
        )
    else:
        c.fail(f"Unexpected value: {value}")
    checks.append(c)


def _check_ssh_port(checks: list[Check]) -> None:
    c = Check("SSH", "Non-default SSH port", "Using a non-standard port reduces noise")
    result = run(
        ["bash", "-c",
         "grep -i '^Port ' /etc/ssh/sshd_config 2>/dev/null | tail -1"],
        check=False, timeout=10,
    )
    value = result.stdout.strip().split()[-1] if result.stdout.strip() else "22"
    if value == "22":
        c.fail(
            "SSH on default port 22",
            "Consider changing to a non-standard port in /etc/ssh/sshd_config",
        )
    else:
        c.pass_(f"SSH on port {value}")
    checks.append(c)


# ── Firewall Checks ────────────────────────────────────────────────


def _check_firewall_active(checks: list[Check]) -> None:
    c = Check("Firewall", "UFW enabled", "Firewall should be active")
    result = run(["ufw", "status"], use_sudo=True, check=False, timeout=10)
    if result.ok and "Status: active" in result.stdout:
        c.pass_("UFW is active")
    elif result.ok and "Status: inactive" in result.stdout:
        c.fail("UFW is inactive", "Run: sudo ufw enable")
    else:
        c.fail("Could not check UFW status")
    checks.append(c)


def _check_firewall_default_deny(checks: list[Check]) -> None:
    c = Check("Firewall", "Default incoming deny", "Default policy should deny incoming")
    result = run(["ufw", "status", "verbose"], use_sudo=True, check=False, timeout=10)
    if not result.ok:
        c.skip("UFW not available")
        checks.append(c)
        return

    if "Default: deny (incoming)" in result.stdout:
        c.pass_("Default deny incoming")
    elif "Status: inactive" in result.stdout:
        c.skip("UFW is inactive")
    else:
        c.fail(
            "Default incoming is not 'deny'",
            "Run: sudo ufw default deny incoming",
        )
    checks.append(c)


# ── Fail2ban Checks ─────────────────────────────────────────────────


def _check_fail2ban_running(checks: list[Check]) -> None:
    c = Check("Fail2ban", "Fail2ban active", "Fail2ban should be running to block brute-force")
    result = run(["systemctl", "is-active", "fail2ban"], check=False, timeout=5)
    if result.stdout.strip() == "active":
        c.pass_("fail2ban is active")
    elif result.stdout.strip() == "inactive":
        c.fail("fail2ban is inactive", "Run: sudo systemctl enable --now fail2ban")
    else:
        c.fail("fail2ban not installed", "Run: sudo apt-get install fail2ban")
    checks.append(c)


def _check_fail2ban_ssh_jail(checks: list[Check]) -> None:
    c = Check("Fail2ban", "SSH jail enabled", "SSH jail should be active in fail2ban")
    result = run(["fail2ban-client", "status", "sshd"], use_sudo=True, check=False, timeout=10)
    if result.ok and "Currently banned" in result.stdout:
        c.pass_("sshd jail is active")
    else:
        # Try alternative name
        result2 = run(["fail2ban-client", "status", "ssh"], use_sudo=True,
                       check=False, timeout=10)
        if result2.ok and "Currently banned" in result2.stdout:
            c.pass_("ssh jail is active")
        else:
            c.fail(
                "No SSH jail found",
                "Enable sshd jail in /etc/fail2ban/jail.local",
            )
    checks.append(c)


# ── Unattended Upgrades ────────────────────────────────────────────


def _check_unattended_upgrades(checks: list[Check]) -> None:
    c = Check("Updates", "Unattended upgrades enabled",
              "Automatic security updates should be enabled")
    result = run(
        ["dpkg", "-l", "unattended-upgrades"], check=False, timeout=10,
    )
    if result.ok and "ii" in result.stdout:
        # Check if it's actually enabled
        config = Path("/etc/apt/apt.conf.d/20auto-upgrades")
        result2 = run(["cat", str(config)], check=False, timeout=5)
        if result2.ok and 'Unattended-Upgrade "1"' in result2.stdout:
            c.pass_("Enabled and configured")
        elif result2.ok:
            c.fail(
                "Package installed but not enabled",
                "Run: sudo dpkg-reconfigure -plow unattended-upgrades",
            )
        else:
            c.fail(
                "Config file missing",
                "Run: sudo dpkg-reconfigure -plow unattended-upgrades",
            )
    else:
        c.fail(
            "unattended-upgrades not installed",
            "Run: sudo apt-get install unattended-upgrades && "
            "sudo dpkg-reconfigure -plow unattended-upgrades",
        )
    checks.append(c)


# ── Open Ports ──────────────────────────────────────────────────────


def _check_open_ports(checks: list[Check]) -> None:
    c = Check("Network", "No unexpected open ports", "Only expected services should listen")
    result = run(["ss", "-tlnp"], check=False, timeout=10)
    if not result.ok:
        c.skip("Could not check open ports")
        checks.append(c)
        return

    # Known safe ports
    known_ports = {"22", "80", "443", "5432", "25", "587", "993", "143"}
    unexpected: list[str] = []

    for line in result.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 4:
            continue
        listen_addr = parts[3]
        # Extract port from address like *:80 or 0.0.0.0:80 or [::]:80
        port_match = re.search(r':(\d+)$', listen_addr)
        if not port_match:
            continue
        port = port_match.group(1)
        if port not in known_ports and int(port) < 32768:
            # Get process name if available
            proc = parts[-1] if len(parts) > 5 else ""
            unexpected.append(f"{port} ({proc})" if proc else port)

    if unexpected:
        c.fail(
            f"Unexpected ports open: {', '.join(unexpected[:5])}",
            "Review with: sudo ss -tlnp",
        )
    else:
        c.pass_("Only expected ports are open")
    checks.append(c)


# ── Malware / Antivirus ────────────────────────────────────────────


def _check_malware_scanner(checks: list[Check]) -> None:
    c = Check("Malware", "Malware scanner installed", "ClamAV or LMD should be installed")
    has_clam = run(["which", "clamscan"], check=False, timeout=5).ok
    has_lmd = Path("/usr/local/sbin/maldet").exists()

    if has_clam and has_lmd:
        c.pass_("ClamAV + LMD installed")
    elif has_clam:
        c.pass_("ClamAV installed")
    elif has_lmd:
        c.pass_("LMD installed")
    else:
        c.fail(
            "No malware scanner installed",
            "Run: bastion malware install",
        )
    checks.append(c)


def _check_malware_schedule(checks: list[Check]) -> None:
    c = Check("Malware", "Scheduled scans configured", "Regular malware scans should be scheduled")
    cron_file = Path("/etc/cron.d/bastion-malware")
    result = run(["sudo", "cat", str(cron_file)], use_sudo=False, check=False, timeout=5)
    if result.ok and "maldet" in result.stdout:
        c.pass_("Bastion malware schedule active")
    elif result.ok and result.stdout:
        c.pass_("Cron file exists")
    else:
        # Check LMD's built-in cron
        lmd_cron = Path("/etc/cron.daily/maldet")
        if lmd_cron.exists():
            c.pass_("LMD daily cron active")
        else:
            c.fail(
                "No scheduled scans",
                "Run: bastion malware schedule --scan daily --update daily",
            )
    checks.append(c)


# ── File Permissions ────────────────────────────────────────────────


def _check_ssh_key_permissions(checks: list[Check]) -> None:
    c = Check("Permissions", "SSH key permissions", "~/.ssh should have correct permissions")
    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.exists():
        c.skip("~/.ssh does not exist")
        checks.append(c)
        return

    result = run(["stat", "-c", "%a", str(ssh_dir)], check=False, timeout=5)
    dir_perm = result.stdout.strip() if result.ok else ""

    issues: list[str] = []
    if dir_perm and dir_perm != "700":
        issues.append(f".ssh dir is {dir_perm} (should be 700)")

    auth_keys = ssh_dir / "authorized_keys"
    if auth_keys.exists():
        result2 = run(["stat", "-c", "%a", str(auth_keys)], check=False, timeout=5)
        perm = result2.stdout.strip() if result2.ok else ""
        if perm and perm not in ("600", "644"):
            issues.append(f"authorized_keys is {perm} (should be 600)")

    if issues:
        c.fail("; ".join(issues), f"Run: chmod 700 {ssh_dir} && chmod 600 {auth_keys}")
    else:
        c.pass_("Correct permissions")
    checks.append(c)


def _check_world_writable(checks: list[Check]) -> None:
    c = Check("Permissions", "No world-writable dirs in /home",
              "Directories in /home should not be world-writable")
    result = run(
        ["bash", "-c",
         "find /home -maxdepth 3 -type d -perm -0002 -not -path '*/node_modules/*' "
         "-not -path '*/.cache/*' -not -path '*/vendor/*' 2>/dev/null | head -5"],
        check=False, timeout=15,
    )
    if result.ok and result.stdout.strip():
        dirs = result.stdout.strip().splitlines()
        c.fail(
            f"Found {len(dirs)} world-writable dir(s): {dirs[0]}...",
            "Run: chmod o-w <directory>",
        )
    else:
        c.pass_("No world-writable directories found")
    checks.append(c)


# ── Postgres Checks ────────────────────────────────────────────────


def _check_postgres_listen(checks: list[Check]) -> None:
    c = Check("PostgreSQL", "Not exposed to all interfaces",
              "PostgreSQL should only listen on localhost unless needed")

    result = run(
        ["bash", "-c",
         "grep -h '^listen_addresses' /etc/postgresql/*/main/postgresql.conf 2>/dev/null"],
        check=False, timeout=10,
    )
    if not result.ok or not result.stdout.strip():
        c.skip("PostgreSQL not found or using default (localhost)")
        checks.append(c)
        return

    value = result.stdout.strip().split("=")[-1].strip().strip("'\"").strip()
    if value in ("localhost", "127.0.0.1", ""):
        c.pass_(f"Listening on {value or 'localhost'}")
    elif value == "*":
        c.fail(
            "Listening on ALL interfaces (*)",
            "Restrict listen_addresses in postgresql.conf unless remote access is needed",
        )
    else:
        c.pass_(f"Listening on {value}")
    checks.append(c)


# ── Nginx Checks ───────────────────────────────────────────────────


def _check_nginx_server_tokens(checks: list[Check]) -> None:
    c = Check("Nginx", "Server tokens hidden",
              "server_tokens should be off to hide version info")
    result = run(
        ["bash", "-c",
         "grep -rh 'server_tokens' /etc/nginx/nginx.conf 2>/dev/null"],
        check=False, timeout=10,
    )
    if not result.ok or not result.stdout.strip():
        c.fail(
            "server_tokens not set (defaults to on)",
            "Add 'server_tokens off;' to nginx.conf http block",
        )
    elif "off" in result.stdout:
        c.pass_("server_tokens off")
    else:
        c.fail(
            "server_tokens is on",
            "Set 'server_tokens off;' in nginx.conf http block",
        )
    checks.append(c)


# ── Main Audit Command ─────────────────────────────────────────────


def _run_all_checks() -> list[Check]:
    """Run all audit checks and return results."""
    checks: list[Check] = []

    # SSH
    _check_ssh_root_login(checks)
    _check_ssh_password_auth(checks)
    _check_ssh_port(checks)

    # Firewall
    _check_firewall_active(checks)
    _check_firewall_default_deny(checks)

    # Fail2ban
    _check_fail2ban_running(checks)
    _check_fail2ban_ssh_jail(checks)

    # Updates
    _check_unattended_upgrades(checks)

    # Network
    _check_open_ports(checks)

    # Malware
    _check_malware_scanner(checks)
    _check_malware_schedule(checks)

    # Permissions
    _check_ssh_key_permissions(checks)
    _check_world_writable(checks)

    # Postgres
    _check_postgres_listen(checks)

    # Nginx
    _check_nginx_server_tokens(checks)

    return checks


@audit.command("run")
@click.option("--category", "-c", default=None,
              help="Only run checks for a specific category (ssh, firewall, fail2ban, etc.)")
@click.pass_context
def audit_run(ctx: click.Context, category: str | None) -> None:
    """Run a full security audit and show results with a score.

    \b
    Full audit:
      bastion audit run

    \b
    Check specific area:
      bastion audit run -c ssh
      bastion audit run -c firewall
    """
    console.print()
    console.rule("[bold blue]Security Audit[/bold blue]")
    console.print()

    checks = _run_all_checks()

    # Filter by category if specified
    if category:
        cat_lower = category.lower()
        checks = [c for c in checks if c.category.lower() == cat_lower]
        if not checks:
            print_error(f"No checks found for category: {category}")
            print_warning("Available: SSH, Firewall, Fail2ban, Updates, Network, "
                          "Malware, Permissions, PostgreSQL, Nginx")
            raise SystemExit(1)

    # Group by category
    categories: dict[str, list[Check]] = {}
    for c in checks:
        categories.setdefault(c.category, []).append(c)

    passed = 0
    failed = 0
    skipped = 0

    for cat_name, cat_checks in categories.items():
        console.print(f"[bold]{cat_name}[/bold]")
        for c in cat_checks:
            if c.passed is True:
                console.print(f"  [green]✓[/green] {c.name}")
                if c.detail:
                    console.print(f"    [dim]{c.detail}[/dim]")
                passed += 1
            elif c.passed is False:
                console.print(f"  [red]✗[/red] {c.name}")
                if c.detail:
                    console.print(f"    [red]{c.detail}[/red]")
                if c.fix_hint:
                    console.print(f"    [dim]Fix: {c.fix_hint}[/dim]")
                failed += 1
            else:
                console.print(f"  [dim]- {c.name} (skipped: {c.detail})[/dim]")
                skipped += 1
        console.print()

    # Score
    total = passed + failed
    if total > 0:
        score = passed / total
        score_pct = f"{score * 100:.0f}%"
        score_fraction = f"{passed}/{total}"

        if score >= 0.9:
            style = "bold green"
            grade = "Excellent"
        elif score >= 0.7:
            style = "bold yellow"
            grade = "Good"
        elif score >= 0.5:
            style = "bold yellow"
            grade = "Fair"
        else:
            style = "bold red"
            grade = "Needs Work"

        console.rule("[bold]Score[/bold]")
        console.print(
            f"  [{style}]{grade} — {score_fraction} checks passed ({score_pct})[/{style}]"
        )
        if skipped:
            console.print(f"  [dim]{skipped} check(s) skipped[/dim]")
        console.print()

        if failed > 0:
            console.print("[dim]Run 'bastion audit run' after fixing issues to re-check.[/dim]")
            console.print()
    else:
        print_warning("No checks were executed.")


@audit.command("ssh")
def audit_ssh() -> None:
    """Quick SSH hardening check."""
    checks: list[Check] = []
    _check_ssh_root_login(checks)
    _check_ssh_password_auth(checks)
    _check_ssh_port(checks)
    _print_category_results("SSH", checks)


@audit.command("firewall")
def audit_firewall() -> None:
    """Quick firewall check."""
    checks: list[Check] = []
    _check_firewall_active(checks)
    _check_firewall_default_deny(checks)
    _print_category_results("Firewall", checks)


@audit.command("ports")
def audit_ports() -> None:
    """Show open ports and flag unexpected ones."""
    checks: list[Check] = []
    _check_open_ports(checks)

    # Also show all listening ports for reference
    result = run(["ss", "-tlnp"], check=False, timeout=10)
    if result.ok:
        console.print()
        console.print("[bold]Listening Ports[/bold]")
        console.print(result.stdout)
        console.print()

    _print_category_results("Open Ports", checks)


def _print_category_results(title: str, checks: list[Check]) -> None:
    """Print results for a single category."""
    console.print()
    console.print(f"[bold]{title}[/bold]")
    for c in checks:
        if c.passed is True:
            console.print(f"  [green]✓[/green] {c.name}: {c.detail}")
        elif c.passed is False:
            console.print(f"  [red]✗[/red] {c.name}: {c.detail}")
            if c.fix_hint:
                console.print(f"    [dim]Fix: {c.fix_hint}[/dim]")
        else:
            console.print(f"  [dim]- {c.name}: {c.detail}[/dim]")
    console.print()
