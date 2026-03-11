"""Tests for the subprocess runner."""

from __future__ import annotations

from unittest.mock import patch

import click
import pytest

from bastion.runner import CommandError, RunResult, run


class TestRunResult:
    def test_ok_when_zero(self):
        r = RunResult(command="echo hi", returncode=0, stdout="hi", stderr="")
        assert r.ok is True

    def test_not_ok_when_nonzero(self):
        r = RunResult(command="false", returncode=1, stdout="", stderr="err")
        assert r.ok is False


class TestRun:
    @patch("bastion.runner.subprocess.run")
    def test_basic_command(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "output\n"
        mock_subprocess.return_value.stderr = ""

        result = run(["echo", "hello"])
        assert result.ok
        assert result.stdout == "output"
        mock_subprocess.assert_called_once()

    @patch("bastion.runner.subprocess.run")
    def test_sudo_prepended(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.stderr = ""

        run(["systemctl", "reload", "nginx"], use_sudo=True)
        call_args = mock_subprocess.call_args[0][0]
        assert call_args[0] == "sudo"

    @patch("bastion.runner.subprocess.run")
    def test_string_args_split(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.stderr = ""

        run("echo hello world")
        call_args = mock_subprocess.call_args[0][0]
        assert call_args == ["echo", "hello", "world"]

    @patch("bastion.runner.subprocess.run")
    def test_check_raises_on_failure(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.stderr = "fail"

        with pytest.raises(CommandError) as exc_info:
            run(["false"], check=True)
        assert exc_info.value.result.returncode == 1

    @patch("bastion.runner.subprocess.run")
    def test_check_false_no_raise(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 1
        mock_subprocess.return_value.stdout = ""
        mock_subprocess.return_value.stderr = "fail"

        result = run(["false"], check=False)
        assert not result.ok

    def test_dry_run_skips_subprocess(self):
        """In dry-run mode, no subprocess is spawned."""

        @click.command()
        @click.pass_context
        def dummy(ctx):
            ctx.ensure_object(dict)
            ctx.obj["dry_run"] = True
            result = run(["rm", "-rf", "/"])
            assert result.ok
            assert result.stdout == ""

        runner = click.testing.CliRunner()
        result = runner.invoke(dummy)
        assert result.exit_code == 0
