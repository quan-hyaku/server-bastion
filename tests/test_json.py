"""--json mode: stdout is exactly one parseable document, exit code preserved."""

from __future__ import annotations

import json
from unittest.mock import patch

from click.testing import CliRunner

from bastion.cli import cli
from bastion.output import plain
from bastion.runner import RunResult


def test_plain_strips_markup():
    assert plain("[green]ON[/green] [dim]x[/dim]") == "ON x"


def test_json_table_and_clean_stdout():
    status = "Status\n|- Number of jail:\t2\n`- Jail list:\tsshd, nginx-botsearch"
    with patch("bastion.commands.fail2ban.run",
               return_value=RunResult(command="", returncode=0, stdout=status, stderr="")):
        result = CliRunner().invoke(cli, ["--json", "fail2ban", "list-jails"])
    assert result.exit_code == 0
    doc = json.loads(result.stdout)  # whole stdout must be the document
    assert doc["ok"] is True
    assert doc["tables"][0]["name"] == "fail2ban_jails"
    assert doc["tables"][0]["rows"] == [{"Jail": "sshd"}, {"Jail": "nginx-botsearch"}]


def test_json_failure_still_emits_document():
    result = CliRunner().invoke(cli, ["--json", "nginx", "disable", "../x"])
    assert result.exit_code == 1
    doc = json.loads(result.stdout)
    assert doc["ok"] is False
    assert doc["messages"][-1]["level"] == "error"
    assert "Invalid site name" in doc["messages"][-1]["text"]


def test_json_audit_has_structured_checks():
    with patch("bastion.commands.audit.run",
               return_value=RunResult(command="", returncode=1, stdout="", stderr="")):
        result = CliRunner().invoke(cli, ["--json", "audit", "run", "-c", "ssh"])
    doc = json.loads(result.stdout)
    names = {c["name"] for c in doc["data"]["checks"]}
    assert "Root login disabled" in names
    assert set(doc["data"]["score"]) == {"passed", "failed", "skipped"}
    assert all(c["status"] in ("pass", "fail", "skip") for c in doc["data"]["checks"])


def test_human_mode_unchanged():
    result = CliRunner().invoke(cli, ["nginx", "disable", "../x"])
    assert result.exit_code == 1
    assert "Invalid site name" in result.output
    assert not result.output.lstrip().startswith("{")
