"""Rich console helpers for consistent terminal output."""

from __future__ import annotations

from typing import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def print_success(message: str) -> None:
    console.print(f"[bold green]✓[/bold green] {message}")


def print_error(message: str) -> None:
    console.print(f"[bold red]✗[/bold red] {message}", style="red")


def print_warning(message: str) -> None:
    console.print(f"[bold yellow]⚠[/bold yellow] {message}", style="yellow")


def print_command(cmd: str, *, dry_run: bool = False) -> None:
    prefix = "[dim]\\[DRY RUN][/dim] " if dry_run else "[dim]$[/dim] "
    console.print(f"{prefix}[cyan]{cmd}[/cyan]")


def print_table(
    title: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[str]],
) -> None:
    table = Table(title=title, show_header=True, header_style="bold magenta")
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def print_panel(title: str, content: str) -> None:
    console.print(Panel(content, title=title, border_style="blue"))
