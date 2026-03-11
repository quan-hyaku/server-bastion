"""Tests for firewall commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from bastion.cli import cli
from bastion.runner import RunResult


class TestFirewallCommands:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["firewall", "--help"])
        assert result.exit_code == 0
        assert "Manage firewall" in result.output

    @patch("bastion.commands.firewall.run")
    def test_status(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="ufw status", returncode=0,
            stdout="Status: active\nTo                         Action      From\n22/tcp                     ALLOW       Anywhere",
            stderr="",
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["firewall", "status"])
        assert result.exit_code == 0

    @patch("bastion.commands.firewall.run")
    def test_allow_port(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="ufw allow", returncode=0, stdout="Rule added", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["firewall", "allow", "443"])
        assert result.exit_code == 0
        call_args = mock_run.call_args[0][0]
        assert "443/tcp" in call_args

    @patch("bastion.commands.firewall.run")
    def test_deny_port_udp(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="ufw deny", returncode=0, stdout="Rule added", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["firewall", "deny", "53", "--proto", "udp"])
        assert result.exit_code == 0
        call_args = mock_run.call_args[0][0]
        assert "53/udp" in call_args
