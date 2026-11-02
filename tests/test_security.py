"""Regression checks for the security hardening: one small test per guard."""

from __future__ import annotations

import io
import json
import stat
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import importlib

from bastion.cli import cli

# bastion.commands re-exports the click groups under the module names, so go via importlib
malware = importlib.import_module("bastion.commands.malware")
nginx = importlib.import_module("bastion.commands.nginx")
postgres = importlib.import_module("bastion.commands.postgres")
ssl = importlib.import_module("bastion.commands.ssl")
from bastion.runner import RunResult, write_file_sudo

OK = RunResult(command="mocked", returncode=0, stdout="", stderr="")


# ── path traversal guards ───────────────────────────────────────────

@pytest.mark.parametrize("site", ["../../etc/passwd", "a/b", ".", ".."])
def test_nginx_rejects_site_paths(site):
    with patch("bastion.commands.nginx.run") as mock_run:
        result = CliRunner().invoke(cli, ["nginx", "disable", site])
    assert result.exit_code == 1
    mock_run.assert_not_called()


def test_fail2ban_rejects_jail_path():
    with patch("bastion.commands.fail2ban.run") as mock_run:
        result = CliRunner().invoke(cli, ["fail2ban", "remove-jail", "../nginx/nginx", "--force"])
    assert result.exit_code == 1
    mock_run.assert_not_called()


# ── LMD conf.maldet is sourced by bash as root ──────────────────────

@pytest.mark.parametrize("value", ['$(id)', '`id`', 'a"; rm -rf /; #', "x\ny", "back\\slash"])
def test_lmd_config_rejects_shell_metachars(value):
    with patch("bastion.commands.malware.write_file_sudo") as mock_write:
        with pytest.raises(SystemExit):
            malware._set_lmd_config("email_addr", value)
    mock_write.assert_not_called()


def test_lmd_config_rejects_bad_key():
    with pytest.raises(SystemExit):
        malware._set_lmd_config("x; id", "1")


def test_lmd_config_writes_plain_value():
    with patch("bastion.commands.malware.read_file_sudo", return_value='email_addr="old"\n'), \
         patch("bastion.commands.malware.write_file_sudo") as mock_write:
        malware._set_lmd_config("email_addr", "admin@example.com")
    assert mock_write.call_args[0][1] == 'email_addr="admin@example.com"\n'


def test_lmd_staging_dir_not_in_tmp():
    assert not malware.LMD_STAGING_DIR.startswith("/tmp")


# ── crontab edited in Python, not via bash -c string interpolation ──

def test_cron_install_replaces_existing_entry_without_shell():
    existing = "0 1 * * * /other/job\n# bastion: cloudflare IP refresh\n0 4 * * * /usr/local/bin/x\n"
    calls = []

    def fake_run(args, **kw):
        calls.append((list(args), kw))
        if args[:3] == ["sudo", "crontab", "-l"]:
            return RunResult(command="", returncode=0, stdout=existing, stderr="")
        return OK

    with patch("bastion.commands.nginx.run", side_effect=fake_run):
        nginx._install_cronjob("/usr/local/bin/x")

    write = next(c for c in calls if c[0] == ["crontab", "-"])
    assert write[1]["use_sudo"] is True
    assert write[1]["input"] == (
        "0 1 * * * /other/job\n# bastion: cloudflare IP refresh\n0 4 * * * /usr/local/bin/x\n"
    )
    assert not any("bash" in c[0] for c in calls)


# ── pg_hba CIDR removal matches the field, not a substring ──────────

def test_hba_remove_exact_cidr():
    hba = (
        "host all all 10.0.0.0/24 scram-sha-256    # bastion-managed\n"
        "host all all 10.0.0.0/2 scram-sha-256    # bastion-managed\n"
    )
    with patch("bastion.commands.postgres.read_file_sudo", return_value=hba), \
         patch("bastion.commands.postgres.write_file_sudo") as mock_write:
        removed = postgres._remove_hba_rules("/x/pg_hba.conf", cidr="10.0.0.0/2")
    assert removed == 1
    assert "10.0.0.0/24" in mock_write.call_args[0][1]
    assert "10.0.0.0/2 " not in mock_write.call_args[0][1]


# ── secrets on disk ─────────────────────────────────────────────────

def test_token_file_created_0600(tmp_path, monkeypatch):
    token_file = tmp_path / "cf-token"
    monkeypatch.setattr(ssl, "TOKEN_FILE", token_file)
    ssl._save_token("x" * 40)
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert token_file.read_text() == "x" * 40


def test_write_file_sudo_mode_locks_down_before_content():
    with patch("bastion.runner.run") as mock_run:
        write_file_sudo("/etc/ssl/bastion/d/key.pem", "SECRET", mode=0o600)
    argv = [c.args[0] for c in mock_run.call_args_list]
    inputs = [c.kwargs.get("input") for c in mock_run.call_args_list]
    assert argv == [
        ["tee", "/etc/ssl/bastion/d/key.pem"],
        ["chmod", "600", "/etc/ssl/bastion/d/key.pem"],
        ["tee", "/etc/ssl/bastion/d/key.pem"],
    ]
    assert inputs == ["", None, "SECRET"]


# ── Cloudflare API via urllib (token never in argv) ─────────────────

def test_cf_request_surfaces_api_error_from_http_error():
    body = json.dumps({"success": False, "errors": [{"message": "Invalid token"}]}).encode()
    err = urllib.error.HTTPError("https://api.cloudflare.com", 403, "Forbidden", {}, io.BytesIO(body))
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(SystemExit):
            ssl._cf_request("GET", "/zones", "t" * 40)


def test_cf_request_sends_bearer_header():
    resp = MagicMock()
    resp.__enter__.return_value.read.return_value = json.dumps({"success": True, "result": []}).encode()
    with patch("urllib.request.urlopen", return_value=resp) as mock_open:
        ssl._cf_request("GET", "/zones", "t" * 40)
    req = mock_open.call_args[0][0]
    assert req.get_header("Authorization") == "Bearer " + "t" * 40


# ── option ranges ───────────────────────────────────────────────────

def test_schedule_hour_out_of_range():
    result = CliRunner().invoke(cli, ["malware", "schedule", "--scan", "daily", "--hour", "24"])
    assert result.exit_code == 2
