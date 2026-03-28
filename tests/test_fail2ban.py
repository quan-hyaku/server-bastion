"""Tests for fail2ban commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from bastion.cli import cli
from bastion.commands.fail2ban import BUNDLED_JAILS, CUSTOM_FILTER_JAILS
from bastion.runner import RunResult


class TestFail2banCommands:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "--help"])
        assert result.exit_code == 0
        assert "Manage fail2ban" in result.output

    @patch("bastion.commands.fail2ban.run")
    def test_status(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="fail2ban-client status", returncode=0,
            stdout="Status\n|- Number of jail: 1\n`- Jail list: sshd",
            stderr="",
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "status"])
        assert result.exit_code == 0

    @patch("bastion.commands.fail2ban.run")
    def test_ban_ip(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="fail2ban-client set sshd banip", returncode=0, stdout="1", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "ban", "192.168.1.100", "--jail", "sshd"])
        assert result.exit_code == 0

    @patch("bastion.commands.fail2ban.run")
    def test_list_jails(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="fail2ban-client status", returncode=0,
            stdout="Status\n|- Number of jail: 2\n`- Jail list:\tsshd, nginx-http-auth",
            stderr="",
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "list-jails"])
        assert result.exit_code == 0


class TestFail2banSetup:
    @patch("bastion.commands.fail2ban.write_file_sudo")
    @patch("bastion.commands.fail2ban.run")
    def test_setup_single_jail(self, mock_run: MagicMock, mock_write: MagicMock):
        mock_run.return_value = RunResult(
            command="mocked", returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "setup", "sshd"])
        assert result.exit_code == 0
        assert "Jail deployed" in result.output
        assert "reloaded" in result.output.lower()

    @patch("bastion.commands.fail2ban.write_file_sudo")
    @patch("bastion.commands.fail2ban.run")
    def test_setup_all(self, mock_run: MagicMock, mock_write: MagicMock):
        mock_run.return_value = RunResult(
            command="mocked", returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "setup", "--all"])
        assert result.exit_code == 0
        assert f"{len(BUNDLED_JAILS)} jail(s) deployed" in result.output

    @patch("bastion.commands.fail2ban.write_file_sudo")
    @patch("bastion.commands.fail2ban.run")
    def test_setup_custom_filter_jail_deploys_filter(self, mock_run: MagicMock, mock_write: MagicMock):
        mock_run.return_value = RunResult(
            command="mocked", returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "setup", "nginx-script-scan"])
        assert result.exit_code == 0
        assert "Filter deployed" in result.output
        assert "Jail deployed" in result.output

    @patch("bastion.commands.fail2ban.write_file_sudo")
    @patch("bastion.commands.fail2ban.run")
    def test_setup_builtin_jail_no_filter(self, mock_run: MagicMock, mock_write: MagicMock):
        """sshd uses a built-in filter, no custom filter should be deployed."""
        mock_run.return_value = RunResult(
            command="mocked", returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "setup", "sshd"])
        assert result.exit_code == 0
        assert "Filter deployed" not in result.output

    def test_setup_unknown_jail(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "setup", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown jail" in result.output

    def test_setup_no_args(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "setup"])
        assert result.exit_code == 1
        assert "Specify jail names" in result.output


class TestFail2banRemoveJail:
    @patch("bastion.commands.fail2ban.run")
    def test_remove_with_force(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="mocked", returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "remove-jail", "sshd", "--force"])
        assert result.exit_code == 0
        assert "Jail removed" in result.output

    @patch("bastion.commands.fail2ban.run")
    def test_remove_custom_filter_jail_removes_filter(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="mocked", returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "remove-jail", "nginx-script-scan", "--force"])
        assert result.exit_code == 0
        assert "Filter removed" in result.output
        assert "Jail removed" in result.output

    @patch("bastion.commands.fail2ban.run")
    def test_remove_prompts_without_force(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="mocked", returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "remove-jail", "sshd"], input="y\n")
        assert result.exit_code == 0


class TestFail2banShowConfig:
    def test_show_sshd(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "show-config", "sshd"])
        assert result.exit_code == 0
        assert "[sshd]" in result.output
        assert "maxretry" in result.output

    def test_show_script_scan_includes_filter(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "show-config", "nginx-script-scan"])
        assert result.exit_code == 0
        assert "Jail: nginx-script-scan" in result.output
        assert "Filter: nginx-script-scan" in result.output
        assert "failregex" in result.output

    def test_show_unknown(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["fail2ban", "show-config", "nope"])
        assert result.exit_code == 1
        assert "Unknown jail" in result.output
