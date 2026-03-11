"""Tests for config loading."""

from __future__ import annotations

import pytest

from bastion.config import NginxConfig, PostgresConfig, ServerProfile, load_config


class TestLoadConfig:
    def test_load_full_config(self, sample_config_yaml):
        profile = load_config(sample_config_yaml)
        assert profile.name == "test-server"
        assert profile.description == "test profile"
        assert profile.nginx.sites_available == "/tmp/sites-available"
        assert profile.postgres.host == "127.0.0.1"
        assert profile.postgres.port == 5433

    def test_load_minimal_config(self, tmp_path):
        config = tmp_path / "minimal.yaml"
        config.write_text("name: minimal\n")
        profile = load_config(config)
        assert profile.name == "minimal"
        # Defaults should be applied
        assert profile.nginx.sites_available == "/etc/nginx/sites-available"
        assert profile.postgres.port == 5432

    def test_load_empty_config(self, tmp_path):
        config = tmp_path / "empty.yaml"
        config.write_text("")
        profile = load_config(config)
        assert profile.name == "empty"

    def test_load_missing_file(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path.yaml")


class TestServerProfile:
    def test_defaults(self):
        profile = ServerProfile()
        assert profile.name == "default"
        assert isinstance(profile.nginx, NginxConfig)
        assert isinstance(profile.postgres, PostgresConfig)
