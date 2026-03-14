"""SSL certificate management — Cloudflare Origin certs and Let's Encrypt DNS-01."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import click

from bastion.output import console, print_error, print_success, print_table, print_warning
from bastion.runner import read_file_sudo, run, write_file_sudo


# ── Constants ───────────────────────────────────────────────────────

SSL_DIR = Path("/etc/ssl/bastion")
NGINX_SITES = Path("/etc/nginx/sites-enabled")

# Cloudflare API
CF_API_BASE = "https://api.cloudflare.com/client/v4"


# ── Validation ──────────────────────────────────────────────────────


def _validate_domain(domain: str) -> None:
    """Validate domain name format."""
    pattern = r'^(\*\.)?[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
    if not re.match(pattern, domain):
        print_error(f"Invalid domain: {domain}")
        raise SystemExit(1)


def _validate_api_token(token: str) -> None:
    """Basic validation of Cloudflare API token format."""
    if not token or len(token) < 20:
        print_error("Invalid Cloudflare API token.")
        raise SystemExit(1)


# ── Token management ───────────────────────────────────────────────

TOKEN_FILE = Path.home() / ".config" / "bastion" / "cloudflare-token"


def _save_token(token: str) -> None:
    """Save Cloudflare API token to config."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    TOKEN_FILE.chmod(0o600)


def _load_token() -> str | None:
    """Load saved Cloudflare API token."""
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    return None


def _get_token(token: str | None) -> str:
    """Get token from flag, saved file, or prompt."""
    if token:
        return token
    saved = _load_token()
    if saved:
        return saved
    print_error(
        "No Cloudflare API token provided.\n"
        "  Pass --token <TOKEN> or run: bastion ssl cloudflare-token --set <TOKEN>\n"
        "  Create one at: https://dash.cloudflare.com/profile/api-tokens\n"
        "  Required permissions: Zone.SSL and Certificates, Zone.Zone (read)"
    )
    raise SystemExit(1)


# ── Cloudflare API helpers ──────────────────────────────────────────


def _cf_request(
    method: str,
    endpoint: str,
    token: str,
    data: dict | None = None,
) -> dict:
    """Make a Cloudflare API request using curl."""
    url = f"{CF_API_BASE}{endpoint}"
    cmd = [
        "curl", "-s", "-X", method, url,
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
    ]
    if data:
        cmd.extend(["-d", json.dumps(data)])

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        print_error(f"API request failed: {proc.stderr}")
        raise SystemExit(1)

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print_error(f"Invalid API response: {proc.stdout[:200]}")
        raise SystemExit(1)

    if not result.get("success", False):
        errors = result.get("errors", [])
        msg = errors[0].get("message", "Unknown error") if errors else "Unknown error"
        print_error(f"Cloudflare API error: {msg}")
        raise SystemExit(1)

    return result


def _get_zone_id(domain: str, token: str) -> str:
    """Get Cloudflare zone ID for a domain."""
    # Extract root domain (e.g., sub.example.com -> example.com)
    parts = domain.split(".")
    if parts[0] == "*":
        parts = parts[1:]
    # Try from most specific to root
    for i in range(len(parts) - 1):
        zone_name = ".".join(parts[i:])
        result = _cf_request("GET", f"/zones?name={zone_name}", token)
        zones = result.get("result", [])
        if zones:
            return zones[0]["id"]

    print_error(f"Zone not found for domain: {domain}")
    raise SystemExit(1)


# ── Certificate helpers ─────────────────────────────────────────────


def _cert_dir(domain: str) -> Path:
    """Get the cert storage directory for a domain."""
    return SSL_DIR / domain.replace("*.", "wildcard.")


def _cert_paths(domain: str) -> tuple[Path, Path]:
    """Return (cert_path, key_path) for a domain."""
    d = _cert_dir(domain)
    return d / "cert.pem", d / "key.pem"


