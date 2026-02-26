"""Desired-state reconciliation engine for Azure DevOps repositories.

This module compares the configuration declared in the YAML file against the
live state fetched from Azure DevOps and applies the minimal set of API calls
needed to make reality match the declaration – exactly like Terraform does.

Usage pattern::

    from adomator.reconciler import Reconciler
    from adomator.client import AzureDevOpsClient
    from adomator.config import load_config

    config = load_config("my-project.yaml")
    client = AzureDevOpsClient(config.organization, config.token)
    reconciler = Reconciler(client, config)

    plan = reconciler.plan()        # returns list of Change objects (dry-run)
    reconciler.apply(plan)          # applies the changes
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from adomator.client import (
    AzureDevOpsClient,
    GIT_PERMISSION_BITS,
    POLICY_TYPE_BUILD_VALIDATION,
    POLICY_TYPE_COMMENT_REQUIREMENTS,
    POLICY_TYPE_MERGE_STRATEGY,
    POLICY_TYPE_MIN_REVIEWERS,
    POLICY_TYPE_STATUS_CHECK,
    POLICY_TYPE_WORK_ITEM_LINKING,
    _bits_for_names,
)
from adomator.models import (
    BranchPolicies,
    BuildValidationPolicy,
    ProjectConfig,
    RepositoryOverride,
    RepositorySettings,
    SecuritySettings,
    StatusPolicy,
)

logger = logging.getLogger(__name__)


class ChangeType(str, Enum):
    UPDATE_REPO = "update_repository"
    CREATE_POLICY = "create_policy"
    UPDATE_POLICY = "update_policy"
    DELETE_POLICY = "delete_policy"
    SET_PERMISSIONS = "set_permissions"


@dataclass
class Change:
    """Describes a single change that needs to be applied."""

    change_type: ChangeType
    resource: str  # Human-readable description of the affected resource
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.change_type.value}] {self.resource}"


# ---------------------------------------------------------------------------
# Internal helpers to build policy settings dicts
# ---------------------------------------------------------------------------


def _scope_for_branch(repo_id: str, branch: str) -> list[dict[str, Any]]:
    """Return an Azure DevOps policy scope entry for a branch."""
    ref_name = branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"
    return [{"repositoryId": repo_id, "refName": ref_name, "matchKind": "Exact"}]


def _reviewer_settings(repo_id: str, branch: str, policy: Any) -> dict[str, Any]:
    return {
        "minimumApproverCount": policy.minimum_approver_count,
        "creatorVoteCounts": policy.creator_vote_counts,
        "allowDownvotes": policy.allow_downvotes,
        "resetOnSourcePush": policy.reset_on_source_push,
        "resetRejectionsOnSourcePush": policy.reset_on_push_to_pr_source_branch,
        "requireVoteOnLastIteration": policy.require_vote_on_last_iteration,
        "scope": _scope_for_branch(repo_id, branch),
    }


def _merge_strategy_settings(repo_id: str, branch: str, policy: Any) -> dict[str, Any]:
    return {
        "allowSquash": policy.allow_squash,
        "allowNoFastForward": policy.allow_no_fast_forward,
        "allowRebase": policy.allow_rebase,
        "allowRebaseMerge": policy.allow_rebase_merge,
        "scope": _scope_for_branch(repo_id, branch),
    }


def _build_validation_settings(repo_id: str, branch: str, policy: BuildValidationPolicy) -> dict[str, Any]:
    return {
        "buildDefinitionId": policy.build_definition_id,
        "displayName": policy.display_name,
        "queueOnSourceUpdateOnly": policy.queue_on_source_update,
        "manualQueueOnly": False,
        "validDuration": policy.valid_duration,
        "scope": _scope_for_branch(repo_id, branch),
    }


def _status_settings(repo_id: str, branch: str, policy: StatusPolicy) -> dict[str, Any]:
    return {
        "statusName": policy.status_name,
        "statusGenre": policy.status_genre,
        "authorId": policy.authorized_user,
        "invalidateOnSourceUpdate": policy.invalidate_on_source_update,
        "displayName": policy.display_name,
        "scope": _scope_for_branch(repo_id, branch),
    }


def _settings_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Shallow equality check for policy settings, ignoring ``None`` values in *b*."""
    for key, val in b.items():
        if val is None:
            continue
        if a.get(key) != val:
            return False
    return True


# ---------------------------------------------------------------------------
# Reconciler
# ---------------------------------------------------------------------------


