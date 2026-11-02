"""Rich console helpers for consistent terminal output."""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

# ── JSON mode ───────────────────────────────────────────────────────
# When `bastion --json` is used, every helper below also records what it
# printed. cli.py redirects human output to stderr and writes json_document()
# to stdout, so callers get one parseable object and the usual exit code.

_json: dict[str, Any] | None = None  # None = human output only


def json_mode(enabled: bool) -> None:
    global _json
    _json = {"ok": True, "messages": [], "tables": [], "data": {}} if enabled else None


def json_document(*, ok: bool) -> str:
    assert _json is not None, "json_mode(True) was not called"
    _json["ok"] = ok
    return json.dumps(_json)


def plain(markup: str) -> str:
    """Strip Rich markup: '[green]ON[/green]' -> 'ON'."""
    return Text.from_markup(markup).plain


def _record(level: str, text: str) -> None:
    if _json is not None:
        _json["messages"].append({"level": level, "text": plain(text)})


def print_data(name: str, value: Any) -> None:
    """Attach structured data to the JSON document. No-op in human mode."""
    if _json is not None:
        _json["data"][name] = value


def print_success(message: str) -> None:
    _record("success", message)
    console.print(f"[bold green]✓[/bold green] {message}")


def print_error(message: str) -> None:
    _record("error", message)
    console.print(f"[bold red]✗[/bold red] {message}", style="red")


def print_warning(message: str) -> None:
    _record("warning", message)
    console.print(f"[bold yellow]⚠[/bold yellow] {message}", style="yellow")


def print_command(cmd: str, *, dry_run: bool = False) -> None:
    _record("dry-run" if dry_run else "command", cmd)
    prefix = "[dim]\\[DRY RUN][/dim] " if dry_run else "[dim]$[/dim] "
    console.print(f"{prefix}[cyan]{cmd}[/cyan]")


def print_table(
    title: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[str]],
) -> None:
    if _json is not None:
        _json["tables"].append({
            "name": re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"),
            "title": title,
            "columns": list(columns),
            "rows": [dict(zip(columns, (plain(cell) for cell in row))) for row in rows],
        })
    table = Table(title=title, show_header=True, header_style="bold magenta")
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def print_panel(title: str, content: str) -> None:
    console.print(Panel(content, title=title, border_style="blue"))
