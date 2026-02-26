"""YAML configuration loading and validation for adomator."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from adomator.models import ProjectConfig


_ENV_VAR_RE = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?$")


def _resolve_env_vars(value: Any) -> Any:
    """Recursively resolve ``$VAR`` / ``${VAR}`` placeholders from environment variables."""
    if isinstance(value, str):
        match = _ENV_VAR_RE.match(value.strip())
        if match:
            env_name = match.group(1)
            resolved = os.environ.get(env_name)
            if resolved is None:
                raise ValueError(
                    f"Environment variable '{env_name}' referenced in configuration is not set."
                )
            return resolved
        return value
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value


def load_config(path: str | Path) -> ProjectConfig:
    """Load and validate a project configuration YAML file.

    Environment variable placeholders (``$VAR`` or ``${VAR}``) in string values
    are resolved before validation.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        A validated :class:`~adomator.models.ProjectConfig` instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If environment variables are missing or YAML is invalid.
        pydantic.ValidationError: If the configuration fails schema validation.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)

    if raw is None:
        raise ValueError(f"Configuration file is empty: {config_path}")

    if not isinstance(raw, dict):
        raise ValueError(f"Configuration file must contain a YAML mapping: {config_path}")

    resolved = _resolve_env_vars(raw)

    try:
        return ProjectConfig(**resolved)
    except ValidationError:
        raise