def _get_cert_expiry(cert_path: Path) -> datetime | None:
    """Get expiry date from a certificate file."""
    result = run(
        ["sudo", "openssl", "x509", "-enddate", "-noout", "-in", str(cert_path)],
        use_sudo=False, check=False, timeout=10,
    )
    if not result.ok or "notAfter=" not in result.stdout:
        return None
    date_str = result.stdout.split("notAfter=")[1].strip()
    try:
        return datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _get_cert_subject(cert_path: Path) -> str:
    """Get CN/SAN from a certificate file."""
    result = run(
        ["sudo", "openssl", "x509", "-subject", "-noout", "-in", str(cert_path)],
        use_sudo=False, check=False, timeout=10,
    )
    if result.ok and "CN" in result.stdout:
        match = re.search(r'CN\s*=\s*(.+)', result.stdout)
        return match.group(1).strip() if match else ""
    return ""


def _get_cert_issuer(cert_path: Path) -> str:
    """Get issuer from a certificate file."""
    result = run(
        ["sudo", "openssl", "x509", "-issuer", "-noout", "-in", str(cert_path)],
        use_sudo=False, check=False, timeout=10,
    )
    if result.ok:
        if "Cloudflare" in result.stdout:
            return "Cloudflare Origin"
        if "Let's Encrypt" in result.stdout or "R3" in result.stdout or "R10" in result.stdout or "R11" in result.stdout:
            return "Let's Encrypt"
        match = re.search(r'O\s*=\s*([^,/]+)', result.stdout)
        return match.group(1).strip() if match else "Unknown"
    return "Unknown"


# ── Commands ────────────────────────────────────────────────────────


@click.group("ssl")
def ssl() -> None:
    """SSL certificate management."""


# ── Token ───────────────────────────────────────────────────────────


@ssl.command("cloudflare-token")
@click.option("--set", "set_token", default=None, help="Save Cloudflare API token.")
@click.option("--show", is_flag=True, help="Show saved token (masked).")
@click.option("--remove", is_flag=True, help="Remove saved token.")
def manage_token(set_token: str | None, show: bool, remove: bool) -> None:
    """Manage Cloudflare API token.

    \b
    Save token:
      bastion ssl cloudflare-token --set <TOKEN>

    \b
    Show saved token:
      bastion ssl cloudflare-token --show

    \b
    Create a token at:
      https://dash.cloudflare.com/profile/api-tokens
      Required permissions: Zone.SSL and Certificates, Zone.Zone (read)
    """
    if set_token:
        _validate_api_token(set_token)
        _save_token(set_token)
        print_success(f"Token saved to {TOKEN_FILE}")
    elif show:
        saved = _load_token()
        if saved:
            masked = saved[:4] + "*" * (len(saved) - 8) + saved[-4:]
            click.echo(f"Token: {masked}")
            click.echo(f"File:  {TOKEN_FILE}")
        else:
            print_warning("No token saved. Run: bastion ssl cloudflare-token --set <TOKEN>")
    elif remove:
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
            print_success("Token removed.")
        else:
            print_warning("No token to remove.")
    else:
        # Default: show status
        if _load_token():
            print_success(f"Token configured at {TOKEN_FILE}")
        else:
            print_warning("No token saved. Run: bastion ssl cloudflare-token --set <TOKEN>")


# ── Origin Certificate ──────────────────────────────────────────────


@ssl.command("cloudflare-origin")
@click.argument("domain")
@click.option("--wildcard/--no-wildcard", default=True, show_default=True,
              help="Include wildcard (*.domain.com) in the certificate.")
@click.option("--validity", type=click.Choice(["7", "30", "90", "365", "730", "1095", "5475"]),
              default="5475", show_default=True,
              help="Certificate validity in days (5475 = 15 years).")
@click.option("--token", default=None, help="Cloudflare API token.", envvar="CF_API_TOKEN")
@click.option("--install/--no-install", default=True, show_default=True,
              help="Install the cert to nginx after creating it.")
