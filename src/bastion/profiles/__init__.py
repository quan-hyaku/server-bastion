"""Bundled tuning presets."""

from importlib import resources
from pathlib import Path


def get_profile_path(name: str) -> Path:
    """Get the path to a bundled profile YAML."""
    return resources.files("bastion.profiles").joinpath(f"{name}.yaml")
