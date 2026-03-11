"""Tests for tune commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from bastion.cli import cli
from bastion.runner import RunResult


class TestTuneCommands:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["tune", "--help"])
        assert result.exit_code == 0
        assert "System configuration" in result.output

    @patch("bastion.commands.tune.run")
    def test_sysctl_set(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="sysctl -w", returncode=0, stdout="vm.swappiness = 10", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["tune", "sysctl", "vm.swappiness", "10"])
        assert result.exit_code == 0

    @patch("bastion.commands.tune.run")
    def test_show_sysctl(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="sysctl -a", returncode=0,
            stdout="net.core.somaxconn = 128\nvm.swappiness = 60\nfs.file-max = 1000000",
            stderr="",
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["tune", "show", "--section", "sysctl"])
        assert result.exit_code == 0
