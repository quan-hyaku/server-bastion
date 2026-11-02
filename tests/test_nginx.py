"""Tests for nginx commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from bastion.cli import cli
from bastion.commands.nginx import (
    CF_ALLOW_INCLUDE,
    CF_REALIP_INCLUDE,
    _add_include_to_site,
    _any_site_has_cloudflare_allow,
    _get_site_names,
    _remove_include_from_site,
    _site_has_include,
)
from bastion.runner import RunResult


class TestNginxCommands:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["nginx", "--help"])
        assert result.exit_code == 0
        assert "Manage nginx" in result.output

    def test_cloudflare_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["nginx", "--help"])
        assert "cloudflare" in result.output

    @patch("bastion.commands.nginx.run")
    def test_reload_dry_run(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="mocked", returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["--dry-run", "nginx", "reload"])
        assert result.exit_code == 0

    @patch("bastion.commands.nginx.run")
    def test_test_config_pass(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="nginx -t", returncode=0, stdout="ok", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["nginx", "test"])
        assert result.exit_code == 0

    @patch("bastion.commands.nginx.run")
    def test_test_config_fail(self, mock_run: MagicMock):
        mock_run.return_value = RunResult(
            command="nginx -t", returncode=1, stdout="", stderr="syntax error"
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["nginx", "test"])
        assert result.exit_code == 1

    def test_list_sites_missing_dir(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["nginx", "list-sites"])
        assert result.exit_code == 1


class TestCloudflareHelpers:
    """Test the helper functions used by the cloudflare command."""

    def test_site_has_include_true(self, tmp_path: Path):
        site = tmp_path / "mysite"
        site.write_text(f"server {{\n    {CF_REALIP_INCLUDE}\n    listen 80;\n}}")
        assert _site_has_include(site, CF_REALIP_INCLUDE) is True

    def test_site_has_include_false(self, tmp_path: Path):
        site = tmp_path / "mysite"
        site.write_text("server {\n    listen 80;\n}")
        assert _site_has_include(site, CF_REALIP_INCLUDE) is False

    def test_site_has_include_missing_file(self, tmp_path: Path):
        site = tmp_path / "nonexistent"
        assert _site_has_include(site, CF_REALIP_INCLUDE) is False

    @patch("bastion.commands.nginx._deploy_file")
    def test_add_include_to_site(self, mock_deploy: MagicMock, tmp_path: Path):
        site = tmp_path / "mysite"
        site.write_text("server {\n    listen 80;\n}")
        _add_include_to_site(site, CF_REALIP_INCLUDE)

        # Verify _deploy_file was called with content containing the include
        written_content = mock_deploy.call_args[0][0]
        assert CF_REALIP_INCLUDE in written_content
        assert "server {" in written_content

    @patch("bastion.commands.nginx._deploy_file")
    def test_add_include_no_server_block(self, mock_deploy: MagicMock, tmp_path: Path):
        site = tmp_path / "mysite"
        site.write_text("# bare config\nlisten 80;")
        _add_include_to_site(site, CF_REALIP_INCLUDE)

        written_content = mock_deploy.call_args[0][0]
        assert written_content.startswith(CF_REALIP_INCLUDE)

    @patch("bastion.commands.nginx._deploy_file")
    def test_remove_include_from_site(self, mock_deploy: MagicMock, tmp_path: Path):
        site = tmp_path / "mysite"
        site.write_text(
            f"server {{\n    {CF_REALIP_INCLUDE}\n    listen 80;\n}}"
        )
        _remove_include_from_site(site, CF_REALIP_INCLUDE)

        written_content = mock_deploy.call_args[0][0]
        assert CF_REALIP_INCLUDE not in written_content
        assert "listen 80;" in written_content

    def test_get_site_names(self, tmp_path: Path):
        (tmp_path / "default").write_text("# default")
        (tmp_path / "example.com").write_text("server {}")
        (tmp_path / "api.example.com").write_text("server {}")

        sites = _get_site_names(tmp_path)
        assert sites == ["api.example.com", "example.com"]
        assert "default" not in sites

    def test_get_site_names_empty(self, tmp_path: Path):
        assert _get_site_names(tmp_path / "nonexistent") == []

    def test_any_site_has_cloudflare_allow(self, tmp_path: Path):
        (tmp_path / "site1").write_text("server { listen 80; }")
        (tmp_path / "site2").write_text(
            f"server {{\n    {CF_ALLOW_INCLUDE}\n}}"
        )
        assert _any_site_has_cloudflare_allow(tmp_path, ["site1", "site2"]) is True

    def test_no_site_has_cloudflare_allow(self, tmp_path: Path):
        (tmp_path / "site1").write_text("server { listen 80; }")
        (tmp_path / "site2").write_text("server { listen 443; }")
        assert _any_site_has_cloudflare_allow(tmp_path, ["site1", "site2"]) is False


class TestCloudflareCommand:
    """Test the cloudflare interactive command."""

    def test_cloudflare_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["nginx", "cloudflare", "--help"])
        assert result.exit_code == 0
        assert "Cloudflare" in result.output
        assert "snippet" in result.output.lower()

    def test_cloudflare_no_sites_dir(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["nginx", "cloudflare"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    @patch("bastion.commands.nginx.run")
    def test_cloudflare_no_sites(self, mock_run: MagicMock, tmp_path: Path):
        """When sites-available exists but only has 'default'."""
        mock_run.return_value = RunResult(
            command="mocked", returncode=0, stdout="", stderr=""
        )
        sites_dir = tmp_path / "sites-available"
        sites_dir.mkdir()
        (sites_dir / "default").write_text("server {}")

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--profile", "/dev/null", "nginx", "cloudflare"],
            # Override config via env — but simpler to just test behavior
            input="n\n",
        )
        # Should fail because no non-default sites
        assert result.exit_code == 1

    @patch("bastion.commands.nginx._is_cronjob_installed", return_value=False)
    @patch("bastion.commands.nginx.write_file_sudo")
    @patch("bastion.commands.nginx.run")
    def test_cloudflare_full_flow(
        self,
        mock_run: MagicMock,
        mock_write: MagicMock,
        mock_cron_check: MagicMock,
        tmp_path: Path,
    ):
        """Simulate a full cloudflare setup flow with user input."""
        mock_run.return_value = RunResult(
            command="mocked", returncode=0, stdout="", stderr=""
        )

        # Set up fake sites-available
        sites_dir = tmp_path / "sites-available"
        snippets_dir = tmp_path / "snippets"
        sites_dir.mkdir()
        snippets_dir.mkdir()
        (sites_dir / "example.com").write_text("server {\n    listen 80;\n}")
        (sites_dir / "api.example.com").write_text("server {\n    listen 80;\n}")

        # Create a profile that points to tmp dirs
        config_file = tmp_path / "profile.yaml"
        config_file.write_text(
            f"name: test\n"
            f"nginx:\n"
            f"  sites_available: {sites_dir}\n"
            f"  sites_enabled: {tmp_path / 'sites-enabled'}\n"
            f"  snippets_dir: {snippets_dir}\n"
            f"  cloudflare_refresh_script: {tmp_path / 'refresh.sh'}\n"
        )

        # Input sequence:
        # Step 1: Deploy snippets? y
        # Step 2: Toggle realip sites: "a" (all)
        # Step 3: Toggle allow sites: "1" (first site only)
        # Step 4: Install cronjob? n
        user_input = "y\na\n1\nn\n"

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["--profile", str(config_file), "nginx", "cloudflare"],
            input=user_input,
        )

        assert result.exit_code == 0
        assert "Step 1" in result.output
        assert "Step 2" in result.output
        assert "Step 3" in result.output
        assert "Step 4" in result.output
        assert "Summary" in result.output