def cloudflare_origin_cert(
    domain: str,
    wildcard: bool,
    validity: str,
    token: str | None,
    install: bool,
) -> None:
    """Create a Cloudflare Origin certificate for a domain.

    Origin certificates are trusted by Cloudflare's edge servers and provide
    end-to-end encryption between Cloudflare and your origin server.
    They are free, can last up to 15 years, and require zero renewal.

    \b
    Basic usage (15-year cert with wildcard):
      bastion ssl cloudflare-origin example.com

    \b
    Without wildcard:
      bastion ssl cloudflare-origin example.com --no-wildcard

    \b
    Short-lived cert:
      bastion ssl cloudflare-origin example.com --validity 365
    """
    _validate_domain(domain)
    token = _get_token(token)

    # Build hostnames list
    hostnames = [domain]
    if wildcard and not domain.startswith("*."):
        hostnames.append(f"*.{domain}")

    console.print(f"\n[bold]Creating Cloudflare Origin certificate[/bold]")
    console.print(f"  Domain:    {domain}")
    console.print(f"  Hostnames: {', '.join(hostnames)}")
    console.print(f"  Validity:  {validity} days")
    console.print()

    # Get zone ID
    zone_id = _get_zone_id(domain, token)

    # Check for existing cert
    cert_path, key_path = _cert_paths(domain)
    if cert_path.exists():
        expiry = _get_cert_expiry(cert_path)
        if expiry:
            days_left = (expiry - datetime.now(timezone.utc)).days
            if days_left > 30:
                print_warning(f"Certificate already exists ({days_left} days remaining).")
                if not click.confirm("Replace it?", default=False):
                    return

    # Create origin certificate via API
    click.echo("Requesting certificate from Cloudflare...")
    data = {
        "hostnames": hostnames,
        "requested_validity": int(validity),
        "request_type": "origin-rsa",
        "csr": "",  # Let Cloudflare generate the key pair
    }
    result = _cf_request("POST", f"/certificates", token, data)

    cert_data = result.get("result", {})
    certificate = cert_data.get("certificate", "")
    private_key = cert_data.get("private_key", "")
    cert_id = cert_data.get("id", "")

    if not certificate or not private_key:
        print_error("API returned empty certificate or key.")
        raise SystemExit(1)

    # Save cert and key
    cert_dir = _cert_dir(domain)
    run(["mkdir", "-p", str(cert_dir)], use_sudo=True)
    write_file_sudo(cert_path, certificate)
    write_file_sudo(key_path, private_key)
    # Restrict key permissions
    run(["chmod", "600", str(key_path)], use_sudo=True)
    run(["chmod", "644", str(cert_path)], use_sudo=True)

    print_success(f"Certificate saved to {cert_path}")
    print_success(f"Private key saved to {key_path}")

    # Save cert ID for future revocation
    id_path = cert_dir / "cert-id"
    write_file_sudo(id_path, cert_id)

    # Install to nginx if requested
    if install:
        _install_cert_nginx(domain, cert_path, key_path)

    console.print()
    console.print("[bold green]Done![/bold green] Your origin certificate is ready.")
    console.print()
    console.print("[dim]Remember to set SSL mode to 'Full (strict)' in Cloudflare dashboard:[/dim]")
    console.print(f"[dim]  https://dash.cloudflare.com → {domain} → SSL/TLS → Full (strict)[/dim]")
    console.print()


# ── Let's Encrypt DNS-01 ────────────────────────────────────────────


@ssl.command("certbot-dns")
@click.argument("domain")
@click.option("--wildcard/--no-wildcard", default=False, show_default=True,
              help="Request a wildcard certificate (*.domain.com).")
@click.option("--token", default=None, help="Cloudflare API token.", envvar="CF_API_TOKEN")
@click.option("--email", default=None, help="Email for Let's Encrypt notifications.")
@click.option("--install/--no-install", default=True, show_default=True,
              help="Install the cert to nginx after creating it.")
