"""Root CLI group and global options."""

from __future__ import annotations

import click

from bastion import __version__
from bastion.commands import ALL_COMMANDS
from bastion.config import load_config


@click.group()
@click.version_option(version=__version__, prog_name="bastion")
@click.option("--dry-run", is_flag=True, help="Print commands without executing.")
@click.option("--profile", type=click.Path(exists=True), help="Path to YAML server profile.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output.")
@click.pass_context
def cli(ctx: click.Context, dry_run: bool, profile: str | None, verbose: bool) -> None:
    """bastion — Linux server administration CLI."""
    ctx.ensure_object(dict)
    ctx.obj["dry_run"] = dry_run
    ctx.obj["verbose"] = verbose
    if profile:
        ctx.obj["config"] = load_config(profile)


for cmd in ALL_COMMANDS:
    cli.add_command(cmd)
