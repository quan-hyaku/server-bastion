"""Firewall (ufw) management commands."""

from __future__ import annotations

import click

from bastion.output import print_success, print_table
from bastion.runner import run


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
    run(["ufw", "allow", f"{port}/{proto}"], use_sudo=True)
    print_success(f"Allowed {port}/{proto}.")


@firewall.command("deny")
@click.argument("port")
@click.option("--proto", type=click.Choice(["tcp", "udp"]), default="tcp", help="Protocol.")
def deny_port(port: str, proto: str) -> None:
    """Deny incoming traffic on a port."""
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
