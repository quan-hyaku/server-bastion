"""Bundled config templates."""

from importlib import resources
from pathlib import Path


def get_template_path(subpath: str) -> Path:
    """Get path to a bundled template file."""
    return resources.files("bastion.templates").joinpath(subpath)