class Reconciler:
    """Compare declared configuration against live Azure DevOps state and produce/apply changes."""

    def __init__(self, client: AzureDevOpsClient, config: ProjectConfig) -> None:
        self._client = client
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self) -> list[Change]:
        """Compute the list of changes required to reach the desired state.

        This method is read-only; it does not modify any Azure DevOps resource.

        Returns:
            Ordered list of :class:`Change` objects describing what *apply*
            would do.
        """
        changes: list[Change] = []
        for repo_override in self._config.repositories:
            effective = self._config.effective_settings(repo_override)
            repo = self._client.get_repository(self._config.project, repo_override.name)
            if repo is None:
                logger.warning(
                    "Repository '%s' not found in project '%s' – skipping.",
                    repo_override.name,
                    self._config.project,
                )
                continue

            changes.extend(self._plan_repo_settings(repo, effective["repository"]))
            changes.extend(
                self._plan_branch_policies(repo, effective["branch_policies"])
            )
            changes.extend(
                self._plan_security(repo, effective["security"])
            )
        return changes

    def apply(self, changes: list[Change] | None = None) -> list[Change]:
        """Apply changes to Azure DevOps.

        If *changes* is ``None``, :meth:`plan` is called first.

        Returns:
            The list of :class:`Change` objects that were applied.
        """
        if changes is None:
            changes = self.plan()

        for change in changes:
            logger.info("Applying: %s", change)
            self._apply_change(change)

        return changes

    # ------------------------------------------------------------------
    # Planning helpers
    # ------------------------------------------------------------------

    def _plan_repo_settings(
        self, repo: Any, desired: RepositorySettings
    ) -> list[Change]:
        changes = []
        updates: dict[str, Any] = {}

        if repo.default_branch != desired.default_branch:
            updates["default_branch"] = desired.default_branch

        if repo.is_disabled != desired.is_disabled:
            updates["is_disabled"] = desired.is_disabled

        if updates:
            changes.append(
                Change(
                    change_type=ChangeType.UPDATE_REPO,
                    resource=f"repository/{repo.name}",
                    details={
                        "project": self._config.project,
                        "repo_id": repo.id,
                        **updates,
                    },
                )
            )
        return changes

    def _plan_branch_policies(
        self, repo: Any, desired_policies: dict[str, BranchPolicies]
    ) -> list[Change]:
        changes: list[Change] = []
        existing = self._client.get_repo_policy_configurations(
            self._config.project, repo.id
        )

        for branch, desired in desired_policies.items():
            changes.extend(
                self._plan_single_branch_policies(repo, branch, desired, existing)
            )
        return changes

    def _plan_single_branch_policies(
        self,
        repo: Any,
        branch: str,
        desired: BranchPolicies,
        existing: list[Any],
    ) -> list[Change]:
        changes: list[Change] = []
        prefix = f"repository/{repo.name}/branch/{branch}"

        def _find_existing(type_id: str, extra_match: dict[str, Any] | None = None) -> Any | None:
            for p in existing:
                if p.type and p.type.id == type_id:
                    scope = (p.settings or {}).get("scope", [])
                    for s in scope:
                        ref = branch if branch.startswith("refs/heads/") else f"refs/heads/{branch}"
                        if s.get("repositoryId") == repo.id and s.get("refName") == ref:
                            if extra_match:
                                match = all(
                                    (p.settings or {}).get(k) == v
                                    for k, v in extra_match.items()
                                )
                                if not match:
                                    continue
                            return p
            return None

        # -- Reviewer policy --
        if desired.reviewer is not None:
            pol = desired.reviewer
            desired_settings = _reviewer_settings(repo.id, branch, pol)
            existing_pol = _find_existing(POLICY_TYPE_MIN_REVIEWERS)
            changes.extend(
                self._diff_policy(
                    prefix + "/reviewer",
                    POLICY_TYPE_MIN_REVIEWERS,
                    desired_settings,
                    pol.blocking,
                    pol.enabled,
                    existing_pol,
                )
            )

        # -- Comment requirements --
        if desired.comment is not None:
            pol = desired.comment
            desired_settings = {"scope": _scope_for_branch(repo.id, branch)}
            existing_pol = _find_existing(POLICY_TYPE_COMMENT_REQUIREMENTS)
            changes.extend(
                self._diff_policy(
                    prefix + "/comment",
                    POLICY_TYPE_COMMENT_REQUIREMENTS,
                    desired_settings,
                    pol.blocking,
                    pol.enabled,
                    existing_pol,
                )
            )

        # -- Merge strategy --
        if desired.merge_strategy is not None:
            pol = desired.merge_strategy
            desired_settings = _merge_strategy_settings(repo.id, branch, pol)
            existing_pol = _find_existing(POLICY_TYPE_MERGE_STRATEGY)
            changes.extend(
                self._diff_policy(
                    prefix + "/merge_strategy",
                    POLICY_TYPE_MERGE_STRATEGY,
                    desired_settings,
                    pol.blocking,
                    pol.enabled,
                    existing_pol,
                )
            )

        # -- Work item linking --
        if desired.work_item is not None:
            pol = desired.work_item
            desired_settings = {"scope": _scope_for_branch(repo.id, branch)}
            existing_pol = _find_existing(POLICY_TYPE_WORK_ITEM_LINKING)
            changes.extend(
                self._diff_policy(
                    prefix + "/work_item",
                    POLICY_TYPE_WORK_ITEM_LINKING,
                    desired_settings,
                    pol.blocking,
                    pol.enabled,
                    existing_pol,
                )
            )

        # -- Build validations --
        for bv in desired.build_validations:
            desired_settings = _build_validation_settings(repo.id, branch, bv)
            existing_pol = _find_existing(
                POLICY_TYPE_BUILD_VALIDATION,
                {"buildDefinitionId": bv.build_definition_id},
            )
            changes.extend(
                self._diff_policy(
                    prefix + f"/build_validation/{bv.build_definition_id}",
                    POLICY_TYPE_BUILD_VALIDATION,
                    desired_settings,
                    bv.blocking,
                    bv.enabled,
                    existing_pol,
                )
            )

        # -- Status checks --
        for sc in desired.statuses:
            desired_settings = _status_settings(repo.id, branch, sc)
            existing_pol = _find_existing(
                POLICY_TYPE_STATUS_CHECK,
                {"statusName": sc.status_name, "statusGenre": sc.status_genre},
            )
            changes.extend(
                self._diff_policy(
                    prefix + f"/status/{sc.status_genre}/{sc.status_name}",
                    POLICY_TYPE_STATUS_CHECK,
                    desired_settings,
                    sc.blocking,
                    sc.enabled,
                    existing_pol,
                )
            )

        return changes

    def _diff_policy(
        self,
        resource: str,
        type_id: str,
        desired_settings: dict[str, Any],
        blocking: bool,
        enabled: bool,
        existing: Any | None,
    ) -> list[Change]:
        """Return CREATE or UPDATE changes if policy differs from desired state."""
        if existing is None:
            return [
                Change(
                    change_type=ChangeType.CREATE_POLICY,
                    resource=resource,
                    details={
                        "project": self._config.project,
                        "type_id": type_id,
                        "settings": desired_settings,
                        "blocking": blocking,
                        "enabled": enabled,
                    },
                )
            ]
        # Check if update needed
        needs_update = (
            existing.is_blocking != blocking
            or existing.is_enabled != enabled
            or not _settings_equal(existing.settings or {}, desired_settings)
        )
        if needs_update:
            return [
                Change(
                    change_type=ChangeType.UPDATE_POLICY,
                    resource=resource,
                    details={
                        "project": self._config.project,
                        "policy_id": existing.id,
                        "type_id": type_id,
                        "settings": desired_settings,
                        "blocking": blocking,
                        "enabled": enabled,
                    },
                )
            ]
        return []

    def _plan_security(
        self, repo: Any, desired: SecuritySettings
    ) -> list[Change]:
        changes: list[Change] = []
        if not desired.permissions:
            return changes

        project_id = repo.project.id if repo.project else None
        if project_id is None:
            logger.warning(
                "Cannot determine project ID for repo '%s' – skipping security reconciliation.",
                repo.name,
            )
            return changes

        existing_acl = self._client.query_repo_acl(project_id, repo.id)
        existing_by_descriptor = {
            entry["descriptor"]: entry for entry in existing_acl
        }

        for perm_entry in desired.permissions:
            descriptor = self._client.resolve_principal_descriptor(
                self._config.project, perm_entry.principal
            )
            if descriptor is None:
                logger.warning(
                    "Cannot resolve principal '%s' – skipping permission entry.",
                    perm_entry.principal,
                )
                continue

            allow_bits = _bits_for_names(perm_entry.allow)
            deny_bits = _bits_for_names(perm_entry.deny)

            current = existing_by_descriptor.get(descriptor, {})
            if current.get("allow") != allow_bits or current.get("deny") != deny_bits:
                changes.append(
                    Change(
                        change_type=ChangeType.SET_PERMISSIONS,
                        resource=f"repository/{repo.name}/permissions/{perm_entry.principal}",
                        details={
                            "project_id": project_id,
                            "repo_id": repo.id,
                            "descriptor": descriptor,
                            "allow_bits": allow_bits,
                            "deny_bits": deny_bits,
                        },
                    )
                )
        return changes

    # ------------------------------------------------------------------
    # Apply helpers
    # ------------------------------------------------------------------

    def _apply_change(self, change: Change) -> None:
        d = change.details
        if change.change_type == ChangeType.UPDATE_REPO:
            self._client.update_repository(
                project=d["project"],
                repo_id=d["repo_id"],
                default_branch=d.get("default_branch"),
                is_disabled=d.get("is_disabled"),
            )
        elif change.change_type == ChangeType.CREATE_POLICY:
            self._client.create_policy(
                project=d["project"],
                policy_type_id=d["type_id"],
                settings=d["settings"],
                blocking=d["blocking"],
                enabled=d["enabled"],
            )
        elif change.change_type == ChangeType.UPDATE_POLICY:
            self._client.update_policy(
                project=d["project"],
                policy_id=d["policy_id"],
                policy_type_id=d["type_id"],
                settings=d["settings"],
                blocking=d["blocking"],
                enabled=d["enabled"],
            )
        elif change.change_type == ChangeType.DELETE_POLICY:
            self._client.delete_policy(
                project=d["project"],
                policy_id=d["policy_id"],
            )
        elif change.change_type == ChangeType.SET_PERMISSIONS:
            self._client.set_repo_permissions(
                project_id=d["project_id"],
                repo_id=d["repo_id"],
                descriptor=d["descriptor"],
                allow_bits=d["allow_bits"],
                deny_bits=d["deny_bits"],
            )
        else:
            raise ValueError(f"Unknown change type: {change.change_type}")
