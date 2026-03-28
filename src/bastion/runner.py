"""Subprocess wrapper with sudo, dry-run, and logging support."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import click

from bastion.output import print_command, print_error


@dataclass(frozen=True, slots=True)
class RunResult:
    """Result of a shell command execution."""

    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandError(Exception):
    """Raised when a shell command fails."""

    def __init__(self, result: RunResult) -> None:
        self.result = result
        super().__init__(f"Command failed (exit {result.returncode}): {result.command}")


def run(
    args: str | Sequence[str],
    *,
    use_sudo: bool = False,
    check: bool = True,
    timeout: int = 30,
    ctx: click.Context | None = None,
    env: dict[str, str] | None = None,
    input: str | None = None,
) -> RunResult:
    """Execute a shell command with dry-run, sudo, and logging support.

    All command modules should call this instead of subprocess directly.
    """
    if isinstance(args, str):
        cmd_parts = shlex.split(args)
    else:
        cmd_parts = list(args)

    if use_sudo:
        cmd_parts = ["sudo", *cmd_parts]

    cmd_display = shlex.join(cmd_parts)

    # Resolve dry-run from Click context
    dry_run = False
    if ctx is None:
        ctx = click.get_current_context(silent=True)
    if ctx and ctx.obj:
        dry_run = ctx.obj.get("dry_run", False)

    if dry_run:
        print_command(cmd_display, dry_run=True)
        return RunResult(command=cmd_display, returncode=0, stdout="", stderr="")

    verbose = False
    if ctx and ctx.obj:
        verbose = ctx.obj.get("verbose", False)
    if verbose:
        print_command(cmd_display)

    proc = subprocess.run(
        cmd_parts,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        input=input,
    )

    result = RunResult(
        command=cmd_display,
        returncode=proc.returncode,
        stdout=proc.stdout.strip() if proc.stdout else "",
        stderr=proc.stderr.strip() if proc.stderr else "",
    )

    if check and not result.ok:
        print_error(f"Command failed: {result.stderr or result.stdout}")
        raise CommandError(result)

    return result


def read_file_sudo(path: Path | str) -> str:
    """Read a root-owned file via sudo cat."""
    result = run(["sudo", "cat", str(path)], use_sudo=False, timeout=10)
    return result.stdout


def write_file_sudo(path: Path | str, content: str) -> None:
    """Write content to a root-owned file via sudo tee.

    Uses subprocess stdin to pass content safely — no shell injection possible.
    Respects --dry-run and raises CommandError on failure.
    """
    run(["tee", str(path)], use_sudo=True, input=content, timeout=10)
