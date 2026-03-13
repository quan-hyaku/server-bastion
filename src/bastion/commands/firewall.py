"""Firewall (ufw) management commands."""

from __future__ import annotations

import re

import click

from bastion.output import print_error, print_success, print_table
from bastion.runner import run


def _validate_port(port: str) -> None:
    """Validate a port number or range (e.g. '80', '8000:9000')."""
    pattern = r'^(\d+)(:\d+)?$'
    match = re.match(pattern, port)
    if not match:
        print_error(f"Invalid port: {port}  (use a number like 80 or range like 8000:9000)")
        raise SystemExit(1)
    parts = port.split(":")
    for p in parts:
        num = int(p)
        if num < 1 or num > 65535:
            print_error(f"Port out of range: {p}  (must be 1-65535)")
            raise SystemExit(1)


@click.group("firewall")
def firewall() -> None:
    """Manage firewall rules (ufw)."""


@firewall.command("status")
def fw_status() -> None:
    """Show firewall status."""
    result = run(["ufw", "status", "verbose"], use_sudo=True, check=False)
    click.echo(result.stdout)


@firewall.command("allow")
@click.argument("port")
@click.option("--proto", type=click.Choice(["tcp", "udp"]), default="tcp", help="Protocol.")
def allow_port(port: str, proto: str) -> None:
    """Allow incoming traffic on a port."""
    _validate_port(port)
    run(["ufw", "allow", f"{port}/{proto}"], use_sudo=True)
    print_success(f"Allowed {port}/{proto}.")


@firewall.command("deny")
@click.argument("port")
@click.option("--proto", type=click.Choice(["tcp", "udp"]), default="tcp", help="Protocol.")
def deny_port(port: str, proto: str) -> None:
    """Deny incoming traffic on a port."""
    _validate_port(port)
    run(["ufw", "deny", f"{port}/{proto}"], use_sudo=True)
    print_success(f"Denied {port}/{proto}.")


@firewall.command("list-rules")
def list_rules() -> None:
    """List all firewall rules."""
    result = run(["ufw", "status", "numbered"], use_sudo=True, check=False)
    if result.stdout:
        click.echo(result.stdout)
    else:
        click.echo("No rules configured.")
