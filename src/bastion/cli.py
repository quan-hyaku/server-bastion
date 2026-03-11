"""Root CLI group and global options."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

from bastion import __version__
from bastion.commands import ALL_COMMANDS
from bastion.config import load_config
from bastion.output import print_error, print_success
from bastion.runner import CommandError


class BastionCLI(click.Group):
    """Custom Click group that catches CommandError and shows clean output."""

    def invoke(self, ctx: click.Context) -> None:
        try:
            super().invoke(ctx)
        except CommandError:
            # Error message already printed by runner.run()
            raise SystemExit(1)


@click.group(cls=BastionCLI)
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


# ── Self-update command ──────────────────────────────────────────────

INSTALL_DIR = Path.home() / ".local" / "share" / "bastion"


@cli.command("self-update")
def self_update() -> None:
    """Pull latest changes and reinstall bastion."""
    if not (INSTALL_DIR / ".git").is_dir():
        print_error(f"Source directory is not a git repo: {INSTALL_DIR}")
        print_error("Self-update requires installation via --repo.")
        raise SystemExit(1)

    click.echo(f"Updating from {INSTALL_DIR}...")

    # git pull
    result = subprocess.run(
        ["git", "pull"],
        cwd=INSTALL_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print_error(f"git pull failed: {result.stderr}")
        raise SystemExit(1)

    if "Already up to date" in result.stdout:
        print_success("Already up to date.")
        return

    click.echo(result.stdout.strip())

    # Reinstall via uv
    click.echo("Reinstalling...")
    result = subprocess.run(
        ["uv", "tool", "install", str(INSTALL_DIR), "--force"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print_error(f"uv tool install failed: {result.stderr}")
        raise SystemExit(1)

    print_success("bastion updated successfully.")

    # Show new version by running the new binary
    result = subprocess.run(
        ["bastion", "--version"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        click.echo(result.stdout.strip())


for cmd in ALL_COMMANDS:
    cli.add_command(cmd)
