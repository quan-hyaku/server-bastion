"""Fail2ban management commands."""

from __future__ import annotations

import ipaddress
from importlib import resources
from pathlib import Path

import click

from bastion.config import get_config
from bastion.output import print_error, print_success, print_table, print_warning
from bastion.runner import run, write_file_sudo


def _validate_ip(ip: str) -> None:
    """Validate an IP address string."""
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        print_error(f"Invalid IP address: {ip}")
        raise SystemExit(1)

# All bundled jail names
BUNDLED_JAILS = ["sshd", "nginx-script-scan", "nginx-http-auth", "nginx-botsearch"]

# Jails that need a custom filter deployed (not built-in to fail2ban)
CUSTOM_FILTER_JAILS = ["nginx-script-scan", "nginx-botsearch"]


def _template_dir():
    """Get path to bundled fail2ban templates."""
    return resources.files("bastion.templates.fail2ban")


def _deploy_file(src_content: str, dst: Path) -> None:
    """Write content to a system path via sudo tee."""
    write_file_sudo(dst, src_content)


@click.group("fail2ban")
def fail2ban() -> None:
    """Manage fail2ban jails and bans."""


@fail2ban.command("status")
@click.option("--jail", default=None, help="Show status for a specific jail.")
@click.pass_context
def f2b_status(ctx: click.Context, jail: str | None) -> None:
    """Show fail2ban status."""
    cfg = get_config(ctx).fail2ban
    cmd = [cfg.client_cmd, "status"]
    if jail:
        cmd.append(jail)
    result = run(cmd, use_sudo=True, check=False)
    click.echo(result.stdout)


@fail2ban.command("ban")
@click.argument("ip")
@click.option("--jail", required=True, help="Jail to ban the IP in.")
@click.pass_context
def ban_ip(ctx: click.Context, ip: str, jail: str) -> None:
    """Ban an IP address in a jail."""
    _validate_ip(ip)
    cfg = get_config(ctx).fail2ban
    run([cfg.client_cmd, "set", jail, "banip", ip], use_sudo=True)
    print_success(f"Banned {ip} in jail '{jail}'.")


@fail2ban.command("unban")
@click.argument("ip")
@click.option("--jail", required=True, help="Jail to unban the IP from.")
@click.pass_context
def unban_ip(ctx: click.Context, ip: str, jail: str) -> None:
    """Unban an IP address from a jail."""
    _validate_ip(ip)
    cfg = get_config(ctx).fail2ban
    run([cfg.client_cmd, "set", jail, "unbanip", ip], use_sudo=True)
    print_success(f"Unbanned {ip} from jail '{jail}'.")


@fail2ban.command("list-jails")
@click.pass_context
def list_jails(ctx: click.Context) -> None:
    """List all fail2ban jails."""
    cfg = get_config(ctx).fail2ban
    result = run([cfg.client_cmd, "status"], use_sudo=True, check=False)
    if not result.ok:
        print_error("Failed to query fail2ban.")
        raise SystemExit(1)

    jails: list[str] = []
    for line in result.stdout.splitlines():
        if "Jail list:" in line:
            _, _, jail_str = line.partition("Jail list:")
            jails = [j.strip() for j in jail_str.split(",") if j.strip()]

    rows = [(j,) for j in jails]
    print_table("Fail2ban Jails", ["Jail"], rows)


@fail2ban.command("setup")
@click.argument("jails", nargs=-1)
@click.option("--all", "install_all", is_flag=True, help="Install all bundled jails.")
@click.pass_context
def setup_jails(ctx: click.Context, jails: tuple[str, ...], install_all: bool) -> None:
    """Deploy fail2ban jail and filter configs.

    Install specific jails by name, or use --all for everything.

    \b
    Available jails:
      sshd              - SSH brute-force (ban after 5 attempts)
      nginx-script-scan - Block .php/.env/.git scanners
      nginx-http-auth   - Nginx basic auth brute-force
      nginx-botsearch   - Block bots probing wp-admin, phpmyadmin, etc.
    """
    cfg = get_config(ctx).fail2ban
    names = list(BUNDLED_JAILS) if install_all else list(jails)

    if not names:
        print_error("Specify jail names or use --all.")
        click.echo(f"Available: {', '.join(BUNDLED_JAILS)}")
        raise SystemExit(1)

    for name in names:
        if name not in BUNDLED_JAILS:
            print_error(f"Unknown jail: {name}")
            click.echo(f"Available: {', '.join(BUNDLED_JAILS)}")
            raise SystemExit(1)

    for name in names:
        # Deploy custom filter if needed
        if name in CUSTOM_FILTER_JAILS:
            filter_src = _template_dir() / "filter.d" / f"{name}.conf"
            filter_dst = Path(cfg.filter_dir) / f"{name}.conf"
            _deploy_file(filter_src.read_text(), filter_dst)
            print_success(f"Filter deployed: {filter_dst}")

        # Deploy jail config
        jail_src = _template_dir() / "jail.d" / f"{name}.conf"
        jail_dst = Path(cfg.jail_dir) / f"{name}.conf"
        _deploy_file(jail_src.read_text(), jail_dst)
        print_success(f"Jail deployed: {jail_dst}")

    # Reload fail2ban
    run([cfg.client_cmd, "reload"], use_sudo=True)
    print_success(f"Fail2ban reloaded. {len(names)} jail(s) deployed.")


@fail2ban.command("remove-jail")
@click.argument("name")
@click.option("--force", is_flag=True, help="Skip confirmation.")
@click.pass_context
def remove_jail(ctx: click.Context, name: str, force: bool) -> None:
    """Remove a deployed jail config (and its custom filter if any)."""
    cfg = get_config(ctx).fail2ban

    jail_path = Path(cfg.jail_dir) / f"{name}.conf"
    filter_path = Path(cfg.filter_dir) / f"{name}.conf"

    if not force:
        click.confirm(f"Remove jail '{name}'?", abort=True)

    if name in CUSTOM_FILTER_JAILS:
        run(["rm", "-f", str(filter_path)], use_sudo=True)
        print_success(f"Filter removed: {filter_path}")

    run(["rm", "-f", str(jail_path)], use_sudo=True)
    print_success(f"Jail removed: {jail_path}")

    run([cfg.client_cmd, "reload"], use_sudo=True)
    print_success("Fail2ban reloaded.")


@fail2ban.command("show-config")
@click.argument("name")
def show_config(name: str) -> None:
    """Show the bundled config for a jail before deploying.

    \b
    Available: sshd, nginx-script-scan, nginx-http-auth, nginx-botsearch
    """
    if name not in BUNDLED_JAILS:
        print_error(f"Unknown jail: {name}")
        click.echo(f"Available: {', '.join(BUNDLED_JAILS)}")
        raise SystemExit(1)

    jail_src = _template_dir() / "jail.d" / f"{name}.conf"
    click.secho(f"=== Jail: {name} ===", bold=True)
    click.echo(jail_src.read_text())

    if name in CUSTOM_FILTER_JAILS:
        filter_src = _template_dir() / "filter.d" / f"{name}.conf"
        click.secho(f"\n=== Filter: {name} ===", bold=True)
        click.echo(filter_src.read_text())
