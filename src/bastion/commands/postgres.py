"""PostgreSQL administration commands."""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path

import click

from bastion.config import PostgresConfig, get_config
from bastion.output import print_error, print_success, print_table, print_warning
from bastion.runner import RunResult, read_file_sudo, run, write_file_sudo


def _validate_cidr(cidr: str) -> None:
    """Validate a CIDR string. Raises SystemExit on invalid input."""
    try:
        ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        print_error(f"Invalid CIDR: {cidr}  (example: 10.0.0.0/24 or 0.0.0.0/0)")
        raise SystemExit(1)


def _validate_pg_identifier(name: str, label: str) -> None:
    """Validate a PostgreSQL identifier (database or user name)."""
    if name == "all":
        return
    if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_$]*$', name):
        print_error(f"Invalid {label}: {name}  (must be alphanumeric/underscore)")
        raise SystemExit(1)


# ── Common pg config paths ───────────────────────────────────────────

PG_CONFIG_DIRS = [
    "/etc/postgresql/{ver}/main",       # Debian/Ubuntu
    "/var/lib/pgsql/{ver}/data",        # RHEL/CentOS
    "/var/lib/postgresql/{ver}/data",    # Alternative
]

BASTION_HBA_MARKER = "# bastion-managed"


def _find_pg_config_dir() -> Path | None:
    """Auto-detect the PostgreSQL config directory."""
    for pattern in PG_CONFIG_DIRS:
        parent = Path(pattern.split("{ver}")[0])
        if not parent.exists():
            continue
        # Find version directories (e.g., 14, 15, 16)
        versions = sorted(
            [d for d in parent.iterdir() if d.is_dir() and d.name.isdigit()],
            key=lambda d: int(d.name),
            reverse=True,
        )
        for ver_dir in versions:
            conf_dir = Path(pattern.format(ver=ver_dir.name))
            if (conf_dir / "postgresql.conf").exists():
                return conf_dir
    return None


# ── Connection helpers ───────────────────────────────────────────────

def _pg_env(cfg: PostgresConfig) -> dict[str, str] | None:
    """Return environment with PGPASSWORD set, if configured."""
    if cfg.password:
        env = os.environ.copy()
        env["PGPASSWORD"] = cfg.password
        return env
    return None


def _pg_base(cfg: PostgresConfig, tool: str = "psql") -> list[str]:
    """Build the base command for a postgres tool.

    Local host  → sudo -u <user> <tool> -p <port>  (peer auth)
    Remote host → <tool> -h <host> -p <port> -U <user>  (password/md5 auth)
    """
    if cfg.is_local:
        return ["sudo", "-u", cfg.user, tool, "-p", str(cfg.port)]
    return [tool, "-h", cfg.host, "-p", str(cfg.port), "-U", cfg.user]


def _run_pg(cfg: PostgresConfig, cmd: list[str], **kwargs) -> RunResult:
    """Run a postgres command with the correct env/auth."""
    env = _pg_env(cfg)
    return run(cmd, use_sudo=False, env=env, **kwargs)


# ── Remote access helpers ────────────────────────────────────────────

def _get_listen_addresses(conf_path: Path) -> str:
    """Parse listen_addresses from postgresql.conf."""
    content = read_file_sudo(conf_path)
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        match = re.match(r"listen_addresses\s*=\s*'([^']*)'", line)
        if match:
            return match.group(1)
    return "localhost"  # PostgreSQL default


def _set_listen_addresses(conf_path: Path, value: str) -> None:
    """Set listen_addresses in postgresql.conf."""
    content = read_file_sudo(conf_path)
    lines = content.splitlines()
    found = False
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if re.match(r"#?\s*listen_addresses\s*=", stripped):
            if not found:
                new_lines.append(f"listen_addresses = '{value}'")
                found = True
            # Skip duplicate lines
        else:
            new_lines.append(line)

    if not found:
        # Insert after the first comment block
        new_lines.insert(0, f"listen_addresses = '{value}'")

    write_file_sudo(conf_path, "\n".join(new_lines))


def _get_hba_remote_rules(hba_path: Path) -> list[str]:
    """Get bastion-managed remote access rules from pg_hba.conf."""
    content = read_file_sudo(hba_path)
    rules = []
    for line in content.splitlines():
        if BASTION_HBA_MARKER in line:
            # The actual rule is on this line, before the marker comment
            rule = line.split(BASTION_HBA_MARKER)[0].strip()
            if rule:
                rules.append(rule)
    return rules


