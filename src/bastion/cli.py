"""Root CLI group and global options."""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path

import click

from bastion import __version__, output
from bastion.commands import ALL_COMMANDS
from bastion.config import load_config
from bastion.output import print_error, print_success
from bastion.runner import CommandError


class BastionCLI(click.Group):
    """Custom Click group that catches CommandError and shows clean output.

    With --json, human output is redirected to stderr and a single JSON
    document is written to stdout, whatever the outcome. The exit code is
    unchanged, so scripts can rely on either.
    """

    def invoke(self, ctx: click.Context) -> None:
        json_out = bool(ctx.params.get("json_out"))
        output.json_mode(json_out)
        if not json_out:
            return self._invoke(ctx)

        code = 0
        try:
            with contextlib.redirect_stdout(sys.stderr):
                self._invoke(ctx)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        except click.ClickException as exc:
            print_error(exc.format_message())
            code = exc.exit_code
        sys.stdout.write(output.json_document(ok=code == 0) + "\n")
        if code:
            raise SystemExit(code)

    def _invoke(self, ctx: click.Context) -> None:
        try:
            super().invoke(ctx)
        except CommandError:
            # Error message already printed by runner.run()
            raise SystemExit(1)
        except subprocess.TimeoutExpired as exc:
            print_error(f"Command timed out: {exc.cmd}")
            raise SystemExit(1)


@click.group(cls=BastionCLI)
@click.version_option(version=__version__, prog_name="bastion")
@click.option("--dry-run", is_flag=True, help="Print commands without executing.")
@click.option("--profile", type=click.Path(exists=True), help="Path to YAML server profile.")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output.")
@click.option("--json", "json_out", is_flag=True,
              help="Write one JSON document to stdout (human output goes to stderr). "
                   "Combine with non-interactive flags such as --force.")
@click.pass_context
def cli(ctx: click.Context, dry_run: bool, profile: str | None, verbose: bool, json_out: bool) -> None:
    """bastion — Linux server administration CLI."""
    ctx.ensure_object(dict)
    ctx.obj["dry_run"] = dry_run
    ctx.obj["verbose"] = verbose
    ctx.obj["json"] = json_out
    if profile:
        ctx.obj["config"] = load_config(profile)


# ── Self-update command ──────────────────────────────────────────────

INSTALL_DIR = Path.home() / ".local" / "share" / "bastion"


@cli.command("self-update")
@click.pass_context
def self_update(ctx: click.Context) -> None:
    """Pull latest changes and reinstall bastion."""
    dry_run = ctx.obj.get("dry_run", False) if ctx.obj else False

    if dry_run:
        from bastion.output import print_command
        print_command(f"git -C {INSTALL_DIR} pull", dry_run=True)
        print_command(f"uv tool install {INSTALL_DIR} --force --reinstall", dry_run=True)
        return

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

    # Clean __pycache__ to prevent stale bytecode
    for pycache in INSTALL_DIR.rglob("__pycache__"):
        import shutil
        shutil.rmtree(pycache, ignore_errors=True)

    # Reinstall via uv
    click.echo("Reinstalling...")
    result = subprocess.run(
        ["uv", "tool", "install", str(INSTALL_DIR), "--force", "--reinstall"],
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