def certbot_dns(
    domain: str,
    wildcard: bool,
    token: str | None,
    email: str | None,
    install: bool,
) -> None:
    """Get a Let's Encrypt certificate via Cloudflare DNS-01 challenge.

    Uses certbot with the Cloudflare DNS plugin to validate domain ownership
    without HTTP. Works behind Cloudflare proxy.

    Requires certbot and python3-certbot-dns-cloudflare packages.

    \b
    Basic usage:
      bastion ssl certbot-dns example.com --email admin@example.com

    \b
    Wildcard cert:
      bastion ssl certbot-dns example.com --wildcard --email admin@example.com
    """
    _validate_domain(domain)
    token = _get_token(token)

    # Check certbot is installed
    if not run(["which", "certbot"], check=False, timeout=5).ok:
        print_error(
            "certbot is not installed.\n"
            "  Install: sudo apt-get install certbot python3-certbot-dns-cloudflare"
        )
        raise SystemExit(1)

    # Check dns plugin
    result = run(["certbot", "plugins", "--prepare"], use_sudo=True, check=False, timeout=15)
    if "dns-cloudflare" not in result.stdout:
        print_error(
            "certbot-dns-cloudflare plugin not found.\n"
            "  Install: sudo apt-get install python3-certbot-dns-cloudflare"
        )
        raise SystemExit(1)

    # Build domains
    domains = [f"-d {domain}"]
    if wildcard:
        domains.append(f"-d *.{domain}")

    console.print(f"\n[bold]Requesting Let's Encrypt certificate (DNS-01)[/bold]")
    console.print(f"  Domain:    {domain}")
    if wildcard:
        console.print(f"  Wildcard:  *.{domain}")
    console.print()

    # Write cloudflare credentials file (temp, restricted)
    creds_file = Path("/tmp/bastion-cf-creds.ini")
    creds_content = f"dns_cloudflare_api_token = {token}\n"
    creds_file.write_text(creds_content)
    creds_file.chmod(0o600)

    try:
        # Build certbot command
        cmd = [
            "certbot", "certonly",
            "--dns-cloudflare",
            "--dns-cloudflare-credentials", str(creds_file),
            "--dns-cloudflare-propagation-seconds", "30",
            *[part for d in domains for part in d.split()],
            "--non-interactive",
            "--agree-tos",
        ]
        if email:
            cmd.extend(["--email", email])
        else:
            cmd.append("--register-unsafely-without-email")

        result = run(cmd, use_sudo=True, check=False, timeout=120)

        if not result.ok:
            print_error(f"Certbot failed: {result.stderr or result.stdout}")
            raise SystemExit(1)

        print_success("Certificate obtained successfully!")

        # Cert location
        cert_path = Path(f"/etc/letsencrypt/live/{domain}/fullchain.pem")
        key_path = Path(f"/etc/letsencrypt/live/{domain}/privkey.pem")

        if install:
            _install_cert_nginx(domain, cert_path, key_path)

        console.print()
        console.print("[bold green]Done![/bold green] Certificate will auto-renew via certbot timer.")
        console.print()

    finally:
        # Always clean up credentials
        if creds_file.exists():
            creds_file.unlink()


# ── Nginx integration ──────────────────────────────────────────────


def _install_cert_nginx(domain: str, cert_path: Path, key_path: Path) -> None:
    """Update nginx site config to use the given cert/key."""
    # Find matching nginx config
    site_config = _find_nginx_config(domain)
    if not site_config:
        print_warning(
            f"No nginx config found for {domain}.\n"
            f"  Add these lines to your nginx server block:\n"
            f"    ssl_certificate     {cert_path};\n"
            f"    ssl_certificate_key {key_path};"
        )
        return

    console.print(f"  Updating nginx config: {site_config}")

    content = read_file_sudo(site_config)

    # Replace existing ssl_certificate directives
    new_content = content
    cert_pattern = r'(\s*)ssl_certificate\s+[^;]+;'
    key_pattern = r'(\s*)ssl_certificate_key\s+[^;]+;'

    if re.search(cert_pattern, new_content):
        new_content = re.sub(
            cert_pattern,
            rf'\1ssl_certificate {cert_path};',
            new_content,
        )
        new_content = re.sub(
            key_pattern,
            rf'\1ssl_certificate_key {key_path};',
            new_content,
        )
        print_success(f"Updated SSL paths in {site_config.name}")
    else:
        # No existing SSL directives — add them after listen 443
        listen_pattern = r'(listen\s+[^;]*443[^;]*;)'
        match = re.search(listen_pattern, new_content)
        if match:
            insert = (
                f"\n    ssl_certificate {cert_path};"
                f"\n    ssl_certificate_key {key_path};"
            )
            pos = match.end()
            new_content = new_content[:pos] + insert + new_content[pos:]
            print_success(f"Added SSL directives to {site_config.name}")
        else:
            print_warning(
                f"Could not find listen 443 in {site_config.name}.\n"
                f"  Manually add:\n"
                f"    ssl_certificate     {cert_path};\n"
                f"    ssl_certificate_key {key_path};"
            )
            return

    if new_content != content:
        write_file_sudo(site_config, new_content)

        # Test and reload nginx
        test_result = run(["nginx", "-t"], use_sudo=True, check=False, timeout=10)
        if test_result.ok:
            run(["systemctl", "reload", "nginx"], use_sudo=True, timeout=10)
            print_success("Nginx reloaded with new certificate.")
        else:
            print_error(f"Nginx config test failed: {test_result.stderr}")
            print_warning("Restoring previous config...")
            write_file_sudo(site_config, content)
            raise SystemExit(1)


