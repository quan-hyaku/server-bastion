"""Server health dashboard — one command to see everything at a glance."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import click

from bastion.output import console, print_error, print_success, print_table, print_warning
from bastion.runner import run


@click.group("health")
def health() -> None:
    """Server health dashboard."""


# ── Helpers ─────────────────────────────────────────────────────────


def _bytes_to_human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"


def _pct_style(pct: float, warn: float = 80, crit: float = 90) -> str:
    """Return a Rich-styled percentage string."""
    text = f"{pct:.1f}%"
    if pct >= crit:
        return f"[bold red]{text}[/bold red]"
    if pct >= warn:
        return f"[yellow]{text}[/yellow]"
    return f"[green]{text}[/green]"


def _status_dot(ok: bool) -> str:
    return "[green]●[/green]" if ok else "[red]●[/red]"


# ── Disk ────────────────────────────────────────────────────────────


def _get_disk_info() -> list[tuple[str, ...]]:
    """Get disk usage for all real filesystems."""
    result = run(["df", "-h", "--type=ext4", "--type=xfs", "--type=btrfs", "--type=zfs"],
                 check=False, timeout=10)
    rows: list[tuple[str, ...]] = []
    if not result.ok or not result.stdout:
        # Fallback: just df -h excluding pseudo-fs
        result = run(["df", "-h", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs",
                       "-x", "overlay"], check=False, timeout=10)
    if not result.stdout:
        return rows

    for line in result.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 6:
            continue
        fs, size, used, avail, pct_str, mount = (
            parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
        )
        pct = float(pct_str.rstrip("%"))
        rows.append((mount, size, used, avail, _pct_style(pct)))
    return rows


# ── Memory ──────────────────────────────────────────────────────────


def _get_memory_info() -> list[tuple[str, ...]]:
    """Get memory and swap usage."""
    result = run(["free", "-b"], check=False, timeout=10)
    rows: list[tuple[str, ...]] = []
    if not result.stdout:
        return rows

    for line in result.stdout.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            total, used = int(parts[1]), int(parts[2])
            pct = (used / total * 100) if total else 0
            rows.append((
                "RAM",
                _bytes_to_human(total),
                _bytes_to_human(used),
                _bytes_to_human(total - used),
                _pct_style(pct),
            ))
        elif line.startswith("Swap:"):
            parts = line.split()
            total, used = int(parts[1]), int(parts[2])
            if total > 0:
                pct = (used / total * 100) if total else 0
                rows.append((
                    "Swap",
                    _bytes_to_human(total),
                    _bytes_to_human(used),
                    _bytes_to_human(total - used),
                    _pct_style(pct),
                ))
            else:
                rows.append(("Swap", "-", "-", "-", "[yellow]not configured[/yellow]"))
    return rows


# ── CPU & Uptime ────────────────────────────────────────────────────


def _get_load_and_uptime() -> tuple[str, str, int]:
    """Return (load_display, uptime_display, cpu_count)."""
    # CPU count
    result = run(["nproc"], check=False, timeout=5)
    cpus = int(result.stdout) if result.ok and result.stdout.strip().isdigit() else 1

    # Load averages
    result = run(["cat", "/proc/loadavg"], check=False, timeout=5)
    load_display = "unknown"
    if result.ok and result.stdout:
        parts = result.stdout.split()
        loads = [float(parts[i]) for i in range(3)]
        styled = []
        for ld in loads:
            pct = (ld / cpus) * 100
            styled.append(_pct_style(pct, warn=70, crit=90).replace("%", ""))
            # Replace the percentage text with the load value
        styled_loads = []
        for ld in loads:
            pct = (ld / cpus) * 100
            val = f"{ld:.2f}"
            if pct >= 90:
                styled_loads.append(f"[bold red]{val}[/bold red]")
            elif pct >= 70:
                styled_loads.append(f"[yellow]{val}[/yellow]")
            else:
                styled_loads.append(f"[green]{val}[/green]")
        load_display = f"{styled_loads[0]}  {styled_loads[1]}  {styled_loads[2]}  ({cpus} cores)"

    # Uptime
    result = run(["uptime", "-p"], check=False, timeout=5)
    uptime_display = result.stdout.replace("up ", "") if result.ok else "unknown"

    return load_display, uptime_display, cpus


# ── Services ────────────────────────────────────────────────────────


def _get_services_status() -> list[tuple[str, ...]]:
    """Check status of key services."""
    services = [
        ("nginx", "Web Server"),
        ("postgresql", "Database"),
        ("ufw", "Firewall"),
        ("fail2ban", "Intrusion Prevention"),
        ("clamav-daemon", "Antivirus"),
        ("clamav-freshclam", "Virus DB Updates"),
    ]
    rows: list[tuple[str, ...]] = []
    for svc, label in services:
        result = run(["systemctl", "is-active", svc], check=False, timeout=5)
        state = result.stdout.strip() if result.ok else result.stdout.strip() or "not found"
        if state == "active":
            dot = _status_dot(True)
        elif state == "inactive":
            dot = "[yellow]●[/yellow]"
        else:
            dot = _status_dot(False)
        rows.append((f"{dot} {label}", svc, state))
    return rows


# ── SSL Certs ───────────────────────────────────────────────────────


def _get_ssl_certs() -> list[tuple[str, ...]]:
    """Check SSL certificate expiry for sites in /etc/nginx/sites-enabled."""
    rows: list[tuple[str, ...]] = []
    sites_dir = Path("/etc/nginx/sites-enabled")

    # Get domains from nginx configs
    result = run(
        ["bash", "-c", "grep -rh 'ssl_certificate ' /etc/nginx/sites-enabled/ 2>/dev/null | "
         "awk '{print $2}' | tr -d ';' | sort -u"],
        check=False, timeout=10,
    )
    if not result.ok or not result.stdout:
        return rows

    seen: set[str] = set()
    now = datetime.now(timezone.utc)
    for cert_path in result.stdout.splitlines():
        cert_path = cert_path.strip()
        if not cert_path or cert_path in seen:
            continue
        seen.add(cert_path)

        # Get expiry date from cert
        result_ssl = run(
            ["sudo", "openssl", "x509", "-enddate", "-noout", "-in", cert_path],
            use_sudo=False, check=False, timeout=10,
        )
        if not result_ssl.ok or "notAfter=" not in result_ssl.stdout:
            continue

        date_str = result_ssl.stdout.split("notAfter=")[1].strip()
        try:
            expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue

        days_left = (expiry - now).days
        # Extract domain from cert path (e.g., /etc/letsencrypt/live/example.com/fullchain.pem)
        domain = Path(cert_path).parent.name
        if domain in ("live", "ssl", "certs"):
            domain = cert_path

        if days_left < 0:
            status = f"[bold red]EXPIRED {abs(days_left)}d ago[/bold red]"
        elif days_left <= 7:
            status = f"[bold red]{days_left} days[/bold red]"
        elif days_left <= 30:
            status = f"[yellow]{days_left} days[/yellow]"
        else:
            status = f"[green]{days_left} days[/green]"

        rows.append((domain, status, expiry.strftime("%Y-%m-%d")))

    return rows


# ── Security ────────────────────────────────────────────────────────


def _get_failed_logins() -> str:
    """Count failed SSH login attempts in the last 24 hours."""
    result = run(
        ["bash", "-c",
         "journalctl -u ssh -u sshd --since '24 hours ago' --no-pager 2>/dev/null | "
         "grep -ci 'failed\\|invalid' || echo 0"],
        check=False, timeout=15,
    )
    count = result.stdout.strip() if result.ok else "?"
    try:
        n = int(count)
        if n > 100:
            return f"[bold red]{n}[/bold red]"
        elif n > 20:
            return f"[yellow]{n}[/yellow]"
        else:
            return f"[green]{n}[/green]"
    except ValueError:
        return count


def _get_banned_ips() -> str:
    """Get count of currently banned IPs from fail2ban."""
    result = run(["fail2ban-client", "status"], use_sudo=True, check=False, timeout=10)
    if not result.ok:
        return "fail2ban not running"

    total = 0
    # Get jail list
    for line in result.stdout.splitlines():
        if "Jail list:" in line:
            jails = line.split(":", 1)[1].strip()
            for jail in jails.split(","):
                jail = jail.strip()
                if not jail:
                    continue
                jr = run(["fail2ban-client", "status", jail], use_sudo=True,
                         check=False, timeout=10)
                if jr.ok:
                    for jl in jr.stdout.splitlines():
                        if "Currently banned" in jl:
                            try:
                                total += int(jl.split(":")[1].strip())
                            except (ValueError, IndexError):
                                pass
    return str(total)


# ── Updates ─────────────────────────────────────────────────────────


def _get_pending_updates() -> tuple[str, str]:
    """Check for pending security updates. Returns (total, security)."""
    # Check if update data exists
    result = run(
        ["bash", "-c",
         "/usr/lib/update-notifier/apt-check 2>&1 || echo unknown"],
        check=False, timeout=30,
    )
    if not result.ok or "unknown" in result.stdout:
        return ("?", "?")

    output = result.stdout.strip()
    # Format is: "X;Y" where X = total updates, Y = security updates
    if ";" in output:
        parts = output.split(";")
        total = parts[0]
        security = parts[1]
        return (total, security)
    return ("?", "?")


# ── Main Command ────────────────────────────────────────────────────


@health.command("status")
@click.pass_context
def health_status(ctx: click.Context) -> None:
    """Show full server health dashboard.

    \b
    Displays:
      - System info (uptime, load, CPU cores)
      - Disk usage
      - Memory & swap
      - Service status (nginx, postgres, firewall, etc.)
      - SSL certificate expiry
      - Security summary (failed logins, banned IPs)
      - Pending system updates
    """
    console.print()
    console.rule("[bold blue]Server Health Dashboard[/bold blue]")
    console.print()

    # System info
    load_display, uptime_display, cpus = _get_load_and_uptime()
    hostname = run(["hostname"], check=False, timeout=5).stdout.strip()
    console.print(f"  [bold]Host:[/bold]    {hostname}")
    console.print(f"  [bold]Uptime:[/bold]  {uptime_display}")
    console.print(f"  [bold]Load:[/bold]    {load_display}")
    console.print()

    # Disk
    disk_rows = _get_disk_info()
    if disk_rows:
        print_table("Disk Usage", ["Mount", "Size", "Used", "Available", "Usage"], disk_rows)
        console.print()

    # Memory
    mem_rows = _get_memory_info()
    if mem_rows:
        print_table("Memory", ["Type", "Total", "Used", "Free", "Usage"], mem_rows)
        console.print()

    # Services
    svc_rows = _get_services_status()
    if svc_rows:
        print_table("Services", ["Service", "Unit", "Status"], svc_rows)
        console.print()

    # SSL Certs
    ssl_rows = _get_ssl_certs()
    if ssl_rows:
        print_table("SSL Certificates", ["Domain", "Expires In", "Expiry Date"], ssl_rows)
        console.print()

    # Security
    console.print("[bold]Security (last 24h)[/bold]")
    failed = _get_failed_logins()
    banned = _get_banned_ips()
    console.print(f"  Failed SSH logins:  {failed}")
    console.print(f"  Banned IPs:         {banned}")
    console.print()

    # Updates
    total_updates, security_updates = _get_pending_updates()
    try:
        sec_n = int(security_updates)
        if sec_n > 0:
            security_display = f"[bold red]{security_updates}[/bold red]"
        else:
            security_display = f"[green]{security_updates}[/green]"
    except ValueError:
        security_display = security_updates

    console.print("[bold]System Updates[/bold]")
    console.print(f"  Pending updates:    {total_updates}")
    console.print(f"  Security updates:   {security_display}")
    console.print()


@health.command("disk")
def health_disk() -> None:
    """Show disk usage."""
    rows = _get_disk_info()
    if rows:
        print_table("Disk Usage", ["Mount", "Size", "Used", "Available", "Usage"], rows)
    else:
        print_warning("Could not read disk info.")


@health.command("memory")
def health_memory() -> None:
    """Show memory and swap usage."""
    rows = _get_memory_info()
    if rows:
        print_table("Memory", ["Type", "Total", "Used", "Free", "Usage"], rows)
    else:
        print_warning("Could not read memory info.")


@health.command("services")
def health_services() -> None:
    """Show status of key services."""
    rows = _get_services_status()
    if rows:
        print_table("Services", ["Service", "Unit", "Status"], rows)
    else:
        print_warning("Could not check services.")


@health.command("ssl")
def health_ssl() -> None:
    """Check SSL certificate expiry dates."""
    rows = _get_ssl_certs()
    if rows:
        print_table("SSL Certificates", ["Domain", "Expires In", "Expiry Date"], rows)
    else:
        click.echo("No SSL certificates found in /etc/nginx/sites-enabled/.")