def _add_hba_rule(hba_path: Path, cidr: str, database: str = "all", user: str = "all") -> None:
    """Add a remote access rule to pg_hba.conf."""
    content = read_file_sudo(hba_path)
    rule = f"host    {database}    {user}    {cidr}    scram-sha-256"
    tagged = f"{rule}    {BASTION_HBA_MARKER}"

    # Check if rule already exists
    if tagged in content:
        print_warning(f"Rule already exists: {rule}")
        return

    # Append before any final newlines
    content = content.rstrip("\n") + f"\n{tagged}\n"
    write_file_sudo(hba_path, content)
    print_success(f"Added: {rule}")


def _remove_hba_rules(hba_path: Path, cidr: str | None = None) -> int:
    """Remove bastion-managed rules from pg_hba.conf. Returns count removed."""
    content = read_file_sudo(hba_path)
    lines = content.splitlines()
    new_lines = []
    removed = 0

    for line in lines:
        if BASTION_HBA_MARKER in line:
            if cidr is None or cidr in line:
                removed += 1
                continue
        new_lines.append(line)

    if removed:
        write_file_sudo(hba_path, "\n".join(new_lines) + "\n")

    return removed


# ── Commands ─────────────────────────────────────────────────────────

@click.group("postgres")
def postgres() -> None:
    """Manage PostgreSQL databases."""


@postgres.command("status")
@click.pass_context
def pg_status(ctx: click.Context) -> None:
    """Check if PostgreSQL is running."""
    cfg = get_config(ctx).postgres
    cmd = ["pg_isready", "-p", str(cfg.port)]
    if not cfg.is_local:
        cmd.extend(["-h", cfg.host])
    result = run(cmd, check=False)
    if result.ok:
        print_success(f"PostgreSQL is accepting connections on {cfg.host}:{cfg.port}")
    else:
        print_error(f"PostgreSQL is not responding on {cfg.host}:{cfg.port}")
        raise SystemExit(1)


@postgres.command("list-dbs")
@click.pass_context
def list_dbs(ctx: click.Context) -> None:
    """List all PostgreSQL databases."""
    cfg = get_config(ctx).postgres
    cmd = _pg_base(cfg, "psql") + [
        "-t", "-A", "-c",
        "SELECT datname, pg_size_pretty(pg_database_size(datname)) "
        "FROM pg_database WHERE datistemplate = false;",
    ]
    result = _run_pg(cfg, cmd)
    rows = [line.split("|") for line in result.stdout.splitlines() if "|" in line]
    print_table("Databases", ["Name", "Size"], rows)


@postgres.command("create-db")
@click.argument("dbname")
@click.option("--owner", default=None, help="Database owner.")
@click.pass_context
def create_db(ctx: click.Context, dbname: str, owner: str | None) -> None:
    """Create a new PostgreSQL database."""
    _validate_pg_identifier(dbname, "database name")
    if owner:
        _validate_pg_identifier(owner, "owner")
    cfg = get_config(ctx).postgres
    cmd = _pg_base(cfg, "createdb")
    if owner:
        cmd.extend(["-O", owner])
    cmd.append(dbname)
    _run_pg(cfg, cmd)
    print_success(f"Database '{dbname}' created.")