def _find_nginx_config(domain: str) -> Path | None:
    """Find the nginx config file that serves a given domain."""
    sites_available = Path("/etc/nginx/sites-available")
    if not sites_available.exists():
        return None

    # Search for server_name matching the domain
    result = run(
        ["bash", "-c",
         f"grep -rl 'server_name.*{re.escape(domain)}' /etc/nginx/sites-available/ 2>/dev/null"],
        check=False, timeout=10,
    )
    if result.ok and result.stdout.strip():
        # Return first match
        return Path(result.stdout.strip().splitlines()[0])
    return None


# ── Status ──────────────────────────────────────────────────────────


@ssl.command("status")
def ssl_status() -> None:
    """Show all SSL certificates and their expiry status.

    \b
    Scans:
      - /etc/ssl/bastion/ (origin certs managed by bastion)
      - /etc/letsencrypt/live/ (certbot certs)
      - nginx site configs for other cert paths
    """
    console.print()
    rows: list[tuple[str, ...]] = []
    seen_paths: set[str] = set()
    now = datetime.now(timezone.utc)

    # 1. Bastion-managed certs
    result = run(["bash", "-c", f"ls -d {SSL_DIR}/*/cert.pem 2>/dev/null"],
                 check=False, timeout=5)
    if result.ok and result.stdout.strip():
        for cert_file in result.stdout.strip().splitlines():
            cert_path = Path(cert_file.strip())
            if str(cert_path) in seen_paths:
                continue
            seen_paths.add(str(cert_path))
            _add_cert_row(rows, cert_path, now)

    # 2. Let's Encrypt certs
    result = run(["bash", "-c", "ls -d /etc/letsencrypt/live/*/fullchain.pem 2>/dev/null"],
                 check=False, timeout=5)
    if result.ok and result.stdout.strip():
        for cert_file in result.stdout.strip().splitlines():
            cert_path = Path(cert_file.strip())
            if str(cert_path) in seen_paths:
                continue
            seen_paths.add(str(cert_path))
            _add_cert_row(rows, cert_path, now)

    # 3. Certs referenced in nginx configs
    result = run(
        ["bash", "-c",
         "grep -rh 'ssl_certificate ' /etc/nginx/sites-enabled/ 2>/dev/null | "
         "awk '{print $2}' | tr -d ';' | sort -u"],
        check=False, timeout=10,
    )
    if result.ok and result.stdout.strip():
        for cert_file in result.stdout.strip().splitlines():
            cert_path = Path(cert_file.strip())
            if str(cert_path) in seen_paths:
                continue
            seen_paths.add(str(cert_path))
            _add_cert_row(rows, cert_path, now)

    if rows:
        print_table(
            "SSL Certificates",
            ["Domain", "Issuer", "Expires In", "Expiry Date", "Path"],
            rows,
        )
    else:
        click.echo("No SSL certificates found.")
    console.print()


