"""Tests for adomator.reconciler (desired-state diffing)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from adomator.models import (
    BranchPolicies,
    BuildValidationPolicy,
    CommentPolicy,
    DefaultSettings,
    MergeStrategyPolicy,
    PermissionEntry,
    ProjectConfig,
    RepositoryOverride,
    RepositorySettings,
    ReviewerPolicy,
    SecuritySettings,
    StatusPolicy,
)
from adomator.reconciler import (
    Change,
    ChangeType,
    Reconciler,
    _bits_for_names,
    _scope_for_branch,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**kwargs) -> ProjectConfig:
    base = {
        "organization": "my-org",
        "project": "my-project",
        "token": "tok",
    }
    base.update(kwargs)
    return ProjectConfig(**base)


def _make_repo_mock(name: str = "repo1", repo_id: str = "repo-id-1", project_id: str = "proj-id") -> MagicMock:
    repo = MagicMock()
    repo.id = repo_id
    repo.name = name
    repo.default_branch = "refs/heads/main"
    repo.is_disabled = False
    repo.project = MagicMock()
    repo.project.id = project_id
    return repo


def _make_policy_mock(
    policy_id: int,
    type_id: str,
    settings: dict,
    blocking: bool = True,
    enabled: bool = True,
) -> MagicMock:
    pol = MagicMock()
    pol.id = policy_id
    pol.type = MagicMock()
    pol.type.id = type_id
    pol.settings = settings
    pol.is_blocking = blocking
    pol.is_enabled = enabled
    return pol


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestScopeForBranch:
    def test_adds_refs_prefix(self):
        scope = _scope_for_branch("repo-id", "main")
        assert scope[0]["refName"] == "refs/heads/main"

    def test_preserves_existing_prefix(self):
        scope = _scope_for_branch("repo-id", "refs/heads/main")
        assert scope[0]["refName"] == "refs/heads/main"


class TestBitsForNames:
    def test_single_permission(self):
        bits = _bits_for_names(["GenericRead"])
        assert bits == 2

    def test_combined_permissions(self):
        bits = _bits_for_names(["GenericRead", "GenericContribute"])
        assert bits == 6  # 2 | 4

    def test_empty_list(self):
        assert _bits_for_names([]) == 0

    def test_unknown_permission_raises(self):
        with pytest.raises(ValueError, match="UnknownPerm"):
            _bits_for_names(["UnknownPerm"])


class TestReconcilerPlanRepoSettings:
    def test_no_changes_when_up_to_date(self):
        config = _make_config(
            repositories=[{"name": "repo1"}]
        )
        client = MagicMock()
        repo = _make_repo_mock()
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = []
        client.query_repo_acl.return_value = []

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()
        assert changes == []

    def test_default_branch_change_detected(self):
        config = _make_config(
            defaults={"repository": {"default_branch": "develop"}},
            repositories=[{"name": "repo1"}],
        )
        client = MagicMock()
        repo = _make_repo_mock()
        repo.default_branch = "refs/heads/main"  # current state
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = []
        client.query_repo_acl.return_value = []

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()

        update_changes = [c for c in changes if c.change_type == ChangeType.UPDATE_REPO]
        assert len(update_changes) == 1
        assert update_changes[0].details["default_branch"] == "refs/heads/develop"

    def test_is_disabled_change_detected(self):
        config = _make_config(
            defaults={"repository": {"is_disabled": True}},
            repositories=[{"name": "repo1"}],
        )
        client = MagicMock()
        repo = _make_repo_mock()
        repo.is_disabled = False
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = []
        client.query_repo_acl.return_value = []

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()

        update_changes = [c for c in changes if c.change_type == ChangeType.UPDATE_REPO]
        assert len(update_changes) == 1
        assert update_changes[0].details["is_disabled"] is True

    def test_missing_repo_skipped(self):
        config = _make_config(repositories=[{"name": "nonexistent"}])
        client = MagicMock()
        client.get_repository.return_value = None

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()
        assert changes == []


class TestReconcilerPlanBranchPolicies:
    def test_creates_missing_reviewer_policy(self):
        config = _make_config(
            defaults={
                "branch_policies": {
                    "main": {"reviewer": {"minimum_approver_count": 1}}
                }
            },
            repositories=[{"name": "repo1"}],
        )
        client = MagicMock()
        repo = _make_repo_mock()
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = []  # no existing policies
        client.query_repo_acl.return_value = []

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()

        create_changes = [c for c in changes if c.change_type == ChangeType.CREATE_POLICY]
        assert len(create_changes) == 1
        assert create_changes[0].details["settings"]["minimumApproverCount"] == 1

    def test_updates_existing_reviewer_policy_when_changed(self):
        from adomator.reconciler import POLICY_TYPE_MIN_REVIEWERS
        config = _make_config(
            defaults={
                "branch_policies": {
                    "main": {"reviewer": {"minimum_approver_count": 2}}
                }
            },
            repositories=[{"name": "repo1"}],
        )
        client = MagicMock()
        repo = _make_repo_mock()

        existing_policy = _make_policy_mock(
            policy_id=10,
            type_id=POLICY_TYPE_MIN_REVIEWERS,
            settings={
                "minimumApproverCount": 1,  # outdated
                "scope": [{"repositoryId": repo.id, "refName": "refs/heads/main", "matchKind": "Exact"}],
            },
        )
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = [existing_policy]
        client.query_repo_acl.return_value = []

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()

        update_changes = [c for c in changes if c.change_type == ChangeType.UPDATE_POLICY]
        assert len(update_changes) == 1
        assert update_changes[0].details["settings"]["minimumApproverCount"] == 2

    def test_no_policy_changes_when_up_to_date(self):
        from adomator.reconciler import POLICY_TYPE_MIN_REVIEWERS
        config = _make_config(
            defaults={
                "branch_policies": {
                    "main": {
                        "reviewer": {
                            "minimum_approver_count": 1,
                            "creator_vote_counts": False,
                            "allow_downvotes": False,
                            "reset_on_source_push": False,
                            "reset_on_push_to_pr_source_branch": False,
                            "require_vote_on_last_iteration": False,
                        }
                    }
                }
            },
            repositories=[{"name": "repo1"}],
        )
        client = MagicMock()
        repo = _make_repo_mock()

        existing_policy = _make_policy_mock(
            policy_id=10,
            type_id=POLICY_TYPE_MIN_REVIEWERS,
            settings={
                "minimumApproverCount": 1,
                "creatorVoteCounts": False,
                "allowDownvotes": False,
                "resetOnSourcePush": False,
                "resetRejectionsOnSourcePush": False,
                "requireVoteOnLastIteration": False,
                "scope": [{"repositoryId": repo.id, "refName": "refs/heads/main", "matchKind": "Exact"}],
            },
        )
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = [existing_policy]
        client.query_repo_acl.return_value = []

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()
        policy_changes = [c for c in changes if c.change_type in (ChangeType.CREATE_POLICY, ChangeType.UPDATE_POLICY)]
        assert policy_changes == []

    def test_creates_build_validation_policy(self):
        config = _make_config(
            defaults={
                "branch_policies": {
                    "main": {
                        "build_validations": [
                            {
                                "display_name": "CI",
                                "build_definition_id": 5,
                                "queue_on_source_update": True,
                                "valid_duration": 720.0,
                            }
                        ]
                    }
                }
            },
            repositories=[{"name": "repo1"}],
        )
        client = MagicMock()
        repo = _make_repo_mock()
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = []
        client.query_repo_acl.return_value = []

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()

        create_changes = [c for c in changes if c.change_type == ChangeType.CREATE_POLICY]
        assert any(c.details["settings"]["buildDefinitionId"] == 5 for c in create_changes)


class TestReconcilerPlanSecurity:
    def test_permission_change_detected(self):
        config = _make_config(
            defaults={
                "security": {
                    "permissions": [
                        {
                            "principal": "[proj]\\Contributors",
                            "allow": ["GenericRead", "GenericContribute"],
                            "deny": [],
                        }
                    ]
                }
            },
            repositories=[{"name": "repo1"}],
        )
        client = MagicMock()
        repo = _make_repo_mock()
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = []
        # Current ACL has only read access
        client.query_repo_acl.return_value = [
            {"descriptor": "desc-contributors", "allow": 2, "deny": 0}
        ]
        client.resolve_principal_descriptor.return_value = "desc-contributors"

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()

        perm_changes = [c for c in changes if c.change_type == ChangeType.SET_PERMISSIONS]
        assert len(perm_changes) == 1
        assert perm_changes[0].details["allow_bits"] == 6  # GenericRead | GenericContribute

    def test_no_permission_changes_when_up_to_date(self):
        config = _make_config(
            defaults={
                "security": {
                    "permissions": [
                        {
                            "principal": "[proj]\\Contributors",
                            "allow": ["GenericRead"],
                            "deny": [],
                        }
                    ]
                }
            },
            repositories=[{"name": "repo1"}],
        )
        client = MagicMock()
        repo = _make_repo_mock()
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = []
        client.query_repo_acl.return_value = [
            {"descriptor": "desc-contributors", "allow": 2, "deny": 0}
        ]
        client.resolve_principal_descriptor.return_value = "desc-contributors"

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()

        perm_changes = [c for c in changes if c.change_type == ChangeType.SET_PERMISSIONS]
        assert perm_changes == []

    def test_unresolvable_principal_skipped(self):
        config = _make_config(
            defaults={
                "security": {
                    "permissions": [
                        {
                            "principal": "unknown-group",
                            "allow": ["GenericRead"],
                            "deny": [],
                        }
                    ]
                }
            },
            repositories=[{"name": "repo1"}],
        )
        client = MagicMock()
        repo = _make_repo_mock()
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = []
        client.query_repo_acl.return_value = []
        client.resolve_principal_descriptor.return_value = None  # cannot resolve

        reconciler = Reconciler(client, config)
        changes = reconciler.plan()
        perm_changes = [c for c in changes if c.change_type == ChangeType.SET_PERMISSIONS]
        assert perm_changes == []


class TestReconcilerApply:
    def test_apply_calls_correct_client_methods(self):
        config = _make_config(
            defaults={"repository": {"default_branch": "develop"}},
            repositories=[{"name": "repo1"}],
        )
        client = MagicMock()
        repo = _make_repo_mock()
        repo.default_branch = "refs/heads/main"
        client.get_repository.return_value = repo
        client.get_repo_policy_configurations.return_value = []
        client.query_repo_acl.return_value = []

        reconciler = Reconciler(client, config)
        changes = reconciler.apply()

        client.update_repository.assert_called_once_with(
            project="my-project",
            repo_id="repo-id-1",
            default_branch="refs/heads/develop",
            is_disabled=None,
        )
        assert len(changes) == 1

    def test_apply_with_explicit_changes(self):
        config = _make_config(repositories=[{"name": "repo1"}])
        client = MagicMock()

        reconciler = Reconciler(client, config)
        change = Change(
            change_type=ChangeType.UPDATE_REPO,
            resource="repository/repo1",
            details={
                "project": "my-project",
                "repo_id": "repo-id-1",
                "default_branch": "refs/heads/develop",
            },
        )
        reconciler.apply([change])
        client.update_repository.assert_called_once()
