"""Tests for postgres commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from bastion.cli import cli
from bastion.runner import RunResult


class TestPostgresCommands:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["postgres", "--help"])
        assert result.exit_code == 0
        assert "Manage PostgreSQL" in result.output

    @patch("bastion.commands.postgres.run")
    def test_status_ok(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="pg_isready", returncode=0, stdout="accepting connections", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["postgres", "status"])
        assert result.exit_code == 0

    @patch("bastion.commands.postgres.run")
    def test_status_down(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="pg_isready", returncode=2, stdout="", stderr="refused"
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["postgres", "status"])
        assert result.exit_code == 1

    @patch("bastion.commands.postgres.run")
    def test_create_db(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="createdb", returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["postgres", "create-db", "myapp"])
        assert result.exit_code == 0
        # Verify createdb was called with the db name
        call_args = mock_run.call_args[0][0]
        assert "myapp" in call_args

    @patch("bastion.commands.postgres.run")
    def test_drop_db_with_force(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="dropdb", returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["postgres", "drop-db", "myapp", "--force"])
        assert result.exit_code == 0
