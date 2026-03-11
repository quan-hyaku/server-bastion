"""Shared test fixtures."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from bastion.runner import RunResult


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def mock_run(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch runner.run to prevent actual subprocess calls in tests."""
    mock = MagicMock(
        return_value=RunResult(command="mocked", returncode=0, stdout="", stderr="")
    )
    monkeypatch.setattr("bastion.runner.run", mock)
    return mock


@pytest.fixture
def sample_config_yaml(tmp_path):
    """Create a temporary YAML config file."""
    config = tmp_path / "test-profile.yaml"
    config.write_text(
        "name: test-server\n"
        "description: test profile\n"
        "nginx:\n"
        "  sites_available: /tmp/sites-available\n"
        "  sites_enabled: /tmp/sites-enabled\n"
        "postgres:\n"
        "  host: 127.0.0.1\n"
        "  port: 5433\n"
    )
    return config
