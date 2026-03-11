"""Nginx management commands."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import click

from bastion.config import get_config
from bastion.output import (
    console,
    print_error,
    print_success,
    print_table,
    print_warning,
)
from bastion.runner import run


# ── Cloudflare helpers ──────────────────────────────────────────────

CF_REALIP_INCLUDE = "include /etc/nginx/snippets/cloudflare-realip.conf;"
CF_ALLOW_INCLUDE = "include /etc/nginx/snippets/cloudflare-allow.conf;"
CRON_COMMENT = "# bastion: cloudflare IP refresh"
CRON_SCHEDULE = "0 4 * * *"  # daily at 4 AM


def _nginx_templates():
    """Get path to bundled nginx templates."""
    return resources.files("bastion.templates.nginx")


def _deploy_file(src_content: str, dst: Path) -> None:
    """Write content to a system path via sudo tee."""
    run(
        ["bash", "-c", f"cat <<'SERVERCTL_EOF' | sudo tee {dst} > /dev/null\n{src_content}\nSERVERCTL_EOF"],
    )


def _site_has_include(site_path: Path, include_line: str) -> bool:
    """Check if a site config contains a specific include directive."""
    if not site_path.exists():
        return False
    content = site_path.read_text()
    return include_line in content


def _add_include_to_site(site_path: Path, include_line: str) -> None:
    """Add an include directive inside the first server block of a site config."""
    content = site_path.read_text()
    # Insert after the first opening brace of a server block
    marker = "server {"
    idx = content.find(marker)
    if idx == -1:
        marker = "server{"
        idx = content.find(marker)
    if idx == -1:
        print_warning(f"No 'server {{' block in {site_path.name}, adding at top.")
        new_content = f"{include_line}\n{content}"
    else:
        insert_pos = idx + len(marker)
        new_content = content[:insert_pos] + f"\n    {include_line}" + content[insert_pos:]

    _deploy_file(new_content, site_path)


def _remove_include_from_site(site_path: Path, include_line: str) -> None:
    """Remove an include directive from a site config."""
    content = site_path.read_text()
    lines = content.splitlines(keepends=True)
    new_lines = [line for line in lines if include_line not in line]
    _deploy_file("".join(new_lines), site_path)


def _get_site_names(sites_available: Path) -> list[str]:
    """Get sorted list of site config filenames."""
    if not sites_available.exists():
        return []
    return sorted(
        p.name for p in sites_available.iterdir()
        if p.is_file() and p.name != "default"
    )


def _prompt_site_selection(
    sites: list[str],
    sites_dir: Path,
    include_line: str,
    label: str,
) -> tuple[list[str], list[str]]:
    """Interactive checklist: let user toggle sites on/off.

    Entering a site number flips its current state (ON→OFF, OFF→ON).
    'a' enables all, 'n' disables all, empty keeps everything unchanged.

    Returns (to_enable, to_disable) lists of site names.
    """
    current_status = {
        s: _site_has_include(sites_dir / s, include_line)
        for s in sites
    }

    click.echo(f"\n{label}")
    click.echo("Enter site numbers to toggle (comma-separated), 'a' = enable all, 'n' = disable all:")
    click.echo()
    for i, site in enumerate(sites, 1):
        status = "[green]ON[/green]" if current_status[site] else "[dim]OFF[/dim]"
        console.print(f"  {i}) {site}  {status}")
    click.echo()

    raw = click.prompt("Toggle sites", default="", show_default=False)
    raw = raw.strip()

    if not raw:
        return [], []

    if raw.lower() == "a":
        # Enable all — only enable those currently OFF
        to_enable = [s for s in sites if not current_status[s]]
        return to_enable, []

    if raw.lower() == "n":
        # Disable all — only disable those currently ON
        to_disable = [s for s in sites if current_status[s]]
        return [], to_disable

    # Toggle selected sites: ON→OFF, OFF→ON
    toggled = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(sites):
                toggled.add(sites[idx])

    to_enable = [s for s in toggled if not current_status[s]]
    to_disable = [s for s in toggled if current_status[s]]

    return to_enable, to_disable


def _is_cronjob_installed(script_path: str) -> bool:
    """Check if the cloudflare refresh cronjob is installed in root's crontab."""
    result = run(
        ["bash", "-c", f"sudo crontab -l 2>/dev/null | grep -q '{script_path}'"],
        check=False,
    )
    return result.ok


def _install_cronjob(script_path: str) -> None:
    """Add the cloudflare refresh script to root's crontab."""
    cron_line = f"{CRON_SCHEDULE} {script_path}"
    run(
        ["bash", "-c",
         f"(sudo crontab -l 2>/dev/null | grep -v '{script_path}'; "
         f"echo '{CRON_COMMENT}'; echo '{cron_line}') | sudo crontab -"],
    )


def _remove_cronjob(script_path: str) -> None:
    """Remove the cloudflare refresh script from root's crontab."""
    run(
        ["bash", "-c",
         f"sudo crontab -l 2>/dev/null | grep -v '{script_path}' | grep -v '{CRON_COMMENT}' | sudo crontab -"],
        check=False,
    )


