"""Tests for adomator.models."""

import pytest
from pydantic import ValidationError

from adomator.models import (
    BranchPolicies,
    DefaultSettings,
    PermissionEntry,
    ProjectConfig,
    RepositoryOverride,
    RepositorySettings,
    ReviewerPolicy,
    SecuritySettings,
)


class TestRepositorySettings:
    def test_default_branch_prefix_added(self):
        s = RepositorySettings(default_branch="main")
        assert s.default_branch == "refs/heads/main"

    def test_default_branch_prefix_preserved(self):
        s = RepositorySettings(default_branch="refs/heads/develop")
        assert s.default_branch == "refs/heads/develop"

    def test_defaults(self):
        s = RepositorySettings()
        assert s.default_branch == "refs/heads/main"
        assert s.is_disabled is False


class TestProjectConfig:
    def _minimal_config(self, **kwargs):
        base = {
            "organization": "my-org",
            "project": "my-project",
            "token": "mytoken",
        }
        base.update(kwargs)
        return ProjectConfig(**base)

    def test_minimal_config(self):
        cfg = self._minimal_config()
        assert cfg.organization == "my-org"
        assert cfg.project == "my-project"
        assert cfg.token == "mytoken"
        assert cfg.repositories == []

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            ProjectConfig(
                organization="org",
                project="proj",
                token="tok",
                unknown_field="bad",
            )

    def test_repositories_list(self):
        cfg = self._minimal_config(
            repositories=[{"name": "repo1"}, {"name": "repo2"}]
        )
        assert len(cfg.repositories) == 2
        assert cfg.repositories[0].name == "repo1"

    def test_effective_settings_uses_defaults_when_no_override(self):
        cfg = self._minimal_config(
            defaults={
                "repository": {"default_branch": "main", "is_disabled": False},
                "branch_policies": {
                    "main": {
                        "reviewer": {"minimum_approver_count": 1}
                    }
                },
            },
            repositories=[{"name": "repo1"}],
        )
        override = cfg.repositories[0]
        effective = cfg.effective_settings(override)
        assert effective["repository"].default_branch == "refs/heads/main"
        assert "main" in effective["branch_policies"]
        assert effective["branch_policies"]["main"].reviewer.minimum_approver_count == 1

    def test_effective_settings_repo_override_takes_precedence(self):
        cfg = self._minimal_config(
            defaults={
                "repository": {"default_branch": "main"},
            },
            repositories=[
                {
                    "name": "repo1",
                    "repository": {"default_branch": "develop"},
                }
            ],
        )
        override = cfg.repositories[0]
        effective = cfg.effective_settings(override)
        assert effective["repository"].default_branch == "refs/heads/develop"

    def test_effective_settings_branch_policies_merged(self):
        cfg = self._minimal_config(
            defaults={
                "branch_policies": {
                    "main": {
                        "reviewer": {"minimum_approver_count": 1}
                    }
                }
            },
            repositories=[
                {
                    "name": "repo1",
                    "branch_policies": {
                        "main": {
                            "reviewer": {"minimum_approver_count": 2}
                        }
                    },
                }
            ],
        )
        effective = cfg.effective_settings(cfg.repositories[0])
        assert effective["branch_policies"]["main"].reviewer.minimum_approver_count == 2

    def test_effective_settings_security_override(self):
        cfg = self._minimal_config(
            defaults={
                "security": {
                    "permissions": [
                        {"principal": "[proj]\\Contributors", "allow": ["GenericRead"]}
                    ]
                }
            },
            repositories=[
                {
                    "name": "repo1",
                    "security": {
                        "permissions": [
                            {
                                "principal": "[proj]\\Admins",
                                "allow": ["ManagePermissions"],
                            }
                        ]
                    },
                }
            ],
        )
        effective = cfg.effective_settings(cfg.repositories[0])
        principals = [p.principal for p in effective["security"].permissions]
        assert "[proj]\\Admins" in principals
        assert "[proj]\\Contributors" not in principals


class TestPermissionEntry:
    def test_valid_entry(self):
        entry = PermissionEntry(
            principal="[proj]\\Contributors",
            allow=["GenericRead", "GenericContribute"],
            deny=[],
        )
        assert entry.allow == ["GenericRead", "GenericContribute"]

    def test_principal_required(self):
        with pytest.raises(ValidationError):
            PermissionEntry()
