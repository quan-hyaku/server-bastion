"""YAML config loading and typed server profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
import yaml


@dataclass
class NginxConfig:
    sites_available: str = "/etc/nginx/sites-available"
    sites_enabled: str = "/etc/nginx/sites-enabled"
    snippets_dir: str = "/etc/nginx/snippets"
    config_test_cmd: str = "nginx -t"
    reload_cmd: str = "systemctl reload nginx"
    cloudflare_refresh_script: str = "/usr/local/bin/bastion-cloudflare-refresh"


@dataclass
class PostgresConfig:
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    backup_dir: str = "/var/backups/postgresql"


@dataclass
class FirewallConfig:
    backend: str = "ufw"


@dataclass
class Fail2banConfig:
    client_cmd: str = "fail2ban-client"
    jail_dir: str = "/etc/fail2ban/jail.d"
    filter_dir: str = "/etc/fail2ban/filter.d"


@dataclass
class TuneConfig:
    sysctl_conf: str = "/etc/sysctl.d/99-bastion.conf"
    limits_conf: str = "/etc/security/limits.d/99-bastion.conf"


@dataclass
class ServerProfile:
    """Top-level server profile loaded from YAML."""

    name: str = "default"
    description: str = ""
    nginx: NginxConfig = field(default_factory=NginxConfig)
    postgres: PostgresConfig = field(default_factory=PostgresConfig)
    firewall: FirewallConfig = field(default_factory=FirewallConfig)
    fail2ban: Fail2banConfig = field(default_factory=Fail2banConfig)
    tune: TuneConfig = field(default_factory=TuneConfig)


def load_config(path: str | Path) -> ServerProfile:
    """Load a server profile from a YAML file."""
    filepath = Path(path)
    if not filepath.exists():
        raise FileNotFoundError(f"Config file not found: {filepath}")

    with filepath.open() as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    profile = ServerProfile(
        name=raw.get("name", filepath.stem),
        description=raw.get("description", ""),
    )

    if "nginx" in raw:
        profile.nginx = NginxConfig(**raw["nginx"])
    if "postgres" in raw:
        profile.postgres = PostgresConfig(**raw["postgres"])
    if "firewall" in raw:
        profile.firewall = FirewallConfig(**raw["firewall"])
    if "fail2ban" in raw:
        profile.fail2ban = Fail2banConfig(**raw["fail2ban"])
    if "tune" in raw:
        profile.tune = TuneConfig(**raw["tune"])

    return profile


def get_config(ctx: click.Context) -> ServerProfile:
    """Retrieve the active ServerProfile from Click context, or return defaults."""
    if ctx.obj and "config" in ctx.obj:
        return ctx.obj["config"]
    return ServerProfile()