def _any_site_has_cloudflare_allow(sites_dir: Path, sites: list[str]) -> bool:
    """Check if any site has the cloudflare-allow include."""
    return any(_site_has_include(sites_dir / s, CF_ALLOW_INCLUDE) for s in sites)


# ── Commands ────────────────────────────────────────────────────────

@click.group("nginx")
def nginx() -> None:
    """Manage nginx configuration."""


@nginx.command("list-sites")
@click.pass_context
def list_sites(ctx: click.Context) -> None:
    """List available and enabled nginx sites."""
    cfg = get_config(ctx).nginx
    available = Path(cfg.sites_available)
    enabled = Path(cfg.sites_enabled)

    if not available.exists():
        print_error(f"Directory not found: {available}")
        raise SystemExit(1)

    available_sites = sorted(p.name for p in available.iterdir() if p.is_file())
    enabled_names = {p.name for p in enabled.iterdir()} if enabled.exists() else set()

    rows = [
        (site, "[green]enabled[/green]" if site in enabled_names else "[dim]disabled[/dim]")
        for site in available_sites
    ]
    print_table("Nginx Sites", ["Site", "Status"], rows)


@nginx.command("enable")
@click.argument("site")
@click.pass_context
def enable_site(ctx: click.Context, site: str) -> None:
    """Enable an nginx site by creating a symlink."""
    cfg = get_config(ctx).nginx
    source = Path(cfg.sites_available) / site
    target = Path(cfg.sites_enabled) / site

    if not source.exists():
        print_error(f"Site config not found: {source}")
        raise SystemExit(1)

    run(["ln", "-sf", str(source), str(target)], use_sudo=True)
    run(cfg.config_test_cmd, use_sudo=True)
    run(cfg.reload_cmd, use_sudo=True)
    print_success(f"Site '{site}' enabled and nginx reloaded.")


@nginx.command("disable")
@click.argument("site")
@click.pass_context
def disable_site(ctx: click.Context, site: str) -> None:
    """Disable an nginx site by removing its symlink."""
    cfg = get_config(ctx).nginx
    target = Path(cfg.sites_enabled) / site

    if not target.exists():
        print_warning(f"Site '{site}' is not currently enabled.")
        return

    run(["rm", str(target)], use_sudo=True)
    run(cfg.reload_cmd, use_sudo=True)
    print_success(f"Site '{site}' disabled and nginx reloaded.")


@nginx.command("test")
@click.pass_context
def test_config(ctx: click.Context) -> None:
    """Test nginx configuration syntax."""
    cfg = get_config(ctx).nginx
    result = run(cfg.config_test_cmd, use_sudo=True, check=False)
    if result.ok:
        print_success("Nginx configuration test passed.")
    else:
        print_error("Nginx configuration test failed.")
        raise SystemExit(1)


@nginx.command("reload")
@click.pass_context
def reload_nginx(ctx: click.Context) -> None:
    """Reload nginx configuration."""
    cfg = get_config(ctx).nginx
    run(cfg.config_test_cmd, use_sudo=True)
    run(cfg.reload_cmd, use_sudo=True)
    print_success("Nginx reloaded successfully.")


