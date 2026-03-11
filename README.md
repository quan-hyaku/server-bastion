# bastion

A CLI tool for Linux server administration. Wraps common sysadmin tasks — nginx, PostgreSQL, firewall (ufw), fail2ban, and system tuning — into a single unified interface with dry-run support, YAML config profiles, and colored output.

## Install

**On an Ubuntu/Debian server (full install):**

```bash
sudo bash install.sh
```

This installs all system dependencies (nginx, postgres, ufw, fail2ban), uv, and bastion as a system-wide command. See [install.sh](install.sh) for details.

**From a git repo:**

```bash
sudo bash install.sh --repo https://github.com/you/bastion.git
```

**Development (local):**

```bash
uv sync
uv run bastion --help
```

## Usage

```
bastion [--dry-run] [--profile PATH] [--verbose] COMMAND
```

| Flag | Description |
|---|---|
| `--dry-run` | Print commands without executing them |
| `--profile PATH` | Load a YAML config profile |
| `-v, --verbose` | Show each shell command as it runs |

Always test with `--dry-run` first on a production server.

## Commands

### nginx

Manage nginx sites and Cloudflare integration.

```bash
bastion nginx list-sites                     # list sites with enabled/disabled status
bastion nginx enable example.com             # enable site + test + reload
bastion nginx disable example.com            # disable site + reload
bastion nginx test                           # run nginx -t
bastion nginx reload                         # test then reload

bastion nginx cloudflare                     # interactive Cloudflare setup
```

**Cloudflare setup** walks you through:
1. Deploying Cloudflare IP snippet files to `/etc/nginx/snippets/`
2. Toggling real IP restoration per site (CF-Connecting-IP header)
3. Toggling Cloudflare-only access per site (deny non-CF traffic)
4. Installing a daily cronjob to refresh Cloudflare IP ranges

### postgres

Manage PostgreSQL databases.

```bash
bastion postgres status                      # check if postgres is running
bastion postgres list-dbs                     # list databases with sizes
bastion postgres create-db myapp             # create a database
bastion postgres create-db myapp --owner app # create with specific owner
bastion postgres drop-db myapp --force       # drop without confirmation
bastion postgres backup myapp                # pg_dump to /var/backups/postgresql/
bastion postgres backup myapp -o /tmp/db.sql # pg_dump to custom path
```

### firewall

Manage UFW firewall rules.

```bash
bastion firewall status                      # ufw status verbose
bastion firewall allow 443                   # allow 443/tcp
bastion firewall allow 53 --proto udp        # allow 53/udp
bastion firewall deny 3306                   # deny 3306/tcp
bastion firewall list-rules                  # numbered rule list
```

### fail2ban

Manage fail2ban jails, bans, and deploy bundled jail configs.

```bash
bastion fail2ban status                      # overall status
bastion fail2ban status --jail sshd          # status for a specific jail
bastion fail2ban list-jails                  # list active jails
bastion fail2ban ban 1.2.3.4 --jail sshd     # ban an IP
bastion fail2ban unban 1.2.3.4 --jail sshd   # unban an IP
```

**Bundled jails** — deploy pre-configured jail + filter configs:

```bash
bastion fail2ban show-config sshd            # preview config before deploying
bastion fail2ban setup sshd                  # deploy SSH brute-force jail
bastion fail2ban setup nginx-script-scan     # block .php/.env/.git scanners
bastion fail2ban setup --all                 # deploy all 4 jails
bastion fail2ban remove-jail sshd --force    # remove a deployed jail
```

| Jail | What it catches | Ban trigger | Ban duration |
|---|---|---|---|
| `sshd` | SSH brute-force attempts | 5 failures / 10min | 1h (doubles on repeat) |
| `nginx-script-scan` | Requests for .php, .env, .git, wp-admin, etc. | 3 hits / 10min | 24h |
| `nginx-http-auth` | Nginx basic auth brute-force | 5 failures / 10min | 1h |
| `nginx-botsearch` | 404s on scanner paths (phpmyadmin, cgi-bin, etc.) | 2 hits / 10min | 24h |

### tune

System kernel and limits tuning.

```bash
bastion tune show                            # show common sysctl values
bastion tune show --section limits           # show limits.conf
bastion tune sysctl vm.swappiness 10         # set a sysctl value
bastion tune limits '*' nofile 65535         # set a limits.conf entry
bastion tune apply webserver                 # apply bundled preset
bastion tune apply database --force          # apply without confirmation
```

**Bundled presets:**
- `webserver` — optimized for nginx + high-concurrency HTTP
- `database` — optimized for PostgreSQL workloads

## Config Profiles

Create a YAML file to override defaults for your server. All fields are optional.

```yaml
name: "production"
description: "Production web server"

nginx:
  sites_available: "/etc/nginx/sites-available"
  sites_enabled: "/etc/nginx/sites-enabled"
  snippets_dir: "/etc/nginx/snippets"

postgres:
  host: "localhost"
  port: 5432
  user: "postgres"
  backup_dir: "/var/backups/postgresql"

firewall:
  backend: "ufw"

fail2ban:
  client_cmd: "fail2ban-client"
  jail_dir: "/etc/fail2ban/jail.d"
  filter_dir: "/etc/fail2ban/filter.d"

tune:
  sysctl_conf: "/etc/sysctl.d/99-bastion.conf"
  limits_conf: "/etc/security/limits.d/99-bastion.conf"
```

Use it with:
```bash
bastion --profile /etc/bastion/profile.yaml nginx list-sites
```

A default profile is created at `/etc/bastion/profile.yaml` by the installer.

## Project Structure

```
src/bastion/
├── cli.py              # root CLI group + global options
├── config.py           # YAML config loader + typed dataclasses
├── runner.py           # subprocess wrapper (sudo, dry-run, logging)
├── output.py           # Rich terminal output helpers
├── commands/
│   ├── nginx.py        # nginx + cloudflare commands
│   ├── postgres.py     # postgres commands
│   ├── firewall.py     # ufw commands
│   ├── fail2ban.py     # fail2ban commands + jail deployment
│   └── tune.py         # sysctl + limits tuning
├── templates/
│   ├── fail2ban/       # bundled jail + filter configs
│   └── nginx/          # cloudflare snippet templates + refresh script
└── profiles/           # bundled tuning presets (webserver, database)
```

## Development

```bash
uv sync                  # install deps
uv run pytest            # run tests
uv run pytest -v         # verbose test output
uv run bastion --help  # run locally
```

## Requirements

- Python >= 3.11
- Ubuntu/Debian (tested on Ubuntu 22.04+)
- System tools: nginx, postgresql, ufw, fail2ban (installed by `install.sh`)
- Most commands require sudo access
