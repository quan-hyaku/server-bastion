"""System configuration tuning commands."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import click
import yaml

from bastion.config import get_config
from bastion.output import print_error, print_panel, print_success, print_table, print_warning
from bastion.runner import read_file_sudo, run, write_file_sudo


@click.group("tune")
def tune() -> None:
    """System configuration tuning."""


@tune.command("show")
@click.option(
    "--section",
    type=click.Choice(["sysctl", "limits"]),
    default=None,
    help="Filter by section.",
)
@click.pass_context
def show_tuning(ctx: click.Context, section: str | None) -> None:
    """Display current tunable values."""
    cfg = get_config(ctx).tune

    if section is None or section == "sysctl":
        result = run(["sysctl", "-a"], check=False)
        if result.ok and result.stdout:
            # Show a subset of commonly tuned values
            keys = [
                "net.core.somaxconn",
                "net.ipv4.tcp_max_syn_backlog",
                "vm.swappiness",
                "fs.file-max",
                "net.ipv4.ip_local_port_range",
            ]
            rows = []
            for line in result.stdout.splitlines():
                for key in keys:
                    if line.startswith(key):
                        parts = line.split("=", 1)
                        rows.append((parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""))
            print_table("Sysctl (common)", ["Key", "Value"], rows)

    if section is None or section == "limits":
        limits_path = Path(cfg.limits_conf)
        if limits_path.exists():
            content = limits_path.read_text()
            print_panel("Limits Config", content)
        else:
            print_warning(f"No limits file at {limits_path}")


@tune.command("apply")
@click.argument("preset")
@click.option("--force", is_flag=True, help="Skip confirmation.")
@click.pass_context
def apply_preset(ctx: click.Context, preset: str, force: bool) -> None:
    """Apply a tuning preset (webserver, database)."""
    try:
        preset_files = resources.files("bastion.profiles")
        preset_path = preset_files / f"{preset}.yaml"
        if not preset_path.is_file():
            print_error(f"Unknown preset: {preset}")
            raise SystemExit(1)
        preset_data = yaml.safe_load(preset_path.read_text())
    except (FileNotFoundError, TypeError):
        print_error(f"Unknown preset: {preset}")
        raise SystemExit(1)

    if not force:
        click.confirm(f"Apply '{preset}' tuning preset?", abort=True)

    sysctl_values: dict[str, str] = preset_data.get("sysctl", {})
    for key, value in sysctl_values.items():
        run(["sysctl", "-w", f"{key}={value}"], use_sudo=True)

    if sysctl_values:
        # Persist to config file
        cfg = get_config(ctx).tune
        lines = [f"{k} = {v}" for k, v in sysctl_values.items()]
        content = "\n".join(lines) + "\n"
        write_file_sudo(cfg.sysctl_conf, content)

    print_success(f"Preset '{preset}' applied.")


@tune.command("sysctl")
@click.argument("key")
@click.argument("value")
def set_sysctl(key: str, value: str) -> None:
    """Set a single sysctl value."""
    run(["sysctl", "-w", f"{key}={value}"], use_sudo=True)
    print_success(f"Set {key} = {value}")


@tune.command("limits")
@click.argument("domain")
@click.argument("item")
@click.argument("value")
@click.pass_context
def set_limits(ctx: click.Context, domain: str, item: str, value: str) -> None:
    """Set a single limits.conf entry (e.g., '* nofile 65535')."""
    cfg = get_config(ctx).tune
    new_lines = f"{domain} soft {item} {value}\n{domain} hard {item} {value}\n"
    limits_path = Path(cfg.limits_conf)

    # Read existing content and append safely via stdin
    existing = ""
    try:
        existing = read_file_sudo(limits_path)
    except Exception:
        pass  # File may not exist yet

    content = existing.rstrip("\n") + "\n" + new_lines if existing else new_lines
    write_file_sudo(limits_path, content)
    print_success(f"Set {item} = {value} for {domain}")