@nginx.command("cloudflare")
@click.pass_context
def cloudflare_setup(ctx: click.Context) -> None:
    """Interactive Cloudflare setup for nginx sites.

    Walks you through:

    \b
      1. Deploy Cloudflare snippet files (real IP + allow-only)
      2. Toggle real IP restoration per site
      3. Toggle Cloudflare-only access per site
      4. Install/remove the IP refresh cronjob
    """
    cfg = get_config(ctx).nginx
    sites_dir = Path(cfg.sites_available)
    snippets_dir = Path(cfg.snippets_dir)
    script_path = cfg.cloudflare_refresh_script

    if not sites_dir.exists():
        print_error(f"Sites directory not found: {sites_dir}")
        raise SystemExit(1)

    sites = _get_site_names(sites_dir)
    if not sites:
        print_error("No site configs found (excluding 'default').")
        raise SystemExit(1)

    # ── Step 1: Deploy snippet files ────────────────────────────────
    click.secho("\n── Step 1: Deploy Cloudflare snippet files ──", bold=True)
    realip_exists = (snippets_dir / "cloudflare-realip.conf").exists()
    allow_exists = (snippets_dir / "cloudflare-allow.conf").exists()

    if realip_exists and allow_exists:
        click.echo("Snippet files already deployed.")
        redeploy = click.confirm("Re-deploy with latest bundled IPs?", default=False)
    else:
        click.echo("Cloudflare snippet files not found. These provide:")
        click.echo("  - cloudflare-realip.conf  (restore real visitor IPs)")
        click.echo("  - cloudflare-allow.conf   (block non-Cloudflare traffic)")
        redeploy = click.confirm("Deploy snippet files?", default=True)

    if redeploy or not (realip_exists and allow_exists):
        run(["mkdir", "-p", str(snippets_dir)], use_sudo=True)
        realip_src = _nginx_templates() / "cloudflare-realip.conf"
        allow_src = _nginx_templates() / "cloudflare-allow.conf"
        _deploy_file(realip_src.read_text(), snippets_dir / "cloudflare-realip.conf")
        _deploy_file(allow_src.read_text(), snippets_dir / "cloudflare-allow.conf")
        print_success(f"Snippet files deployed to {snippets_dir}")

    # ── Step 2: Toggle real IP restoration per site ─────────────────
    click.secho("\n── Step 2: Cloudflare Real IP restoration ──", bold=True)
    click.echo("Restores the real visitor IP from CF-Connecting-IP header.")
    click.echo("Needed for accurate logs, rate limiting, and geo-IP.")

    to_enable, to_disable = _prompt_site_selection(
        sites, sites_dir, CF_REALIP_INCLUDE,
        "Which sites should use Cloudflare Real IP?",
    )

    for site in to_enable:
        _add_include_to_site(sites_dir / site, CF_REALIP_INCLUDE)
        print_success(f"  {site}: real IP enabled")
    for site in to_disable:
        _remove_include_from_site(sites_dir / site, CF_REALIP_INCLUDE)
        print_warning(f"  {site}: real IP disabled")

    # ── Step 3: Toggle Cloudflare-only access per site ──────────────
    click.secho("\n── Step 3: Cloudflare-only access (block direct traffic) ──", bold=True)
    click.echo("Only allows requests from Cloudflare IPs, denies all others.")
    click.echo("Use this to prevent bypassing Cloudflare via direct server IP.")

    allow_enable, allow_disable = _prompt_site_selection(
        sites, sites_dir, CF_ALLOW_INCLUDE,
        "Which sites should only accept Cloudflare traffic?",
    )

    for site in allow_enable:
        _add_include_to_site(sites_dir / site, CF_ALLOW_INCLUDE)
        print_success(f"  {site}: Cloudflare-only enabled")
    for site in allow_disable:
        _remove_include_from_site(sites_dir / site, CF_ALLOW_INCLUDE)
        print_warning(f"  {site}: Cloudflare-only disabled")

    # ── Step 4: Cronjob ─────────────────────────────────────────────
    click.secho("\n── Step 4: Cloudflare IP refresh cronjob ──", bold=True)

    any_allow = _any_site_has_cloudflare_allow(sites_dir, sites)
    cron_installed = _is_cronjob_installed(script_path)

    if any_allow:
        click.echo("At least one site restricts to Cloudflare-only IPs.")
        click.echo("A daily cronjob keeps the IP whitelist up to date.")
        if cron_installed:
            click.echo("Cronjob is already installed.")
            if click.confirm("Keep the cronjob?", default=True):
                print_success("Cronjob unchanged.")
            else:
                _remove_cronjob(script_path)
                print_warning("Cronjob removed.")
        else:
            if click.confirm("Install daily cronjob to refresh Cloudflare IPs?", default=True):
                refresh_src = _nginx_templates() / "cloudflare-refresh.sh"
                _deploy_file(refresh_src.read_text(), Path(script_path))
                run(["chmod", "+x", script_path], use_sudo=True)
                _install_cronjob(script_path)
                print_success(f"Refresh script deployed to {script_path}")
                print_success(f"Cronjob installed: {CRON_SCHEDULE} (daily at 4 AM)")
            else:
                print_warning("Cronjob not installed. Remember to refresh IPs manually.")
    else:
        click.echo("No sites are using Cloudflare-only access.")
        if cron_installed:
            click.echo("Cronjob is installed but no longer needed.")
            if click.confirm("Remove the cronjob?", default=True):
                _remove_cronjob(script_path)
                print_warning("Cronjob removed.")
        else:
            click.echo("No cronjob needed.")

    # ── Final: test & reload ────────────────────────────────────────
    changes_made = to_enable or to_disable or allow_enable or allow_disable
    if changes_made:
        click.secho("\n── Testing & reloading nginx ──", bold=True)
        result = run(cfg.config_test_cmd, use_sudo=True, check=False)
        if result.ok:
            run(cfg.reload_cmd, use_sudo=True)
            print_success("Nginx configuration valid and reloaded.")
        else:
            print_error("Nginx config test failed! Check your site configs.")
            print_error(result.stderr)
            raise SystemExit(1)

    # ── Summary ─────────────────────────────────────────────────────
    click.secho("\n── Summary ──", bold=True)
    summary_rows = []
    for site in sites:
        sp = sites_dir / site
        realip = "ON" if _site_has_include(sp, CF_REALIP_INCLUDE) else "OFF"
        allow = "ON" if _site_has_include(sp, CF_ALLOW_INCLUDE) else "OFF"
        realip_fmt = f"[green]{realip}[/green]" if realip == "ON" else f"[dim]{realip}[/dim]"
        allow_fmt = f"[green]{allow}[/green]" if allow == "ON" else f"[dim]{allow}[/dim]"
        summary_rows.append((site, realip_fmt, allow_fmt))

    print_table("Cloudflare Status", ["Site", "Real IP", "CF-Only"], summary_rows)

    cron_status = "installed" if _is_cronjob_installed(script_path) else "not installed"
    click.echo(f"Refresh cronjob: {cron_status}")
    click.echo()