def _add_cert_row(
    rows: list[tuple[str, ...]],
    cert_path: Path,
    now: datetime,
) -> None:
    """Add a certificate row to the status table."""
    subject = _get_cert_subject(cert_path) or cert_path.parent.name
    issuer = _get_cert_issuer(cert_path)
    expiry = _get_cert_expiry(cert_path)

    if expiry:
        days_left = (expiry - now).days
        if days_left < 0:
            expires_in = f"[bold red]EXPIRED {abs(days_left)}d ago[/bold red]"
        elif days_left <= 7:
            expires_in = f"[bold red]{days_left} days[/bold red]"
        elif days_left <= 30:
            expires_in = f"[yellow]{days_left} days[/yellow]"
        else:
            expires_in = f"[green]{days_left} days[/green]"
        expiry_date = expiry.strftime("%Y-%m-%d")
    else:
        expires_in = "[dim]unknown[/dim]"
        expiry_date = "-"

    rows.append((subject, issuer, expires_in, expiry_date, str(cert_path)))


# ── Revoke / Remove ────────────────────────────────────────────────


@ssl.command("cloudflare-revoke")
@click.argument("domain")
@click.option("--token", default=None, help="Cloudflare API token.", envvar="CF_API_TOKEN")
@click.option("--delete/--no-delete", default=False, help="Delete cert files after revoking.")
def revoke_cert(domain: str, token: str | None, delete: bool) -> None:
    """Revoke a Cloudflare Origin or Let's Encrypt certificate.

    \b
    Revoke and delete:
      bastion ssl cloudflare-revoke example.com --delete
    """
    _validate_domain(domain)

    cert_path, key_path = _cert_paths(domain)
    id_path = _cert_dir(domain) / "cert-id"

    if not cert_path.exists():
        print_error(f"No certificate found for {domain}")
        raise SystemExit(1)

    issuer = _get_cert_issuer(cert_path)

    if issuer == "Let's Encrypt":
        # Use certbot to revoke
        click.echo(f"Revoking Let's Encrypt certificate for {domain}...")
        result = run(
            ["certbot", "revoke", "--cert-name", domain, "--non-interactive"],
            use_sudo=True, check=False, timeout=60,
        )
        if result.ok:
            print_success(f"Let's Encrypt certificate revoked for {domain}")
        else:
            print_error(f"Revoke failed: {result.stderr or result.stdout}")
            raise SystemExit(1)

    elif issuer == "Cloudflare Origin":
        # Revoke via Cloudflare API
        token = _get_token(token)
        cert_id = ""
        if id_path.exists():
            cert_id = read_file_sudo(id_path).strip()

        if cert_id:
            click.echo(f"Revoking Cloudflare Origin certificate for {domain}...")
            _cf_request("DELETE", f"/certificates/{cert_id}", token)
            print_success(f"Origin certificate revoked for {domain}")
        else:
            print_warning("Certificate ID not found. Cannot revoke via API.")
            print_warning("Revoke manually at: https://dash.cloudflare.com → SSL/TLS → Origin Server")

    if delete:
        cert_dir = _cert_dir(domain)
        run(["rm", "-rf", str(cert_dir)], use_sudo=True, timeout=10)
        print_success(f"Certificate files deleted: {cert_dir}")


# ── Renew ───────────────────────────────────────────────────────────


@ssl.command("certbot-renew")
def renew_certs() -> None:
    """Renew all Let's Encrypt certificates.

    Runs certbot renew. Origin certs don't need renewal (15-year validity).
    """
    if not run(["which", "certbot"], check=False, timeout=5).ok:
        click.echo("certbot not installed — no Let's Encrypt certs to renew.")
        return

    click.echo("Renewing Let's Encrypt certificates...")
    result = run(
        ["certbot", "renew", "--non-interactive"],
        use_sudo=True, check=False, timeout=120,
    )
    if result.ok:
        click.echo(result.stdout)
        print_success("Renewal check complete.")
    else:
        print_error(f"Renewal failed: {result.stderr or result.stdout}")
        raise SystemExit(1)