@postgres.command("drop-db")
@click.argument("dbname")
@click.option("--force", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def drop_db(ctx: click.Context, dbname: str, force: bool) -> None:
    """Drop a PostgreSQL database."""
    _validate_pg_identifier(dbname, "database name")
    if not force:
        click.confirm(f"Drop database '{dbname}'? This cannot be undone", abort=True)
    cfg = get_config(ctx).postgres
    cmd = _pg_base(cfg, "dropdb") + [dbname]
    _run_pg(cfg, cmd)
    print_success(f"Database '{dbname}' dropped.")


@postgres.command("backup")
@click.argument("dbname")
@click.option("--output", "-o", type=click.Path(), default=None, help="Output file path.")
@click.pass_context
def backup_db(ctx: click.Context, dbname: str, output: str | None) -> None:
    """Backup a PostgreSQL database using pg_dump."""
    _validate_pg_identifier(dbname, "database name")
    cfg = get_config(ctx).postgres
    if output is None:
        backup_dir = Path(cfg.backup_dir)
        output = str(backup_dir / f"{dbname}.sql")

    cmd = _pg_base(cfg, "pg_dump") + ["-f", output, dbname]
    _run_pg(cfg, cmd)
    print_success(f"Database '{dbname}' backed up to {output}.")


# ── Remote access management ────────────────────────────────────────

@postgres.command("remote-access")
@click.option("--enable", "action", flag_value="enable", help="Enable remote connections.")
@click.option("--disable", "action", flag_value="disable", help="Disable remote connections (local only).")
@click.option("--status", "action", flag_value="status", default=True, help="Show current remote access config.")
@click.option("--cidr", default=None, help="CIDR range to allow (e.g. 10.0.0.0/24, 0.0.0.0/0).")
@click.option("--database", default="all", help="Database to allow (default: all).")
@click.option("--user", "pg_user", default="all", help="PostgreSQL user to allow (default: all).")
@click.pass_context
def remote_access(
    ctx: click.Context,
    action: str,
    cidr: str | None,
    database: str,
    pg_user: str,
) -> None:
    """Manage remote access to PostgreSQL.

    \b
    Show status:    bastion postgres remote-access
    Enable:         bastion postgres remote-access --enable --cidr 10.0.0.0/24
    Allow all IPs:  bastion postgres remote-access --enable --cidr 0.0.0.0/0
    Disable:        bastion postgres remote-access --disable
    Remove a CIDR:  bastion postgres remote-access --disable --cidr 10.0.0.0/24
    """
    conf_dir = _find_pg_config_dir()
    if not conf_dir:
        print_error("Could not find PostgreSQL config directory.")
        print_error("Checked: " + ", ".join(p.split("{ver}")[0] for p in PG_CONFIG_DIRS))
        raise SystemExit(1)

    pg_conf = conf_dir / "postgresql.conf"
    hba_conf = conf_dir / "pg_hba.conf"

    if action == "status":
        listen = _get_listen_addresses(pg_conf)
        rules = _get_hba_remote_rules(hba_conf)

        click.secho(f"\nPostgreSQL config: {conf_dir}", bold=True)
        click.echo(f"  listen_addresses = '{listen}'")

        if listen in ("localhost", ""):
            print_warning("  Listening on localhost only (no remote access)")
        else:
            print_success(f"  Listening on: {listen}")

        click.echo()
        if rules:
            click.secho("Remote access rules (bastion-managed):", bold=True)
            for rule in rules:
                click.echo(f"  {rule}")
        else:
            click.echo("No bastion-managed remote access rules in pg_hba.conf")
        click.echo()
        return

    if action == "enable":
        if not cidr:
            print_error("--cidr is required. Example: --cidr 10.0.0.0/24")
            raise SystemExit(1)

        _validate_cidr(cidr)
        _validate_pg_identifier(database, "database")
        _validate_pg_identifier(pg_user, "user")

        # Set listen_addresses to '*'
        current_listen = _get_listen_addresses(pg_conf)
        if current_listen != "*":
            _set_listen_addresses(pg_conf, "*")
            print_success("listen_addresses set to '*'")
        else:
            click.echo("listen_addresses already set to '*'")

        # Add HBA rule
        _add_hba_rule(hba_conf, cidr, database=database, user=pg_user)

        # Restart PostgreSQL
        click.echo("Restarting PostgreSQL to apply changes...")
        run(["systemctl", "restart", "postgresql"], use_sudo=True)
        print_success("PostgreSQL restarted. Remote access enabled.")
        return

    if action == "disable":
        if cidr:
            _validate_cidr(cidr)
            # Remove specific CIDR rule only
            removed = _remove_hba_rules(hba_conf, cidr=cidr)
            if removed:
                print_success(f"Removed {removed} rule(s) matching {cidr}")
            else:
                print_warning(f"No bastion-managed rules found for {cidr}")

            # Check if any rules remain
            remaining = _get_hba_remote_rules(hba_conf)
            if not remaining:
                _set_listen_addresses(pg_conf, "localhost")
                print_success("No remote rules remain — listen_addresses set to 'localhost'")
        else:
            # Remove ALL bastion-managed rules and lock down
            removed = _remove_hba_rules(hba_conf)
            _set_listen_addresses(pg_conf, "localhost")
            if removed:
                print_success(f"Removed {removed} remote access rule(s)")
            print_success("listen_addresses set to 'localhost'")

        click.echo("Restarting PostgreSQL to apply changes...")
        run(["systemctl", "restart", "postgresql"], use_sudo=True)
        print_success("PostgreSQL restarted. Remote access disabled.")
