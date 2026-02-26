"""Tests for adomator.config (YAML loading)."""

import os
from pathlib import Path

import pytest
import yaml

from adomator.config import load_config, _resolve_env_vars


class TestResolveEnvVars:
    def test_string_passthrough(self):
        assert _resolve_env_vars("plain") == "plain"

    def test_env_var_resolved(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret")
        assert _resolve_env_vars("$MY_TOKEN") == "secret"

    def test_env_var_braces_resolved(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret")
        assert _resolve_env_vars("${MY_TOKEN}") == "secret"

    def test_env_var_missing_raises(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        with pytest.raises(ValueError, match="MISSING_VAR"):
            _resolve_env_vars("$MISSING_VAR")

    def test_nested_dict(self, monkeypatch):
        monkeypatch.setenv("TOKEN", "tok")
        result = _resolve_env_vars({"token": "$TOKEN", "other": 42})
        assert result == {"token": "tok", "other": 42}

    def test_nested_list(self, monkeypatch):
        monkeypatch.setenv("VAL", "hello")
        result = _resolve_env_vars(["$VAL", "plain"])
        assert result == ["hello", "plain"]

    def test_non_string_passthrough(self):
        assert _resolve_env_vars(123) == 123
        assert _resolve_env_vars(True) is True


class TestLoadConfig:
    def _write_yaml(self, tmp_path: Path, data: dict) -> Path:
        p = tmp_path / "config.yaml"
        p.write_text(yaml.dump(data))
        return p

    def test_load_minimal_config(self, tmp_path):
        p = self._write_yaml(tmp_path, {
            "organization": "org",
            "project": "proj",
            "token": "tok",
        })
        config = load_config(p)
        assert config.organization == "org"
        assert config.project == "proj"
        assert config.token == "tok"

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_empty_file_raises(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        with pytest.raises(ValueError, match="empty"):
            load_config(p)

    def test_non_mapping_raises(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="mapping"):
            load_config(p)

    def test_env_var_in_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_PAT", "supersecret")
        p = self._write_yaml(tmp_path, {
            "organization": "org",
            "project": "proj",
            "token": "${MY_PAT}",
        })
        config = load_config(p)
        assert config.token == "supersecret"

    def test_load_config_with_repositories(self, tmp_path):
        p = self._write_yaml(tmp_path, {
            "organization": "org",
            "project": "proj",
            "token": "tok",
            "repositories": [
                {"name": "repo1"},
                {
                    "name": "repo2",
                    "repository": {"default_branch": "develop"},
                },
            ],
        })
        config = load_config(p)
        assert len(config.repositories) == 2
        assert config.repositories[1].repository.default_branch == "refs/heads/develop"

    def test_load_example_config(self, tmp_path, monkeypatch):
        """The bundled example YAML should load without errors when env var is set."""
        monkeypatch.setenv("AZURE_DEVOPS_TOKEN", "example-token")
        example_path = (
            Path(__file__).parent.parent / "examples" / "my-project.yaml"
        )
        config = load_config(example_path)
        assert config.organization == "my-org"
        assert config.project == "my-project"
        assert len(config.repositories) == 4
