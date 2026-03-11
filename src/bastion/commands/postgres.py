"""PostgreSQL administration commands."""

from __future__ import annotations

from pathlib import Path

import click

from bastion.config import get_config
from bastion.output import print_error, print_success, print_table
from bastion.runner import run


def _psql_cmd(cfg, sql: str) -> list[str]:
    """Build a psql command that runs as the postgres OS user (peer auth)."""
    return ["sudo", "-u", cfg.user, "psql", "-p", str(cfg.port), "-t", "-A", "-c", sql]


@click.group("postgres")
def postgres() -> None:
    """Manage PostgreSQL databases."""


@postgres.command("status")
@click.pass_context
def pg_status(ctx: click.Context) -> None:
    """Check if PostgreSQL is running."""
    cfg = get_config(ctx).postgres
    result = run(
        ["pg_isready", "-p", str(cfg.port)],
        check=False,
    )
    if result.ok:
        print_success("PostgreSQL is accepting connections.")
    else:
        print_error("PostgreSQL is not responding.")
        raise SystemExit(1)


@postgres.command("list-dbs")
@click.pass_context
def list_dbs(ctx: click.Context) -> None:
    """List all PostgreSQL databases."""
    cfg = get_config(ctx).postgres
    result = run(
        _psql_cmd(cfg,
            "SELECT datname, pg_size_pretty(pg_database_size(datname)) "
            "FROM pg_database WHERE datistemplate = false;"
        ),
        use_sudo=False,  # sudo is already in the command
    )
    rows = [line.split("|") for line in result.stdout.splitlines() if "|" in line]
    print_table("Databases", ["Name", "Size"], rows)


@postgres.command("create-db")
@click.argument("dbname")
@click.option("--owner", default=None, help="Database owner.")
@click.pass_context
def create_db(ctx: click.Context, dbname: str, owner: str | None) -> None:
    """Create a new PostgreSQL database."""
    cfg = get_config(ctx).postgres
    cmd = ["sudo", "-u", cfg.user, "createdb", "-p", str(cfg.port)]
    if owner:
        cmd.extend(["-O", owner])
    cmd.append(dbname)
    run(cmd, use_sudo=False)
    print_success(f"Database '{dbname}' created.")


@postgres.command("drop-db")
@click.argument("dbname")
@click.option("--force", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def drop_db(ctx: click.Context, dbname: str, force: bool) -> None:
    """Drop a PostgreSQL database."""
    if not force:
        click.confirm(f"Drop database '{dbname}'? This cannot be undone", abort=True)
    cfg = get_config(ctx).postgres
    run(["sudo", "-u", cfg.user, "dropdb", "-p", str(cfg.port), dbname], use_sudo=False)
    print_success(f"Database '{dbname}' dropped.")


@postgres.command("backup")
@click.argument("dbname")
@click.option("--output", "-o", type=click.Path(), default=None, help="Output file path.")
@click.pass_context
def backup_db(ctx: click.Context, dbname: str, output: str | None) -> None:
    """Backup a PostgreSQL database using pg_dump."""
    cfg = get_config(ctx).postgres
    if output is None:
        backup_dir = Path(cfg.backup_dir)
        output = str(backup_dir / f"{dbname}.sql")

    run([
        "sudo", "-u", cfg.user, "pg_dump", "-p", str(cfg.port),
        "-f", output, dbname,
    ], use_sudo=False)
    print_success(f"Database '{dbname}' backed up to {output}.")
