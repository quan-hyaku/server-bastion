# bastion

A CLI tool for Linux server administration. Wraps common sysadmin tasks — nginx, PostgreSQL, firewall (ufw), fail2ban, and system tuning — into a single unified interface with dry-run support, YAML config profiles, and colored output.

## Install

**On an Ubuntu/Debian server:**

```bash
bash install.sh
```

This installs prerequisites (curl, git, python3), uv, and bastion as a user-local command (`~/.local/bin`). No root needed for the tool itself — sudo is used only for system prerequisites and the commands bastion runs (nginx, ufw, etc.).

**From a git repo:**

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/quan-hyaku/server-bastion/main/install.sh) --repo https://github.com/quan-hyaku/server-bastion.git
```

**With passwordless sudo for bastion commands:**

```bash
bash install.sh --sudoers
```

This creates `/etc/sudoers.d/bastion` allowing your user to run nginx, ufw, fail2ban, postgres, and sysctl commands without a password prompt.

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
| `--json` | One JSON document on stdout, human output on stderr (see below) |

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

### health, audit, ssl, malware

```bash
bastion health status                        # uptime, disk, memory, services, certs, updates
bastion audit run                            # SSH/firewall/fail2ban/ports/permissions checks + score
bastion ssl status                           # all certs with expiry
bastion ssl cloudflare-token --set           # prompts for the token without echo
bastion ssl cloudflare-origin example.com    # 15-year Cloudflare Origin cert, installed into nginx
bastion ssl certbot-dns example.com --email you@example.com   # Let's Encrypt via DNS-01
bastion malware install                      # ClamAV + Linux Malware Detect
bastion malware scan /home --background      # throttled scan, log in /var/log/bastion/
bastion malware schedule --scan daily --update daily
bastion self-update                          # git pull + reinstall (--repo installs only)
```

## JSON output (integrating from another app)

Add `--json` before the command. stdout then carries exactly one JSON object and all
human output moves to stderr; the exit code is unchanged (0 ok, 1 failure, 2 usage).

```bash
bastion --json health status
bastion --json audit run
bastion --json fail2ban list-jails
bastion --json nginx enable example.com        # write commands report ok + messages
bastion --json malware uninstall --lmd --force # pass --force; prompts are not JSON-friendly
```

Shape:

```json
{
  "ok": true,
  "messages": [{"level": "success", "text": "Site 'example.com' enabled and nginx reloaded."}],
  "tables":   [{"name": "fail2ban_jails", "title": "Fail2ban Jails",
                "columns": ["Jail"], "rows": [{"Jail": "sshd"}]}],
  "data":     {}
}
```

Every table a command prints appears in `tables` with Rich markup stripped. `audit run`
adds `data.checks` (category, name, status pass/fail/skip, detail, fix) and `data.score`;
`health status` adds `data.system` (host, uptime, cpus, load, failed SSH logins, banned
IPs, pending/security updates).

**Keep bastion off the network.** It runs commands with sudo, so do not wrap it in an HTTP
server. When your application runs on the same host, call it as a subprocess and parse stdout:

```python
import json, subprocess

proc = subprocess.run(
    ["bastion", "--json", "health", "status"],
    capture_output=True, text=True, timeout=120,
)
report = json.loads(proc.stdout)      # always present, even on failure
if not report["ok"]:
    raise RuntimeError(report["messages"][-1]["text"])
```

Two things the calling account needs:

- **Passwordless sudo for the commands bastion runs**, otherwise `sudo` waits for a password
  and the subprocess hangs. Run `bash install.sh --sudoers` *as the account your app runs
  under* (for example `www-data` or a dedicated `bastion-app` user). That account is then
  effectively root for those commands, so treat it accordingly.
- **Non-interactive flags** (`--force`, `--no-install`, ...) on commands that would prompt.

### Calling from a Docker container

Bastion manages the **host** (nginx, ufw, fail2ban, systemd, apt). Installing it inside a
container does nothing useful, and giving the container the Docker socket or `--privileged`
to reach the host is a bigger hole than anything bastion protects. Do not pass bastion into
the container. Bridge out to the host over SSH with a key that can only run an allowlist:

1. On the host, create a low-privilege account for the app and give it bastion's sudoers
   entries: `sudo -u bastion-app bash install.sh --sudoers`.

2. Install a forced-command gateway at `/usr/local/bin/bastion-gateway` (owned by root, `0755`):

   ```bash
   #!/bin/bash
   # Only ever runs `bastion --json <allowed subcommand>`; the caller cannot pick anything else.
   set -euo pipefail
   read -ra args <<< "${SSH_ORIGINAL_COMMAND:-}"
   case "${args[0]:-} ${args[1]:-}" in
     "health status"|"audit run"|"ssl status"|"nginx list-sites"|"fail2ban list-jails") ;;
     *) echo '{"ok": false, "messages": [{"level": "error", "text": "command not allowed"}]}'; exit 1 ;;
   esac
   exec bastion --json "${args[@]}"
   ```

3. In `/home/bastion-app/.ssh/authorized_keys`, pin the container's key to that gateway:

   ```
   command="/usr/local/bin/bastion-gateway",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty ssh-ed25519 AAAA... app
   ```

4. From the container, call the host through the bridge gateway (`host.docker.internal` on
   Docker Desktop; on Linux add `--add-host=host.docker.internal:host-gateway` to `docker run`
   or `extra_hosts: ["host.docker.internal:host-gateway"]` in compose) with the private key
   mounted read-only:

   ```python
   proc = subprocess.run(
       ["ssh", "-i", "/run/secrets/bastion_key", "-o", "StrictHostKeyChecking=accept-new",
        "bastion-app@host.docker.internal", "health", "status"],
       capture_output=True, text=True, timeout=120,
   )
   report = json.loads(proc.stdout)
   ```

Extend the allowlist in step 2 as your app needs more commands. If sshd must not listen on
the bridge, bind it to the bridge address only (`ListenAddress 172.17.0.1`) and keep ufw's
rule for port 22 scoped to that source.

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
bastion --profile ~/.config/bastion/profile.yaml nginx list-sites
```

A default profile is created at `~/.config/bastion/profile.yaml` by the installer.

## Project Structure

```
src/bastion/
├── cli.py              # root CLI group + global options
├── config.py           # YAML config loader + typed dataclasses
├── runner.py           # subprocess wrapper (sudo, dry-run, logging)
├── output.py           # Rich terminal output helpers
├── commands/
│   ├── audit.py        # security audit checks + score
│   ├── health.py       # health dashboard
│   ├── malware.py      # ClamAV + LMD install/scan/schedule
│   ├── nginx.py        # nginx + cloudflare commands
│   ├── postgres.py     # postgres commands
│   ├── ssl.py          # Cloudflare Origin + Let's Encrypt certs
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
